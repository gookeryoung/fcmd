"""层级执行器：层屏障模型的三种调度策略。

承载 ``sequential`` / ``thread`` / ``async`` 三种策略的层内调度实现：

* :func:`_run_layer_sequential` —— 逐个运行某层任务，确定性顺序，适合调试。
* :func:`_run_layer_threaded` —— 线程池并发运行同层任务，适合 I/O 密集同步任务。
* :func:`_run_layer_async` —— 事件循环并发运行同层任务，适合 I/O 密集异步任务。

三者共享 :func:`_build_spec_map`（层任务 spec 映射预构建）与
:func:`fcmd.engine.task_runner._store_result`（结果存储）。

驱动函数 :func:`_drive_sequential` / :func:`_drive_threaded` /
:func:`_async_drive` 按层迭代调用对应执行函数，由
:func:`fcmd.engine.executors._dispatch_strategy` 按 ``strategy`` 派发。

与 :func:`fcmd.engine.dependency_runner._run_dependency` 的区别：本模块是**层屏障模型**——
同层任务并发，但必须整层完成后才进入下一层；依赖驱动调度无层屏障，
任务在依赖完成后立即启动，最大化并行度。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from fcmd.apis.dag import Graph
from fcmd.apis.task import TaskResult, TaskSpec

from .task_runner import (
    _build_context,
    _ExecContext,
    _run_async_task,
    _run_sync_task,
    _store_result,
)


def _build_spec_map(
    layer: list[str],
    graph: Graph,
) -> dict[str, TaskSpec[Any]]:
    """预构建 ``{name: spec}`` 映射，供层执行函数复用，消除重复 ``resolved_spec`` 调用。"""
    return {name: graph.resolved_spec(name) for name in layer}


# ---------------------------------------------------------------------- #
# 层执行函数
# ---------------------------------------------------------------------- #
def _run_layer_sequential(
    layer: list[str],
    graph: Graph,
    ctx: _ExecContext,
    layer_idx: int,
) -> None:
    """逐个运行某层的任务。"""
    specs = _build_spec_map(layer, graph)
    for name in layer:
        spec = specs[name]
        task_ctx = _build_context(spec, ctx.context, ctx.statuses)
        result = _run_sync_task(spec, task_ctx, layer_idx, ctx)
        _store_result(result, spec, ctx)


def _run_layer_threaded(
    layer: list[str],
    graph: Graph,
    ctx: _ExecContext,
    layer_idx: int,
    pool: concurrent.futures.ThreadPoolExecutor,
) -> None:
    """在线程池中并发运行某层的任务。

    使用**迭代提交**（窗口大小等于池 worker 数），避免一次性提交全部任务时
    worker 在主线程处理失败前已拉取后续任务，导致 ``cancel()`` 无效。
    当某任务失败时立即停止提交新任务，并取消尚未拉取的排队任务。
    """
    if not layer:  # pragma: no cover - Graph.layers() 不产生空层
        return
    specs = _build_spec_map(layer, graph)
    context_snapshot = dict(ctx.context)
    statuses_snapshot = dict(ctx.statuses)

    def _run_threaded_task(name: str) -> tuple[dict[str, Any], TaskResult[Any]]:
        spec = specs[name]
        task_ctx = _build_context(spec, context_snapshot, statuses_snapshot)
        return task_ctx, _run_sync_task(spec, task_ctx, layer_idx, ctx)

    # 窗口大小：不超过池 worker 数，也不超过层任务数
    max_concurrent = min(getattr(pool, "_max_workers", 1), len(layer))
    pending = iter(layer)
    active: dict[concurrent.futures.Future[tuple[dict[str, Any], TaskResult[Any]]], str] = {}
    completed: dict[str, tuple[dict[str, Any], TaskResult[Any]]] = {}

    # 预提交第一批（窗口大小）
    try:
        for _ in range(max_concurrent):
            try:
                name = next(pending)
            except StopIteration:
                break
            active[pool.submit(_run_threaded_task, name)] = name

        # 迭代：等待首个完成 → 存入结果 → 失败则取消剩余 → 否则提交下一个
        while active:
            done, _ = concurrent.futures.wait(
                active,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for fut in done:
                name = active.pop(fut)
                try:
                    completed[name] = fut.result()
                except BaseException:
                    # fail-fast：取消所有尚未完成的 future（排队中的会被移除，
                    # 正在运行的无法中断 —— 但因为我们用了窗口提交，此时最多只有
                    # max_concurrent 个活动任务，而失败的这个已经在运行，其他排队
                    # 的可以被 cancel；新的 pending 根本不会被提交）。
                    for remaining in active:
                        remaining.cancel()
                    raise
            # 提交下一个 pending（如果还有且没有发生失败）
            try:
                next_name = next(pending)
                active[pool.submit(_run_threaded_task, next_name)] = next_name
            except StopIteration:
                pass
    finally:
        for name, (_, result) in completed.items():
            _store_result(result, specs[name], ctx)


async def _run_layer_async(
    layer: list[str],
    graph: Graph,
    ctx: _ExecContext,
    layer_idx: int,
) -> None:
    """在事件循环上并发运行某层的任务。"""
    if not layer:  # pragma: no cover - Graph.layers() 不产生空层
        return
    specs = _build_spec_map(layer, graph)
    context_snapshot = dict(ctx.context)
    statuses_snapshot = dict(ctx.statuses)

    async def _run_one(name: str) -> tuple[dict[str, Any], TaskResult[Any]]:
        spec = specs[name]
        task_ctx = _build_context(spec, context_snapshot, statuses_snapshot)
        result = await _run_async_task(spec, task_ctx, layer_idx, ctx)
        return task_ctx, result

    results = await asyncio.gather(*[_run_one(name) for name in layer])
    for name, (_, result) in zip(layer, results, strict=True):
        _store_result(result, specs[name], ctx)


# ---------------------------------------------------------------------- #
# 驱动函数：按层迭代调用对应执行函数
# ---------------------------------------------------------------------- #
def _drive_sequential(
    graph: Graph,
    layers: list[list[str]],
    ctx: _ExecContext,
) -> None:
    for idx, layer in enumerate(layers, 1):
        _run_layer_sequential(layer, graph, ctx, idx)


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
            _run_layer_threaded(layer, graph, ctx, idx, pool)


async def _async_drive(
    graph: Graph,
    layers: list[list[str]],
    ctx: _ExecContext,
) -> None:
    for idx, layer in enumerate(layers, 1):
        await _run_layer_async(layer, graph, ctx, idx)
