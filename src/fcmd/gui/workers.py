"""Qt 后台任务与定时器：把阻塞调用移出 GUI 线程，结果回投主线程。

任务跑在**守护线程池**（:mod:`fcmd.shared.pool`）里，信号跨线程投递由 Qt 排队到
GUI 线程执行：不需要自己写轮询，也不会在窗口销毁后回调到已释放的控件（连接随
控件失效）。

使用方式：

    submit(
        lambda: llmenv.list_states(),
        on_done=self._apply_states,
        on_error=self._report,
        on_finished=self._idle,
    )

- 异常在任务线程内捕获并转成字符串交给 ``on_error``，不会让进程崩溃；
- 任务对象在完成前保持强引用，避免被回收；
- ``periodic`` 用 ``QTimer`` 实现固定间隔回调，语义与旧版的轮询一致。

**不能用 ``QThreadPool``**：它的 worker 不是守护线程，``QCoreApplication`` 析构时
会 ``waitForDone()``，进程退出还要 ``join`` 线程池线程。只要有一个任务卡在不可控
IO 上（``wsl.exe`` 子进程、DNS、TLS、镜像站限速），用户看到的就是「窗口关了、
进程长时间不退」。:func:`submit` 与 :func:`submit_daemon` 都跑在守护线程里，
关闭应用时进程可以直接退出，未完成任务由解释器结束。
"""

from __future__ import annotations

import threading
import traceback
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from fcmd.shared.pool import default_pool

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

__all__ = ["EventBridge", "ProgressBridge", "TaskSignals", "periodic", "submit", "submit_daemon"]


class EventBridge(QObject):  # pragma: no cover - Qt 信号桥
    """通用后台事件桥：工作线程 ``emit`` 元组，界面按 kind 分发。

    各窗口原先各复制一份 ``_TaskBridge`` / ``_ConvertBridge``（都是
    ``QObject + Signal(object)``），统一收敛到这里。
    """

    event = Signal(object)


class ProgressBridge(QObject):  # pragma: no cover - Qt 信号桥
    """单值进度桥：工作线程 ``emit`` ``(done, total)`` 或百分比对象。

    适合只需「一条进度」的下载 / 转换窗口；多种事件混合的场景继续用
    :class:`EventBridge`。
    """

    progress = Signal(object)


class TaskSignals(QObject):  # pragma: no cover - Qt 信号对象
    """任务结果信号：``done(result)`` / ``failed(message)`` / ``finished()``。"""

    done = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _Task:  # pragma: no cover - 需要 Qt 事件循环
    """一次已提交的后台任务：持有信号对象与执行句柄，供测试等待 / 断言。"""

    def __init__(self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = TaskSignals()
        self.future: Any = None

    def run(self) -> None:
        _run_and_emit(self._fn, self._args, self._kwargs, self.signals)

    def done(self) -> bool:
        """任务是否已结束（完成 / 异常 / 已取消）。"""
        return bool(self.future is None or self.future.done())


def _run_and_emit(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    signals: TaskSignals,
) -> None:
    """执行任务并把结果 / 异常按 ``done → failed → finished`` 发回主线程。"""
    try:
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            signals.failed.emit(detail)
        else:
            signals.done.emit(result)
        finally:
            signals.finished.emit()
    except RuntimeError:  # pragma: no cover - 解释器退出时信号源可能已析构
        return


_PENDING: set[_Task] = set()
"""未完成任务的强引用；从提交到 ``finished`` 期间由这里持有。"""


def submit(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    **kwargs: Any,
) -> _Task:  # pragma: no cover - 需要 Qt 事件循环
    """把 ``fn`` 投递到守护线程池；回调按 ``done → failed → finished`` 顺序触发。"""
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
    task.future = default_pool().submit(task.run)
    return task


_DAEMON_PENDING: set[threading.Thread] = set()
"""未完成的守护线程强引用；直到 ``finished`` 回到主线程才释放。"""

_DAEMON_SIGNALS: set[TaskSignals] = set()
"""未完成的信号对象强引用：若发送者先于排队事件被回收，``finished`` 会丢失。"""


def submit_daemon(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    **kwargs: Any,
) -> threading.Thread:  # pragma: no cover - 需要 Qt 事件循环
    """在守护线程执行 ``fn``，语义与 :func:`submit` 相同，同样不阻塞应用退出。

    与 :func:`submit` 的差别只在调度方式：本函数每个任务独占一个线程，不受共享池
    并发上限约束，适合「一次只有一个、且可能长时间阻塞」的下载 / 拉列表任务；
    ``submit`` 走进程内共享池，适合短任务（状态轮询、目录扫描）。两者的 worker
    都是守护线程，关闭应用时进程可以直接退出。
    """
    signals = TaskSignals()
    thread: threading.Thread | None = None

    def _cleanup() -> None:
        """在 GUI 线程收到 ``finished`` 后再释放引用，避免丢事件。"""
        _DAEMON_SIGNALS.discard(signals)
        if thread is not None:
            _DAEMON_PENDING.discard(thread)
        if on_finished is not None:
            on_finished()

    if on_done is not None:
        signals.done.connect(on_done)
    if on_error is not None:
        signals.failed.connect(on_error)
    signals.finished.connect(_cleanup)

    thread = threading.Thread(
        target=lambda: _run_and_emit(fn, args, kwargs, signals),
        name="fcmd-daemon-task",
        daemon=True,
    )
    _DAEMON_SIGNALS.add(signals)
    _DAEMON_PENDING.add(thread)
    thread.start()
    return thread


def periodic(owner: QObject, interval_ms: int, callback: Callable[[], None]) -> QTimer:  # pragma: no cover
    """创建并启动固定间隔定时器；``owner`` 销毁时定时器随之释放。"""
    timer = QTimer(owner)
    timer.setInterval(interval_ms)
    timer.timeout.connect(callback)
    timer.start()
    return timer
