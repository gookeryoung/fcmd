"""``fcmd.infras.text.align``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

import unicodedata

__all__ = ["align_center", "align_justify", "align_left", "align_right", "display_width", "pad_display"]


def display_width(text: str) -> int:
    """终端显示宽度：东亚宽字符（中文 / 全角）按 2 列，其余按 1 列。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad_display(text: str, width: int, align: str = "left") -> str:
    """按终端显示宽度填充空格；``align`` 取 ``left`` / ``right``。"""
    padding = max(0, width - display_width(text))
    return (" " * padding + text) if align == "right" else (text + " " * padding)


def align_left(text: str, width: int) -> str:
    """左对齐文本（右侧填充空格）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.ljust(width)


def align_right(text: str, width: int) -> str:
    """右对齐文本（左侧填充空格）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.rjust(width)


def align_center(text: str, width: int) -> str:
    """居中对齐文本（两侧填充空格）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.center(width)


def align_justify(text: str, width: int) -> str:
    """两端对齐文本（多行段落，最后一行左对齐）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    lines = text.splitlines()
    if not lines:
        return "".ljust(width)
    result: list[str] = []
    multi_line = len(lines) > 1
    for i, line in enumerate(lines):
        is_last = multi_line and i == len(lines) - 1
        result.append(_justify_line(line, width, is_last))
    return "\n".join(result)


def _justify_line(line: str, width: int, is_last: bool) -> str:
    """对单行执行两端对齐（内部 helper）。"""
    if not line.strip() or is_last:
        return line.ljust(width)
    words = line.split()
    if len(words) <= 1:
        return line.ljust(width)
    total_chars = sum(len(w) for w in words)
    total_gaps = len(words) - 1
    total_spaces = width - total_chars
    if total_spaces < total_gaps:
        return line.ljust(width)
    base = total_spaces // total_gaps
    extra = total_spaces % total_gaps
    parts: list[str] = []
    for j, word in enumerate(words):
        parts.append(word)
        if j < total_gaps:
            spaces = base + (1 if j < extra else 0)
            parts.append(" " * spaces)
    return "".join(parts)
