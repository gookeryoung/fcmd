"""中立文本 IO 能力：二进制探测与按行读取。

供 :mod:`fcmd.infras.fs` 等基础件共用；本模块只依赖标准库。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["is_binary_file", "read_text_lines"]

# 二进制检测读取前 1024 字节
_BINARY_SNIFF_SIZE = 1024


def is_binary_file(path: Path) -> bool:
    """通过前 1024 字节是否含 ``\\x00`` 判定二进制文件。"""
    try:
        with path.open("rb") as f:
            chunk = f.read(_BINARY_SNIFF_SIZE)
    except OSError:
        return True
    return b"\x00" in chunk


def read_text_lines(path: Path, *, binary_message: str = "二进制文件") -> list[str]:
    """读取文本文件所有行（保留行尾），utf-8 失败时回退为 replace。"""
    if is_binary_file(path):
        raise ValueError(f"{binary_message}: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
