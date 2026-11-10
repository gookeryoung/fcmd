"""哈希域公共实现：hashtool / hashfile（无工具注册副作用）。

供 ``fcmd.dsl.actions.toolcmd.hashing`` 的 ``@action`` 声明复用；字符串与文件哈希
算法、目录遍历过滤规则只在本模块保留一份。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

__all__ = ["FILE_ALGORITHMS", "compute_file_hash", "ensure_algorithm", "hash_text", "iter_directory_files"]

# 支持的哈希算法（供文件哈希命令的 --algorithm 参数 choices 白名单）
FILE_ALGORITHMS: frozenset[str] = frozenset({"md5", "sha1", "sha256", "sha512"})

# 文件哈希分块大小（64 KB）
_CHUNK_SIZE = 64 * 1024


def ensure_algorithm(algorithm: str) -> None:
    """校验哈希算法是否受支持，不支持时抛 ValueError。"""
    if algorithm not in FILE_ALGORITHMS:
        raise ValueError(f"不支持的哈希算法: {algorithm}，支持: {sorted(FILE_ALGORITHMS)}")


def hash_text(text: str, algorithm: str) -> str:
    """通用字符串哈希计算，返回小写十六进制摘要。"""
    hasher = hashlib.new(algorithm)
    hasher.update(text.encode("utf-8"))
    return hasher.hexdigest()


def compute_file_hash(file_path: Path, algorithm: str) -> str:
    """计算单个文件的哈希值（分块读取支持大文件）。"""
    hasher = hashlib.new(algorithm)
    with file_path.open("rb") as file:
        while True:
            chunk = file.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def iter_directory_files(directory: Path) -> Iterator[Path]:
    """遍历目录下需要哈希的文件（跳过常见忽略目录与 .pyc/.pyo）。"""
    # 延迟导入：仅目录哈希场景需要
    from fcmd.core.filefilter import DEFAULT_IGNORE_EXTS, DEFAULT_IGNORE_SPEC, should_skip_parts

    for file_path in sorted(directory.rglob("*")):
        if not file_path.is_file():
            continue
        if should_skip_parts(file_path.parts, DEFAULT_IGNORE_SPEC):
            continue
        if file_path.suffix in DEFAULT_IGNORE_EXTS:
            continue
        yield file_path
