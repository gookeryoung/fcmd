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
from fcmd.cli._common import _BUILTIN_COMMANDS
from fcmd.console import print_error

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

# 路由表与对外声明的内建命令名单必须一致（新增/删除内建命令时漏改一处即启动报错）
if set(_HANDLERS) != set(_BUILTIN_COMMANDS):
    raise RuntimeError(
        f"内建命令注册不一致: _HANDLERS={sorted(_HANDLERS)}，_BUILTIN_COMMANDS={sorted(_BUILTIN_COMMANDS)}"
    )


def run_builtin(name: str, argv: list[str]) -> int:
    """分发内建命令，返回退出码。未知命令打印错误返回 1。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        print_error(f"未知内建命令 {name!r}")
        return 1
    return handler(argv)  # type: ignore[no-any-return]
