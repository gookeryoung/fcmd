"""哈希工具动作：字符串哈希（hashtool）与文件哈希（hashfile）。

业务实现统一在 :mod:`fcmd.tools.hashbase`；本模块只保留 ``@action``
声明与参数转接。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.dsl.actions import action
from fcmd.tools import hashbase

__all__: list[str] = []


# ---------------------------------------------------------------------- #
# 字符串哈希（hashtool）
# ---------------------------------------------------------------------- #
@action("hashtool_md5", param_help={"text": "待哈希的字符串"})
def _hashtool_md5(text: str) -> None:
    """计算字符串的 MD5 哈希。"""
    print(hashbase.hash_text(text, "md5"))


@action("hashtool_sha1", param_help={"text": "待哈希的字符串"})
def _hashtool_sha1(text: str) -> None:
    """计算字符串的 SHA1 哈希。"""
    print(hashbase.hash_text(text, "sha1"))


@action("hashtool_sha256", param_help={"text": "待哈希的字符串"})
def _hashtool_sha256(text: str) -> None:
    """计算字符串的 SHA256 哈希。"""
    print(hashbase.hash_text(text, "sha256"))


@action("hashtool_sha512", param_help={"text": "待哈希的字符串"})
def _hashtool_sha512(text: str) -> None:
    """计算字符串的 SHA512 哈希。"""
    print(hashbase.hash_text(text, "sha512"))


# ---------------------------------------------------------------------- #
# 文件哈希（hashfile）
# ---------------------------------------------------------------------- #
@action(
    "hashfile_f",
    param_help={
        "path": "目标文件路径",
        "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）",
    },
)
def _hashfile_f(path: str, algorithm: str = "sha256") -> None:
    """计算并打印单个文件的哈希值。"""
    hashbase.ensure_algorithm(algorithm)
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    digest = hashbase.compute_file_hash(file_path, algorithm)
    print(f"{algorithm}  {digest}  {file_path}")


@action(
    "hashfile_d",
    param_help={
        "directory": "目标目录路径",
        "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）",
    },
)
def _hashfile_d(directory: str, algorithm: str = "sha256") -> None:
    """计算目录下所有文件的哈希值并打印。

    跳过常见忽略目录（.git/__pycache__ 等）与 .pyc/.pyo 文件。
    """
    hashbase.ensure_algorithm(algorithm)
    dir_path = Path(directory)
    if not dir_path.is_dir():
        print(f"目录不存在: {dir_path}")
        return
    for file_path in hashbase.iter_directory_files(dir_path):
        digest = hashbase.compute_file_hash(file_path, algorithm)
        print(f"{algorithm}  {digest}  {file_path}")
