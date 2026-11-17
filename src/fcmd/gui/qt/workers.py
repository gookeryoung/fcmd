"""Qt 后台任务与定时器：把阻塞调用移出 GUI 线程，结果回投主线程。

旧版基座用「daemon 线程 + ``queue.Queue`` + 定时轮询」。Qt 下等价
做法是 ``QThreadPool`` + 信号：信号跨线程投递由 Qt 排队到 GUI 线程执行，
不需要自己写轮询，也不会在窗口销毁后回调到已释放的控件（连接随控件失效）。

使用方式：

    submit(
        lambda: llmenv.list_states(),
        on_done=self._apply_states,
        on_error=self._report,
        on_finished=self._idle,
    )

- 异常在任务线程内捕获并转成字符串交给 ``on_error``，不会让进程崩溃；
- 任务对象在完成前保持强引用，避免 ``QRunnable`` 被回收；
- ``periodic`` 用 ``QTimer`` 实现固定间隔回调，语义与旧版的轮询一致。
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

__all__ = ["TaskSignals", "periodic", "submit"]


class TaskSignals(QObject):  # pragma: no cover - Qt 信号对象
    """任务结果信号：``done(result)`` / ``failed(message)`` / ``finished()``。"""

    done = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _Task(QRunnable):  # pragma: no cover - Qt 线程池
    """在线程池里执行 ``fn(*args, **kwargs)`` 并把结果 / 异常转成信号。"""

    def __init__(self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            try:
                result = self._fn(*self._args, **self._kwargs)
            except Exception as exc:
                detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                self.signals.failed.emit(detail)
            else:
                self.signals.done.emit(result)
            finally:
                self.signals.finished.emit()
        except RuntimeError:  # pragma: no cover - 解释器退出时信号源可能已析构
            return


_PENDING: set[_Task] = set()
"""未完成任务的强引用；``QRunnable`` 交给线程池后由这里持有到结束。"""


def submit(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    **kwargs: Any,
) -> _Task:  # pragma: no cover - 需要 Qt 事件循环
    """把 ``fn`` 投递到全局线程池；回调按 ``done → failed → finished`` 顺序触发。"""
    task = _Task(fn, args, kwargs)
    _PENDING.add(task)

    def _cleanup() -> None:
        _PENDING.discard(task)
        if on_finished is not None:
            on_finished()

    if on_done is not None:
        task.signals.done.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    task.signals.finished.connect(_cleanup)
    QThreadPool.globalInstance().start(task)
    return task


def pending_count() -> int:
    """尚未完成的任务数（测试与调试用）。"""
    return len(_PENDING)


def periodic(owner: QObject, interval_ms: int, callback: Callable[[], None]) -> QTimer:  # pragma: no cover
    """创建并启动固定间隔定时器；``owner`` 销毁时定时器随之释放。"""
    timer = QTimer(owner)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    timer.start()
    return timer
