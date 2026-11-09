"""alarmclock 工具测试。

验证 ``fcmd.tools.system.alarmclock`` 模块：
- 工具注册
- 时间解析 / 下次触发时刻 / 剩余时长格式化
- ``AlarmState`` 设定、取消、到点判定与重复顺延
- 设置文件读写与异常回退
- GUI 启动入口的降级行为
"""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.system import alarmclock
from fcmd.tools.system.alarmclock import (
    DIGITAL_BORDER_COLORS,
    AlarmSettings,
    AlarmState,
    config_path,
    format_duration,
    load_settings,
    next_border_color,
    next_occurrence,
    parse_clock,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """alarmclock 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["alarmclock"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "alarmclock.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "alarmclock.json"

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """保存后能原样读回（父目录自动创建）。"""
        path = tmp_path / "nested" / "alarmclock.json"
        expected = AlarmSettings(alarm="08:30:00", repeat=True, geometry="500x400+10+20")
        save_settings(expected, path)
        assert path.is_file()
        assert load_settings(path) == expected

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        """文件不存在时返回默认设置。"""
        assert load_settings(tmp_path / "missing.json") == AlarmSettings()

    def test_load_invalid_json_returns_defaults(self, tmp_path: Path) -> None:
        """JSON 损坏时返回默认设置。"""
        path = tmp_path / "bad.json"
        path.write_text("{oops", encoding="utf-8")
        assert load_settings(path) == AlarmSettings()

    def test_load_non_object_returns_defaults(self, tmp_path: Path) -> None:
        """JSON 顶层不是对象时返回默认设置。"""
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == AlarmSettings()

    def test_load_wrong_types_are_filtered(self, tmp_path: Path) -> None:
        """字段类型不符时逐项回退默认值。"""
        path = tmp_path / "wrong.json"
        path.write_text('{"alarm": 123, "repeat": "yes", "geometry": 5}', encoding="utf-8")
        assert load_settings(path) == AlarmSettings()

    def test_load_invalid_alarm_is_dropped(self, tmp_path: Path) -> None:
        """保存的闹钟时间非法时丢弃该字段。"""
        path = tmp_path / "invalid.json"
        path.write_text('{"alarm": "99:99:99", "repeat": true, "geometry": "480x400"}', encoding="utf-8")
        assert load_settings(path) == AlarmSettings(alarm=None, repeat=True, geometry="480x400")

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(AlarmSettings(), tmp_path / "x.json")  # 不应抛出


# ============================================================================ #
# 纯逻辑
# ============================================================================ #
class TestParseClock:
    """时间解析。"""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("08:05:09", time(8, 5, 9)),
            (" 08:05 ", time(8, 5, 0)),
            ("00:00:00", time(0, 0, 0)),
            ("23:59:59", time(23, 59, 59)),
        ],
    )
    def test_valid(self, text: str, expected: time) -> None:
        assert parse_clock(text) == expected

    @pytest.mark.parametrize("text", ["", "8", "aa:bb:cc", "24:00:00", "12:60:00", "12:00:60", "12:00:00:00"])
    def test_invalid(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_clock(text)


class TestScheduling:
    """触发时刻与时长格式化。"""

    NOW = datetime(2026, 1, 1, 10, 0, 0)

    def test_next_occurrence_today(self) -> None:
        assert next_occurrence(time(12, 0, 0), self.NOW) == datetime(2026, 1, 1, 12, 0, 0)

    def test_next_occurrence_tomorrow(self) -> None:
        assert next_occurrence(time(9, 0, 0), self.NOW) == datetime(2026, 1, 2, 9, 0, 0)

    def test_next_occurrence_same_second_is_immediate(self) -> None:
        assert next_occurrence(time(10, 0, 0), self.NOW) == self.NOW

    @pytest.mark.parametrize(
        ("delta", "expected"),
        [
            (timedelta(0), "00:00:00"),
            (timedelta(seconds=59), "00:00:59"),
            (timedelta(hours=1, minutes=1, seconds=1), "01:01:01"),
            (timedelta(seconds=-30), "00:00:00"),
        ],
    )
    def test_format_duration(self, delta: timedelta, expected: str) -> None:
        assert format_duration(delta) == expected

    def test_next_border_color_avoids_current(self) -> None:
        color = next_border_color("#00aa00", random.Random(0))
        assert color in DIGITAL_BORDER_COLORS
        assert color != "#00aa00"

    def test_next_border_color_unknown_current(self) -> None:
        assert next_border_color("#123456", random.Random(0)) in DIGITAL_BORDER_COLORS

    def test_next_border_color_single_choice(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(alarmclock, "DIGITAL_BORDER_COLORS", ("#111111",))
        assert next_border_color("#111111", random.Random(0)) == "#111111"


class TestAlarmState:
    """闹钟状态机。"""

    NOW = datetime(2026, 1, 1, 10, 0, 0)

    def test_initial_state(self) -> None:
        state = AlarmState()
        assert not state.armed
        assert state.remaining(self.NOW) is None
        assert not state.is_due(self.NOW)

    def test_arm_and_cancel(self) -> None:
        state = AlarmState()
        target = state.arm(time(10, 5, 0), self.NOW)
        assert target == datetime(2026, 1, 1, 10, 5, 0)
        assert state.armed
        assert state.remaining(self.NOW) == timedelta(minutes=5)
        assert not state.is_due(self.NOW)

        state.cancel()
        assert not state.armed
        assert not state.is_due(datetime(2026, 1, 2, 0, 0, 0))

    def test_is_due(self) -> None:
        state = AlarmState(target=self.NOW)
        assert state.is_due(self.NOW)
        assert state.remaining(self.NOW) == timedelta(0)

    def test_dismiss_without_alarm(self) -> None:
        state = AlarmState()
        state.dismiss(self.NOW)  # 不应抛出
        assert not state.armed

    def test_dismiss_once(self) -> None:
        state = AlarmState(target=self.NOW)
        state.dismiss(self.NOW)
        assert not state.armed

    def test_dismiss_repeat_advances_to_tomorrow(self) -> None:
        state = AlarmState(target=datetime(2026, 1, 1, 10, 0, 0), repeat=True)
        state.dismiss(datetime(2026, 1, 1, 10, 0, 5))
        assert state.target == datetime(2026, 1, 2, 10, 0, 0)

    def test_dismiss_repeat_skips_missed_days(self) -> None:
        state = AlarmState(target=datetime(2026, 1, 1, 10, 0, 0), repeat=True)
        state.dismiss(datetime(2026, 1, 5, 9, 0, 0))
        assert state.target == datetime(2026, 1, 5, 10, 0, 0)


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["480x400", "480x400+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "480", "480x400abc", "480 x 400"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(alarmclock, "AlarmClockWindow", fake_window)
        alarmclock.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(alarmclock, "AlarmClockWindow", lambda _path: _Window())
        alarmclock.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(alarmclock, "run_gui", lambda: calls.append(True))
        alarmclock.launch_cmd()
        assert calls == [True]
