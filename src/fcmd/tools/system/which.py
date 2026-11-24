"""which - 查找可执行命令路径（跨平台）。"""

from __future__ import annotations

import fcmd as fx
from fcmd.core.result import emit_result
from fcmd.infras.system import locate_commands

__all__ = ["which"]
__tool_aliases__ = ["wch"]


@fx.tool(
    "which",
    help="查找可执行命令路径（跨平台）",
    param_help={"commands": "要查找的命令名称列表"},
)
def which(commands: list[str], json_output: bool = False) -> None:
    """逐条查找可执行命令路径并回显（``--json`` 输出结构化结果）。"""
    found = locate_commands(commands)
    if json_output:
        emit_result(
            "which",
            None,
            {
                "commands": [{"name": name, "path": path} for name, path in found],
                "missing": [name for name, path in found if path is None],
            },
        )
        return
    for name, path in found:
        print(f"{name} -> {path}" if path else f"{name} -> 未找到")
