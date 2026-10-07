"""对称加密 DSL 动作：cryptool。

原模块 ``fcmd.cli.crypto.cryptool`` 为纯 Python 实现（hashlib / hmac / os
标准库），无子进程调用。采用 HMAC-SHA256 CTR 模式 + Encrypt-then-MAC
构造，支持密钥模式与密码模式两种加密方案。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

from fcmd.dsl.actions import action

__all__: list[str] = []

# PBKDF2 参数
_PBKDF2_ITERATIONS = 100_000
_SALT_BYTES = 16

# CTR 模式参数
_NONCE_BYTES = 16
_BLOCK_SIZE = 32  # HMAC-SHA256 输出长度（字节）
_TAG_BYTES = 32  # HMAC-SHA256 认证标签长度
_KEY_BYTES = 32  # 加密密钥 / MAC 密钥长度


# ============================================================================
# 公共类
# ============================================================================


class Cryptool:
    """加密工具。"""

    @property
    def random_key(self) -> str:
        """生成随机密钥（64 字节，url-safe base64 编码）。"""
        return base64.urlsafe_b64encode(os.urandom(_KEY_BYTES * 2)).decode("ascii")

    def get_derive_keys(self, password: str, salt: bytes) -> tuple[bytes, bytes]:
        """用 PBKDF2-HMAC-SHA256 从密码派生加密密钥与 MAC 密钥。"""
        material = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_BYTES * 2
        )
        return material[:_KEY_BYTES], material[_KEY_BYTES:]

    def encrypt(self, plaintext: str, enc_key: bytes, mac_key: bytes) -> str:
        """加密文本（使用密钥）。"""
        blob = _encrypt_bytes(plaintext.encode("utf-8"), enc_key, mac_key)
        return base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, token: str, enc_key: bytes, mac_key: bytes) -> str:
        """解密文本（使用密钥）。"""
        blob = _b64decode(token)
        plaintext = _decrypt_bytes(blob, enc_key, mac_key)
        return _decode_utf8(plaintext)

    def encrypt_with_password(self, plaintext: str, password: str) -> str:
        """加密文本（使用密码，随机盐混入密文）。"""
        salt = os.urandom(_SALT_BYTES)
        enc_key, mac_key = self.get_derive_keys(password, salt)
        blob = _encrypt_bytes(plaintext.encode("utf-8"), enc_key, mac_key)
        return base64.urlsafe_b64encode(salt + blob).decode("ascii")

    def decrypt_with_password(self, blob: str, password: str) -> str:
        """解密文本（使用密码）。"""
        raw = _b64decode(blob)
        if len(raw) < _SALT_BYTES + _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("密文过短，无法提取盐值")
        salt = raw[:_SALT_BYTES]
        enc_key, mac_key = self.get_derive_keys(password, salt)
        plaintext = _decrypt_bytes(raw[_SALT_BYTES:], enc_key, mac_key)
        return _decode_utf8(plaintext)


_cryptool = Cryptool()


# ============================================================================
# 内部辅助
# ============================================================================


def _parse_key(key: str) -> tuple[bytes, bytes]:
    """解析 url-safe base64 编码的密钥为 ``(enc_key, mac_key)``。"""
    raw = _b64decode(key)
    if len(raw) != _KEY_BYTES * 2:
        raise ValueError(f"密钥长度应为 {_KEY_BYTES * 2} 字节，实际 {len(raw)} 字节")
    return raw[:_KEY_BYTES], raw[_KEY_BYTES:]


def _ctr_crypt(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    """HMAC-SHA256 CTR 模式加解密（XOR 对称）。"""
    result = bytearray()
    counter = int.from_bytes(nonce, "big")
    for offset in range(0, len(data), _BLOCK_SIZE):
        counter_bytes = counter.to_bytes(_NONCE_BYTES, "big")
        keystream = hmac.new(enc_key, counter_bytes, hashlib.sha256).digest()
        block = data[offset : offset + _BLOCK_SIZE]
        xored = int.from_bytes(block, "big") ^ int.from_bytes(keystream[: len(block)], "big")
        result.extend(xored.to_bytes(len(block), "big"))
        counter += 1
    return bytes(result)


def _encrypt_bytes(plaintext: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
    """加密字节序列（返回 ``nonce + ciphertext + tag``）。"""
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = _ctr_crypt(plaintext, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return nonce + ciphertext + tag


def _decrypt_bytes(blob: bytes, enc_key: bytes, mac_key: bytes) -> bytes:
    """解密字节序列（输入 ``nonce + ciphertext + tag``），先验证认证标签。"""
    min_len = _NONCE_BYTES + _TAG_BYTES
    if len(blob) < min_len:
        raise ValueError("密文过短，无法解析")
    nonce = blob[:_NONCE_BYTES]
    tag = blob[-_TAG_BYTES:]
    ciphertext = blob[_NONCE_BYTES:-_TAG_BYTES]
    expected_tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("认证失败（密钥错误或密文损坏）")
    return _ctr_crypt(ciphertext, enc_key, nonce)


def _b64decode(text: str) -> bytes:
    """url-safe base64 解码，失败时抛 ``ValueError``。"""
    try:
        return base64.urlsafe_b64decode(text.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"无效的 base64 格式: {exc}") from exc


def _decode_utf8(data: bytes) -> str:
    """UTF-8 解码，失败时抛 ``ValueError``。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"解密成功但非有效 UTF-8 文本: {exc}") from exc


# ============================================================================
# DSL 动作
# ============================================================================


@action("cryptool_genkey", param_help={})
def cryptool_genkey() -> None:
    """生成一个新的 64 字节随机密钥并打印（url-safe base64 编码）。"""
    print(_cryptool.random_key)


@action(
    "cryptool_encrypt",
    param_help={
        "text": "待加密的明文",
        "password": "加密密码（与 key 二选一）",
        "key": "加密密钥（与 password 二选一）",
        "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
    },
)
def cryptool_encrypt(text: str, password: str = "", key: str = "", env: bool = False) -> None:
    """加密文本（需指定 password 或 key，二选一）。"""
    if env:
        password = os.getenv("FCMD_CRYPT_PASSWORD") or ""
        key = os.getenv("FCMD_CRYPT_KEY") or ""
    if not password and not key:
        print("错误: 请指定 --password 或 --key")
        return
    try:
        if key:
            enc_key, mac_key = _parse_key(key)
            result = _cryptool.encrypt(text, enc_key, mac_key)
        else:
            result = _cryptool.encrypt_with_password(text, password)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(f"加密结果: {result}")


@action(
    "cryptool_decrypt",
    param_help={
        "text": "待解密的密文",
        "password": "解密密码（与 key 二选一）",
        "key": "加密密钥（与 password 二选一）",
        "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
    },
)
def cryptool_decrypt(text: str, password: str = "", key: str = "", env: bool = False) -> None:
    """解密文本（需指定 password 或 key，二选一）。"""
    if env:
        password = os.getenv("FCMD_CRYPT_PASSWORD") or ""
        key = os.getenv("FCMD_CRYPT_KEY") or ""
    if not password and not key:
        print("错误: 请指定 --password 或 --key")
        return
    try:
        if key:
            enc_key, mac_key = _parse_key(key)
            result = _cryptool.decrypt(text, enc_key, mac_key)
        else:
            result = _cryptool.decrypt_with_password(text, password)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(f"解密结果: {result}")
