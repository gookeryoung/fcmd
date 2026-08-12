"""层级执行器：层屏障模型的三种调度策略。

承载 ``sequential`` / ``thread`` / ``async`` 三种策略的层内调度实现：

* :class:`SequentialLayerRunner` —— 逐个运行某层任务，确定性顺序，适合调试。
* :class:`ThreadedLayerRunner` —— 线程池并发运行同层任务，适合 I/O 密集同步任务。
* :class:`AsyncLayerRunner` —— 事件循环并发运行同层任务，适合 I/O 密集异步任务。

三者共享 :func:`_filter_and_sort`（层任务过滤与 spec 映射预构建）与
:func:`fcmd._task_runner._store_result`（结果存储）。

驱动函数 :func:`_drive_sequential` / :func:`_drive_threaded` /
:func:`_async_drive` 按层迭代调用对应 Runner，由
:func:`fcmd.executors._dispatch_strategy` 按 ``strategy`` 派发。

与 :class:`fcmd.executors.DependencyRunner` 的区别：本模块是**层屏障模型**——
同层任务并发，但必须整层完成后才进入下一层；DependencyRunner 无层屏障，
任务在依赖完成后立即启动，最大化并行度。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from ._task_runner import (
    AsyncTaskRunner,
    SyncTaskRunner,
    _build_context,
    _ExecContext,
    _store_result,
)
from .apis.dag import Graph
from .apis.task import TaskResult, TaskSpec


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


# ---------------------------------------------------------------------- #
# 驱动函数：按层迭代调用对应 Runner
# ---------------------------------------------------------------------- #
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
