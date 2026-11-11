"""txttool —— 文本文件行数/词数/字符数统计、排序、去重与大小写转换。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "txttool",
    subcommand="count",
    help="统计行/词/字符数",
    param_help={"path": "目标文件路径"},
)
def count(path: str) -> None:
    """统计文本文件的行数、单词数、字符数。"""
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    stats = textinfra.count_text(textinfra.read_text_file(file_path))
    print(f"行数: {stats['lines']}")
    print(f"词数: {stats['words']}")
    print(f"字符数: {stats['chars']}")


@fx.tool(
    "txttool",
    subcommand="sort",
    help="排序文本行",
    param_help={
        "path": "目标文件路径",
        "reverse": "是否逆序排序（默认 False）",
    },
)
def sort(path: str, reverse: bool = False) -> None:
    """对文本文件的行排序。"""
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    print(textinfra.sort_lines(textinfra.read_text_file(file_path), reverse=reverse))


@fx.tool(
    "txttool",
    subcommand="unique",
    help="去重文本行",
    param_help={"path": "目标文件路径"},
)
def unique(path: str) -> None:
    """去重文本文件的行（保持首次出现的顺序）。"""
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    print(textinfra.unique_lines(textinfra.read_text_file(file_path)))


@fx.tool(
    "txttool",
    subcommand="case",
    help="大小写转换",
    param_help={
        "path": "目标文件路径",
        "mode": "转换模式（upper/lower/title/capitalize/swapcase，默认 upper）",
    },
)
def case(path: str, mode: str = "upper") -> None:
    """转换文本文件的大小写。"""
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return
    try:
        print(textinfra.convert_case(textinfra.read_text_file(file_path), mode))
    except ValueError as error:
        print(f"错误: {error}")
