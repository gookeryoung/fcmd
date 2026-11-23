"""``fcmd.infras.text.regex``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

import re

__all__ = ["find_all", "match_pattern", "replace_pattern", "split_pattern"]


def match_pattern(pattern: str, text: str) -> dict[str, str] | None:
    """在文本开头匹配正则表达式。"""
    try:
        m = re.match(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc
    if m is None:
        return None
    groups = ",".join(g if g is not None else "" for g in m.groups())
    return {
        "match": m.group(0),
        "start": str(m.start()),
        "end": str(m.end()),
        "groups": groups,
    }


def find_all(pattern: str, text: str) -> list[str]:
    """查找文本中所有非重叠匹配。"""
    try:
        matches = re.findall(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc
    return [str(m) if not isinstance(m, str) else m for m in matches]


def replace_pattern(pattern: str, replacement: str, text: str) -> str:
    """替换文本中所有匹配。"""
    try:
        return re.sub(pattern, replacement, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc


def split_pattern(pattern: str, text: str) -> list[str]:
    """按正则分割文本。"""
    try:
        return re.split(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc
