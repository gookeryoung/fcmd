"""alarmclock 的 Qt 主窗口与入口。

数字时钟每秒刷新并轮换边框颜色；按 ``HH:MM:SS`` 设定闹钟或用「延时」按钮快速
设定 N 分钟后的提醒；到点弹出置顶提醒窗口并轮流闪烁背景色，勾选「重复」则每天
同一时间再次提醒。

纯逻辑（时间解析、下一次触发、状态机、颜色轮换）继续复用
:mod:`fcmd.tools.system.alarmclock`；本模块只负责 Qt 控件、定时器与提醒窗口。
设置写回 ``${FCMD_HOME:-~/.fcmd}/ui/alarmclock.json``。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from fcmd._compat import override
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.widgets import Card, button, hint_label
from fcmd.tools.system.alarmclock import (
    BLINK_BG_COLORS,
    BLINK_INTERVAL,
    DELAY_STEPS,
    DIGITAL_BORDER_COLORS,
    DIGITAL_FORMAT,
    REMINDER_TEXT,
    REMINDER_TITLE,
    AlarmSettings,
    AlarmState,
    format_duration,
    load_settings,
    next_border_color,
    parse_clock,
    save_settings,
)

if TYPE_CHECKING:
    from typing import Any

__all__ = ["AlarmClockQtWindow", "run_qt"]

DEFAULT_SIZE = (680, 700)
_WINDOW_MIN_SIZE = (620, 600)
_CLOCK_COLORS = DIGITAL_BORDER_COLORS
_BG_COLORS = BLINK_BG_COLORS


class _ReminderDialog(QDialog):  # pragma: no cover - 模态提醒窗口
    """到点提醒：置顶、闪烁背景，关闭后由宿主决定是否顺延。"""

    def __init__(self, parent: QWidget, *, on_close: Any) -> None:
        super().__init__(parent)
        self.setWindowTitle(REMINDER_TITLE)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._color = random.choice(list(_BG_COLORS))
        self._on_close = on_close
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 24)
        layout.setSpacing(16)
        self.label = QLabel(REMINDER_TEXT, self)
        font = QFont(self.font())
        font.setPointSize(24)
        font.setBold(True)
        self.label.setFont(font)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)
        close_button = button("关闭提醒")
        close_button.clicked.connect(self._close)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setInterval(BLINK_INTERVAL)
        self._timer.timeout.connect(self._blink)
        self._apply_color()

    def start(self) -> None:
        """开始闪烁并显示窗口。"""
        self._timer.start()
        self.show()

    def _apply_color(self) -> None:
        self.setStyleSheet(f"background-color: {self._color};")
        self.label.setStyleSheet(f"color: red; background-color: {self._color};")

    def _blink(self) -> None:
        choices = [color for color in _BG_COLORS if color != self._color] or list(_BG_COLORS)
        self._color = random.choice(choices)
        self._apply_color()

    def _close(self) -> None:
        self._timer.stop()
        self._on_close()
        self.close()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        self._on_close()
        event.accept()


class AlarmClockQtWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """定时提醒主窗口：数字时钟 + 时间设置 + 提醒弹窗。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._state = AlarmState(repeat=self._settings.repeat)
        self._border_color = _CLOCK_COLORS[0]
        self._reminder: _ReminderDialog | None = None
        super().__init__(
            title="定时提醒",
            config_name="alarmclock",
            default_size=DEFAULT_SIZE,
            min_size=_WINDOW_MIN_SIZE,
            on_close=self._close_allowed,
            persist=self._persist_settings,
        )
        self._build_widgets()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()
        self.refresh_clock(datetime.now())

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        self.clock_panel = QFrame(self)
        self.clock_panel.setObjectName("AlarmClockHero")
        hero = QVBoxLayout(self.clock_panel)
        hero.setContentsMargins(24, 18, 24, 18)
        hero.setSpacing(4)

        self.date_label = QLabel("", self.clock_panel)
        self.date_label.setObjectName("AlarmClockDate")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(self.date_label)

        self.clock_label = QLabel(datetime.now().strftime(DIGITAL_FORMAT), self.clock_panel)
        self.clock_label.setObjectName("AlarmClockTime")
        clock_font = QFont()
        clock_font.setFamilies(("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"))
        clock_font.setPointSize(72)
        clock_font.setBold(True)
        self.clock_label.setFont(clock_font)
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock_label.setMinimumHeight(118)
        hero.addWidget(self.clock_label)

        self.status = QLabel("未设置闹钟", self.clock_panel)
        self.status.setObjectName("AlarmClockStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hero.addWidget(self.status)
        self._show_status("未设置闹钟", "muted")
        self.add(self.clock_panel)

        settings_card = Card("闹钟设置")
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.setSpacing(10)
        time_label = QLabel("闹钟时间", settings_card)
        time_label.setObjectName("AlarmFormLabel")
        time_row.addWidget(time_label)
        self.time_edit = QLineEdit(self._initial_alarm_text(), settings_card)
        self.time_edit.setObjectName("AlarmTimeInput")
        self.time_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_edit.setMinimumWidth(160)
        self.time_edit.setMaximumWidth(190)
        self.time_edit.returnPressed.connect(self.toggle_alarm)
        time_row.addWidget(self.time_edit)
        time_row.addStretch(1)
        self.set_button = button("设置闹钟", kind="primary")
        self.set_button.setProperty("alarmAction", "true")
        self.set_button.setMinimumWidth(120)
        self.set_button.clicked.connect(self.toggle_alarm)
        time_row.addWidget(self.set_button)
        settings_card.body.addLayout(time_row)

        delay_row = QHBoxLayout()
        delay_row.setContentsMargins(0, 0, 0, 0)
        delay_row.setSpacing(6)
        delay_label = QLabel("延时(分钟)", settings_card)
        delay_label.setObjectName("AlarmFormLabel")
        delay_row.addWidget(delay_label)
        for minutes in DELAY_STEPS:
            widget = button(str(minutes))
            widget.setProperty("alarmDelay", "true")
            widget.clicked.connect(lambda _checked=False, value=minutes: self.set_delay(value))
            delay_row.addWidget(widget)
        reset = button("重置")
        reset.setProperty("alarmDelay", "true")
        reset.clicked.connect(self.reset_time)
        delay_row.addWidget(reset)
        delay_row.addStretch(1)
        settings_card.body.addLayout(delay_row)

        self.repeat = QCheckBox("重复（每天）", settings_card)
        self.repeat.setObjectName("AlarmRepeat")
        self.repeat.setChecked(self._settings.repeat)
        self.repeat.toggled.connect(self._sync_repeat)
        settings_card.body.addWidget(self.repeat)
        self.add(settings_card)

        self.add(hint_label("到点后弹窗置顶闪烁，点击「关闭提醒」结束本次提醒。"))
        self.add_stretch()

    def _show_status(self, text: str, tone: str) -> None:
        """更新提醒状态文字与语义色（muted / accent / success / danger）。"""
        self.status.setText(text)
        self.status.setProperty("tone", tone)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _initial_alarm_text(self) -> str:
        """初始时间输入框内容：恢复上次设定，否则为 1 分钟后。"""
        if self._settings.alarm:
            return self._settings.alarm
        return (datetime.now() + timedelta(minutes=DELAY_STEPS[0])).strftime(DIGITAL_FORMAT)

    # ------------------------------------------------------------------ #
    # 时钟与状态刷新
    # ------------------------------------------------------------------ #
    def _on_tick(self) -> None:
        now = datetime.now()
        self.refresh_clock(now)
        if self._state.is_due(now) and self._reminder is None:
            self.open_reminder()

    def refresh_clock(self, now: datetime) -> None:
        """刷新时钟文本、日期、强调边框与状态栏剩余时间。"""
        weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        self.clock_label.setText(now.strftime(DIGITAL_FORMAT))
        self.date_label.setText(f"{now:%Y-%m-%d}  {weekdays[now.weekday()]}")
        self._border_color = next_border_color(self._border_color)
        self.clock_panel.setStyleSheet(f"QFrame#AlarmClockHero {{ border: 3px solid {self._border_color}; }}")
        self.refresh_status(now)

    def refresh_status(self, now: datetime) -> None:
        """有闹钟时显示目标时间与剩余时长（与既有文案一致）。"""
        if not self._state.armed:
            return
        assert self._state.target is not None
        remaining = self._state.remaining(now) or timedelta(0)
        self.status.setText(
            f"闹钟已设置: {self._state.target.strftime(DIGITAL_FORMAT)}（剩余 {format_duration(remaining)}）"
        )

    # ------------------------------------------------------------------ #
    # 闹钟操作
    # ------------------------------------------------------------------ #
    def toggle_alarm(self) -> None:
        """设置 / 取消闹钟。"""
        if self._state.armed:
            self.cancel_alarm()
        else:
            self.arm_alarm()

    def arm_alarm(self) -> None:
        """按输入框时间设定闹钟；非法时间提示后保持不变。"""
        try:
            clock = parse_clock(self.time_edit.text())
        except ValueError as exc:
            QMessageBox.critical(self, "时间无效", str(exc))
            return
        self._state.repeat = self.repeat.isChecked()
        target = self._state.arm(clock, datetime.now())
        self.time_edit.setText(target.strftime(DIGITAL_FORMAT))
        self.set_button.setText("取消闹钟")
        self.set_button.setObjectName("Danger")
        self._show_status(f"已设置闹钟: {target.strftime(DIGITAL_FORMAT)}", "accent")

    def cancel_alarm(self) -> None:
        """取消闹钟并恢复按钮文案。"""
        self._state.cancel()
        self.set_button.setText("设置闹钟")
        self.set_button.setObjectName("Primary")
        self._show_status("闹钟已取消", "muted")

    def set_delay(self, minutes: int) -> None:
        """把闹钟时间设为当前时间 + ``minutes`` 分钟。"""
        self.time_edit.setText((datetime.now() + timedelta(minutes=minutes)).strftime(DIGITAL_FORMAT))

    def reset_time(self) -> None:
        """把闹钟时间重置为当前时刻。"""
        self.time_edit.setText(datetime.now().strftime(DIGITAL_FORMAT))

    def _sync_repeat(self, checked: bool) -> None:
        self._state.repeat = bool(checked)

    # ------------------------------------------------------------------ #
    # 提醒窗口
    # ------------------------------------------------------------------ #
    def open_reminder(self) -> None:
        """弹出置顶提醒窗口并开始闪烁。"""
        QApplication.beep()
        dialog = _ReminderDialog(self, on_close=self.close_reminder)
        dialog.adjustSize()
        dialog.setMinimumWidth(360)
        dialog.start()
        self._reminder = dialog
        self._show_status("⏰ 闹钟响了！", "danger")

    def close_reminder(self) -> None:
        """关闭提醒窗口，并按「重复」设置决定是否顺延。"""
        self._reminder = None
        self._state.repeat = self.repeat.isChecked()
        self._state.dismiss(datetime.now())
        if self._state.armed:
            assert self._state.target is not None
            self.time_edit.setText(self._state.target.strftime(DIGITAL_FORMAT))
            self._show_status(f"下次提醒: {self._state.target.strftime(DIGITAL_FORMAT)}", "success")
        else:
            self.set_button.setText("设置闹钟")
            self.set_button.setObjectName("Primary")
            self._show_status("闹钟已关闭", "muted")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _persist_settings(self, geometry: str) -> None:
        alarm = (
            self._state.target.strftime(DIGITAL_FORMAT)
            if self._state.target is not None
            else (self.time_edit.text().strip() or None)
        )
        save_settings(
            AlarmSettings(alarm=alarm, repeat=self.repeat.isChecked(), geometry=geometry),
            self._own_settings_path,
        )

    def _close_allowed(self) -> bool:
        if self._reminder is not None:
            self._reminder.close()
        return True

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._tick_timer.isActive():
            self._tick_timer.stop()
        super().closeEvent(event)


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.app import run_window

    run_window(lambda: AlarmClockQtWindow(settings_path), app_name="定时提醒")
