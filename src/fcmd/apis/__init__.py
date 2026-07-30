"""fcmd.apis —— 框架级公共 API 聚合。

包含 8 个子模块：
- task: 任务数据结构（TaskSpec、RetryPolicy、RunConfig）
- dag: 图编排（Graph、GraphDefaults）
- errors: 异常类型
- report: 执行报告（RunReport、TaskResult）
- context: 依赖注入工具
- profiling: 性能分析（ProfileReport、TaskProfile）
- toolkit: @fx.tool 装饰器框架（ToolSpec、build_tool_graph 等）
- command: 命令执行器（内部使用，不对外导出）
"""

from __future__ import annotations

from fcmd.apis.context import Context, build_call_args, describe_injection
from fcmd.apis.dag import Graph, GraphDefaults
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
    "list_subcommands",
    "list_tools",
    "run_tool",
    "task",
    "tool",
]
