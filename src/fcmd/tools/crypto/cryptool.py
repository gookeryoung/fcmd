"""cryptool 工具：Fernet 密钥生成、加密与解密。

加解密逻辑在 :mod:`fcmd.infras.crypto`，本模块只做参数适配、环境变量读取与输出。
"""

from __future__ import annotations

import os

import fcmd as fx
from fcmd.infras import crypto


def _require_fernet() -> bool:
    """cryptography 未安装时打印提示，返回是否可用。"""
    if not crypto.HAS_CRYPTOGRAPHY:
        print("未安装 cryptography 库，请安装: pip install fcmd[crypto]")
        return False
    return True


@fx.tool("cryptool", subcommand="genkey", help="生成加密密钥", param_help={})
def cryptool_genkey() -> None:
    """生成一个新的 Fernet 密钥并打印（32 字节，url-safe base64）。"""
    if not _require_fernet():
        return
    print(crypto._cryptool.random_key)


@fx.tool(
    "cryptool",
    subcommand="encrypt",
    help="加密文本",
    param_help={
        "text": "待加密的明文",
        "password": "加密密码（与 key 二选一）",
        "key": "加密密钥（与 password 二选一）",
        "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
    },
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
    try:
        result = crypto._cryptool.encrypt(text, key) if key else crypto._cryptool.encrypt_with_password(text, password)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(f"加密结果: {result}")


@fx.tool(
    "cryptool",
    subcommand="decrypt",
    help="解密文本",
    param_help={
        "text": "待解密的密文",
        "password": "解密密码（与 key 二选一）",
        "key": "加密密钥（与 password 二选一）",
        "env": "为 True 时从 FCMD_CRYPT_PASSWORD / FCMD_CRYPT_KEY 环境变量读取",
    },
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
    try:
        result = crypto._cryptool.decrypt(text, key) if key else crypto._cryptool.decrypt_with_password(text, password)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(f"解密结果: {result}")
