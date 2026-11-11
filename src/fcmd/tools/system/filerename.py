"""filerename - 批量重命名文件名主干（正则替换/插入/大小写）。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras.fs import rename_case, rename_insert, rename_replace

__all__ = ["case", "insert", "replace"]


@fx.tool(
    "filerename",
    subcommand="replace",
    help="正则替换文件名主干",
    param_help={
        "files": "待重命名的文件列表",
        "pattern": "正则表达式（Python re 语法，支持反向引用）",
        "replacement": "替换字符串（默认空字符串，即删除匹配部分）",
        "preview": "仅预览不实际执行",
    },
)
def replace(files: list[Path], pattern: str, replacement: str = "", preview: bool = False) -> None:
    """正则替换文件名主干中的匹配部分（保留扩展名，仅匹配时执行）。"""
    for message in rename_replace(files, pattern, replacement, preview):
        print(message)


@fx.tool(
    "filerename",
    subcommand="insert",
    help="在文件名指定位置插入文本",
    param_help={
        "files": "待重命名的文件列表",
        "text": "待插入文本",
        "position": "插入位置（0=开头，负数从末尾计算，超出范围自动截断）",
        "preview": "仅预览不实际执行",
    },
)
def insert(files: list[Path], text: str, position: int = 0, preview: bool = False) -> None:
    """在文件名主干指定位置插入文本（保留扩展名）。"""
    for message in rename_insert(files, text, position, preview):
        print(message)


@fx.tool(
    "filerename",
    subcommand="case",
    help="转换文件名大小写",
    param_help={
        "files": "待重命名的文件列表",
        "mode": "转换模式：lower / upper / title（默认 lower）",
        "preview": "仅预览不实际执行",
    },
)
def case(files: list[Path], mode: str = "lower", preview: bool = False) -> None:
    """转换文件名主干大小写（保留扩展名）。"""
    for message in rename_case(files, mode, preview):
        print(message)
