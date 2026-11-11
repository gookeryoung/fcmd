"""cmderw 传输队列的暂停门与并行调度纯逻辑。"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "DEFAULT_QUEUE_PARALLEL",
    "MAX_QUEUE_PARALLEL",
    "MIN_QUEUE_PARALLEL",
    "PauseGate",
    "clamp_parallel",
    "parallel_map",
]

MIN_QUEUE_PARALLEL = 1
MAX_QUEUE_PARALLEL = 8
DEFAULT_QUEUE_PARALLEL = 1


class PauseGate:
    """可暂停 / 恢复的执行门；恢复态默认放行。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._event.set()

    @property
    def paused(self) -> bool:
        """当前是否暂停。"""
        return not self._event.is_set()

    def pause(self) -> None:
        """暂停：后续 ``wait`` 阻塞。"""
        self._event.clear()

    def resume(self) -> None:
        """恢复：唤醒所有等待者。"""
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """等待恢复；返回是否已恢复。"""
        return self._event.wait(timeout)


def clamp_parallel(value: int) -> int:
    """把并行上限夹到 ``MIN_QUEUE_PARALLEL``..``MAX_QUEUE_PARALLEL``。"""
    return max(MIN_QUEUE_PARALLEL, min(MAX_QUEUE_PARALLEL, value))


def parallel_map[T](items: Sequence[T], worker: Callable[[T], None], *, parallel: int) -> None:
    """按并行上限执行 ``worker``；``worker`` 需自行处理业务异常。"""
    limit = clamp_parallel(parallel)
    if limit <= 1 or len(items) <= 1:
        for item in items:
            worker(item)
        return
    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = [pool.submit(worker, item) for item in items]
        for future in futures:
            future.result()
