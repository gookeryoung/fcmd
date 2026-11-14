"""alarmclock 窗口测试。

``AlarmClockWindow`` 依赖 Tk，CI 无显示环境，因此全部用替身模块：
注入假的 ``tkinter`` / ``tkinter.ttk`` / ``tkinter.messagebox``，
替换 ``create_root`` / 主题安装，直接调用窗口方法而不是进入 mainloop。
覆盖初始化、时钟刷新、闹钟设定 / 取消、延时、提醒窗口闪烁与关闭、
设置落盘与生命周期。
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, override

import pytest

from fcmd.tools.system import alarmclock
from fcmd.tools.system.alarmclock import (
    DIGITAL_BORDER_COLORS,
    REMINDER_TITLE,
    AlarmClockWindow,
    AlarmSettings,
    save_settings,
)

_NOW = datetime(2026, 1, 1, 10, 0, 0)


class _FrozenDatetime(datetime):
    """固定 ``now()``，让延时 / 重置 / 默认时间可断言。"""

    @classmethod
    @override
    def now(cls, tz: Any = None) -> _FrozenDatetime:
        _ = tz
        return cls(_NOW.year, _NOW.month, _NOW.day, _NOW.hour, _NOW.minute, _NOW.second)


class _FakeVar:
    """替身 ``StringVar`` / ``BooleanVar``。"""

    def __init__(self, value: Any = None) -> None:
        self._value = value

    def get(self) -> Any:
        return self._value

    def set(self, value: Any) -> None:
        self._value = value


class _FakeWidget:
    """替身 Tk 控件：记录构造参数与 configure 调用。"""

    def __init__(self, master: Any = None, **options: Any) -> None:
        self.master = master
        self.options: dict[str, Any] = dict(options)

    def pack(self, **options: Any) -> None:
        self.options["pack"] = options

    def grid(self, **options: Any) -> None:
        self.options["grid"] = options

    def configure(self, **options: Any) -> None:
        self.options.update(options)

    def destroy(self) -> None:
        self.options["destroyed"] = True


class _FakeToplevel(_FakeWidget):
    """替身 ``Toplevel``：记录定时器、协议与闪烁配置。"""

    def __init__(self, master: Any = None, **options: Any) -> None:
        super().__init__(master, **options)
        self.after_calls: list[tuple[int, Any]] = []
        self.cancelled: list[str] = []
        self.protocols: dict[str, Any] = {}
        self.destroyed = False
        self._next_timer = 0

    def title(self, text: str) -> None:
        self.options["title"] = text

    def attributes(self, *args: Any) -> None:
        self.options.setdefault("attributes", []).append(args)

    def transient(self, master: Any) -> None:
        self.options["transient"] = master

    def protocol(self, name: str, callback: Any) -> None:
        self.protocols[name] = callback

    def after(self, delay: int, callback: Any) -> str:
        self._next_timer += 1
        timer = f"reminder-timer-{self._next_timer}"
        self.after_calls.append((delay, callback))
        return timer

    def after_cancel(self, timer: str) -> None:
        self.cancelled.append(timer)

    def update_idletasks(self) -> None:
        pass

    def winfo_reqwidth(self) -> int:
        return 360

    def winfo_reqheight(self) -> int:
        return 220

    def geometry(self, spec: str) -> None:
        self.options["geometry"] = spec

    def grab_set(self) -> None:
        self.options["grabbed"] = True

    def grab_release(self) -> None:
        self.options["grabbed"] = False

    def focus_force(self) -> None:
        self.options["focused"] = True

    @override
    def destroy(self) -> None:
        self.destroyed = True


class _FakeRoot:
    """替身 Tk 根窗口。"""

    def __init__(self) -> None:
        self.after_calls: list[tuple[int, Any]] = []
        self.cancelled: list[str] = []
        self.bells = 0
        self.destroyed = False
        self._next_timer = 0

    def after(self, delay: int, callback: Any) -> str:
        self._next_timer += 1
        timer = f"root-timer-{self._next_timer}"
        self.after_calls.append((delay, callback))
        return timer

    def after_cancel(self, timer: str) -> None:
        self.cancelled.append(timer)

    def bell(self) -> None:
        self.bells += 1

    def winfo_geometry(self) -> str:
        return "480x400+300+200"

    def winfo_width(self) -> int:
        return 480

    def winfo_height(self) -> int:
        return 400

    def winfo_rootx(self) -> int:
        return 300

    def winfo_rooty(self) -> int:
        return 200

    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080

    def destroy(self) -> None:
        self.destroyed = True


@dataclass
class _Gui:
    """一次窗口构造的替身环境。"""

    window: AlarmClockWindow
    root: _FakeRoot
    errors: list[tuple[str, str]]
    path: Path


def _theme() -> Any:
    """替身主题：只需 palette 与 spacing。"""
    return SimpleNamespace(
        palette=SimpleNamespace(text="#ffffff", log_bg="#000000", danger="#ff0000", muted="#888888"),
        spacing=SimpleNamespace(md=8, sm=4, xs=2, xxs=1, xl=12, lg=6),
    )


@pytest.fixture
def make_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[AlarmSettings | None], _Gui]:
    """构造注入替身依赖的 ``AlarmClockWindow``。"""

    def build(settings: AlarmSettings | None = None) -> _Gui:
        root = _FakeRoot()
        errors: list[tuple[str, str]] = []

        fake_tk = types.ModuleType("tkinter")
        fake_tk.__dict__.update(
            {"StringVar": _FakeVar, "BooleanVar": _FakeVar, "Label": _FakeWidget, "Toplevel": _FakeToplevel}
        )
        fake_ttk = types.ModuleType("tkinter.ttk")
        for name in ("Button", "Checkbutton", "Entry", "Frame", "Label", "LabelFrame"):
            setattr(fake_ttk, name, _FakeWidget)
        fake_messagebox = types.ModuleType("tkinter.messagebox")
        fake_messagebox.__dict__.update({"showerror": lambda title, text: errors.append((title, text))})
        fake_tk.__dict__.update({"ttk": fake_ttk, "messagebox": fake_messagebox})

        monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
        monkeypatch.setitem(sys.modules, "tkinter.ttk", fake_ttk)
        monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_messagebox)
        monkeypatch.setattr(alarmclock, "datetime", _FrozenDatetime)
        monkeypatch.setattr(alarmclock._guiwindow, "create_root", lambda **_kwargs: (root, _theme()))
        monkeypatch.setattr(alarmclock._guitheme, "ui_font_family", lambda _root: "Segoe UI")
        monkeypatch.setattr(alarmclock._guitheme, "install_theme_switcher", lambda _root: None)

        path = tmp_path / "alarmclock.json"
        if settings is not None:
            save_settings(settings, path)
        return _Gui(window=AlarmClockWindow(path), root=root, errors=errors, path=path)

    return build


def _read_settings(path: Path) -> dict[str, Any]:
    """读取落盘的设置 JSON。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _options(widget: Any) -> dict[str, Any]:
    """读取替身控件的构造 / ``configure`` 选项。"""
    options = getattr(widget, "options", None)
    assert isinstance(options, dict)
    return options


class TestWindowInit:
    """构造窗口：恢复设置、默认时间与定时器。"""

    def test_restores_saved_alarm_and_repeat(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window(AlarmSettings(alarm="08:30:00", repeat=True, geometry="480x400+10+10"))
        assert gui.window._time_var.get() == "08:30:00"
        assert gui.window._repeat_var.get() is True
        assert gui.window._status_var.get() == "未设置闹钟"
        assert gui.root.after_calls[0][0] == 1000

    def test_defaults_to_one_minute_later(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        assert gui.window._time_var.get() == "10:01:00"
        assert gui.window._clock_var.get() == "10:00:00"
        assert gui.window._state.repeat is False


class TestAlarmActions:
    """设定 / 取消 / 延时 / 重置 / 重复开关。"""

    def test_arm_then_cancel(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._time_var.set("23:59:59")
        gui.window._repeat_var.set(True)
        gui.window.toggle_alarm()

        assert gui.window._state.armed
        assert gui.window._state.target == datetime(2026, 1, 1, 23, 59, 59)
        assert gui.window._state.repeat is True
        assert _options(gui.window._set_button)["text"] == "取消闹钟"
        assert _options(gui.window._set_button)["style"] == "Danger.TButton"
        assert gui.window._status_var.get() == "已设置闹钟: 23:59:59"

        gui.window.toggle_alarm()
        assert not gui.window._state.armed
        assert _options(gui.window._set_button)["text"] == "设置闹钟"
        assert _options(gui.window._set_button)["style"] == "Accent.TButton"
        assert gui.window._status_var.get() == "闹钟已取消"

    def test_invalid_time_shows_error(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._time_var.set("bad")
        gui.window.toggle_alarm()
        assert not gui.window._state.armed
        assert gui.errors == [("时间无效", "时间格式应为 HH:MM:SS")]

    @pytest.mark.parametrize(("minutes", "expected"), [(1, "10:01:00"), (30, "10:30:00"), (90, "11:30:00")])
    def test_set_delay(self, make_window: Callable[..., _Gui], minutes: int, expected: str) -> None:
        gui = make_window()
        gui.window.set_delay(minutes)
        assert gui.window._time_var.get() == expected

    def test_reset_time(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._time_var.set("23:00:00")
        gui.window.reset_time()
        assert gui.window._time_var.get() == "10:00:00"

    def test_sync_repeat(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._repeat_var.set(True)
        gui.window._sync_repeat()
        assert gui.window._state.repeat is True


class TestClockRefresh:
    """时钟与状态刷新。"""

    def test_rotates_border_color(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._refresh_clock(datetime(2026, 1, 1, 10, 0, 5))
        assert gui.window._clock_var.get() == "10:00:05"
        assert gui.window._border_color in DIGITAL_BORDER_COLORS
        assert _options(gui.window._clock_label)["highlightbackground"] == gui.window._border_color
        assert _options(gui.window._clock_label)["highlightcolor"] == gui.window._border_color

    def test_unarmed_keeps_status(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._refresh_status(_NOW)
        assert gui.window._status_var.get() == "未设置闹钟"

    def test_armed_shows_remaining(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.arm(time(10, 5, 0), _NOW)
        gui.window._refresh_status(datetime(2026, 1, 1, 10, 1, 0))
        assert gui.window._status_var.get() == "闹钟已设置: 10:05:00（剩余 00:04:00）"


class TestTick:
    """每秒回调：刷新时钟、到点弹窗、继续排程。"""

    def test_reschedules_without_alarm(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._on_tick()
        assert gui.window._reminder is None
        assert gui.root.after_calls[-1][0] == 1000

    def test_opens_single_reminder_when_due(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.target = datetime(2026, 1, 1, 9, 0, 0)
        gui.window._on_tick()

        assert gui.root.bells == 1
        reminder = gui.window._reminder
        assert isinstance(reminder, _FakeToplevel)
        assert reminder.options["title"] == REMINDER_TITLE
        assert reminder.options["grabbed"] is True
        assert reminder.options["geometry"] == "360x220+360+290"
        assert reminder.protocols["WM_DELETE_WINDOW"] == gui.window.close_reminder
        assert gui.window._status_var.get() == "⏰ 闹钟响了！"

        gui.window._on_tick()
        assert gui.root.bells == 1


class TestBlink:
    """提醒窗口闪烁。"""

    def test_without_reminder_is_noop(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._blink()  # 不应抛

    def test_rotates_background_and_reschedules(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.target = datetime(2026, 1, 1, 9, 0, 0)
        gui.window._on_tick()
        reminder = gui.window._reminder
        assert isinstance(reminder, _FakeToplevel)
        previous = reminder.options["bg"]

        gui.window._blink()

        assert reminder.options["bg"] != previous
        assert gui.window._blink_timer is not None
        assert len(reminder.after_calls) == 2
        assert gui.window._reminder_label is not None
        assert _options(gui.window._reminder_label)["bg"] == reminder.options["bg"]
        assert reminder.after_calls[-1][0] == alarmclock.BLINK_INTERVAL

    def test_tolerates_missing_label(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.target = datetime(2026, 1, 1, 9, 0, 0)
        gui.window._on_tick()
        reminder = gui.window._reminder
        assert isinstance(reminder, _FakeToplevel)
        previous = reminder.options["bg"]

        gui.window._reminder_label = None
        gui.window._blink()

        assert reminder.options["bg"] != previous


class TestCloseReminder:
    """关闭提醒窗口与重复顺延。"""

    def test_without_reminder_is_noop(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window.close_reminder()  # 不应抛
        assert gui.window._reminder is None

    def test_non_repeat_clears_alarm(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.target = datetime(2026, 1, 1, 9, 0, 0)
        gui.window._on_tick()
        reminder = gui.window._reminder
        assert isinstance(reminder, _FakeToplevel)
        timer = gui.window._blink_timer

        gui.window.close_reminder()

        assert gui.window._reminder is None
        assert gui.window._reminder_label is None
        assert reminder.cancelled == [timer]
        assert reminder.destroyed is True
        assert not gui.window._state.armed
        assert _options(gui.window._set_button)["text"] == "设置闹钟"
        assert gui.window._status_var.get() == "闹钟已关闭"

    def test_repeat_advances_to_tomorrow(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._repeat_var.set(True)
        gui.window._state.repeat = True
        gui.window._state.target = datetime(2026, 1, 1, 9, 0, 0)
        gui.window._on_tick()

        gui.window.close_reminder()

        assert gui.window._state.armed
        assert gui.window._state.target == datetime(2026, 1, 2, 9, 0, 0)
        assert gui.window._time_var.get() == "09:00:00"
        assert gui.window._status_var.get() == "下次提醒: 09:00:00"


class TestClose:
    """关闭窗口：清理定时器、保存设置、销毁根窗口。"""

    def test_cancels_timers_and_saves_alarm(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.arm(time(12, 0, 0), _NOW)
        tick_timer = gui.window._tick_timer

        gui.window.close()

        assert gui.root.cancelled == [tick_timer]
        assert gui.root.destroyed is True
        saved = _read_settings(gui.path)
        assert saved["alarm"] == "12:00:00"
        assert saved["repeat"] is False
        assert saved["geometry"] == "480x400+300+200"

    def test_closes_open_reminder(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._state.target = datetime(2026, 1, 1, 9, 0, 0)
        gui.window._on_tick()
        reminder = gui.window._reminder
        assert isinstance(reminder, _FakeToplevel)

        gui.window.close()

        assert reminder.destroyed is True
        assert gui.window._reminder is None
        assert _read_settings(gui.path)["alarm"] == "10:01:00"

    def test_saves_typed_time_when_not_armed(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._time_var.set("  06:30:00  ")
        gui.window.close()
        assert _read_settings(gui.path)["alarm"] == "06:30:00"

    def test_empty_time_saves_none(self, make_window: Callable[..., _Gui]) -> None:
        gui = make_window()
        gui.window._time_var.set("")
        gui.window.close()
        assert _read_settings(gui.path)["alarm"] is None
