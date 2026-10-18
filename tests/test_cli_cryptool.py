"""cryptool 工具测试。

验证 ``fcmd.dsl.actions.crypto`` 模块：
- 工具注册与三子命令结构（genkey/encrypt/decrypt）
- 密钥模式与密码模式的加解密往返一致性
- 密文格式与密钥派生正确性
- 错误分支（无效密钥/密文/密码、密文损坏、密钥长度错误）
- CLI 子命令端到端

实现基于 ``cryptography`` 的 Fernet（AES-128-CBC + HMAC-SHA256，Encrypt-then-MAC）。
"""

from __future__ import annotations

import base64

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool
from fcmd.dsl.actions.crypto import (
    _KEY_BYTES,
    _SALT_BYTES,
    _b64decode,
    _cryptool,
    _fernet,
    _validate_key,
)

# 固定密钥与密码，避免随机干扰错误分支测试
# 注：随机密钥的 urlsafe base64 有概率以 "-" 开头，被 argparse 误判为选项导致 flaky；
# 因此用确定性密钥：32 字节原始密钥的 url-safe base64
_KEY = base64.urlsafe_b64encode(bytes(range(_KEY_BYTES))).decode("ascii")
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
        """生成的密钥为 url-safe base64 编码的 32 字节。"""
        key = _cryptool.random_key
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
        assert len(raw) == _KEY_BYTES

    def test_generate_key_unique(self) -> None:
        """两次生成的密钥不同（随机性）。"""
        assert _cryptool.random_key != _cryptool.random_key

    def test_derive_key_length(self) -> None:
        """派生密钥解码后为 32 字节。"""
        key = _cryptool.derive_key("password", _SALT)
        assert len(base64.urlsafe_b64decode(key)) == _KEY_BYTES

    def test_derive_key_deterministic(self) -> None:
        """相同密码与盐派生出相同密钥。"""
        assert _cryptool.derive_key("password", _SALT) == _cryptool.derive_key("password", _SALT)

    def test_derive_key_different_password(self) -> None:
        """不同密码派生出不同密钥。"""
        assert _cryptool.derive_key("password1", _SALT) != _cryptool.derive_key("password2", _SALT)

    def test_derive_key_different_salt(self) -> None:
        """不同盐派生出不同密钥。"""
        k1 = _cryptool.derive_key("password", b"0123456789abcdef")
        k2 = _cryptool.derive_key("password", b"fedcba9876543210")
        assert k1 != k2


# ============================================================================ #
# 密钥模式加解密
# ============================================================================ #
class TestEncryptWithKey:
    """密钥模式加解密测试。"""

    def test_round_trip_basic(self) -> None:
        """基本往返一致。"""
        token = _cryptool.encrypt("hello", _KEY)
        assert _cryptool.decrypt(token, _KEY) == "hello"

    @pytest.mark.parametrize("text", ["", "a", "hello", "中文测试", "hello world!", "a" * 100, "x" * 32])
    def test_round_trip_various(self, text: str) -> None:
        """多种文本往返一致。"""
        token = _cryptool.encrypt(text, _KEY)
        assert _cryptool.decrypt(token, _KEY) == text

    def test_ciphertext_differs_from_plaintext(self) -> None:
        """密文不等于明文。"""
        assert "hello" not in _cryptool.encrypt("hello", _KEY)

    def test_ciphertext_random_iv(self) -> None:
        """相同明文两次加密产生不同密文（随机 IV）。"""
        assert _cryptool.encrypt("hello", _KEY) != _cryptool.encrypt("hello", _KEY)

    def test_decrypt_wrong_key_raises(self) -> None:
        """用错误密钥解密抛 ValueError。"""
        token = _cryptool.encrypt("hello", _KEY)
        with pytest.raises(ValueError, match="认证失败"):
            _cryptool.decrypt(token, _cryptool.random_key)

    def test_decrypt_tampered_ciphertext_raises(self) -> None:
        """篡改密文后认证失败。"""
        token = _cryptool.encrypt("hello", _KEY)
        # 篡改 base64 中的一个字符
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(ValueError):
            _cryptool.decrypt(tampered, _KEY)

    def test_decrypt_invalid_key_raises(self) -> None:
        """非 base64 密钥抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的 base64 格式"):
            _cryptool.decrypt("token", "!!!not base64!!!")


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
        assert _cryptool.encrypt_with_password("hello", _PASSWORD) != _cryptool.encrypt_with_password(
            "hello", _PASSWORD
        )

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
        """过短密文（不足盐长度）抛 ValueError。"""
        short = base64.urlsafe_b64encode(b"tooshort").decode("ascii")
        with pytest.raises(ValueError, match="密文过短"):
            _cryptool.decrypt_with_password(short, _PASSWORD)

    def test_password_blob_layout(self) -> None:
        """密码模式密文布局：16 字节盐 + Fernet token。"""
        raw = base64.urlsafe_b64decode(_cryptool.encrypt_with_password("hello", _PASSWORD))
        assert len(raw) > _SALT_BYTES
        token_raw = base64.urlsafe_b64decode(raw[_SALT_BYTES:])
        assert token_raw[0] == 0x80  # Fernet 版本字节


# ============================================================================ #
# 内部辅助函数
# ============================================================================ #
class TestInternals:
    """内部辅助函数测试。"""

    def test_validate_key_valid(self) -> None:
        """有效密钥原样返回 url-safe base64。"""
        assert _validate_key(b"\x00" * _KEY_BYTES) == base64.urlsafe_b64encode(b"\x00" * _KEY_BYTES)

    def test_validate_key_wrong_length_raises(self) -> None:
        """密钥长度不是 32 字节抛 ValueError。"""
        with pytest.raises(ValueError, match=f"密钥长度应为 {_KEY_BYTES} 字节"):
            _validate_key(b"short")

    def test_b64decode_invalid_raises(self) -> None:
        """非 base64 输入抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的 base64 格式"):
            _b64decode("!!!invalid!!!")

    def test_decrypt_non_utf8_plaintext_raises(self) -> None:
        """解密结果非有效 UTF-8 时抛 ValueError。

        直接用 ``_fernet`` 加密非 UTF-8 字节序列，绕过 ``encrypt`` 的 UTF-8 编码，
        使 ``decrypt`` 在认证通过后于 UTF-8 解码处失败。
        """
        # 0xff 0xfe 不是合法 UTF-8 序列开头
        token = _fernet(_KEY).encrypt(b"\xff\xfe\x00\x01").decode("ascii")
        with pytest.raises(ValueError, match="解密成功但非有效 UTF-8 文本"):
            _cryptool.decrypt(token, _KEY)


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
        assert len(raw) == _KEY_BYTES

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
        token = _cryptool.encrypt("hello", _KEY)
        code = run_tool("cryptool", ["decrypt", "--key", _KEY, "--quiet", "--", token])
        assert code == 0
        assert "hello" in capsys.readouterr().out

    def test_decrypt_with_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """密码模式解密还原明文。"""
        blob = _cryptool.encrypt_with_password("hello", "mypass")
        code = run_tool("cryptool", ["decrypt", "--password", "mypass", "--quiet", "--", blob])
        assert code == 0
        assert "hello" in capsys.readouterr().out

    def test_encrypt_no_credentials(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未指定 password/key 时提示。"""
        code = run_tool("cryptool", ["encrypt", "hello", "--quiet"])
        assert code == 0
        assert "请指定 --password 或 --key" in capsys.readouterr().out

    def test_decrypt_no_credentials(self, capsys: pytest.CaptureFixture[str]) -> None:
        """未指定 password/key 时提示。"""
        code = run_tool("cryptool", ["decrypt", "somedata", "--quiet"])
        assert code == 0
        assert "请指定 --password 或 --key" in capsys.readouterr().out

    def test_encrypt_invalid_key(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效密钥提示错误。"""
        code = run_tool("cryptool", ["encrypt", "hello", "--key", "!!!invalid!!!", "--quiet"])
        assert code == 0
        assert "无效的 base64 格式" in capsys.readouterr().out

    def test_decrypt_wrong_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """错误密码提示认证失败。"""
        blob = _cryptool.encrypt_with_password("hello", "correct")
        code = run_tool("cryptool", ["decrypt", "--password", "wrong", "--quiet", "--", blob])
        assert code == 0
        assert "认证失败" in capsys.readouterr().out

    def test_decrypt_invalid_base64(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效 base64 密文提示错误。"""
        code = run_tool("cryptool", ["decrypt", "!!!invalid!!!", "--password", "pw", "--quiet"])
        assert code == 0
        assert "无效的 base64 格式" in capsys.readouterr().out

    def test_encrypt_unicode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Unicode 文本加解密。"""
        blob = _cryptool.encrypt_with_password("中文测试", "pw")
        code = run_tool("cryptool", ["decrypt", "--password", "pw", "--quiet", "--", blob])
        assert code == 0
        assert "中文测试" in capsys.readouterr().out

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
        assert "hello" in capsys.readouterr().out

    def test_encrypt_with_env_key(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        """--env 从 FCMD_CRYPT_KEY 读取密钥加密。"""
        monkeypatch.setenv("FCMD_CRYPT_KEY", _KEY)
        monkeypatch.delenv("FCMD_CRYPT_PASSWORD", raising=False)
        code = run_tool("cryptool", ["encrypt", "hello", "--env", "--quiet"])
        assert code == 0
        out = capsys.readouterr().out.strip()
        token = out.split("加密结果: ", 1)[-1]
        assert _cryptool.decrypt(token, _KEY) == "hello"
