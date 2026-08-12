"""依赖驱动调度器：无层屏障的最大并行度策略。

:class:`DependencyRunner` 实现 ``dependency`` 策略：任务在其所有硬/软依赖
完成后立即启动，无需等待同层其他任务。所有任务通过 ``asyncio`` 并发调度，
同步任务卸载到线程池。

核心优化：**增量就绪集**。用 ``in_degree`` 计数器 + ``dependents`` 反向邻接表
替代每轮 O(N) 扫描 ``remaining``，每轮调度开销从 O(N*D) 降至 O(D_out)，
大图（10k+ 任务）显著加速。

与 :mod:`fcmd._layer_runner` 的层屏障模型对比：本模块无层屏障，任务就绪即启动，
最大化并行度；层模型必须整层完成后才进入下一层，适合需要确定性顺序的场景。

fail-fast 语义：首个异常即取消剩余任务并抛出（匹配 ``asyncio.gather`` 语义）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ._task_runner import (
    AsyncTaskRunner,
    _build_context,
    _ExecContext,
    _store_result,
)
from .apis.dag import Graph
from .apis.task import TaskResult, TaskSpec


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

    本类直接调用模块级共享辅助函数（:func:`fcmd._task_runner._store_result`），
    职责清晰。
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
