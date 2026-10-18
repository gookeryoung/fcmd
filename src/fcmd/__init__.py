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

from typing import Any

__version__ = "0.4.0"

__all__ = [
    "Context",
    "CycleError",
    "DuplicateTaskError",
    "FcmdError",
    "Graph",
    "GraphDefaults",
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
    "task": ("fcmd.core.task", "task"),
    "tool": ("fcmd.core.toolkit", "tool"),
}

# Strategy 是 Literal 类型别名，从执行引擎导入
_LAZY_ATTRS["Strategy"] = ("fcmd.core.execution.executors", "Strategy")


def __getattr__(name: str) -> Any:
    """懒加载公共 API 符号。

    首次访问时从对应模块导入并缓存到 ``globals()``，后续直接命中。
    """
    mapping = _LAZY_ATTRS.get(name)
    if mapping is None:
        raise AttributeError(f"module 'fcmd' has no attribute {name!r}")
    module_path, attr_name = mapping
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value  # 缓存到全局，后续直接命中
    return value


def __dir__() -> list[str]:
    """补全建议。"""
    return sorted(set(globals()) | set(__all__))
