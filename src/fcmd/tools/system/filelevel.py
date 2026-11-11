"""filelevel - 文件名等级标记（PUB/NOR/INT/CON/CLA）设置与清除。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras.fs import set_file_level

__all__ = ["set_level"]


@fx.tool(
    "filelevel",
    subcommand="set",
    help="设置文件等级",
    param_help={
        "files": "文件路径列表",
        "level": "文件等级（0-4），0 用于清除等级",
    },
)
def set_level(files: list[Path], level: int = 0) -> None:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA）。"""
    for message in set_file_level(files, level):
        print(message)
