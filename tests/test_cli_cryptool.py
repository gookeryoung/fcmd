"""cryptool 工具测试。

验证 ``fcmd.cli.crypto.cryptool`` 模块：
- 工具注册与三子命令结构（genkey/encrypt/decrypt）
- 密钥模式与密码模式的加解密往返一致性
- 密文格式与密钥派生正确性
- 错误分支（无效密钥/密文/密码、密文损坏、密钥长度错误）
- CLI 子命令端到端
"""

from __future__ import annotations

import base64
import os

import pytest

from fcmd.apis.toolkit import list_subcommands, list_tools, run_tool
from fcmd.cli.crypto.cryptool import (
    _cryptool,
    _decrypt_bytes,
    _encrypt_bytes,
    _parse_key,
)

# 固定密钥与密码，避免随机干扰错误分支测试
# 注：曾用 _cryptool.random_key（@property 每次生成新随机串），但 urlsafe base64
# 有 1/64 概率以 "-" 开头，被 argparse 误判为选项导致 flaky；改用确定性 64 字节密钥
_KEY = base64.urlsafe_b64encode(bytes(range(64))).decode("ascii")
_PASSWORD = "test-password-123"
_SALT = b"0123456789abcdef"  # 16 字节固定盐


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """cryptool 已注册到工具表。"""
        assert "cryptool" in list_tools()

    def test_subcommands(self) -> None:
        """cryptool 有 genkey/encrypt/decrypt 三个子命令。"""
        subs = list_subcommands("cryptool")
        assert set(subs) == {"genkey", "encrypt", "decrypt"}


# ============================================================================ #
# 密钥生成与派生
# ============================================================================ #
class TestKeyManagement:
    """密钥生成与派生测试。"""

    def test_generate_key_is_base64(self) -> None:
        """生成的密钥为 url-safe base64 编码的 64 字节。"""
        key = _cryptool.random_key
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
        assert len(raw) == 64

    def test_generate_key_unique(self) -> None:
        """两次生成的密钥不同（随机性）。"""
        assert _cryptool.random_key != _cryptool.random_key

    def test_derive_keys_length(self) -> None:
        """派生密钥各 32 字节。"""
        enc_key, mac_key = _cryptool.get_derive_keys("password", _SALT)
        assert len(enc_key) == 32
        assert len(mac_key) == 32

    def test_derive_keys_deterministic(self) -> None:
        """相同密码与盐派生出相同密钥。"""
        k1 = _cryptool.get_derive_keys("password", _SALT)
        k2 = _cryptool.get_derive_keys("password", _SALT)
        assert k1 == k2

    def test_derive_keys_different_password(self) -> None:
        """不同密码派生出不同密钥。"""
        k1 = _cryptool.get_derive_keys("password1", _SALT)
        k2 = _cryptool.get_derive_keys("password2", _SALT)
        assert k1 != k2

    def test_derive_keys_different_salt(self) -> None:
        """不同盐派生出不同密钥。"""
        k1 = _cryptool.get_derive_keys("password", b"0123456789abcdef")
        k2 = _cryptool.get_derive_keys("password", b"fedcba9876543210")
        assert k1 != k2

    def test_derive_keys_enc_mac_distinct(self) -> None:
        """加密密钥与 MAC 密钥不同。"""
        enc_key, mac_key = _cryptool.get_derive_keys("password", _SALT)
        assert enc_key != mac_key


# ============================================================================ #
# 密钥模式加解密
# ============================================================================ #
class TestEncryptWithKey:
    """密钥模式加解密测试。"""

    def test_round_trip_basic(self) -> None:
        """基本往返一致。"""
        enc_key, mac_key = _parse_key(_KEY)
        token = _cryptool.encrypt("hello", enc_key, mac_key)
        assert _cryptool.decrypt(token, enc_key, mac_key) == "hello"

    @pytest.mark.parametrize("text", ["", "a", "hello", "中文测试", "hello world!", "a" * 100, "x" * 32])
    def test_round_trip_various(self, text: str) -> None:
        """多种文本往返一致。"""
        enc_key, mac_key = _parse_key(_KEY)
        token = _cryptool.encrypt(text, enc_key, mac_key)
        assert _cryptool.decrypt(token, enc_key, mac_key) == text

    def test_ciphertext_differs_from_plaintext(self) -> None:
        """密文不等于明文。"""
        enc_key, mac_key = _parse_key(_KEY)
        token = _cryptool.encrypt("hello", enc_key, mac_key)
        assert "hello" not in token

    def test_ciphertext_random_nonce(self) -> None:
        """相同明文两次加密产生不同密文（随机 nonce）。"""
        enc_key, mac_key = _parse_key(_KEY)
        t1 = _cryptool.encrypt("hello", enc_key, mac_key)
        t2 = _cryptool.encrypt("hello", enc_key, mac_key)
        assert t1 != t2

    def test_decrypt_wrong_key_raises(self) -> None:
        """用错误密钥解密抛 ValueError。"""
        enc_key, mac_key = _parse_key(_KEY)
        token = _cryptool.encrypt("hello", enc_key, mac_key)
        other_key = _cryptool.random_key
        wrong_enc, wrong_mac = _parse_key(other_key)
        with pytest.raises(ValueError, match="认证失败"):
            _cryptool.decrypt(token, wrong_enc, wrong_mac)

    def test_decrypt_tampered_ciphertext_raises(self) -> None:
        """篡改密文后认证失败。"""
        enc_key, mac_key = _parse_key(_KEY)
        token = _cryptool.encrypt("hello", enc_key, mac_key)
        # 篡改 base64 中的一个字符
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(ValueError):
            _cryptool.decrypt(tampered, enc_key, mac_key)

    def test_decrypt_invalid_base64_raises(self) -> None:
        """非 base64 输入抛 ValueError。"""
        enc_key, mac_key = _parse_key(_KEY)
        with pytest.raises(ValueError, match="无效的 base64 格式"):
            _cryptool.decrypt("!!!not base64!!!", enc_key, mac_key)

    def test_decrypt_short_ciphertext_raises(self) -> None:
        """过短密文抛 ValueError。"""
        enc_key, mac_key = _parse_key(_KEY)
        short = base64.urlsafe_b64encode(b"tooshort").decode("ascii")
        with pytest.raises(ValueError, match="密文过短"):
            _cryptool.decrypt(short, enc_key, mac_key)


# ============================================================================ #
# 密码模式加解密
# ============================================================================ #
class TestEncryptWithPassword:
    """密码模式加解密测试。"""

    def test_round_trip_basic(self) -> None:
        """基本往返一致。"""
        blob = _cryptool.encrypt_with_password("hello", _PASSWORD)
        assert _cryptool.decrypt_with_password(blob, _PASSWORD) == "hello"

    @pytest.mark.parametrize("text", ["", "a", "hello", "中文测试", "hello world!", "a" * 100, "x" * 32])
    def test_round_trip_various(self, text: str) -> None:
        """多种文本往返一致。"""
        blob = _cryptool.encrypt_with_password(text, _PASSWORD)
        assert _cryptool.decrypt_with_password(blob, _PASSWORD) == text

    def test_ciphertext_random_salt(self) -> None:
        """相同明文+密码两次加密产生不同密文（随机盐）。"""
        b1 = _cryptool.encrypt_with_password("hello", _PASSWORD)
        b2 = _cryptool.encrypt_with_password("hello", _PASSWORD)
        assert b1 != b2

    def test_decrypt_wrong_password_raises(self) -> None:
        """错误密码解密抛 ValueError。"""
        blob = _cryptool.encrypt_with_password("hello", _PASSWORD)
        with pytest.raises(ValueError, match="认证失败"):
            _cryptool.decrypt_with_password(blob, "wrong-password")

    def test_decrypt_invalid_base64_raises(self) -> None:
        """非 base64 输入抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的 base64 格式"):
            _cryptool.decrypt_with_password("!!!not base64!!!", _PASSWORD)

    def test_decrypt_short_ciphertext_raises(self) -> None:
        """过短密文抛 ValueError。"""
        short = base64.urlsafe_b64encode(b"tooshort").decode("ascii")
        with pytest.raises(ValueError, match="密文过短"):
            _cryptool.decrypt_with_password(short, _PASSWORD)

    def test_password_blob_longer_than_key_blob(self) -> None:
        """密码模式密文比密钥模式多 16 字节盐。"""
        enc_key, mac_key = _parse_key(_KEY)
        key_blob = _cryptool.encrypt("hello", enc_key, mac_key)
        pwd_blob = _cryptool.encrypt_with_password("hello", _PASSWORD)
        key_raw = base64.urlsafe_b64decode(key_blob)
        pwd_raw = base64.urlsafe_b64decode(pwd_blob)
        assert len(pwd_raw) == len(key_raw) + 16  # 多 16 字节盐


# ============================================================================ #
# 内部辅助函数
# ============================================================================ #
class TestInternals:
    """内部辅助函数测试。"""

    def test_parse_key_valid(self) -> None:
        """有效密钥解析为两个 32 字节密钥。"""
        enc_key, mac_key = _parse_key(_KEY)
        assert len(enc_key) == 32
        assert len(mac_key) == 32
        assert enc_key != mac_key

    def test_parse_key_wrong_length_raises(self) -> None:
        """密钥长度不是 64 字节抛 ValueError。"""
        bad_key = base64.urlsafe_b64encode(b"short").decode("ascii")
        with pytest.raises(ValueError, match="密钥长度应为 64 字节"):
            _parse_key(bad_key)

    def test_parse_key_invalid_base64_raises(self) -> None:
        """非 base64 密钥抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的 base64 格式"):
            _parse_key("!!!invalid!!!")

    def test_encrypt_decrypt_bytes_round_trip(self) -> None:
        """_encrypt_bytes / _decrypt_bytes 往返一致。"""
        enc_key, mac_key = _parse_key(_KEY)
        plaintext = b"test plaintext bytes"
        blob = _encrypt_bytes(plaintext, enc_key, mac_key)
        assert _decrypt_bytes(blob, enc_key, mac_key) == plaintext

    def test_decrypt_bytes_tampered_tag_raises(self) -> None:
        """篡改认证标签后抛 ValueError。"""
        enc_key, mac_key = _parse_key(_KEY)
        blob = bytearray(_encrypt_bytes(b"hello", enc_key, mac_key))
        # 翻转最后一个字节（tag 末尾）
        blob[-1] ^= 0xFF
        with pytest.raises(ValueError, match="认证失败"):
            _decrypt_bytes(bytes(blob), enc_key, mac_key)

    def test_decrypt_bytes_tampered_nonce_raises(self) -> None:
        """篡改 nonce 后抛 ValueError。"""
        enc_key, mac_key = _parse_key(_KEY)
        blob = bytearray(_encrypt_bytes(b"hello", enc_key, mac_key))
        # 翻转第一个字节（nonce 末尾）
        blob[0] ^= 0xFF
        with pytest.raises(ValueError, match="认证失败"):
            _decrypt_bytes(bytes(blob), enc_key, mac_key)

    def test_decrypt_bytes_short_raises(self) -> None:
        """过短输入抛 ValueError。"""
        enc_key, mac_key = _parse_key(_KEY)
        with pytest.raises(ValueError, match="密文过短"):
            _decrypt_bytes(b"short", enc_key, mac_key)

    def test_ctr_crypt_empty(self) -> None:
        """空数据 CTR 加密返回空。"""
        from fcmd.cli.crypto.cryptool import _ctr_crypt

        result = _ctr_crypt(b"", b"\x00" * 32, b"\x00" * 16)
        assert result == b""

    def test_ctr_crypt_symmetric(self) -> None:
        """CTR 模式加解密对称（同一密钥+nonce 两次 XOR 还原）。"""
        from fcmd.cli.crypto.cryptool import _ctr_crypt

        enc_key = os.urandom(32)
        nonce = os.urandom(16)
        data = b"hello world " * 10
        encrypted = _ctr_crypt(data, enc_key, nonce)
        decrypted = _ctr_crypt(encrypted, enc_key, nonce)
        assert decrypted == data

    def test_decrypt_non_utf8_plaintext_raises(self) -> None:
        """解密结果非有效 UTF-8 时抛 ValueError。

        通过 ``_encrypt_bytes`` 直接加密非 UTF-8 字节序列，绕过 ``encrypt`` 的 UTF-8 编码，
        使 ``decrypt`` 在 MAC 验证通过后于 ``_decode_utf8`` 处失败。
        """
        import base64 as _b64

        from fcmd.cli.crypto.cryptool import _encrypt_bytes

        enc_key, mac_key = _parse_key(_KEY)
        # 0xff 0xfe 不是合法 UTF-8 序列开头
        blob = _encrypt_bytes(b"\xff\xfe\x00\x01", enc_key, mac_key)
        token = _b64.urlsafe_b64encode(blob).decode("ascii")
        with pytest.raises(ValueError, match="解密成功但非有效 UTF-8 文本"):
            _cryptool.decrypt(token, enc_key, mac_key)


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestCryptoolCLI:
    """``cryptool`` 通过 ``run_tool`` 调用测试。"""

    def test_genkey(self, capsys: pytest.CaptureFixture[str]) -> None:
        """genkey 生成 base64 密钥。"""
        code = run_tool("cryptool", ["genkey", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out.strip()
        raw = base64.urlsafe_b64decode(out.encode("ascii"))
        assert len(raw) == 64

    def test_encrypt_with_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """密钥模式加密产生 base64 密文。"""
        code = run_tool("cryptool", ["encrypt", "hello", "--key", _KEY, "--quiet"])
        assert code == 0
        out = capsys.readouterr().out.strip()
        token = out.split("加密结果: ", 1)[-1]
        assert token  # 非空
        base64.urlsafe_b64decode(token.encode("ascii"))  # 可解码为 base64

    def test_encrypt_with_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """密码模式加密产生 base64 密文。"""
        code = run_tool("cryptool", ["encrypt", "hello", "--password", "mypass", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out.strip()
        blob = out.split("加密结果: ", 1)[-1]
        assert blob
        base64.urlsafe_b64decode(blob.encode("ascii"))

    def test_decrypt_with_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """密钥模式解密还原明文。"""
        enc_key, mac_key = _parse_key(_KEY)
        token = _cryptool.encrypt("hello", enc_key, mac_key)
        code = run_tool("cryptool", ["decrypt", "--key", _KEY, "--quiet", "--", token])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_decrypt_with_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """密码模式解密还原明文。"""
        blob = _cryptool.encrypt_with_password("hello", "mypass")
        code = run_tool("cryptool", ["decrypt", "--password", "mypass", "--quiet", "--", blob])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_encrypt_no_credentials(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未指定 password/key 时提示。"""
        code = run_tool("cryptool", ["encrypt", "hello", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请指定 --password 或 --key" in out

    def test_decrypt_no_credentials(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未指定 password/key 时提示。"""
        code = run_tool("cryptool", ["decrypt", "somedata", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请指定 --password 或 --key" in out

    def test_encrypt_invalid_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效密钥提示错误。"""
        code = run_tool("cryptool", ["encrypt", "hello", "--key", "!!!invalid!!!", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的 base64 格式" in out

    def test_decrypt_wrong_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """错误密码提示认证失败。"""
        blob = _cryptool.encrypt_with_password("hello", "correct")
        code = run_tool("cryptool", ["decrypt", "--password", "wrong", "--quiet", "--", blob])
        assert code == 0
        out = capsys.readouterr().out
        assert "认证失败" in out

    def test_decrypt_invalid_base64(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效 base64 密文提示错误。"""
        code = run_tool("cryptool", ["decrypt", "!!!invalid!!!", "--password", "pw", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的 base64 格式" in out

    def test_encrypt_unicode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Unicode 文本加解密。"""
        blob = _cryptool.encrypt_with_password("中文测试", "pw")
        code = run_tool("cryptool", ["decrypt", "--password", "pw", "--quiet", "--", blob])
        assert code == 0
        out = capsys.readouterr().out
        assert "中文测试" in out

    def test_encrypt_with_env_password(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--env 从 FCMD_CRYPT_PASSWORD 读取密码加密。"""
        monkeypatch.setenv("FCMD_CRYPT_PASSWORD", "env-pwd")
        monkeypatch.delenv("FCMD_CRYPT_KEY", raising=False)
        code = run_tool("cryptool", ["encrypt", "hello", "--env", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out.strip()
        blob = out.split("加密结果: ", 1)[-1]
        # 用相同密码解密应还原明文
        assert _cryptool.decrypt_with_password(blob, "env-pwd") == "hello"

    def test_decrypt_with_env_password(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--env 从 FCMD_CRYPT_PASSWORD 读取密码解密。"""
        blob = _cryptool.encrypt_with_password("hello", "env-pwd")
        monkeypatch.setenv("FCMD_CRYPT_PASSWORD", "env-pwd")
        monkeypatch.delenv("FCMD_CRYPT_KEY", raising=False)
        code = run_tool("cryptool", ["decrypt", "--env", "--quiet", "--", blob])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_encrypt_with_env_key(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """--env 从 FCMD_CRYPT_KEY 读取密钥加密。"""
        monkeypatch.setenv("FCMD_CRYPT_KEY", _KEY)
        monkeypatch.delenv("FCMD_CRYPT_PASSWORD", raising=False)
        code = run_tool("cryptool", ["encrypt", "hello", "--env", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out.strip()
        token = out.split("加密结果: ", 1)[-1]
        enc_key, mac_key = _parse_key(_KEY)
        assert _cryptool.decrypt(token, enc_key, mac_key) == "hello"
