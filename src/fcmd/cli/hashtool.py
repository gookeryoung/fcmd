"""hashtool - 字符串哈希工具。

基于标准库 ``hashlib`` 提供常见哈希算法：MD5、SHA1、SHA256、SHA512。
输出为小写十六进制摘要。

示例
----
    fcmd hashtool md5 "hello"                # 计算 MD5
    fcmd hashtool sha1 "hello"               # 计算 SHA1
    fcmd hashtool sha256 "hello"             # 计算 SHA256
    fcmd hashtool sha512 "hello"             # 计算 SHA512
"""

from __future__ import annotations

import hashlib

import fcmd

__all__ = [
    "hash_md5",
    "hash_sha1",
    "hash_sha256",
    "hash_sha512",
    "list_algorithms",
]

# 支持的哈希算法映射
_ALGORITHMS: dict[str, str] = {
    "md5": "md5",
    "sha1": "sha1",
    "sha256": "sha256",
    "sha512": "sha512",
}


def list_algorithms() -> list[str]:
    """列出支持的哈希算法名。"""
    return list(_ALGORITHMS.keys())


def _hash(text: str, algorithm: str) -> str:
    """通用哈希计算。

    Parameters
    ----------
    text:
        待哈希的字符串
    algorithm:
        ``hashlib`` 算法名（如 ``md5``/``sha256``）

    Returns
    -------
    str
        小写十六进制摘要
    """
    h = hashlib.new(algorithm)
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def hash_md5(text: str) -> str:
    """计算 MD5 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串

    Returns
    -------
    str
        32 字符小写十六进制摘要
    """
    return _hash(text, "md5")


def hash_sha1(text: str) -> str:
    """计算 SHA1 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串

    Returns
    -------
    str
        40 字符小写十六进制摘要
    """
    return _hash(text, "sha1")


def hash_sha256(text: str) -> str:
    """计算 SHA256 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串

    Returns
    -------
    str
        64 字符小写十六进制摘要
    """
    return _hash(text, "sha256")


def hash_sha512(text: str) -> str:
    """计算 SHA512 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串

    Returns
    -------
    str
        128 字符小写十六进制摘要
    """
    return _hash(text, "sha512")


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("hashtool", subcommand="md5", help="计算 MD5 哈希")
def md5_cmd(text: str) -> None:
    """计算字符串的 MD5 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串
    """
    print(hash_md5(text))


@fcmd.tool("hashtool", subcommand="sha1", help="计算 SHA1 哈希")
def sha1_cmd(text: str) -> None:
    """计算字符串的 SHA1 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串
    """
    print(hash_sha1(text))


@fcmd.tool("hashtool", subcommand="sha256", help="计算 SHA256 哈希")
def sha256_cmd(text: str) -> None:
    """计算字符串的 SHA256 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串
    """
    print(hash_sha256(text))


@fcmd.tool("hashtool", subcommand="sha512", help="计算 SHA512 哈希")
def sha512_cmd(text: str) -> None:
    """计算字符串的 SHA512 哈希。

    Parameters
    ----------
    text:
        待哈希的字符串
    """
    print(hash_sha512(text))


@fcmd.main("hashtool")
def main() -> None:
    pass
