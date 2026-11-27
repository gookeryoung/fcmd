"""守护线程并行池：进程退出时不再等待未完成任务。

``concurrent.futures.ThreadPoolExecutor`` 的 worker 是**非守护**线程，解释器退出
（含 ``atexit`` 与 ``threading._shutdown``）会 ``join`` 每一个 worker。只要有一个
任务卡在时长不可控的 IO 上（``wsl.exe`` 子进程、DNS、TLS 握手、镜像站限速），
「关窗」就会变成「窗口没了、进程挂几分钟到几十分钟」，任务管理器里才看得出。

本模块用「守护线程 + 队列」提供同样的提交 / 结果语义：

- ``submit(fn, *args)`` 返回标准 :class:`concurrent.futures.Future`，
  ``result()`` / ``add_done_callback()`` / :func:`concurrent.futures.as_completed`
  都能直接使用；
- :meth:`DaemonPool.map` 与 ``ThreadPoolExecutor.map`` 一致：按输入顺序返回，
  异常原样抛给调用方；
- worker 是守护线程，进程退出时由解释器直接结束，不再阻塞 ``atexit``；
- 空闲 worker 复用，不做「每次调用新建线程池」。

仅供「可能长时间阻塞、且中途结果不值得等待」的任务使用；需要保证执行完成
（例如把结果写进配置）的任务仍走同步调用。

本模块放在 ``shared`` 层（ADR 0001）：``infras`` 的网络探测、``gui`` 的后台任务与
``apps`` 的扫描 / 拷贝都要用同一套线程池，不能让 ``gui`` 反向依赖 ``infras``。
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from typing import Any, TypeVar

__all__ = ["DaemonPool", "default_pool", "parallel_map"]

_T = TypeVar("_T")
_R = TypeVar("_R")

_DEFAULT_MAX_WORKERS = 8
"""默认并发上限：够覆盖轮询 / 探测 / 下载这类短任务，又不会把 wsl.exe 打满。"""


class DaemonPool:
    """固定上限的守护线程池；用法与 ``ThreadPoolExecutor`` 近似。

    ``DaemonPool(max_workers, thread_name_prefix)`` 可作上下文管理器：退出时投递
    哨兵让空闲 worker 结束（卡住的任务仍在守护线程里，不会拖住进程退出）。
    """

    def __init__(self, max_workers: int = _DEFAULT_MAX_WORKERS, *, thread_name_prefix: str = "fcmd-pool") -> None:
        self._max_workers = max(1, int(max_workers))
        self._prefix = thread_name_prefix
        self._queue: queue.SimpleQueue[
            tuple[Future[Any], Callable[..., Any], tuple[Any, ...], dict[str, Any]] | None
        ] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._closed = False

    # ------------------------------------------------------------------ #
    # 提交
    # ------------------------------------------------------------------ #
    def submit(self, fn: Callable[..., _R], /, *args: Any, **kwargs: Any) -> Future[_R]:
        """把 ``fn(*args, **kwargs)`` 交给池执行；返回标准 Future。"""
        future: Future[_R] = Future()
        if self._closed:
            future.set_exception(RuntimeError("线程池已关闭"))
            return future
        self._ensure_worker()
        self._queue.put((future, fn, args, kwargs))
        return future

    def map(self, fn: Callable[[_T], _R], items: Iterable[_T]) -> list[_R]:
        """按并发上限执行 ``fn``，按输入顺序返回结果；任一失败原样抛出。"""
        values = list(items)
        futures = [self.submit(fn, value) for value in values]
        return [future.result() for future in futures]

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:  # noqa: ARG002 - 与 ThreadPoolExecutor 签名兼容
        """投递哨兵结束空闲 worker；卡住的任务留给守护线程。

        签名与 ``ThreadPoolExecutor.shutdown`` 兼容，但**不会 join** 正在执行的任务：
        那正是本模块要避免的行为（卡住的 IO 任务会拖住关窗 / 退出）。``wait`` 只用来
        保持调用点无需改动；``cancel_futures=True`` 会取消尚未开始的任务。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            workers = list(self._workers)
        if cancel_futures:
            self._cancel_pending()
        for _ in workers:
            self._queue.put(None)

    def _cancel_pending(self) -> None:
        """清空待执行队列并取消对应 Future（已在跑的不管）。"""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                continue
            future, _fn, _args, _kwargs = item
            future.cancel()

    def __enter__(self) -> DaemonPool:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _ensure_worker(self) -> None:
        """按需补足 worker：新任务到来时没有空闲线程才新建（最多 ``max_workers``）。"""
        with self._lock:
            if len(self._workers) >= self._max_workers:
                return
            index = len(self._workers) + 1
            thread = threading.Thread(
                target=self._work,
                name=f"{self._prefix}-{index}",
                daemon=True,
            )
            self._workers.append(thread)
        thread.start()

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:  # 关闭哨兵：空闲线程退出
                return
            future, fn, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)


_DEFAULT_POOL: DaemonPool | None = None
_DEFAULT_LOCK = threading.Lock()


def default_pool() -> DaemonPool:
    """进程内共享的守护线程池（GUI 后台任务用；不注册 ``atexit`` 关闭）。"""
    global _DEFAULT_POOL  # noqa: PLW0603 - 进程级单例
    if _DEFAULT_POOL is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_POOL is None:
                workers = max(4, min(16, (os.cpu_count() or 4) * 2))
                _DEFAULT_POOL = DaemonPool(workers, thread_name_prefix="fcmd-task")
    return _DEFAULT_POOL


def parallel_map(fn: Callable[[_T], _R], items: Iterable[_T], *, workers: int = _DEFAULT_MAX_WORKERS) -> list[_R]:
    """一次性并发执行并等待结果（等价 ``ThreadPoolExecutor.map``，但不拖住退出）。"""
    values = list(items)
    if not values:
        return []
    if workers <= 1 or len(values) == 1:
        return [fn(value) for value in values]
    with DaemonPool(workers) as pool:
        return pool.map(fn, values)
