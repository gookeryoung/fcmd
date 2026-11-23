"""filelevel - 文件名等级标记（PUB/NOR/INT/CON/CLA）设置、清除与配置。"""

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
        "level": "文件等级（0 清除；正整数使用配置中的等级）",
    },
)
def set_level(files: list[Path], level: int = 0) -> None:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA）。

    等级映射与括号字符从 ``${FCMD_HOME:-~/.fcmd}/filelevel.json`` 直接读取；
    手工编辑该文件后，下一次 ``set`` 立即生效。
    """
    for message in set_file_level(files, level):
        print(message)
