"""fcmd.core —— 运行时内核与公共 API 聚合。

包含 7 个基本面模块：
- task: 任务数据结构（TaskSpec、RetryPolicy、RunConfig）
- dag: 图编排（Graph、GraphDefaults、graph 快捷构造）
- errors: 异常类型
- report: 执行报告（RunReport、TaskResult）
- context: 依赖注入工具
- profiling: 性能分析（ProfileReport、TaskProfile）
- toolkit: @fx.tool 装饰器框架（ToolSpec、build_tool_graph 等）

子包：
- execution: 执行引擎——run/executors、四种策略执行器
- orchestration: YAML 编排与 ``if`` 条件求值
- models: 通用数据模型（CommandResult、PDF 页码/拆分/合并）

执行引擎位于 :mod:`fcmd.core.execution`，YAML 编排位于
:mod:`fcmd.core.orchestration`，经顶层 ``fcmd.__init__`` 懒加载暴露。

本包自身也通过 ``__getattr__`` 懒加载所有子模块，使首次访问
``fcmd.core.toolkit`` / ``fcmd.core.run_tool`` 等不会在父包加载时
触发整条导入链（toolkit → exec → execution → asyncio）。
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
    "Context": ("fcmd.core.context", "Context"),
    "build_call_args": ("fcmd.core.context", "build_call_args"),
    "describe_injection": ("fcmd.core.context", "describe_injection"),
    "Graph": ("fcmd.core.dag", "Graph"),
    "GraphDefaults": ("fcmd.core.dag", "GraphDefaults"),
    "graph": ("fcmd.core.dag", "graph"),
    "CycleError": ("fcmd.core.errors", "CycleError"),
    "DuplicateTaskError": ("fcmd.core.errors", "DuplicateTaskError"),
    "FcmdError": ("fcmd.core.errors", "FcmdError"),
    "InjectionError": ("fcmd.core.errors", "InjectionError"),
    "MissingDependencyError": ("fcmd.core.errors", "MissingDependencyError"),
    "TaskFailedError": ("fcmd.core.errors", "TaskFailedError"),
    "TaskTimeoutError": ("fcmd.core.errors", "TaskTimeoutError"),
    "ProfileReport": ("fcmd.core.profiling", "ProfileReport"),
    "TaskProfile": ("fcmd.core.profiling", "TaskProfile"),
    "RunReport": ("fcmd.core.report", "RunReport"),
    "RetryPolicy": ("fcmd.core.task", "RetryPolicy"),
    "RunConfig": ("fcmd.core.task", "RunConfig"),
    "TaskCmd": ("fcmd.core.task", "TaskCmd"),
    "TaskResult": ("fcmd.core.task", "TaskResult"),
    "TaskSpec": ("fcmd.core.task", "TaskSpec"),
    "TaskStatus": ("fcmd.core.task", "TaskStatus"),
    "cmd": ("fcmd.core.task", "cmd"),
    "task": ("fcmd.core.task", "task"),
    "ToolExitCode": ("fcmd.core.toolkit", "ToolExitCode"),
    "ToolSpec": ("fcmd.core.toolkit", "ToolSpec"),
    "build_tool_graph": ("fcmd.core.toolkit", "build_tool_graph"),
    "clear_tool_registry": ("fcmd.core.toolkit", "clear_tool_registry"),
    "get_tool": ("fcmd.core.toolkit", "get_tool"),
    "list_subcommands": ("fcmd.core.toolkit", "list_subcommands"),
    "list_tools": ("fcmd.core.toolkit", "list_tools"),
    "run_tool": ("fcmd.core.toolkit", "run_tool"),
    "tool": ("fcmd.core.toolkit", "tool"),
    # ---- 子模块名（供测试 from fcmd.core import toolkit / _tool_exec）----
    "toolkit": ("fcmd.core.toolkit", None),
    "_tool_exec": ("fcmd.core.toolkit.exec", None),
}


def __getattr__(name: str) -> Any:
    """懒加载公共 API 符号与子模块。

    首次访问时从对应模块导入并缓存到 ``globals()``，后续直接命中。
    """
    mapping = _LAZY_ATTRS.get(name)
    if mapping is None:
        raise AttributeError(f"module 'fcmd.core' has no attribute {name!r}")
    module_path, attr_name = mapping
    import importlib

    module = importlib.import_module(module_path)
    if attr_name is None:
        # 请求子模块自身（如 ``from fcmd.core import toolkit``）
        value = module
    else:
        value = getattr(module, attr_name)
    globals()[name] = value  # 缓存到全局，后续直接命中
    return value


def __dir__() -> list[str]:
    """补全建议。"""
    return sorted(set(globals()) | set(__all__))
