"""工具描述符：``ToolSpec`` / ``ToolStep`` / ``step``。

只放数据结构与装配，不依赖 argparse / 执行引擎。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..exit_codes import ExitCode
from ..task import RetryPolicy

ToolExitCode = ExitCode


def _noop() -> None:
    """聚合任务的占位函数。"""


@dataclass(frozen=True)
class ToolStep:
    """``@fx.tool(steps=...)`` 中的一步：任务名 + 只注入该步的静态参数。

    ``kwargs`` 不同于函数签名参数：它不进入 CLI / schema，只写给流水线
    内部任务（如 ``_init(create=True)``），避免内部开关泄漏成用户选项。
    """

    task: str
    kwargs: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task:
            raise ValueError("ToolStep.task 必须为非空字符串")


def step(task: str, /, **kwargs: Any) -> ToolStep:
    """构造 :class:`ToolStep`，用于 ``@fx.tool(steps=[...])`` 有序流水线。"""
    return ToolStep(task=task, kwargs=dict(kwargs))


def _normalize_steps(steps: Sequence[str | ToolStep]) -> tuple[ToolStep, ...]:
    """把 ``steps`` 元素（任务名或 :class:`ToolStep`）统一成 :class:`ToolStep`。"""
    normalized: list[ToolStep] = []
    for item in steps:
        if isinstance(item, str):
            normalized.append(ToolStep(task=item))
        elif isinstance(item, ToolStep):
            normalized.append(item)
        else:
            raise TypeError(f"steps 元素必须是 str 或 ToolStep，收到 {type(item).__name__}")
    names = [item.task for item in normalized]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"steps 中任务不能重复: {duplicates}")
    return tuple(normalized)


@dataclass(frozen=True)
class ToolSpec:
    """工具描述符：由 ``@fx.tool`` 装饰器注册。

    封装函数 + CLI 元数据 + DAG 编排参数，运行时映射为 :class:`TaskSpec`。

    参数
    ----
    name:
        工具名（如 ``"pymake"``）；多个 ``@fx.tool`` 共用同名即多 subcommand 工具
    subcommand:
        子命令名；``None`` 表示单命令工具（整个工具仅一个函数）
    func:
        被装饰的函数（签名驱动 CLI，函数体即逻辑）
    help:
        子命令帮助文本；默认用函数 docstring
    description:
        工具描述，用于 fcmd 工具列表
    cmd:
        命令列表或 shell 字符串；有 ``cmd`` 执行命令，函数体不执行（签名仅驱动 CLI）
    needs:
        依赖任务名（引用同 tool 的其他 subcommand）；语义是「必须先完成」，
        不表达「必须已具备某状态」（后者用 ``preconditions``）
    steps:
        有序流水线：第 n 步依赖第 n-1 步；目标任务依赖最后一步。
        元素为任务名或 :func:`step`（可携带只注入该步的静态参数）。
        与 ``cmd`` 互斥；声明 ``steps`` 即视为聚合任务
    preconditions:
        目标子命令的前置断言：构建 DAG 前按签名注入参数执行，失败即中止；
        不进入 DAG、不计步数；``--dry-run`` 时跳过
    aggregate:
        显式声明是否为编排壳任务；``None`` 表示按函数体自动推断
        （历史行为），``True`` / ``False`` 强制指定
    strategy:
        执行策略：``"sequential"`` / ``"thread"`` / ``"async"`` / ``"dependency"``
    cwd:
        工作目录（cmd 任务装饰器级默认）；若函数签名有 ``cwd`` 参数则被 CLI 值覆盖
    allow_upstream_skip:
        上游 SKIPPED 时本任务仍执行
    passthrough:
        透传 :class:`TaskSpec` 对应字段：``True`` 时 cmd 任务不捕获
        stdout/stderr、直接透传终端（交互式命令用）
    hidden:
        不暴露为 subcommand（内部 job，仅被 needs 引用）
    env / retry / timeout:
        透传 :class:`TaskSpec` 对应字段
    param_help:
        参数名 → 帮助文本映射（DSL 声明式命令用）；``None`` 时参数帮助退化为
        参数名（与既有签名推导行为一致）
    examples:
        示例调用列表（完整 ``fcmd ...`` 命令）；同时进入 ``--help`` 末尾与
        ``fcmd info --json`` 的 schema，供人直接照抄、供模型减少参数猜测
    """

    name: str
    subcommand: str | None
    func: Callable[..., Any]
    help: str = ""
    description: str = ""
    cmd: tuple[str, ...] | str | None = None
    needs: tuple[str, ...] = ()
    steps: tuple[ToolStep, ...] = ()
    preconditions: tuple[Callable[..., Any], ...] = ()
    aggregate: bool | None = None
    strategy: Literal["sequential", "thread", "async", "dependency"] | None = None
    cwd: str | Path | None = None
    allow_upstream_skip: bool = False
    passthrough: bool = False
    hidden: bool = False
    env: Mapping[str, str] | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None
    param_help: Mapping[str, str] | None = None
    examples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.steps and self.cmd is not None:
            raise ValueError(f"工具 {self.name!r} 子命令 {self.subcommand!r}: steps 与 cmd 互斥")
        if self.steps and self.aggregate is False:
            raise ValueError(f"工具 {self.name!r} 子命令 {self.subcommand!r}: steps 已是聚合任务，不能 aggregate=False")
        if self.cmd is not None and self.aggregate is True:
            raise ValueError(f"工具 {self.name!r} 子命令 {self.subcommand!r}: cmd 任务不能 aggregate=True")


def _task_name(spec: ToolSpec) -> str:
    """子命令在 DAG 内的任务名（与 :func:`_build_task_spec` 保持一致）。"""
    return spec.subcommand if spec.subcommand is not None else spec.name
