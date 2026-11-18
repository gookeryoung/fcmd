"""hashtool 工具：字符串 MD5 / SHA1 / SHA256 / SHA512 哈希。

哈希计算在 :mod:`fcmd.infras.hashing`，本模块只做参数适配与输出。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.core.result import emit_result
from fcmd.infras import hashing


def _hash(subcommand: str, algorithm: str, text: str, json_output: bool) -> None:
    """计算哈希：JSON 模式输出含 ``algorithm`` / ``digest`` 的信封。"""
    digest = hashing.hash_text(text, algorithm)
    if json_output:
        emit_result("hashtool", subcommand, {"algorithm": algorithm, "digest": digest})
        return
    print(digest)


@fx.tool(
    "hashtool",
    subcommand="md5",
    help="计算 MD5 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_md5(text: str, json_output: bool = False) -> None:
    """计算字符串的 MD5 哈希。"""
    _hash("md5", "md5", text, json_output)


@fx.tool(
    "hashtool",
    subcommand="sha1",
    help="计算 SHA1 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_sha1(text: str, json_output: bool = False) -> None:
    """计算字符串的 SHA1 哈希。"""
    _hash("sha1", "sha1", text, json_output)


@fx.tool(
    "hashtool",
    subcommand="sha256",
    help="计算 SHA256 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_sha256(text: str, json_output: bool = False) -> None:
    """计算字符串的 SHA256 哈希。"""
    _hash("sha256", "sha256", text, json_output)


@fx.tool(
    "hashtool",
    subcommand="sha512",
    help="计算 SHA512 哈希",
    param_help={"text": "待哈希的字符串"},
)
def hashtool_sha512(text: str, json_output: bool = False) -> None:
    """计算字符串的 SHA512 哈希。"""
    _hash("sha512", "sha512", text, json_output)
