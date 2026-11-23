"""``fcmd.infras.text.case``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

import re

__all__ = ["to_camel", "to_kebab", "to_pascal", "to_snake"]


_WORD_PATTERN = re.compile(
    r"[^A-Za-z0-9]+"  # 非字母数字序列
    r"|(?<=[a-z0-9])(?=[A-Z])"  # 小写/数字→大写 边界
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # 大写(连续)→大写+小写 边界（HTTP|Server）
)


def _split_words(text: str) -> list[str]:
    """将输入字符串拆分为小写单词列表。"""
    if not text:
        return []
    parts = _WORD_PATTERN.split(text)
    return [p.lower() for p in parts if p]


def to_snake(text: str) -> str:
    """转换为 snake_case。"""
    return "_".join(_split_words(text))


def to_camel(text: str) -> str:
    """转换为 camelCase。"""
    words = _split_words(text)
    if not words:
        return ""
    return words[0] + "".join(w.capitalize() for w in words[1:])


def to_pascal(text: str) -> str:
    """转换为 PascalCase。"""
    return "".join(w.capitalize() for w in _split_words(text))


def to_kebab(text: str) -> str:
    """转换为 kebab-case。"""
    return "-".join(_split_words(text))
