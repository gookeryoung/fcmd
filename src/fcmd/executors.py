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
本模块按职责分层：

* :mod:`fcmd._task_runner` —— **任务级**执行器与共享状态
  (:class:`fcmd._task_runner._ExecContext` / 线程池 / 跳过重试失败处理 /
  :class:`fcmd._task_runner.SyncTaskRunner` /
  :class:`fcmd._task_runner.AsyncTaskRunner`)。
* 本模块 —— **层级/依赖调度**与公共 :func:`run` 入口：
  :class:`SequentialLayerRunner` / :class:`ThreadedLayerRunner` /
  :class:`AsyncLayerRunner` （层屏障模型）与 :class:`DependencyRunner`
  （依赖驱动，增量就绪集 ``in_degree`` 计数器 + ``dependents`` 反向邻接表，
  大图 10k+ 任务调度开销从 O(N²) 降至 O(N)）。

所有策略共享统一异步内核，支持：
* :class:`RetryPolicy`（max_attempts/delay/backoff/jitter/retry_on）
* 软依赖注入与默认值
* 按任务策略覆盖
* ``continue_on_error``
* 条件判断（上下文感知）
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Iterable, Mapping
from dataclasses import replace as dc_replace
from typing import Any, Literal

from ._task_runner import (
    AsyncTaskRunner,
    SyncTaskRunner,
    _build_context,
    _emit,
    _ExecContext,
    _shutdown_thread_pool,
)
from .apis.context import describe_injection
from .apis.dag import Graph
from .apis.errors import TaskFailedError
from .apis.report import RunReport
from .apis.task import EventCallback, RunConfig, TaskEvent, TaskResult, TaskSpec, TaskStatus
from .console import get_console

logger = logging.getLogger(__name__)

# 观察者回调类型。
Strategy = Literal["sequential", "thread", "async", "dependency"]


# ---------------------------------------------------------------------- #
# 共享辅助：结果存储
# ---------------------------------------------------------------------- #
def _filter_and_sort(
    layer: list[str],
    graph: Graph,
) -> tuple[list[str], dict[str, TaskSpec[Any]]]:
    """返回待运行列表与 specs 映射。

    预构建 ``{name: spec}`` 映射，供调用方复用，消除 runner 内的重复 ``resolved_spec`` 调用。
    """
    specs: dict[str, TaskSpec[Any]] = {}
    to_run: list[str] = []
    for name in layer:
        spec = graph.resolved_spec(name)
        specs[name] = spec
        to_run.append(name)
    return to_run, specs


def _store_result(
    result: TaskResult[Any],
    spec: TaskSpec[Any],
    ctx: _ExecContext,
) -> None:
    """存储任务结果到 context/statuses/report 并触发事件。"""
    ctx.context[spec.name] = result.value
    ctx.statuses[spec.name] = result.status.value
    ctx.report.results[spec.name] = result
    _emit(ctx.on_event, result)


# ---------------------------------------------------------------------- #
# 层执行器
# ---------------------------------------------------------------------- #
class SequentialLayerRunner:
    """逐个运行某层的任务。"""

    @staticmethod
    def execute(
        layer: list[str],
        graph: Graph,
        ctx: _ExecContext,
        layer_idx: int,
    ) -> None:
        to_run, specs = _filter_and_sort(layer, graph)
        for name in to_run:
            spec = specs[name]
            task_ctx = _build_context(spec, ctx.context, ctx.statuses)
            result = SyncTaskRunner.run(spec, task_ctx, layer_idx, ctx)
            _store_result(result, spec, ctx)


class ThreadedLayerRunner:
    """在线程池中并发运行某层的任务。"""

    @staticmethod
    def execute(
        layer: list[str],
        graph: Graph,
        ctx: _ExecContext,
        layer_idx: int,
        pool: concurrent.futures.ThreadPoolExecutor,
    ) -> None:
        to_run, specs = _filter_and_sort(layer, graph)
        if not to_run:  # pragma: no cover - Graph.layers() 不产生空层
            return
        context_snapshot = dict(ctx.context)
        statuses_snapshot = dict(ctx.statuses)

        def _run_threaded_task(name: str) -> tuple[dict[str, Any], TaskResult[Any]]:
            spec = specs[name]
            task_ctx = _build_context(spec, context_snapshot, statuses_snapshot)
            return task_ctx, SyncTaskRunner.run(spec, task_ctx, layer_idx, ctx)

        future_to_name: dict[concurrent.futures.Future[tuple[dict[str, Any], TaskResult[Any]]], str] = {
            pool.submit(_run_threaded_task, name): name for name in to_run
        }
        completed: dict[str, tuple[dict[str, Any], TaskResult[Any]]] = {}
        try:
            for fut in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[fut]
                completed[name] = fut.result()
        except BaseException:
            # fail-fast：首个任务失败时取消同层其他未完成的 future（对齐 DependencyRunner 语义）。
            # 注意：正在运行的任务无法中断（Python 线程限制），仅取消排队中的任务；
            # 已完成的任务结果仍需存储（finally 块）。
            for other in future_to_name:
                if not other.done():
                    other.cancel()
            raise
        finally:
            for name, (_, result) in completed.items():
                _store_result(result, specs[name], ctx)


class AsyncLayerRunner:
    """在事件循环上并发运行某层的任务。"""

    @staticmethod
    async def execute(
        layer: list[str],
        graph: Graph,
        ctx: _ExecContext,
        layer_idx: int,
    ) -> None:
        to_run, specs = _filter_and_sort(layer, graph)
        if not to_run:  # pragma: no cover - Graph.layers() 不产生空层
            return
        context_snapshot = dict(ctx.context)
        statuses_snapshot = dict(ctx.statuses)

        async def _run_async_task(name: str) -> tuple[dict[str, Any], TaskResult[Any]]:
            spec = specs[name]
            task_ctx = _build_context(spec, context_snapshot, statuses_snapshot)
            result = await AsyncTaskRunner.run(spec, task_ctx, layer_idx, ctx)
            return task_ctx, result

        results = await asyncio.gather(*[_run_async_task(name) for name in to_run])
        for name, (_, result) in zip(to_run, results):
            _store_result(result, specs[name], ctx)


def _build_dependency_index(
    remaining: set[str],
    all_specs: Mapping[str, TaskSpec[Any]],
    completed: set[str],
) -> tuple[dict[str, int], dict[str, list[str]], set[str]]:
    """构建增量就绪集索引：in_degree 计数器 + dependents 反向邻接表 + 初始 ready 集合。

    用于 :class:`DependencyRunner` 替代每轮 O(N) 扫描 ``remaining``。
    每轮调度开销从 O(N*D) 降至 O(D_out)，大图（10k+ 任务）显著加速。
    """
    in_degree: dict[str, int] = {}
    dependents: dict[str, list[str]] = {name: [] for name in all_specs}
    ready: set[str] = set()
    for name in remaining:
        spec = all_specs[name]
        # 软依赖可能不在图中（由 defaults 提供默认值），不计入就绪计数。
        deps = (*spec.depends_on, *(d for d in spec.soft_depends_on if d in all_specs))
        unsatisfied = [d for d in deps if d not in completed]
        in_degree[name] = len(unsatisfied)
        for d in unsatisfied:
            if d not in dependents:  # pragma: no cover - dependents 已用 all_specs 全部名称预初始化
                dependents[d] = []
            dependents[d].append(name)
        if in_degree[name] == 0:
            ready.add(name)
    return in_degree, dependents, ready


class DependencyRunner:
    """依赖驱动调度：任务在硬/软依赖完成后立即启动，无层屏障。

    所有任务通过 asyncio 并发调度。同步任务卸载到线程池。

    本类直接调用模块级共享辅助函数（:func:`_store_result`），职责清晰。
    """

    @staticmethod
    async def execute(
        graph: Graph,
        ctx: _ExecContext,
    ) -> None:
        all_names = list(graph.all_specs().keys())
        all_specs: dict[str, TaskSpec[Any]] = {name: graph.resolved_spec(name) for name in all_names}

        # 事件驱动调度：跟踪 completed / in_flight / remaining。
        completed: set[str] = set()
        in_flight: dict[str, asyncio.Task[TaskResult[Any]]] = {}
        remaining: set[str] = set(all_names)

        # 增量就绪集：用 in_degree 计数器 + dependents 反向邻接表替代每轮 O(N) 扫描。
        in_degree, dependents, ready = _build_dependency_index(remaining, all_specs, completed)

        def _on_complete(name: str) -> None:
            """任务完成后，递减其依赖者的 in_degree，新就绪的加入 ready 集合。"""
            for dependent in dependents.get(name, ()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    ready.add(dependent)

        async def _run_task(name: str) -> TaskResult[Any]:
            spec = all_specs[name]
            task_ctx = _build_context(spec, ctx.context, ctx.statuses)
            result = await AsyncTaskRunner.run(spec, task_ctx, None, ctx)
            _store_result(result, spec, ctx)
            return result

        loop = asyncio.get_running_loop()

        # 主循环：调度就绪任务 → 等待完成 → 更新 completed → 重复。
        # fail-fast：首个异常即取消剩余任务并抛出（匹配 gather 语义）。
        while remaining or in_flight:
            # 调度所有就绪任务
            if ready:
                to_schedule = list(ready)
                ready.clear()
                for name in to_schedule:
                    remaining.discard(name)
                    in_flight[name] = loop.create_task(_run_task(name))

            if not in_flight:  # pragma: no cover - 图已校验无环，防御性处理
                if remaining:
                    raise RuntimeError(f"调度死锁：剩余任务 {remaining} 无法就绪")
                break

            done, _ = await asyncio.wait(in_flight.values(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                done_name = next(n for n, t in in_flight.items() if t is task)
                del in_flight[done_name]
                completed.add(done_name)
                _on_complete(done_name)
                exc = task.exception()
                if exc is not None:
                    for t in in_flight.values():
                        if not t.done():
                            t.cancel()
                    raise exc


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
    """按策略派发执行。"""
    if strategy == "sequential":
        layers = graph.layers()
        _drive_sequential(graph, layers, ctx)
    elif strategy == "thread":
        layers = graph.layers()
        _drive_threaded(graph, layers, ctx, max_workers)
    elif strategy == "async":
        layers = graph.layers()
        asyncio.run(_async_drive(graph, layers, ctx))
    elif strategy == "dependency":
        asyncio.run(DependencyRunner.execute(graph, ctx))
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


def _drive_sequential(
    graph: Graph,
    layers: list[list[str]],
    ctx: _ExecContext,
) -> None:
    for idx, layer in enumerate(layers, 1):
        SequentialLayerRunner.execute(layer, graph, ctx, idx)


def _drive_threaded(
    graph: Graph,
    layers: list[list[str]],
    ctx: _ExecContext,
    max_workers: int | None,
) -> None:
    # 线程池在整个 run() 内复用，避免逐层创建/销毁线程的开销。
    max_layer_size = max((len(layer) for layer in layers), default=1)
    pool_workers = max_workers or max(1, min(32, max_layer_size))
    with concurrent.futures.ThreadPoolExecutor(max_workers=pool_workers) as pool:
        for idx, layer in enumerate(layers, 1):
            ThreadedLayerRunner.execute(layer, graph, ctx, idx, pool)


async def _async_drive(
    graph: Graph,
    layers: list[list[str]],
    ctx: _ExecContext,
) -> None:
    for idx, layer in enumerate(layers, 1):
        await AsyncLayerRunner.execute(layer, graph, ctx, idx)
