"""textdiff —— 文本文件 unified diff 与目录差异比较。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "textdiff",
    subcommand="file",
    help="比较两个文本文件",
    param_help={
        "file1": "待比较文件路径",
        "file2": "待比较文件路径",
        "context": "上下文行数（默认 3）",
        "color": "启用 ANSI 彩色输出（默认关闭）",
    },
)
def file(file1: Path, file2: Path, context: int = 3, color: bool = False) -> None:
    """输出两个文本文件的 unified diff。"""
    if not file1.exists():
        print(f"文件不存在: {file1}")
        return
    if not file2.exists():
        print(f"文件不存在: {file2}")
        return
    try:
        result = textinfra.compare_files(file1, file2, context)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    if not result:
        print("文件内容相同")
        return
    if color:
        result = textinfra.colorize_diff(result)
    print(result, end="")


@fx.tool(
    "textdiff",
    subcommand="dir",
    help="比较两个目录",
    param_help={
        "dir1": "待比较目录路径",
        "dir2": "待比较目录路径",
        "pattern": "文件名 glob 模式（默认所有文件）",
        "recursive": "是否递归比较子目录（默认 True）",
    },
)
def dir(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> None:
    """列出两个目录中差异文件。"""
    if not dir1.is_dir():
        print(f"目录不存在: {dir1}")
        return
    if not dir2.is_dir():
        print(f"目录不存在: {dir2}")
        return
    print(textinfra.compare_directories(dir1, dir2, pattern, recursive))
