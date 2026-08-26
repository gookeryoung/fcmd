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
"""

from __future__ import annotations

from fcmd.apis.context import Context, build_call_args, describe_injection
from fcmd.apis.dag import Graph, GraphDefaults, graph
from fcmd.apis.errors import (
    CycleError,
    DuplicateTaskError,
    FcmdError,
    InjectionError,
    MissingDependencyError,
    TaskFailedError,
    TaskTimeoutError,
)
from fcmd.apis.profiling import ProfileReport, TaskProfile
from fcmd.apis.report import RunReport
from fcmd.apis.task import (
    RetryPolicy,
    RunConfig,
    TaskCmd,
    TaskResult,
    TaskSpec,
    TaskStatus,
    cmd,
    task,
)
from fcmd.apis.toolkit import (
    ToolExitCode,
    ToolSpec,
    build_tool_graph,
    clear_tool_registry,
    get_tool,
    list_subcommands,
    list_tools,
    run_tool,
    tool,
)

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
]
