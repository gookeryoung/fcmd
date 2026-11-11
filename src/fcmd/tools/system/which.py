"""which - 查找可执行命令路径（跨平台）。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras.system import which_commands

__all__ = ["which"]
__tool_aliases__ = ["wch"]


@fx.tool(
    "which",
    help="查找可执行命令路径（跨平台）",
    param_help={"commands": "要查找的命令名称列表"},
)
def which(commands: list[str]) -> None:
    """逐条查找可执行命令路径并回显。"""
    for message in which_commands(commands):
        print(message)
