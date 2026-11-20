"""Qt 后台任务：``submit_daemon`` 的线程属性与回调语义。"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from fcmd.gui.qt import workers


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait(app: QApplication, predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def test_submit_daemon_runs_in_daemon_thread() -> None:
    """任务跑在守护线程：应用退出时不必等未完成的网络请求。"""
    app = _app()
    results: list[tuple[bool, int]] = []
    finished: list[bool] = []
    thread = workers.submit_daemon(
        lambda: (threading.current_thread().daemon, 42),
        on_done=results.append,
        on_finished=lambda: finished.append(True),
    )
    _wait(app, lambda: bool(finished) and thread not in workers._DAEMON_PENDING)
    assert thread.daemon
    assert results == [(True, 42)]
    assert finished == [True]
    assert thread not in workers._DAEMON_PENDING


def test_submit_daemon_reports_errors_on_main_thread() -> None:
    """异常转成 ``on_error`` 文案；回调仍在 GUI 线程执行。"""
    app = _app()
    errors: list[str] = []
    main_thread = threading.current_thread()

    def boom() -> None:
        raise ValueError("boom")

    def on_error(message: str) -> None:
        assert threading.current_thread() is main_thread
        errors.append(message)

    workers.submit_daemon(boom, on_error=on_error)
    _wait(app, lambda: bool(errors))
    assert errors == ["ValueError: boom"]
