"""对称加密 DSL 动作：cryptool。

加密由 `cryptography`_ 的 **Fernet** 承担（AES-128-CBC + HMAC-SHA256，
Encrypt-then-MAC，业界审计过的构造），不再自实现 CTR 模式与认证标签。

.. _cryptography: https://cryptography.io/en/latest/fernet/

两种凭据模式：

- **密钥模式**：``genkey`` 生成的 32 字节 Fernet 密钥（url-safe base64）。
- **密码模式**：随机盐 + PBKDF2-HMAC-SHA256（480k 轮）派生 Fernet 密钥，
  盐拼接在密文前，随密文一起传输。

依赖：``fcmd[crypto]``。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib.util
import os
from typing import Any

from fcmd.dsl.actions import action

__all__ = ["Cryptool"]

# cryptography 惰性导入：``dsl/actions/__init__`` 在每条 DSL 命令启动时 eager
# 导入全部动作模块，顶层导入会额外付出几十毫秒的 cryptography 导入税。
HAS_CRYPTOGRAPHY = importlib.util.find_spec("cryptography") is not None

# 惰性占位（_require_fernet 首次调用填充；类型为 Any，避免导入期依赖）
Fernet: Any = None
_InvalidToken: Any = None


# PBKDF2 参数（OWASP 2023 推荐：SHA-256 / 480k 轮）
_PBKDF2_ITERATIONS = 480_000
_SALT_BYTES = 16
_KEY_BYTES = 32


def _require_fernet() -> bool:
    """cryptography 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_CRYPTOGRAPHY:
        print("未安装 cryptography 库，请安装: pip install fcmd[crypto]")
        return False
    global Fernet, _InvalidToken
    if Fernet is None:
        from cryptography.fernet import Fernet as _fernet_cls
        from cryptography.fernet import InvalidToken as _invalid_token

        Fernet, _InvalidToken = _fernet_cls, _invalid_token
    return True


class Cryptool:
    """加解密门面：密钥模式与密码模式。"""

    @property
    def random_key(self) -> str:
        """生成随机 Fernet 密钥（32 字节，url-safe base64）。"""
        _require_fernet()
        return Fernet.generate_key().decode("ascii")

    def derive_key(self, password: str, salt: bytes) -> bytes:
        """用 PBKDF2-HMAC-SHA256 从密码派生 Fernet 密钥。"""
        material = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_KEY_BYTES)
        return base64.urlsafe_b64encode(material)

    def encrypt(self, plaintext: str, key: str) -> str:
        """用密钥加密文本，返回 url-safe base64 密文。"""
        return _fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str, key: str) -> str:
        """用密钥解密文本。"""
        return _decrypt_token(token, _fernet(key))

    def encrypt_with_password(self, plaintext: str, password: str) -> str:
        """用密码加密文本（随机盐前缀 + Fernet 密文）。"""
        salt = os.urandom(_SALT_BYTES)
        token = _fernet_from_derived(self.derive_key(password, salt)).encrypt(plaintext.encode("utf-8"))
        return base64.urlsafe_b64encode(salt + token).decode("ascii")

    def decrypt_with_password(self, blob: str, password: str) -> str:
        """用密码解密文本。"""
        raw = _b64decode(blob)
        if len(raw) <= _SALT_BYTES:
            raise ValueError("密文过短，无法提取盐值")
        salt, token = raw[:_SALT_BYTES], raw[_SALT_BYTES:]
        return _decrypt_token(token, _fernet_from_derived(self.derive_key(password, salt)))


_cryptool = Cryptool()


# ============================================================================
# 内部辅助
# ============================================================================


def _b64decode(text: str) -> bytes:
    """url-safe base64 解码，失败时抛 ``ValueError``。"""
    try:
        return base64.urlsafe_b64decode(text.encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"无效的 base64 格式: {exc}") from exc


def _validate_key(raw: bytes) -> bytes:
    """校验密钥长度并返回 Fernet 密钥（url-safe base64）。"""
    if len(raw) != _KEY_BYTES:
        raise ValueError(f"密钥长度应为 {_KEY_BYTES} 字节，实际 {len(raw)} 字节（请用 genkey 生成）")
    return base64.urlsafe_b64encode(raw)


def _fernet(key: str) -> Any:
    """从用户提供的密钥串构造 Fernet。"""
    _require_fernet()
    return Fernet(_validate_key(_b64decode(key)))


def _fernet_from_derived(key: bytes) -> Any:
    """从已编码的派生密钥构造 Fernet。"""
    _require_fernet()
    return Fernet(key)


def _decrypt_token(token: str | bytes, fernet: Any) -> str:
    """解密并校验认证标签，失败时转成 ``ValueError``。"""
    try:
        plaintext = fernet.decrypt(token)
    except _InvalidToken as exc:
        raise ValueError("认证失败（密钥或密码错误，或密文已损坏）") from exc
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"解密成功但非有效 UTF-8 文本: {exc}") from exc


# ============================================================================
# DSL 动作
# ============================================================================


@action("cryptool_genkey", param_help={})
def cryptool_genkey() -> None:
    """生成一个新的 Fernet 密钥并打印（32 字节，url-safe base64）。"""
    if not _require_fernet():
        return
    print(_cryptool.random_key)


@action(
    "cryptool_encrypt",
    param_help={
        "text": "待加密的明文",
        "password": "加密密码（与 key 二选一）",
        "key": "加密密钥（与 password 二选一）",
        "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
    },
    errors=(ValueError,),
)
def cryptool_encrypt(text: str, password: str = "", key: str = "", env: bool = False) -> None:
    """加密文本（需指定 password 或 key，二选一）。"""
    if not _require_fernet():
        return
    if env:
        password = os.getenv("FCMD_CRYPT_PASSWORD") or ""
        key = os.getenv("FCMD_CRYPT_KEY") or ""
    if not password and not key:
        print("错误: 请指定 --password 或 --key")
        return
    result = _cryptool.encrypt(text, key) if key else _cryptool.encrypt_with_password(text, password)
    print(f"加密结果: {result}")


@action(
    "cryptool_decrypt",
    param_help={
        "text": "待解密的密文",
        "password": "解密密码（与 key 二选一）",
        "key": "加密密钥（与 password 二选一）",
        "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
    },
    errors=(ValueError,),
)
def cryptool_decrypt(text: str, password: str = "", key: str = "", env: bool = False) -> None:
    """解密文本（需指定 password 或 key，二选一）。"""
    if not _require_fernet():
        return
    if env:
        password = os.getenv("FCMD_CRYPT_PASSWORD") or ""
        key = os.getenv("FCMD_CRYPT_KEY") or ""
    if not password and not key:
        print("错误: 请指定 --password 或 --key")
        return
    result = _cryptool.decrypt(text, key) if key else _cryptool.decrypt_with_password(text, password)
    print(f"解密结果: {result}")
