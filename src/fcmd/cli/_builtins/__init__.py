"""fcmd.cli._builtins —— 内建命令实现子包。

每个模块提供一个 ``run(argv: list[str]) -> int`` 入口函数，
由 :func:`run_builtin` 按命令名分发。命令行路由与工具发现状态
分别在 :mod:`fcmd.cli.main` 与 :mod:`fcmd.cli._discovery`。
"""

from __future__ import annotations

from fcmd.cli._builtins import (
    completion_cmd,
    doctor_cmd,
    env_cmd,
    graph_cmd,
    info_cmd,
    profiler_cmd,
    yaml_cmd,
)
from fcmd.console import get_console

__all__ = ["run_builtin"]

# 内建命令名 → run(argv) 入口
_HANDLERS: dict[str, object] = {
    "graph": graph_cmd.run,
    "info": info_cmd.run,
    "completion": completion_cmd.run,
    "yaml": yaml_cmd.run,
    "env": env_cmd.run,
    "doctor": doctor_cmd.run,
    "profiler": profiler_cmd.run,
}


def run_builtin(name: str, argv: list[str]) -> int:
    """分发内建命令，返回退出码。未知命令打印错误返回 1。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        get_console().print(f"[red]错误:[/red] 未知内建命令 {name!r}")
        return 1
    return handler(argv)  # type: ignore[no-any-return]
