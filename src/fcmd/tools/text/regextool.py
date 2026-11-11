"""regextool —— 正则匹配/查找/替换/分割。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "regextool",
    subcommand="match",
    help="匹配正则表达式",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待匹配的文本",
    },
)
def match(pattern: str, text: str) -> None:
    """在文本开头匹配正则表达式并打印结果。"""
    try:
        result = textinfra.match_pattern(pattern, text)
    except ValueError as error:
        print(f"错误: {error}")
        return
    if result is None:
        print("未匹配")
        return
    for key, value in result.items():
        print(f"{key}: {value}")


@fx.tool(
    "regextool",
    subcommand="find",
    help="查找所有匹配",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待查找的文本",
    },
)
def find(pattern: str, text: str) -> None:
    """查找文本中所有匹配并逐行打印。"""
    try:
        matches = textinfra.find_all(pattern, text)
    except ValueError as error:
        print(f"错误: {error}")
        return
    if not matches:
        print("未匹配")
        return
    for item in matches:
        print(item)


@fx.tool(
    "regextool",
    subcommand="replace",
    help="替换匹配",
    param_help={
        "pattern": "正则表达式字符串",
        "replacement": "替换字符串（支持反向引用）",
        "text": "待替换的文本",
    },
)
def replace(pattern: str, replacement: str, text: str) -> None:
    """替换文本中所有匹配并打印结果。"""
    try:
        print(textinfra.replace_pattern(pattern, replacement, text))
    except ValueError as error:
        print(f"错误: {error}")


@fx.tool(
    "regextool",
    subcommand="split",
    help="按正则分割",
    param_help={
        "pattern": "正则表达式字符串（分隔符模式）",
        "text": "待分割的文本",
    },
)
def split(pattern: str, text: str) -> None:
    """按正则分割文本并逐行打印片段。"""
    try:
        parts = textinfra.split_pattern(pattern, text)
    except ValueError as error:
        print(f"错误: {error}")
        return
    for part in parts:
        print(part)
