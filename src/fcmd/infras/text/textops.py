"""``fcmd.infras.text.textops``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

__all__ = ["convert_case", "count_text", "read_text_file", "sort_lines", "unique_lines"]


_CASE_MODES: dict[str, Callable[[str], str]] = {
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
    "capitalize": str.capitalize,
    "swapcase": str.swapcase,
}


def read_text_file(path: str | Path) -> str:
    """读取文本文件内容（utf-8）。"""
    return Path(path).read_text(encoding="utf-8")


def count_text(text: str) -> dict[str, int]:
    """统计文本的行数、单词数、字符数。"""
    return {
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "chars": len(text),
    }


def sort_lines(text: str, reverse: bool = False) -> str:
    """对文本行排序。"""
    return "\n".join(sorted(text.splitlines(), reverse=reverse))


def unique_lines(text: str) -> str:
    """去重文本行（保持首次出现的顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        if line not in seen:
            seen.add(line)
            result.append(line)
    return "\n".join(result)


def convert_case(text: str, mode: str = "upper") -> str:
    """转换文本大小写。"""
    if mode not in _CASE_MODES:
        raise ValueError(f"不支持的模式: {mode}，支持: {', '.join(_CASE_MODES)}")
    return _CASE_MODES[mode](text)
