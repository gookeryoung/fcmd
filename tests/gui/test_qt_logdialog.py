"""``LogChannelMixin`` 与 ``LogDialog`` 的离屏测试：缓冲回放、实时追加与清空。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.logdialog import LogChannelMixin


class _LogHost(LogChannelMixin, QObject):
    """最小宿主：按 mixin 约定声明 ``logLine`` 信号。"""

    logLine = Signal(str)

    def __init__(self, *, limit: int = 1000) -> None:
        super().__init__()
        self._init_log_channel(limit=limit)


class _StampedHost(_LogHost):
    """覆盖格式化钩子，模拟 wslman 的时间戳前缀。"""

    def format_log_line(self, message: str) -> str:
        return f"<{message}>"


@pytest.fixture(scope="module", autouse=True)
def _app() -> object:
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return app


def test_log_channel_buffers_emits_and_caps() -> None:
    """日志同时进缓冲与信号；超过 limit 后只保留最近若干行。"""
    host = _LogHost(limit=3)
    seen: list[str] = []
    host.logLine.connect(seen.append)
    try:
        for index in range(5):
            host.log(f"line {index}")
        assert seen == [f"line {index}" for index in range(5)]
        assert host._log_lines == ["line 2", "line 3", "line 4"]
        assert host.log_text() == "line 2\nline 3\nline 4"

        host.clear_log()
        assert host._log_lines == []
        assert host.log_text() == ""
    finally:
        host.deleteLater()


def test_log_channel_format_hook_applies_to_buffer_and_signal() -> None:
    """格式化钩子同时作用于缓冲与广播，保证回放 / 实时追加口径一致。"""
    host = _StampedHost()
    seen: list[str] = []
    host.logLine.connect(seen.append)
    try:
        host.log("任务完成")
        assert host._log_lines == ["<任务完成>"]
        assert seen == ["<任务完成>"]
    finally:
        host.deleteLater()


def test_log_channel_dialog_replays_then_appends() -> None:
    """首次打开回放历史，之后实时追加；清空按钮同步清缓冲，关闭不销毁。"""
    host = _LogHost()
    try:
        host.log("启动前日志")
        host.show_log_dialog("任务日志", "提示", parent=None)
        dialog = host._log_dialog
        assert dialog is not None
        assert "启动前日志" in dialog.log_view.text()

        host.log("运行中日志")
        assert "运行中日志" in dialog.log_view.text()

        dialog.log_view.clear_button.click()
        assert host._log_lines == []
        assert dialog.log_view.text() == ""

        host.show_log_dialog("任务日志", "提示", parent=None)
        assert host._log_dialog is dialog
        host.close_log_dialog()
        assert dialog.isVisible() is False
    finally:
        host.deleteLater()
