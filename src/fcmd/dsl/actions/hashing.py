"""哈希工具动作：字符串哈希（hashtool）与文件哈希（hashfile）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fcmd.dsl.actions import action

__all__: list[str] = []

# 支持的哈希算法（供文件哈希命令的 --algorithm 参数 choices 白名单）
_FILE_ALGORITHMS: frozenset[str] = frozenset({"md5", "sha1", "sha256", "sha512"})

# 文件哈希分块大小（64 KB）
_CHUNK_SIZE = 64 * 1024


# ---------------------------------------------------------------------- #
# 字符串哈希（hashtool）
# ---------------------------------------------------------------------- #
def _hash_text(text: str, algorithm: str) -> str:
    """通用字符串哈希计算，返回小写十六进制摘要。"""
    h = hashlib.new(algorithm)
    h.update(text.encode("utf-8"))
    return h.hexdigest()


@action("hashtool_md5", param_help={"text": "待哈希的字符串"})
def _hashtool_md5(text: str) -> None:
    """计算字符串的 MD5 哈希。"""
    print(_hash_text(text, "md5"))


@action("hashtool_sha1", param_help={"text": "待哈希的字符串"})
def _hashtool_sha1(text: str) -> None:
    """计算字符串的 SHA1 哈希。"""
    print(_hash_text(text, "sha1"))


@action("hashtool_sha256", param_help={"text": "待哈希的字符串"})
def _hashtool_sha256(text: str) -> None:
    """计算字符串的 SHA256 哈希。"""
    print(_hash_text(text, "sha256"))


@action("hashtool_sha512", param_help={"text": "待哈希的字符串"})
def _hashtool_sha512(text: str) -> None:
    """计算字符串的 SHA512 哈希。"""
    print(_hash_text(text, "sha512"))


# ---------------------------------------------------------------------- #
# 文件哈希（hashfile）
# ---------------------------------------------------------------------- #
def _compute_file_hash(file_path: Path, algorithm: str) -> str:
    """计算单个文件的哈希值（分块读取支持大文件）。"""
    hasher = hashlib.new(algorithm)
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


@action(
    "hashfile_f",
    param_help={
        "path": "目标文件路径",
        "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）",
    },
)
def _hashfile_f(path: str, algorithm: str = "sha256") -> None:
    """计算并打印单个文件的哈希值。"""
    if algorithm not in _FILE_ALGORITHMS:
        raise ValueError(f"不支持的哈希算法: {algorithm}，支持: {sorted(_FILE_ALGORITHMS)}")
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    digest = _compute_file_hash(file_path, algorithm)
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
    from fcmd.cli._common import IGNORE_DIRS, IGNORE_EXT  # 延迟导入：仅目录哈希场景需要

    if algorithm not in _FILE_ALGORITHMS:
        raise ValueError(f"不支持的哈希算法: {algorithm}，支持: {sorted(_FILE_ALGORITHMS)}")

    dir_path = Path(directory)
    if not dir_path.is_dir():
        print(f"目录不存在: {dir_path}")
        return
    for file_path in sorted(dir_path.rglob("*")):
        if not file_path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in file_path.parts):
            continue
        if file_path.suffix in IGNORE_EXT:
            continue
        digest = _compute_file_hash(file_path, algorithm)
        print(f"{algorithm}  {digest}  {file_path}")
