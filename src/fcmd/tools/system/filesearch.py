"""filesearch - 按文件名或内容递归搜索目录。"""

from __future__ import annotations

import re
from pathlib import Path

import fcmd as fx
from fcmd.infras.fs import search_by_content, search_by_name

__all__ = ["content", "name"]


@fx.tool(
    "filesearch",
    subcommand="name",
    help="按文件名 glob 搜索",
    param_help={
        "directory": "搜索根目录",
        "pattern": "文件名 glob 模式（fnmatch 语法，如 *.py）",
        "include_dirs": "是否同时返回匹配的目录",
    },
)
def name(directory: Path, pattern: str, include_dirs: bool = False) -> None:
    """按文件名 glob 模式递归搜索目录。"""
    try:
        results = search_by_name(directory, pattern, include_dirs=include_dirs)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"错误: {exc}")
        return
    if not results:
        print("（无匹配）")
        return
    for path in results:
        print(path)


@fx.tool(
    "filesearch",
    subcommand="content",
    help="按内容正则搜索",
    param_help={
        "directory": "搜索根目录",
        "pattern": "正则表达式",
        "extension": "限定扩展名（如 .py），空串不限",
    },
)
def content(directory: Path, pattern: str, extension: str = "") -> None:
    """按文件内容正则递归搜索，输出 文件:行号:行内容。"""
    try:
        results = search_by_content(directory, pattern, extension=extension)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"错误: {exc}")
        return
    except re.error as exc:
        print(f"错误: 正则表达式错误: {exc}")
        return
    if not results:
        print("（无匹配）")
        return
    for path, lineno, line in results:
        print(f"{path}:{lineno}:{line}")
