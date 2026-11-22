"""alarmclock Qt 窗口的离屏测试：时钟刷新、闹钟设定 / 取消、延时、提醒与落盘。

Qt 不可用或 CI 无显示环境时使用 ``offscreen`` 平台；时间通过替身 ``datetime``
固定，保证「默认 1 分钟后」「剩余时长」等断言稳定。
"""

from __future__ import annotations

import json
import os
from datetime import datetime as _RealDatetime
from pathlib import Path
from typing import Any

import pytest

from fcmd._compat import override

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.tools.system import _alarmclock_qt
from fcmd.tools.system.alarmclock import (
    DIGITAL_FORMAT,
    AlarmSettings,
    save_settings,
)

_NOW = _RealDatetime(2026, 1, 1, 10, 0, 0)


class _FrozenDatetime(_RealDatetime):
    """固定 ``now()``，让延时 / 重置 / 默认时间可断言。"""

    @classmethod
    @override
    def now(cls, tz: Any = None) -> _FrozenDatetime:
        _ = tz
        return cls(_NOW.year, _NOW.month, _NOW.day, _NOW.hour, _NOW.minute, _NOW.second)


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """隔离设置目录、固定时间并屏蔽模态弹窗。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = create_application("alarmclock-test")
    _qt_theme.apply_theme(application, _guitheme.resolve_theme("dark-blue"))
    monkeypatch.setattr(_alarmclock_qt, "datetime", _FrozenDatetime)
    monkeypatch.setattr(QApplication, "beep", staticmethod(lambda: None))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    win = _alarmclock_qt.AlarmClockQtWindow(tmp_path / "alarmclock.json")
    yield win
    win._tick_timer.stop()
    win.deleteLater()


def test_default_alarm_text_is_one_minute_later(window: Any) -> None:
    assert window.time_edit.text() == "10:01:00"
    assert window.clock_label.text() == _NOW.strftime(DIGITAL_FORMAT)
    assert window.status.text() == "未设置闹钟"
    assert window.repeat.isChecked() is False


def test_restores_saved_alarm_and_repeat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "alarmclock.json"
    save_settings(AlarmSettings(alarm="07:30:00", repeat=True, geometry="480x400+10+20"), path)
    win = _alarmclock_qt.AlarmClockQtWindow(path)
    try:
        assert win.time_edit.text() == "07:30:00"
        assert win.repeat.isChecked() is True
    finally:
        win._tick_timer.stop()
        win.deleteLater()


def test_arm_and_cancel_alarm(window: Any) -> None:
    window.time_edit.setText("10:30")
    window.arm_alarm()
    assert window._state.target == _RealDatetime(2026, 1, 1, 10, 30, 0)
    assert window.time_edit.text() == "10:30:00"
    assert window.set_button.text() == "取消闹钟"
    assert window.status.text() == "已设置闹钟: 10:30:00"
    window.cancel_alarm()
    assert window._state.armed is False
    assert window.set_button.text() == "设置闹钟"
    assert window.status.text() == "闹钟已取消"


def test_invalid_time_keeps_state(window: Any) -> None:
    window.time_edit.setText("25:00")
    window.arm_alarm()
    assert window._state.armed is False


def test_delay_and_reset(window: Any) -> None:
    window.set_delay(15)
    assert window.time_edit.text() == "10:15:00"
    window.reset_time()
    assert window.time_edit.text() == "10:00:00"


def test_refresh_clock_shows_remaining(window: Any) -> None:
    window.arm_alarm()
    window.refresh_clock(_RealDatetime(2026, 1, 1, 10, 0, 5))
    assert "剩余 00:00:55" in window.status.text()


def test_reminder_blinks_and_close_clears(window: Any) -> None:
    window.arm_alarm()
    window.open_reminder()
    assert window._reminder is not None
    first = window._reminder._color
    window._reminder._blink()
    assert window._reminder._color != first or len(_alarmclock_qt._BG_COLORS) == 1
    assert window.status.text() == "⏰ 闹钟响了！"
    window.close_reminder()
    assert window._reminder is None
    assert window._state.armed is False
    assert window.status.text() == "闹钟已关闭"


def test_repeat_reminder_reschedules_next_day(window: Any) -> None:
    window.repeat.setChecked(True)
    window.time_edit.setText("10:00")
    window.arm_alarm()
    window.open_reminder()
    window.close_reminder()
    assert window._state.armed is True
    assert window._state.target is not None
    assert window._state.target.date() == _RealDatetime(2026, 1, 2).date()
    assert window.status.text().startswith("下次提醒: ")


def test_settings_persisted_on_close(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    window = _alarmclock_qt.AlarmClockQtWindow(tmp_path / "alarmclock.json")
    window.time_edit.setText("08:15:00")
    window.repeat.setChecked(True)
    window.arm_alarm()
    window._persist_settings("480x400+10+20")
    window._tick_timer.stop()
    window.deleteLater()
    raw = json.loads((tmp_path / "alarmclock.json").read_text(encoding="utf-8"))
    assert raw["alarm"] == "08:15:00"
    assert raw["repeat"] is True
    assert raw["geometry"] == "480x400+10+20"
