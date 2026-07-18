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

from typing import Any

__version__ = "0.1.2"

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
    "parse_yaml_string",
    "run",
    "run_command",
    "run_tool",
    "task",
    "tool",
]

# 懒加载映射：属性名 -> (模块路径, 符号名)
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "Context": ("fcmd.context", "Context"),
    "CycleError": ("fcmd.errors", "CycleError"),
    "DuplicateTaskError": ("fcmd.errors", "DuplicateTaskError"),
    "FcmdError": ("fcmd.errors", "FcmdError"),
    "Graph": ("fcmd.dag", "Graph"),
    "GraphDefaults": ("fcmd.dag", "GraphDefaults"),
    "InjectionError": ("fcmd.errors", "InjectionError"),
    "MissingDependencyError": ("fcmd.errors", "MissingDependencyError"),
    "ProfileReport": ("fcmd.profiling", "ProfileReport"),
    "RetryPolicy": ("fcmd.task", "RetryPolicy"),
    "RunReport": ("fcmd.report", "RunReport"),
    "TaskCmd": ("fcmd.task", "TaskCmd"),
    "TaskFailedError": ("fcmd.errors", "TaskFailedError"),
    "TaskResult": ("fcmd.task", "TaskResult"),
    "TaskProfile": ("fcmd.profiling", "TaskProfile"),
    "TaskSpec": ("fcmd.task", "TaskSpec"),
    "TaskStatus": ("fcmd.task", "TaskStatus"),
    "TaskTimeoutError": ("fcmd.errors", "TaskTimeoutError"),
    "ToolExitCode": ("fcmd.apis.toolkit", "ToolExitCode"),
    "ToolSpec": ("fcmd.apis.toolkit", "ToolSpec"),
    "build_tool_graph": ("fcmd.apis.toolkit", "build_tool_graph"),
    "cmd": ("fcmd.task", "cmd"),
    "clear_tool_registry": ("fcmd.apis.toolkit", "clear_tool_registry"),
    "describe_injection": ("fcmd.context", "describe_injection"),
    "get_tool": ("fcmd.apis.toolkit", "get_tool"),
    "list_subcommands": ("fcmd.apis.toolkit", "list_subcommands"),
    "list_tools": ("fcmd.apis.toolkit", "list_tools"),
    "load_yaml": ("fcmd.yaml_loader", "load_yaml"),
    "parse_yaml_string": ("fcmd.yaml_loader", "parse_yaml_string"),
    "run": ("fcmd.executors", "run"),
    "run_command": ("fcmd.command", "run_command"),
    "run_tool": ("fcmd.apis.toolkit", "run_tool"),
    "task": ("fcmd.task", "task"),
    "tool": ("fcmd.apis.toolkit", "tool"),
}

# Strategy 是 Literal 类型别名，从 executors 导入
_LAZY_ATTRS["Strategy"] = ("fcmd.executors", "Strategy")


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


def graph(
    *specs: Any,
    defaults: Any = None,
    namespace: str | None = None,
) -> Any:
    """快捷构造图：等价于 ``Graph.from_specs``，接受可变参数而非列表。

    对 ``depends_on`` 为空的纯 fn 任务，自动从必需参数名推断依赖
    （匹配图中任务名的参数被加入 ``depends_on``）。

    示例
    --------
    >>> import fcmd as fx
    >>> @fx.task
    ... def extract() -> list[int]: return [1, 2, 3]
    >>> @fx.task
    ... def double(extract: list[int]) -> list[int]: return [x * 2 for x in extract]
    >>> g = fx.graph(extract, double)  # double 自动依赖 extract
    """
    from fcmd.dag import Graph, GraphDefaults

    return Graph.from_specs(specs, defaults=defaults or GraphDefaults(), namespace=namespace)
