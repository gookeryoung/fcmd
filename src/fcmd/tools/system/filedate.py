"""filedate - 文件名日期前缀添加与清除。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras.fs import add_date_prefixes, clear_date_prefixes

__all__ = ["add", "clear"]


@fx.tool(
    "filedate",
    subcommand="add",
    help="添加/更新文件日期前缀",
    param_help={"files": "文件路径列表"},
)
def add(files: list[Path]) -> None:
    """为文件名添加/更新日期前缀（基于文件修改/创建时间，先清后加避免重复）。"""
    for message in add_date_prefixes(files):
        print(message)


@fx.tool(
    "filedate",
    subcommand="clear",
    help="清除文件日期前缀",
    param_help={"files": "文件路径列表"},
)
def clear(files: list[Path]) -> None:
    """清除文件名中的日期前缀（缺失文件与点文件跳过）。"""
    for message in clear_date_prefixes(files):
        print(message)
