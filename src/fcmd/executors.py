"""执行器与公共 :func:`run` 入口。

四种执行策略：

* ``sequential`` —— 确定性、一次一个任务。最适合调试。
* ``thread``     —— 通过线程池实现层内并发。最适合 I/O 密集型同步任务。
* ``async``      —— 通过 ``asyncio.gather`` 实现层内并发。同步任务被
                    卸载到线程池；异步任务运行在事件循环上。最适合
                    I/O 密集型异步任务。
* ``dependency`` —— 依赖驱动调度：任务在其所有硬依赖完成后立即启动，
                    无需等待同层其他任务。最大化并行度。

架构
----
本模块按职责分层拆分：

* :mod:`fcmd._task_runner` —— **任务级**执行器与共享状态
  (:class:`fcmd._task_runner._ExecContext` / 线程池 / 跳过重试失败处理 /
  :func:`fcmd._task_runner._run_sync_task` /
  :func:`fcmd._task_runner._run_async_task` / :func:`_store_result`)。
* :mod:`fcmd._layer_runner` —— **层屏障模型**调度
  (:func:`fcmd._layer_runner._run_layer_sequential` /
  :func:`fcmd._layer_runner._run_layer_threaded` /
  :func:`fcmd._layer_runner._run_layer_async` + 驱动函数)。
* :mod:`fcmd._dependency_runner` —— **依赖驱动调度**
  (:func:`fcmd._dependency_runner._run_dependency`，基于标准库
  :class:`graphlib.TopologicalSorter` 增量就绪接口，大图 10k+ 任务
  调度开销 O(N))。
* 本模块 —— 公共 :func:`run` 入口与策略派发。

所有策略共享统一异步内核，支持：
* :class:`RetryPolicy`（max_attempts/delay/backoff/jitter/retry_on）
* 软依赖注入与默认值
* 按任务策略覆盖
* ``continue_on_error``
* 条件判断（上下文感知）
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import replace as dc_replace
from typing import Any, Literal

from ._dependency_runner import _run_dependency
from ._layer_runner import _async_drive, _drive_sequential, _drive_threaded
from ._task_runner import _ExecContext, _shutdown_thread_pool
from .apis.context import describe_injection
from .apis.dag import Graph
from .apis.errors import TaskFailedError
from .apis.report import RunReport
from .apis.task import EventCallback, RunConfig, TaskEvent, TaskStatus
from .console import get_console

logger = logging.getLogger(__name__)

# 观察者回调类型。
Strategy = Literal["sequential", "thread", "async", "dependency"]


# ---------------------------------------------------------------------- #
# 公共 API
# ---------------------------------------------------------------------- #
def _make_verbose_callback(on_event: EventCallback | None) -> EventCallback:
    """包装 on_event 回调，在 verbose 模式下打印任务生命周期。"""
    console = get_console()

    def _verbose_callback(event: TaskEvent) -> None:
        dur = f" ({event.duration:.3f}s)" if event.duration is not None else ""
        if event.status == TaskStatus.RUNNING:
            console.print(f"[cyan]>[/cyan] [bold]{event.task!r}[/bold] 开始执行...")
        elif event.status == TaskStatus.SUCCESS:
            console.print(f"[green]OK[/green] [bold]{event.task!r}[/bold] 成功[dim]{dur}[/dim]")
        elif event.status == TaskStatus.FAILED:
            err = f": {event.error}" if event.error else ""
            console.print(
                f"[red]X[/red] [bold]{event.task!r}[/bold] 失败[dim]{dur} (尝试 {event.attempts} 次)[/dim][red]{err}[/red]"
            )
        elif event.status == TaskStatus.SKIPPED:
            reason = f" ({event.reason})" if event.reason else ""
            console.print(f"[yellow]-[/yellow] [bold]{event.task!r}[/bold] 跳过[dim]{reason}[/dim]")
        if on_event is not None:
            on_event(event)

    return _verbose_callback


def _apply_subgraph_filter(
    graph: Graph,
    only: Iterable[str] | None,
    tags: Iterable[str] | None,
) -> Graph:
    """根据 ``only``/``tags`` 过滤图，返回包含传递依赖的子图。

    ``only`` 与 ``tags`` 取并集：匹配任一条件的任务及其所有传递依赖
    （硬依赖 + 软依赖）都会被包含在子图中，使子图可独立执行。
    """
    names: set[str] = set()
    if only is not None:
        names.update(only)
    if tags is not None:
        tag_set = set(tags)
        for name, spec in graph.all_specs().items():
            if tag_set & set(spec.tags):
                names.add(name)
    if not names:
        return Graph(defaults=graph.defaults)
    return graph.subgraph_with_deps(names)


def _dispatch_strategy(
    strategy: Strategy,
    graph: Graph,
    ctx: _ExecContext,
    max_workers: int | None,
) -> None:
    """按策略派发执行。

    ``dependency`` 走依赖驱动路径（无层屏障）；其余三者走层屏障模型，
    共享一次 ``graph.layers()`` 调用。
    """
    if strategy == "dependency":
        asyncio.run(_run_dependency(graph, ctx))
        return
    layers = graph.layers()
    if strategy == "sequential":
        _drive_sequential(graph, layers, ctx)
    elif strategy == "thread":
        _drive_threaded(graph, layers, ctx, max_workers)
    elif strategy == "async":
        asyncio.run(_async_drive(graph, layers, ctx))
    else:  # pragma: no cover - Strategy Literal 已穷尽所有取值
        raise ValueError(f"Unknown strategy: {strategy!r}")


def run(  # noqa: PLR0912, PLR0913
    graph: Graph,
    strategy: Strategy | None = None,
    *,
    config: RunConfig | None = None,
    max_workers: int | None = None,
    dry_run: bool | None = None,
    verbose: bool | None = None,
    on_event: EventCallback | None = None,
    only: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> RunReport:
    """执行图并返回 :class:`RunReport`。

    支持两种调用方式：

    1. 关键字参数平铺（传统方式，向后兼容）::

        fx.run(graph, "thread", dry_run=True, verbose=True)

    2. 通过 :class:`RunConfig` 打包配置（推荐用于参数较多的场景）::

        cfg = RunConfig(strategy="thread", dry_run=True, verbose=True)
        fx.run(graph, config=cfg)

    同时使用时，关键字参数覆盖 ``config`` 中的同名字段。

    参数
    ----
    graph:
        待执行的已校验 :class:`Graph`。
    strategy:
        执行策略: ``"dependency"``（默认，依赖驱动无层屏障，最大并行度）/
        ``"sequential"`` / ``"thread"`` / ``"async"``（层屏障模型）。
    config:
        :class:`RunConfig` 打包配置对象。显式关键字参数优先级更高。
    max_workers:
        ``"thread"`` 的线程池大小。默认 ``min(32, len(layer))``。
    dry_run:
        若为 ``True``，打印执行计划并返回空报告，不执行任务。
    verbose:
        若为 ``True``，打印任务生命周期到 stdout。
    on_event:
        可选回调，在每次状态转换时调用。
    only:
        只运行指定任务名及其传递依赖。与 ``tags`` 取并集。
    tags:
        只运行匹配任意标签的任务及其传递依赖。与 ``only`` 取并集。

    抛出
    ----
    ValueError
        ``strategy`` 不被识别时。
    TaskFailedError
        任何任务耗尽重试后仍失败时（除非 ``continue_on_error=True``）。
    """
    # 合并配置：优先级 显式关键字参数 > config 字段 > 原始默认值
    if strategy is None:
        strategy = config.strategy if config is not None else "dependency"
    if dry_run is None:
        dry_run = config.dry_run if config is not None else False
    if verbose is None:
        verbose = config.verbose if config is not None else False
    if max_workers is None and config is not None:
        max_workers = config.max_workers
    if on_event is None and config is not None:
        on_event = config.on_event
    if only is None and config is not None and config.only is not None:
        only = list(config.only)
    if tags is None and config is not None and config.tags is not None:
        tags = list(config.tags)
    if dry_run:
        layers = graph.layers()
        _print_dry_run(graph, layers)
        return RunReport(success=True)

    # 子图过滤：only/tags 选择任务子集及其传递依赖
    if only is not None or tags is not None:
        graph = _apply_subgraph_filter(graph, only, tags)

    # verbose 模式下，把所有 spec 的 verbose 标记设为 True，
    # 使 run_command 打印执行命令与返回码（任务生命周期由 callback 打印）
    if verbose:
        graph = Graph.from_specs(
            [dc_replace(s, verbose=True) if not s.verbose else s for s in graph.all_specs().values()],
            defaults=graph.defaults,
        )

    # 入口统一校验一次：所有策略共用，避免 layers() / dependency 路径
    # 各自重复调用 validate()。
    graph.validate()

    # 组合回调链：verbose 打印 + 用户回调
    effective_callback: EventCallback | None = None
    if verbose:
        effective_callback = _make_verbose_callback(on_event)
    elif on_event is not None:
        effective_callback = on_event

    report = RunReport()
    context: dict[str, Any] = {}

    logger.info(
        "运行开始: run_id=%s strategy=%s tasks=%d",
        report.run_id,
        strategy,
        len(graph),
        extra={"run_id": report.run_id, "strategy": strategy, "total_tasks": len(graph)},
    )

    # 打包执行上下文：将 context/statuses/report/on_event 捆绑为单一参数传递给调用链。
    ctx = _ExecContext(
        context=context,
        report=report,
        on_event=effective_callback,
        statuses={},
    )

    try:
        _dispatch_strategy(strategy, graph, ctx, max_workers)
    except TaskFailedError:
        report.success = False
        raise
    finally:
        # 关闭线程池：避免线程泄漏。
        _shutdown_thread_pool()

    logger.info(
        "运行结束: run_id=%s success=%s tasks=%d",
        report.run_id,
        report.success,
        len(report.results),
        extra={
            "run_id": report.run_id,
            "success": report.success,
            "total_tasks": len(report.results),
        },
    )
    return report


def _print_dry_run(graph: Graph, layers: list[list[str]]) -> None:
    """打印执行计划但不运行任何任务。"""
    console = get_console()
    console.print(f"[bold]Dry run:[/bold] [cyan]{len(graph)}[/cyan] tasks, [cyan]{len(layers)}[/cyan] layers")
    for idx, layer in enumerate(layers, 1):
        console.print(f"  [dim]Layer {idx}:[/dim] {layer}")
        for name in layer:
            console.print(f"    [cyan]-[/cyan] {describe_injection(graph.resolved_spec(name))}")
