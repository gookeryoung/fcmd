"""hashfile - 文件哈希计算工具。

计算文件或目录下所有文件的哈希值（md5/sha256/sha1），用于校验文件完整性。

示例
----
    fcmd hashfile f README.md              # 计算单文件 sha256
    fcmd hashfile f README.md --algorithm md5
    fcmd hashfile d src                    # 计算目录下全部文件哈希
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import fcmd

__all__ = [
    "compute_hash",
    "hash_directory",
    "hash_file",
]

# ============================================================================
# 配置
# ============================================================================

_CHUNK_SIZE = 64 * 1024


# ============================================================================
# 公共函数
# ============================================================================


def compute_hash(file_path: Path, algorithm: str = "sha256") -> str:
    """计算单个文件的哈希值。

    Parameters
    ----------
    file_path:
        目标文件路径
    algorithm:
        哈希算法（``md5``/``sha256``/``sha1``，默认 ``sha256``）

    Returns
    -------
    str
        十六进制哈希字符串
    """
    hasher = hashlib.new(algorithm)
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_file(path: str, algorithm: str = "sha256") -> None:
    """计算并打印单个文件的哈希值。

    Parameters
    ----------
    path:
        目标文件路径
    algorithm:
        哈希算法（``md5``/``sha256``/``sha1``，默认 ``sha256``）
    """
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    digest = compute_hash(file_path, algorithm)
    print(f"{algorithm}  {digest}  {file_path}")


def hash_directory(directory: str, algorithm: str = "sha256") -> None:
    """计算目录下所有文件的哈希值并打印。

    跳过常见忽略目录（``.git``/``__pycache__`` 等）与 ``.pyc``/``.pyo`` 文件。

    Parameters
    ----------
    directory:
        目标目录路径
    algorithm:
        哈希算法（``md5``/``sha256``/``sha1``，默认 ``sha256``）
    """
    from fcmd.cli._common import IGNORE_DIRS, IGNORE_EXT

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
        digest = compute_hash(file_path, algorithm)
        print(f"{algorithm}  {digest}  {file_path}")


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("hashfile", subcommand="f", help="计算单个文件哈希")
def hash_file_cmd(path: str, algorithm: str = "sha256") -> None:
    """计算单个文件的哈希值。

    Parameters
    ----------
    path:
        目标文件路径
    algorithm:
        哈希算法（``md5``/``sha256``/``sha1``，默认 ``sha256``）
    """
    hash_file(path, algorithm)


@fcmd.tool("hashfile", subcommand="d", help="计算目录下所有文件哈希")
def hash_dir_cmd(directory: str, algorithm: str = "sha256") -> None:
    """计算目录下所有文件的哈希值。

    Parameters
    ----------
    directory:
        目标目录路径
    algorithm:
        哈希算法（``md5``/``sha256``/``sha1``，默认 ``sha256``）
    """
    hash_directory(directory, algorithm)
