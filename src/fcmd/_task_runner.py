"""任务级执行器与共享执行状态。

承载 ``run()`` 调用链中**任务级**的关注点：

* :class:`_ExecContext` —— 捆绑 context/statuses/report/on_event 的执行上下文，
  减少调用链参数传递。
* 线程池复用（:func:`_get_thread_pool` / :func:`_shutdown_thread_pool`）——
  跨 ``run()`` 调用复用，避免 ``asyncio.run()`` 每次重建线程池的开销。
* 无状态辅助（:func:`_is_async_fn` / :func:`_emit` / :func:`_emit_running`
  / :func:`_build_context`）—— 异步判定、观察者事件触发、任务上下文构建。
* 任务级跳过/重试/失败处理（:func:`_prepare_for_execution` /
  :func:`_should_retry` / :func:`_mark_success` / :func:`_finalize_failure`
  / :func:`_handle_failure`）—— 上游跳过预检、条件跳过、重试决策、失败收尾。
* :class:`SyncTaskRunner` / :class:`AsyncTaskRunner` —— 同步/异步任务执行器，
  调用上述模块级函数消除重复代码。

本模块自包含，不依赖 :mod:`fcmd.executors`，由后者按策略派发调用。
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import inspect
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, cast

from .apis.context import build_call_args
from .apis.errors import TaskFailedError, TaskTimeoutError
from .apis.report import RunReport
from .apis.task import EventCallback, TaskEvent, TaskResult, TaskSpec, TaskStatus

logger = logging.getLogger(__name__)

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
                # 用户提供的任务函数可抛任意异常，宽捕获用于重试/失败处理边界
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
                # 异步任务函数可抛任意异常，宽捕获用于重试/失败处理边界
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
