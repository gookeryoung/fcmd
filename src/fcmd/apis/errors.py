"""fcmd 异常体系。

所有自定义异常继承 :class:`FcmdError`，按错误场景分类。
异常包装用 ``raise NewError(...) from exc`` 保留因果链。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .report import RunReport

__all__ = [
    "CycleError",
    "DuplicateTaskError",
    "FcmdError",
    "InjectionError",
    "MissingDependencyError",
    "TaskFailedError",
    "TaskTimeoutError",
]


class FcmdError(Exception):
    """所有 fcmd 异常的公共基类。"""


class CycleError(FcmdError):
    """图中检测到循环依赖。

    参数
    ----
    nodes:
        参与环的节点名列表（非精确环路径，仅指示存在环）。
    """

    def __init__(self, nodes: list[str]) -> None:
        self.nodes = nodes
        super().__init__(f"图中检测到循环依赖，涉及节点: {nodes}")


class DuplicateTaskError(FcmdError):
    """图中存在重名任务。

    参数
    ----
    name:
        重复的任务名。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"任务名重复: {name!r}")


class MissingDependencyError(FcmdError):
    """任务引用了图中不存在的依赖。

    参数
    ----
    task:
        引用方任务名。
    dependency:
        被引用但缺失的依赖名。
    """

    def __init__(self, task: str, dependency: str) -> None:
        self.task = task
        self.dependency = dependency
        super().__init__(f"任务 {task!r} 引用了不存在的依赖 {dependency!r}")


class InjectionError(FcmdError):
    """上下文注入失败（参数名冲突、类型不匹配等）。"""

    def __init__(self, task: str, reason: str) -> None:
        self.task = task
        self.reason = reason
        super().__init__(f"任务 {task!r} 注入失败: {reason}")


class TaskFailedError(FcmdError):
    """任务耗尽重试后仍失败。

    参数
    ----
    task:
        失败的任务名。
    cause:
        触发失败的原始异常。
    attempts:
        总尝试次数（含首次）。
    layer:
        任务所在层索引（``None`` 表示依赖驱动策略）。
    report:
        当前运行报告（含已完成任务的结果，便于诊断）。
    """

    def __init__(
        self,
        *,
        task: str,
        cause: BaseException,
        attempts: int = 1,
        layer: int | None = None,
        report: RunReport | None = None,
    ) -> None:
        self.task = task
        self.cause = cause
        self.attempts = attempts
        self.layer = layer
        self.report = report
        layer_info = f" (层 {layer})" if layer is not None else ""
        super().__init__(f"任务 {task!r} 失败{layer_info}，尝试 {attempts} 次: {cause}")


class TaskTimeoutError(FcmdError):
    """任务执行超时。

    参数
    ----
    task:
        超时的任务名。
    timeout:
        设置的超时秒数。
    """

    def __init__(self, task: str, timeout: float) -> None:
        self.task = task
        self.timeout = timeout
        super().__init__(f"任务 {task!r} 执行超时 ({timeout}s)")
