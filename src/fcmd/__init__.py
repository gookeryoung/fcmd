"""fcmd —— 极速 Python 工具集应用。

公共 API 通过 ``__getattr__`` 懒加载聚合，确保冷启动 < 100ms。
首次访问 ``fx.task`` / ``fx.graph`` / ``fx.run`` 等才触发对应模块导入。

快速上手
--------
    import fcmd as fx

    @fx.task
    def extract() -> list[int]: return [1, 2, 3]

    @fx.task
    def double(extract: list[int]) -> list[int]: return [x * 2 for x in extract]

    graph = fx.graph(extract, double)  # double 自动依赖 extract
    report = fx.run(graph)
    print(report["double"])  # [2, 4, 6]
"""

from __future__ import annotations

import sys

from fcmd._lazyload import lazy_dir, lazy_getattr
from fcmd.core.hooks import register_discovery_hook

if sys.version_info < (3, 11):  # pragma: no cover - 仅 Python 3.10 需要
    # 仅 3.10 需要 PySide6 → PySide2 映射；其它版本跳过该模块导入与安装，
    # 避免 CLI 冷启动付出 importlib.abc / importlib.util 的固定成本。
    from fcmd.gui.compat import install as _install_qt_compat

    _install_qt_compat()

TYPE_CHECKING = False
if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查，运行期不导入 typing
    from typing import Any

__version__ = "0.3.16"

__all__ = [
    "Context",
    "CycleError",
    "DuplicateTaskError",
    "FcmdError",
    "Graph",
    "GraphDefaults",
    "HelpDoc",
    "HelpSection",
    "InjectionError",
    "MissingDependencyError",
    "ProfileReport",
    "RetryPolicy",
    "RunConfig",
    "RunReport",
    "Strategy",
    "TaskCmd",
    "TaskFailedError",
    "TaskProfile",
    "TaskResult",
    "TaskSpec",
    "TaskStatus",
    "TaskTimeoutError",
    "ToolExitCode",
    "ToolSpec",
    "ToolStep",
    "__version__",
    "build_tool_graph",
    "clear_tool_registry",
    "cmd",
    "describe_injection",
    "get_tool",
    "graph",
    "list_subcommands",
    "list_tools",
    "load_yaml",
    "main",
    "parse_yaml_string",
    "run",
    "run_command",
    "run_tool",
    "step",
    "task",
    "tool",
]

# 懒加载映射：属性名 -> (模块路径, 符号名)
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "Context": ("fcmd.core.context", "Context"),
    "CycleError": ("fcmd.core.errors", "CycleError"),
    "DuplicateTaskError": ("fcmd.core.errors", "DuplicateTaskError"),
    "FcmdError": ("fcmd.core.errors", "FcmdError"),
    "Graph": ("fcmd.core.dag", "Graph"),
    "GraphDefaults": ("fcmd.core.dag", "GraphDefaults"),
    "HelpDoc": ("fcmd.shared.helpdoc", "HelpDoc"),
    "HelpSection": ("fcmd.shared.helpdoc", "HelpSection"),
    "InjectionError": ("fcmd.core.errors", "InjectionError"),
    "MissingDependencyError": ("fcmd.core.errors", "MissingDependencyError"),
    "ProfileReport": ("fcmd.core.profiling", "ProfileReport"),
    "RetryPolicy": ("fcmd.core.task", "RetryPolicy"),
    "RunConfig": ("fcmd.core.task", "RunConfig"),
    "RunReport": ("fcmd.core.report", "RunReport"),
    "TaskCmd": ("fcmd.core.task", "TaskCmd"),
    "TaskFailedError": ("fcmd.core.errors", "TaskFailedError"),
    "TaskResult": ("fcmd.core.task", "TaskResult"),
    "TaskProfile": ("fcmd.core.profiling", "TaskProfile"),
    "TaskSpec": ("fcmd.core.task", "TaskSpec"),
    "TaskStatus": ("fcmd.core.task", "TaskStatus"),
    "TaskTimeoutError": ("fcmd.core.errors", "TaskTimeoutError"),
    "ToolExitCode": ("fcmd.core.toolkit", "ToolExitCode"),
    "ToolSpec": ("fcmd.core.toolkit", "ToolSpec"),
    "ToolStep": ("fcmd.core.toolkit", "ToolStep"),
    "build_tool_graph": ("fcmd.core.toolkit", "build_tool_graph"),
    "cmd": ("fcmd.core.task", "cmd"),
    "clear_tool_registry": ("fcmd.core.toolkit", "clear_tool_registry"),
    "describe_injection": ("fcmd.core.context", "describe_injection"),
    "get_tool": ("fcmd.core.toolkit", "get_tool"),
    "graph": ("fcmd.core.dag", "graph"),
    "list_subcommands": ("fcmd.core.toolkit", "list_subcommands"),
    "list_tools": ("fcmd.core.toolkit", "list_tools"),
    "load_yaml": ("fcmd.core.orchestration.yaml_loader", "load_yaml"),
    "main": ("fcmd.core.toolkit", "main"),
    "parse_yaml_string": ("fcmd.core.orchestration.yaml_loader", "parse_yaml_string"),
    "run": ("fcmd.core.execution.executors", "run"),
    "run_command": ("fcmd.core.execution.task_command", "run_command"),
    "run_tool": ("fcmd.core.toolkit", "run_tool"),
    "step": ("fcmd.core.toolkit", "step"),
    "task": ("fcmd.core.task", "task"),
    "tool": ("fcmd.core.toolkit", "tool"),
}

# Strategy 是 Literal 类型别名，从执行引擎导入
_LAZY_ATTRS["Strategy"] = ("fcmd.core.execution.executors", "Strategy")


def _ensure_discovery() -> None:
    """组合根接线：把工具发现实现注册给 core，避免 core→cli 反向依赖。"""
    from fcmd.cli._discovery import ensure_tools_discovered

    ensure_tools_discovered()


register_discovery_hook(_ensure_discovery)


def __getattr__(name: str) -> Any:
    """懒加载公共 API 符号（实现见 :func:`fcmd._lazyload.lazy_getattr`）。"""
    return lazy_getattr(__name__, _LAZY_ATTRS, name, globals())


def __dir__() -> list[str]:
    """补全建议。"""
    return lazy_dir(globals(), __all__)
