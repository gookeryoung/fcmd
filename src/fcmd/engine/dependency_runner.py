"""依赖驱动调度器：无层屏障的最大并行度策略。

:func:`_run_dependency` 实现 ``dependency`` 策略：任务在其所有硬/软依赖
完成后立即启动，无需等待同层其他任务。所有任务通过 ``asyncio`` 并发调度，
同步任务卸载到线程池。

调度核心基于标准库 :class:`graphlib.TopologicalSorter` 的增量就绪接口
（``prepare`` / ``get_ready`` / ``done``）：任务完成即调用 ``done`` 释放后继，
``get_ready`` 返回新就绪任务，无需自维护入度计数器与反向邻接表。

与 :mod:`fcmd.engine.layer_runner` 的层屏障模型对比：本模块无层屏障，任务就绪即启动，
最大化并行度；层模型必须整层完成后才进入下一层，适合需要确定性顺序的场景。

fail-fast 语义：首个异常即取消剩余任务并抛出（匹配 ``asyncio.gather`` 语义）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from graphlib import TopologicalSorter

from fcmd.apis.dag import Graph
from fcmd.apis.task import TaskResult, TaskSpec

from .task_runner import (
    _build_context,
    _ExecContext,
    _run_async_task,
    _store_result,
)


async def _run_dependency(
    graph: Graph,
    ctx: _ExecContext,
) -> None:
    """依赖驱动调度：任务在硬/软依赖完成后立即启动，无层屏障。

    所有任务通过 asyncio 并发调度。同步任务卸载到线程池。
    """
    all_names = list(graph.all_specs().keys())
    all_specs: dict[str, TaskSpec[Any]] = {name: graph.resolved_spec(name) for name in all_names}

    # 前驱映射：硬依赖 + 图内软依赖（软依赖缺失由 defaults 回退，不计入就绪计数）。
    predecessors = {
        name: (*spec.depends_on, *(d for d in spec.soft_depends_on if d in all_specs))
        for name, spec in all_specs.items()
    }
    sorter = TopologicalSorter(predecessors)
    sorter.prepare()

    in_flight: dict[str, asyncio.Task[TaskResult[Any]]] = {}
    loop = asyncio.get_running_loop()

    async def _run_one(name: str) -> TaskResult[Any]:
        spec = all_specs[name]
        task_ctx = _build_context(spec, ctx.context, ctx.statuses)
        result = await _run_async_task(spec, task_ctx, None, ctx)
        _store_result(result, spec, ctx)
        return result

    # 初始就绪集。
    ready: list[str] = list(sorter.get_ready())

    # 主循环：调度就绪任务 → 等待完成 → done 释放后继 → 重复。
    # fail-fast：首个异常即取消剩余任务并抛出（匹配 gather 语义）。
    while ready or in_flight:
        for name in ready:
            in_flight[name] = loop.create_task(_run_one(name))
        ready = []

        if not in_flight:  # pragma: no cover - 图已校验无环，防御性处理
            raise RuntimeError("调度死锁：剩余任务无法就绪")

        done, _ = await asyncio.wait(in_flight.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            done_name = next(n for n, t in in_flight.items() if t is task)
            del in_flight[done_name]
            exc = task.exception()
            if exc is not None:
                for t in in_flight.values():
                    if not t.done():
                        t.cancel()
                raise exc
            sorter.done(done_name)
        # 收集本轮完成释放出的新就绪任务。
        ready = list(sorter.get_ready())
