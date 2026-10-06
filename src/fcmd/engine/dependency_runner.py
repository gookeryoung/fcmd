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

同步快速路径（:func:`_sync_chain_fast_path_ok` /
:func:`_run_dependency_sync`）：纯同步（无协程函数、无 timeout）且图层宽 ≤1
（单点或纯链）的图按拓扑序直接顺序执行——链式图的拓扑序唯一，顺序执行与
依赖驱动调度语义等价（无并行度可损失、fail-fast 连坐一致），可跳过
``asyncio.run`` 与 ``import asyncio`` 的固定导入成本（~50ms）。
"""

from __future__ import annotations

from graphlib import TopologicalSorter
from typing import TYPE_CHECKING, Any

from fcmd.apis.dag import Graph
from fcmd.apis.task import TaskResult, TaskSpec

from .task_runner import (
    _build_context,
    _ExecContext,
    _is_async_fn,
    _run_async_task,
    _run_sync_task,
    _store_result,
)

if TYPE_CHECKING:
    import asyncio


def _build_predecessors(all_specs: dict[str, TaskSpec[Any]]) -> dict[str, tuple[str, ...]]:
    """前驱映射：硬依赖 + 图内软依赖（软依赖缺失由 defaults 回退，不计入就绪计数）。"""
    return {
        name: (*spec.depends_on, *(d for d in spec.soft_depends_on if d in all_specs))
        for name, spec in all_specs.items()
    }


def _sync_chain_fast_path_ok(graph: Graph) -> bool:
    """判断 ``dependency`` 策略能否走同步快速路径。

    条件（全部满足）：

    1. 所有任务为同步函数（无协程函数）——协程必须事件循环驱动；
    2. 无任何任务声明 ``timeout``——同步路径无法强制超时（异步路径经
       ``asyncio.wait_for`` 实现）；
    3. 图层宽 ≤1（单点或纯链）——拓扑序唯一，顺序执行与依赖驱动调度
       语义等价，无并行度损失；宽 >1 的图保留线程池并行能力。
    """
    all_names = list(graph.all_specs().keys())
    all_specs: dict[str, TaskSpec[Any]] = {name: graph.resolved_spec(name) for name in all_names}
    for spec in all_specs.values():
        if spec.timeout is not None or _is_async_fn(spec):
            return False
    return all(len(layer) <= 1 for layer in graph.layers())


def _run_dependency_sync(graph: Graph, ctx: _ExecContext) -> None:
    """同步快速路径：纯同步链式图按拓扑序直接执行（无事件循环、无线程池）。

    fail-fast 语义与 :func:`_run_dependency` 一致：任务失败（耗尽重试且未
    ``continue_on_error``）抛 :class:`~fcmd.apis.errors.TaskFailedError`，
    后续任务不再执行。
    """
    all_names = list(graph.all_specs().keys())
    all_specs: dict[str, TaskSpec[Any]] = {name: graph.resolved_spec(name) for name in all_names}
    predecessors = _build_predecessors(all_specs)
    for name in TopologicalSorter(predecessors).static_order():
        spec = all_specs[name]
        task_ctx = _build_context(spec, ctx.context, ctx.statuses)
        result = _run_sync_task(spec, task_ctx, None, ctx)
        _store_result(result, spec, ctx)


async def _run_dependency(
    graph: Graph,
    ctx: _ExecContext,
) -> None:
    """依赖驱动调度：任务在硬/软依赖完成后立即启动，无层屏障。

    所有任务通过 asyncio 并发调度。同步任务卸载到线程池。
    """
    import asyncio  # 下沉导入：纯同步链式图走 _run_dependency_sync，不付出 asyncio 导入成本

    all_names = list(graph.all_specs().keys())
    all_specs: dict[str, TaskSpec[Any]] = {name: graph.resolved_spec(name) for name in all_names}
    predecessors = _build_predecessors(all_specs)
    sorter = TopologicalSorter(predecessors)
    sorter.prepare()

    in_flight: dict[str, asyncio.Task[TaskResult[Any]]] = {}
    # 反向映射：task -> 任务名，O(1) 完成任务查找（替代 O(N) next() 扫描）。
    _task_to_name: dict[asyncio.Task[TaskResult[Any]], str] = {}
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
            task = loop.create_task(_run_one(name))
            in_flight[name] = task
            _task_to_name[task] = name
        ready = []

        if not in_flight:  # pragma: no cover - 图已校验无环，防御性处理
            raise RuntimeError("调度死锁：剩余任务无法就绪")

        done, _ = await asyncio.wait(in_flight.values(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            done_name = _task_to_name.pop(task)
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
