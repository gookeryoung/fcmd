"""hashtool 工具：字符串 MD5 / SHA1 / SHA256 / SHA512 哈希。

哈希计算在 :mod:`fcmd.infras.hashing`，本模块只做参数适配与输出。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import hashing


@fx.tool(
    "hashtool",
    subcommand="md5",
    help="计算 MD5 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_md5(text: str) -> None:
    """计算字符串的 MD5 哈希。"""
    print(hashing.hash_text(text, "md5"))


@fx.tool(
    "hashtool",
    subcommand="sha1",
    help="计算 SHA1 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_sha1(text: str) -> None:
    """计算字符串的 SHA1 哈希。"""
    print(hashing.hash_text(text, "sha1"))


@fx.tool(
    "hashtool",
    subcommand="sha256",
    help="计算 SHA256 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_sha256(text: str) -> None:
    """计算字符串的 SHA256 哈希。"""
    print(hashing.hash_text(text, "sha256"))


@fx.tool(
    "hashtool",
    subcommand="sha512",
    help="计算 SHA512 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_sha512(text: str) -> None:
    """计算字符串的 SHA512 哈希。"""
    print(hashing.hash_text(text, "sha512"))
