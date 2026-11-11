"""taskkill - 按名称终止进程（跨平台）。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras.system import kill_processes

__all__ = ["taskkill"]
__tool_aliases__ = ["taskk"]


@fx.tool(
    "taskkill",
    help="按名称终止进程（跨平台）",
    param_help={"names": "进程名称列表（按名称前缀匹配）"},
)
def taskkill(names: list[str]) -> None:
    """按名称前缀终止进程，逐条回显结果。"""
    for message in kill_processes(names):
        print(message)
