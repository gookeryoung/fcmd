"""fcmd.apis —— 框架级公共 API 聚合。

包含 7 个子模块：
- task: 任务数据结构（TaskSpec、RetryPolicy、RunConfig）
- dag: 图编排（Graph、GraphDefaults、graph 快捷构造）
- errors: 异常类型
- report: 执行报告（RunReport、TaskResult）
- context: 依赖注入工具
- profiling: 性能分析（ProfileReport、TaskProfile）
- toolkit: @fx.tool 装饰器框架（ToolSpec、build_tool_graph 等）

执行引擎（run/executors）位于 :mod:`fcmd.engine`，YAML 编排位于
:mod:`fcmd.orchestration`，经顶层 ``fcmd.__init__`` 懒加载暴露。

本包自身也通过 ``__getattr__`` 懒加载所有子模块，使首次访问
``fcmd.apis.toolkit`` / ``fcmd.apis.run_tool`` 等不会在父包加载时
触发整条导入链（toolkit → _tool_exec → engine → asyncio）。
"""

from __future__ import annotations

from typing import Any

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
    "TaskCmd",
    "TaskFailedError",
    "TaskProfile",
    "TaskResult",
    "TaskSpec",
    "TaskStatus",
    "TaskTimeoutError",
    "ToolExitCode",
    "ToolSpec",
    "_tool_exec",
    "build_call_args",
    "build_tool_graph",
    "clear_tool_registry",
    "cmd",
    "describe_injection",
    "get_tool",
    "graph",
    "list_subcommands",
    "list_tools",
    "run_tool",
    "task",
    "tool",
    "toolkit",
]

# 懒加载映射：公共符号 / 子模块名 -> (模块路径, 符号名 或 None 表示模块自身)
_LAZY_ATTRS: dict[str, tuple[str, str | None]] = {
    # ---- 公共符号 ----
    "Context": ("fcmd.apis.context", "Context"),
    "build_call_args": ("fcmd.apis.context", "build_call_args"),
    "describe_injection": ("fcmd.apis.context", "describe_injection"),
    "Graph": ("fcmd.apis.dag", "Graph"),
    "GraphDefaults": ("fcmd.apis.dag", "GraphDefaults"),
    "graph": ("fcmd.apis.dag", "graph"),
    "CycleError": ("fcmd.apis.errors", "CycleError"),
    "DuplicateTaskError": ("fcmd.apis.errors", "DuplicateTaskError"),
    "FcmdError": ("fcmd.apis.errors", "FcmdError"),
    "InjectionError": ("fcmd.apis.errors", "InjectionError"),
    "MissingDependencyError": ("fcmd.apis.errors", "MissingDependencyError"),
    "TaskFailedError": ("fcmd.apis.errors", "TaskFailedError"),
    "TaskTimeoutError": ("fcmd.apis.errors", "TaskTimeoutError"),
    "ProfileReport": ("fcmd.apis.profiling", "ProfileReport"),
    "TaskProfile": ("fcmd.apis.profiling", "TaskProfile"),
    "RunReport": ("fcmd.apis.report", "RunReport"),
    "RetryPolicy": ("fcmd.apis.task", "RetryPolicy"),
    "RunConfig": ("fcmd.apis.task", "RunConfig"),
    "TaskCmd": ("fcmd.apis.task", "TaskCmd"),
    "TaskResult": ("fcmd.apis.task", "TaskResult"),
    "TaskSpec": ("fcmd.apis.task", "TaskSpec"),
    "TaskStatus": ("fcmd.apis.task", "TaskStatus"),
    "cmd": ("fcmd.apis.task", "cmd"),
    "task": ("fcmd.apis.task", "task"),
    "ToolExitCode": ("fcmd.apis.toolkit", "ToolExitCode"),
    "ToolSpec": ("fcmd.apis.toolkit", "ToolSpec"),
    "build_tool_graph": ("fcmd.apis.toolkit", "build_tool_graph"),
    "clear_tool_registry": ("fcmd.apis.toolkit", "clear_tool_registry"),
    "get_tool": ("fcmd.apis.toolkit", "get_tool"),
    "list_subcommands": ("fcmd.apis.toolkit", "list_subcommands"),
    "list_tools": ("fcmd.apis.toolkit", "list_tools"),
    "run_tool": ("fcmd.apis.toolkit", "run_tool"),
    "tool": ("fcmd.apis.toolkit", "tool"),
    # ---- 子模块名（供测试 from fcmd.apis import toolkit / _tool_exec）----
    "toolkit": ("fcmd.apis.toolkit", None),
    "_tool_exec": ("fcmd.apis._tool_exec", None),
}


def __getattr__(name: str) -> Any:
    """懒加载公共 API 符号与子模块。

    首次访问时从对应模块导入并缓存到 ``globals()``，后续直接命中。
    """
    mapping = _LAZY_ATTRS.get(name)
    if mapping is None:
        raise AttributeError(f"module 'fcmd.apis' has no attribute {name!r}")
    module_path, attr_name = mapping
    import importlib

    module = importlib.import_module(module_path)
    if attr_name is None:
        # 请求子模块自身（如 ``from fcmd.apis import toolkit``）
        value = module
    else:
        value = getattr(module, attr_name)
    globals()[name] = value  # 缓存到全局，后续直接命中
    return value


def __dir__() -> list[str]:
    """补全建议。"""
    return sorted(set(globals()) | set(__all__))
