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
本模块通过 **模块级函数** 消除同步/异步任务执行器之间的重复代码：

* 模块级跳过/重试函数（:func:`_prepare_for_execution` / :func:`_should_retry`
  / :func:`_mark_success` / :func:`_handle_failure` / :func:`_finalize_failure`）
  —— 上游跳过 / 条件跳过的预检、重试决策、成功/失败后处理。
* :class:`SyncTaskRunner` / :class:`AsyncTaskRunner` —— 任务级执行器，调用上述函数。
* 模块级共享辅助（:func:`_filter_and_sort` / :func:`_store_result`）
  —— 缓存过滤、结果存储。
* :class:`SequentialLayerRunner` / :class:`ThreadedLayerRunner` /
  :class:`AsyncLayerRunner` —— 层级执行器，调用上述模块级辅助。
* :class:`DependencyRunner` —— 依赖驱动调度（非层模型），同样调用模块级辅助。
  使用 **增量就绪集**（``in_degree`` 计数器 + ``dependents`` 反向邻接表）替代
  每轮 O(N) 扫描，大图（10k+ 任务）调度开销从 O(N²) 降至 O(N)。

所有策略共享统一异步内核，支持：
* :class:`RetryPolicy`（max_attempts/delay/backoff/jitter/retry_on）
* 软依赖注入与默认值
* 按任务策略覆盖
* ``continue_on_error``
* 条件判断（上下文感知）
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import inspect
import logging
import threading
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import datetime
from typing import Any, Awaitable, Literal, cast

from .console import get_console
from .context import build_call_args, describe_injection
from .dag import Graph
from .errors import TaskFailedError, TaskTimeoutError
from .report import RunReport
from .task import EventCallback, TaskEvent, TaskResult, TaskSpec, TaskStatus

logger = logging.getLogger(__name__)

# verbose 模式输出用的 Console（通过懒加载，不抢占冷启动）

# 线程池复用：asyncio.run() 每次创建新事件循环，默认线程池也随之重建。
# 模块级缓存让线程池跨 run() 调用复用，避免重复创建/销毁线程的开销。
_thread_pool: concurrent.futures.ThreadPoolExecutor | None = None
_thread_pool_lock = threading.Lock()


def _get_thread_pool() -> concurrent.futures.ThreadPoolExecutor:
    """获取复用的线程池（惰性创建）。"""
    global _thread_pool  # noqa: PLW0603
    if _thread_pool is None:
        with _thread_pool_lock:
            if _thread_pool is None:
                _thread_pool = concurrent.futures.ThreadPoolExecutor()
    return _thread_pool


def _shutdown_thread_pool() -> None:
    """关闭复用的线程池。"""
    global _thread_pool  # noqa: PLW0603
    if _thread_pool is not None:
        pool = _thread_pool
        _thread_pool = None
        pool.shutdown(wait=False)


# 兜底：防止未经 run() 直接使用执行器的场景导致线程池泄漏。
atexit.register(_shutdown_thread_pool)


# 观察者回调类型。
Strategy = Literal["sequential", "thread", "async", "dependency"]


@dataclass(frozen=True)
class _ExecContext:
    """执行上下文：捆绑 run() 调用链中共享的状态，减少参数传递。

    将 context/statuses/report/on_event 打包为单一参数，使调用链中每个函数
    的参数数 ≤5。frozen=True 保证调用链中不可意外替换整体引用，但不阻止对
    context/report 等可变属性的内部修改（如 ``ctx.context[name] = value``）。

    statuses 单独维护上游任务状态映射（``{task_name: status_value}``），
    供 ``conditions`` 模块的状态检查函数（``success()``/``failure()``/
    ``always()``）通过 :data:`fcmd.task.Context` 的 ``__status__`` 键访问。
    """

    context: dict[str, Any]
    report: RunReport
    on_event: EventCallback | None
    statuses: dict[str, str]


# ---------------------------------------------------------------------- #
# 无状态公共辅助
# ---------------------------------------------------------------------- #
def _is_async_fn(spec: TaskSpec[Any]) -> bool:
    """判断 ``spec.effective_fn`` 是否为协程函数。"""
    return inspect.iscoroutinefunction(spec.effective_fn)


def _emit(on_event: EventCallback | None, result: TaskResult[Any]) -> None:
    """若注册了回调则触发一个观察者事件。"""
    if on_event is None:
        return
    on_event(
        TaskEvent(
            task=result.spec.name,
            status=result.status,
            attempts=result.attempts,
            error=repr(result.error) if result.error else None,
            duration=result.duration,
            reason=result.reason,
        )
    )


def _emit_running(on_event: EventCallback | None, spec: TaskSpec[Any]) -> None:
    """触发 RUNNING 事件（任务开始执行时）。"""
    if on_event is None:
        return
    on_event(
        TaskEvent(
            task=spec.name,
            status=TaskStatus.RUNNING,
            attempts=0,
            error=None,
            duration=None,
            reason=None,
        )
    )


def _build_context(
    spec: TaskSpec[Any],
    global_context: Mapping[str, Any],
    global_statuses: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """构建本任务的上下文：硬依赖 + 软依赖（含默认值回退）+ 上游状态。

    硬依赖：若上游 SKIPPED/FAILED 则不注入（本任务通常也会被跳过）。
    软依赖：上游成功则注入其值；否则注入 ``spec.defaults`` 中的默认值（或 ``None``）。
    上游状态：通过 ``__status__`` 键注入，仅含本任务的硬依赖状态，
    供 ``conditions`` 模块的状态检查函数（``success()``/``failure()``/
    ``always()``）访问。
    """
    # 快速路径：无依赖且无状态查询需求时直接返回空 dict。
    has_deps = bool(spec.depends_on) or bool(spec.soft_depends_on)
    needs_status = bool(spec.conditions) and global_statuses is not None
    if not has_deps and not needs_status:
        return {}
    ctx: dict[str, Any] = {}
    if needs_status:
        # 仅注入本任务硬依赖的状态，避免泄漏无关任务状态。
        ctx["__status__"] = {
            dep: global_statuses[dep]  # type: ignore[index]
            for dep in spec.depends_on
            if dep in global_statuses  # type: ignore[operator]
        }
    for dep in spec.depends_on:
        if dep in global_context:
            ctx[dep] = global_context[dep]
    for dep in spec.soft_depends_on:
        if dep in global_context:
            ctx[dep] = global_context[dep]
        elif dep in spec.defaults:
            ctx[dep] = spec.defaults[dep]
        else:
            ctx[dep] = None
    return ctx


# ---------------------------------------------------------------------- #
# 任务级跳过 / 重试 / 成功处理：模块级函数
# ---------------------------------------------------------------------- #
def _upstream_skip_reason(spec: TaskSpec[Any], report: RunReport) -> str | None:
    """硬依赖被 SKIPPED/FAILED 时返回原因字符串，否则 ``None``。

    软依赖不影响本检查——软依赖被跳过时注入默认值。
    """
    if spec.allow_upstream_skip:
        return None
    for dep in spec.depends_on:
        if (
            dep not in report.results
        ):  # pragma: no cover - _validate_references 保证依赖在图中，_store_result 保证结果已存储
            continue
        dep_status = report.results[dep].status
        if dep_status in (TaskStatus.SKIPPED, TaskStatus.FAILED):
            return f"上游任务 {dep!r} 状态为 {dep_status.value}"
    return None


def _prepare_for_execution(
    spec: TaskSpec[Any],
    context: Mapping[str, Any],
    report: RunReport,
    on_event: EventCallback | None,
) -> TaskResult[Any] | None:
    """执行前预检：上游跳过 / 条件跳过。

    返回 SKIPPED TaskResult 或 ``None``（继续执行）。
    条件判断委托给 :meth:`TaskSpec.should_execute`，避免重复实现。
    """
    # 快速路径：无依赖、无条件时直接放行（最常见场景），
    # 省去 _upstream_skip_reason 与 should_execute 两次函数调用开销。
    if not spec.depends_on and not spec.conditions:
        return None
    # 1. 上游被跳过/失败
    skip_reason = _upstream_skip_reason(spec, report)
    # 2. 条件（单一来源：TaskSpec.should_execute）
    if skip_reason is None:
        should_run, cond_reason = spec.should_execute(context)
        if not should_run:
            skip_reason = cond_reason or "条件不满足"
    if skip_reason is None:
        return None
    # 构造 SKIPPED 结果
    result: TaskResult[Any] = TaskResult(
        spec=spec,
        status=TaskStatus.SKIPPED,
        finished_at=datetime.now(),
        reason=skip_reason,
    )
    _emit(on_event, result)
    logger.info(
        "task %r skipped (%s)",
        spec.name,
        skip_reason,
        extra={
            "run_id": report.run_id,
            "task_name": spec.name,
            "status": TaskStatus.SKIPPED.value,
            "reason": skip_reason,
        },
    )
    return result


def _should_retry(spec: TaskSpec[Any], attempts: int, exc: BaseException) -> bool:
    """是否应继续重试。"""
    return attempts < spec.retry.max_attempts and spec.retry.should_retry(exc)


def _mark_success(result: TaskResult[Any], value: Any) -> None:
    """标记任务成功。"""
    result.value = value
    result.status = TaskStatus.SUCCESS
    result.finished_at = datetime.now()


def _finalize_failure(
    result: TaskResult[Any],
    layer_idx: int | None,
    ctx: _ExecContext,
    continue_on_error: bool,
) -> None:
    """标记任务为 FAILED。若 ``continue_on_error`` 为真则不抛出异常。

    失败结果在抛出前写入 ``ctx.report.results``，使流式 API 能在 re-raise 前
    访问该结果。
    """
    result.status = TaskStatus.FAILED
    result.finished_at = datetime.now()
    ctx.report.results[result.spec.name] = result
    ctx.report.success = False
    _emit(ctx.on_event, result)
    if continue_on_error:
        logger.warning(
            "task %r failed but continue_on_error=True; continuing.",
            result.spec.name,
            extra={
                "run_id": ctx.report.run_id,
                "task_name": result.spec.name,
                "status": TaskStatus.FAILED.value,
                "attempts": result.attempts,
                "error_type": type(result.error).__name__ if result.error else "Unknown",
            },
        )
        return
    raise TaskFailedError(
        task=result.spec.name,
        cause=result.error if result.error is not None else RuntimeError("unknown"),
        attempts=result.attempts,
        layer=layer_idx,
        report=ctx.report,
    )


def _handle_failure(
    spec: TaskSpec[Any],
    result: TaskResult[Any],
    exc: BaseException,
    layer_idx: int | None,
    ctx: _ExecContext,
) -> bool:
    """统一处理失败：超时转换、重试决策、finalize。

    Returns
    -------
    bool
        ``True`` 表示已 finalize（不再重试）；``False`` 表示应继续重试。
    """
    run_id = ctx.report.run_id
    if isinstance(exc, asyncio.TimeoutError):
        exc = TaskTimeoutError(spec.name, spec.timeout or 0.0)
        logger.warning(
            "task %r timed out (attempt %d/%d); retrying",
            spec.name,
            result.attempts,
            spec.retry.max_attempts,
            extra={
                "run_id": run_id,
                "task_name": spec.name,
                "status": TaskStatus.FAILED.value,
                "attempts": result.attempts,
                "error_type": "TaskTimeoutError",
            },
        )
    else:
        logger.warning(
            "task %r failed (attempt %d/%d): %r; retrying",
            spec.name,
            result.attempts,
            spec.retry.max_attempts,
            exc,
            extra={
                "run_id": run_id,
                "task_name": spec.name,
                "status": TaskStatus.FAILED.value,
                "attempts": result.attempts,
                "error_type": type(exc).__name__,
            },
        )
    result.error = exc
    if _should_retry(spec, result.attempts, exc):
        return False
    _finalize_failure(result, layer_idx, ctx, spec.continue_on_error)
    return True


# ---------------------------------------------------------------------- #
# 任务执行器：同步 / 异步（调用模块级跳过/重试函数）
# ---------------------------------------------------------------------- #
class SyncTaskRunner:
    """同步任务执行器：带重试与跳过预检。"""

    @staticmethod
    def run(
        spec: TaskSpec[Any],
        task_ctx: Mapping[str, Any],
        layer_idx: int | None,
        ctx: _ExecContext,
    ) -> TaskResult[Any]:
        skipped = _prepare_for_execution(spec, task_ctx, ctx.report, ctx.on_event)
        if skipped is not None:
            return skipped

        result: TaskResult[Any] = TaskResult(spec=spec)
        result.started_at = datetime.now()
        args, kwargs = build_call_args(spec, task_ctx)

        _emit_running(ctx.on_event, spec)

        while True:
            result.attempts += 1
            try:
                # 快速路径：无 env/cwd 时直接调用，跳过上下文管理器创建开销。
                if spec.env is None and spec.cwd is None:
                    value = spec.effective_fn(*args, **kwargs)
                else:
                    with spec.env_context():
                        value = spec.effective_fn(*args, **kwargs)
                _mark_success(result, value)
                return result
            except Exception as exc:
                if _handle_failure(spec, result, exc, layer_idx, ctx):
                    return result
                wait = spec.retry.wait_seconds(result.attempts)
                if wait > 0:
                    time.sleep(wait)


class AsyncTaskRunner:
    """异步任务执行器：在事件循环上运行同步或异步任务，带重试与跳过预检。"""

    @staticmethod
    async def run(
        spec: TaskSpec[Any],
        task_ctx: Mapping[str, Any],
        layer_idx: int | None,
        ctx: _ExecContext,
    ) -> TaskResult[Any]:
        skipped = _prepare_for_execution(spec, task_ctx, ctx.report, ctx.on_event)
        if skipped is not None:
            return skipped

        result: TaskResult[Any] = TaskResult(spec=spec)
        result.started_at = datetime.now()
        args, kwargs = build_call_args(spec, task_ctx)
        loop = asyncio.get_running_loop()

        _emit_running(ctx.on_event, spec)

        while True:
            result.attempts += 1
            try:
                value = await _execute_async_task(spec, args, kwargs, loop)
                _mark_success(result, value)
                return result
            except Exception as exc:
                if _handle_failure(spec, result, exc, layer_idx, ctx):
                    return result
                wait = spec.retry.wait_seconds(result.attempts)
                if wait > 0:
                    await asyncio.sleep(wait)


async def _execute_async_task(
    spec: TaskSpec[Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> Any:
    """执行异步或同步任务（带超时处理）。"""
    # 异步任务直接 await
    if _is_async_fn(spec):
        coro = cast(Awaitable[Any], spec.effective_fn(*args, **kwargs))
        return await asyncio.wait_for(coro, timeout=spec.timeout) if spec.timeout is not None else await coro

    # 同步任务：卸载到线程池
    fut = _submit_sync_task(spec, args, kwargs, loop)
    return await asyncio.wait_for(fut, timeout=spec.timeout) if spec.timeout is not None else await fut


def _submit_sync_task(
    spec: TaskSpec[Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    loop: asyncio.AbstractEventLoop,
) -> asyncio.Future[Any]:
    """提交同步任务到线程池，返回 Future。"""

    def fn_call() -> Any:
        # 快速路径：无 env/cwd 时直接调用，跳过上下文管理器创建开销。
        if spec.env is None and spec.cwd is None:
            return spec.effective_fn(*args, **kwargs)
        with spec.env_context():
            return spec.effective_fn(*args, **kwargs)

    # 复用模块级线程池，避免每次 asyncio.run() 创建新线程池的开销。
    return loop.run_in_executor(_get_thread_pool(), fn_call)


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
        for fut in concurrent.futures.as_completed(future_to_name):
            name = future_to_name[fut]
            completed[name] = fut.result()
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
    """包装 on_event 回调，在 verbose 模式下用 rich 打印任务生命周期。"""
    console = get_console()

    def _verbose_callback(event: TaskEvent) -> None:
        dur = f" ({event.duration:.3f}s)" if event.duration is not None else ""
        if event.status == TaskStatus.RUNNING:
            console.print(f"[cyan]▸[/cyan] [bold]{event.task!r}[/bold] 开始执行...")
        elif event.status == TaskStatus.SUCCESS:
            console.print(f"[green]✓[/green] [bold]{event.task!r}[/bold] 成功[dim]{dur}[/dim]")
        elif event.status == TaskStatus.FAILED:
            err = f": {event.error}" if event.error else ""
            console.print(
                f"[red]✗[/red] [bold]{event.task!r}[/bold] 失败[dim]{dur} (尝试 {event.attempts} 次)[/dim][red]{err}[/red]"
            )
        elif event.status == TaskStatus.SKIPPED:
            reason = f" ({event.reason})" if event.reason else ""
            console.print(f"[yellow]○[/yellow] [bold]{event.task!r}[/bold] 跳过[dim]{reason}[/dim]")
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


def run(  # noqa: PLR0913
    graph: Graph,
    strategy: Strategy = "dependency",
    *,
    max_workers: int | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    on_event: EventCallback | None = None,
    only: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> RunReport:
    """执行图并返回 :class:`RunReport`。

    参数
    ----
    graph:
        待执行的已校验 :class:`Graph`。
    strategy:
        执行策略: ``"dependency"``（默认，依赖驱动无层屏障，最大并行度）/
        ``"sequential"`` / ``"thread"`` / ``"async"``（层屏障模型）。
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


# 流式执行迭代器（保留接口，P0 不实现）
def run_iter(  # noqa: PLR0913
    graph: Graph,
    strategy: Strategy = "dependency",
    *,
    max_workers: int | None = None,
    verbose: bool = False,
    on_event: EventCallback | None = None,
    only: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
) -> Iterator[tuple[str, TaskResult[Any]]]:
    """流式执行图（P1 阶段实现）。"""
    raise NotImplementedError("run_iter 将在 P1 阶段实现。")
