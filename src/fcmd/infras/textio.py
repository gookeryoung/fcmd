"""中立文本 IO 能力：编码探测、二进制探测与按行读取。

供 :mod:`fcmd.infras.fs` 等基础件共用；本模块只依赖标准库。
"""

from __future__ import annotations

import codecs
from collections.abc import Sequence
from pathlib import Path

__all__ = [
    "DEFAULT_ENCODINGS",
    "TEXT_BOMS",
    "decode_text",
    "detect_encoding",
    "has_text_bom",
    "is_binary_file",
    "read_text_lines",
]

# 二进制检测读取前 1024 字节
_BINARY_SNIFF_SIZE = 1024

DEFAULT_ENCODINGS: tuple[str, ...] = ("utf-8", "gb18030")
"""未知编码时的候选顺序；都不匹配则由调用方指定的 fallback 兜底。"""

TEXT_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
"""BOM → 编码名；UTF-32 必须排在 UTF-16 之前（``FF FE 00 00`` 以 ``FF FE`` 开头）。"""


def has_text_bom(data: bytes) -> bool:
    """是否以 UTF-8 / UTF-16 / UTF-32 BOM 开头（用于区分文本与二进制）。"""
    return any(data.startswith(bom) for bom, _encoding in TEXT_BOMS)


def detect_encoding(
    data: bytes,
    candidates: Sequence[str] = DEFAULT_ENCODINGS,
    fallback: str = "latin-1",
) -> str:
    """按 BOM → 候选编码的顺序探测；都不匹配时返回 ``fallback``。"""
    for bom, encoding in TEXT_BOMS:
        if data.startswith(bom):
            return encoding
    for encoding in candidates:
        try:
            data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return encoding
    return fallback


def decode_text(data: bytes, encoding: str = "auto") -> str:
    """解码字节；``auto`` 时先探测，未知编码抛 ``ValueError``。"""
    name = detect_encoding(data) if encoding == "auto" else encoding
    try:
        return data.decode(name, errors="replace")
    except LookupError as exc:
        raise ValueError(f"未知编码：{encoding}") from exc


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
