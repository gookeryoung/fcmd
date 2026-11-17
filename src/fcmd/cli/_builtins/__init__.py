"""fcmd.cli._builtins —— 内建命令实现子包。

每个模块提供一个 ``run(argv: list[str]) -> int`` 入口函数，
由 :func:`run_builtin` 按命令名分发。命令行路由与工具发现状态
分别在 :mod:`fcmd.cli.main` 与 :mod:`fcmd.cli._discovery`。

子模块按需导入：``fcmd env`` 只需要 ``env_cmd``，不应连带导入
``doctor_cmd`` / ``yaml_cmd``（两者分别拉入 rich 与 PyYAML 依赖链）。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from fcmd.cli._common import _BUILTIN_COMMANDS
from fcmd.console import print_error
from fcmd.core.exit_codes import ExitCode

__all__ = ["run_builtin"]

# 内建命令名 → (子模块名, 入口函数名)。只存字符串，分发时才导入对应模块。
_ROUTES: dict[str, tuple[str, str]] = {
    "graph": ("graph_cmd", "run"),
    "info": ("info_cmd", "run"),
    "completion": ("completion_cmd", "run"),
    "yaml": ("yaml_cmd", "run"),
    "env": ("env_cmd", "run"),
    "doctor": ("doctor_cmd", "run"),
    "profiler": ("profiler_cmd", "run"),
}

# 路由表与对外声明的内建命令名单必须一致（新增/删除内建命令时漏改一处即启动报错）
if set(_ROUTES) != set(_BUILTIN_COMMANDS):
    raise RuntimeError(f"内建命令注册不一致: _ROUTES={sorted(_ROUTES)}，_BUILTIN_COMMANDS={sorted(_BUILTIN_COMMANDS)}")


def run_builtin(name: str, argv: list[str]) -> int:
    """分发内建命令，返回退出码。未知命令属用法错误（退出码 2）。"""
    route = _ROUTES.get(name)
    if route is None:
        print_error(
            f"未知内建命令 {name!r}",
            code="USAGE",
            hint="运行 fcmd --help 查看可用工具与内建命令",
        )
        return int(ExitCode.USAGE)
    module_name, func_name = route
    module = importlib.import_module(f"{__name__}.{module_name}")
    handler: Callable[[list[str]], int] = getattr(module, func_name)
    return handler(argv)
