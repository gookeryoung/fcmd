"""hashfile 工具：单文件与目录下所有文件的哈希。

哈希计算与目录过滤在 :mod:`fcmd.infras.hashing`，本模块只做参数适配与输出。
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras import hashing


@fx.tool(
    "hashfile",
    subcommand="f",
    help="计算单个文件哈希",
    param_help={
        "path": "目标文件路径",
        "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）",
    },
)
def hashfile_f(path: Path, algorithm: str = "sha256") -> None:
    """计算并打印单个文件的哈希值。"""
    hashing.ensure_algorithm(algorithm)
    if not path.is_file():
        print(f"文件不存在: {path}")
        return
    digest = hashing.compute_file_hash(path, algorithm)
    print(f"{algorithm}  {digest}  {path}")


@fx.tool(
    "hashfile",
    subcommand="d",
    help="计算目录下所有文件哈希",
    param_help={
        "directory": "目标目录路径",
        "algorithm": "哈希算法（md5/sha256/sha1，默认 sha256）",
    },
)
def hashfile_d(directory: Path, algorithm: str = "sha256") -> None:
    """计算目录下所有文件的哈希值并打印。

    跳过常见忽略目录（.git/__pycache__ 等）与 .pyc/.pyo 文件。
    """
    hashing.ensure_algorithm(algorithm)
    if not directory.is_dir():
        print(f"目录不存在: {directory}")
        return
    for file_path in hashing.iter_directory_files(directory):
        digest = hashing.compute_file_hash(file_path, algorithm)
        print(f"{algorithm}  {digest}  {file_path}")
