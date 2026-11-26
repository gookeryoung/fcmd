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
from PySide6.QtGui import QCloseEvent, QFont, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd._compat import override
from fcmd.gui import style as _qt_theme
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.widgets import (
    Card,
    ContentHeightGuard,
    FlowLayout,
    ResponsiveLabel,
    button,
    fit_flow_widths,
    messages,
)
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

DEFAULT_SIZE = (620, 380)
_WINDOW_MIN_SIZE = (460, 380)
_SIDE_RESERVE = 140
_CLOCK_COLORS = DIGITAL_BORDER_COLORS
_BG_COLORS = BLINK_BG_COLORS


class _ReminderDialog(QDialog):  # pragma: no cover - 模态提醒窗口
    """到点提醒：置顶、主题化闪烁面板，关闭后由宿主决定是否顺延。"""

    def __init__(self, parent: QWidget, *, on_close: Any) -> None:
        super().__init__(parent)
        self.setObjectName("AlarmReminderDialog")
        self.setWindowTitle(REMINDER_TITLE)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._on_close = on_close
        palette = _qt_theme.active_theme().palette
        self._accents = (palette.danger, palette.warning)
        self._color = random.choice(self._accents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)
        self.hero = QFrame(self)
        self.hero.setObjectName("AlarmReminderHero")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(30, 24, 30, 24)
        hero_layout.setSpacing(8)
        self.label = QLabel(REMINDER_TEXT, self.hero)
        self.label.setObjectName("AlarmReminderTitle")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        hero_layout.addWidget(self.label)
        hint = QLabel("闹钟时间到，点击下方按钮结束本次提醒", self.hero)
        hint.setObjectName("AlarmReminderHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hero_layout.addWidget(hint)
        layout.addWidget(self.hero)
        self.close_button = button("关闭提醒", kind="primary")
        self.close_button.setProperty("alarmAction", "true")
        self.close_button.setMinimumWidth(150)
        self.close_button.clicked.connect(self._close)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setInterval(BLINK_INTERVAL)
        self._timer.timeout.connect(self._blink)
        self._apply_color()

    def start(self) -> None:
        """开始闪烁并显示窗口。"""
        self._timer.start()
        self.show()

    def _apply_color(self) -> None:
        """只闪烁主题强调色边框与标题，背景保持主界面卡片风格。"""
        self.hero.setProperty("tone", "warning" if self._color == self._accents[1] else "danger")
        self.hero.style().unpolish(self.hero)
        self.hero.style().polish(self.hero)
        self.label.setProperty("tone", "warning" if self._color == self._accents[1] else "danger")
        self.label.style().unpolish(self.label)
        self.label.style().polish(self.label)

    def _blink(self) -> None:
        choices = [color for color in self._accents if color != self._color] or list(self._accents)
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
        self._height_guard = ContentHeightGuard(self, self.scroll, floor=_WINDOW_MIN_SIZE[1])
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
        hero.setContentsMargins(20, 10, 20, 10)
        hero.setSpacing(0)

        self.date_label = QLabel("", self.clock_panel)
        self.date_label.setObjectName("AlarmClockDate")
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(self.date_label)

        self.clock_label = ResponsiveLabel(
            datetime.now().strftime(DIGITAL_FORMAT),
            owner=self,
            parent=self.clock_panel,
            min_point=24,
            max_point=50,
        )
        self.clock_label.setObjectName("AlarmClockTime")
        clock_font = QFont()
        clock_font.setFamilies(("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"))
        clock_font.setBold(True)
        self.clock_label.setFont(clock_font)
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock_label.setMinimumWidth(0)
        self.clock_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.clock_label.setMinimumHeight(88)
        hero.addWidget(self.clock_label)

        self.status = QLabel("未设置闹钟", self.clock_panel)
        self.status.setObjectName("AlarmClockStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hero.addWidget(self.status)
        self._show_status("未设置闹钟", "muted")
        self._top_width = 0
        self.add(self.clock_panel)

        settings_card = Card("闹钟设置")
        settings_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        settings_card.layout().setContentsMargins(12, 8, 12, 6)
        settings_card.body.setSpacing(4)
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 3)
        form.setColumnStretch(2, 4)
        self._form = form
        time_label = QLabel("闹钟时间", settings_card)
        time_label.setObjectName("AlarmFormLabel")
        form.addWidget(time_label, 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.time_edit = QLineEdit(self._initial_alarm_text(), settings_card)
        self.time_edit.setObjectName("AlarmTimeInput")
        self.time_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_edit.setMinimumWidth(150)
        self.time_edit.setMaximumWidth(190)
        self.time_edit.returnPressed.connect(self.toggle_alarm)
        self._top_controls = QWidget(settings_card)
        self._top_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_row = QHBoxLayout(self._top_controls)
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)
        top_row.addWidget(self.time_edit)
        self.set_button = button("设置闹钟", kind="primary")
        self.set_button.setProperty("alarmAction", "true")
        self.set_button.setMinimumWidth(116)
        self.set_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_button.clicked.connect(self.toggle_alarm)
        top_row.addWidget(self.set_button, 1)
        form.addWidget(self._top_controls, 0, 1, 1, 2)

        delay_label = QLabel("延时(分钟)", settings_card)
        delay_label.setObjectName("AlarmFormLabel")
        delay_label.setFixedHeight(32)
        delay_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.delay_label = delay_label
        form.addWidget(delay_label, 1, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.delay_host = QWidget(settings_card)
        delay_host = self.delay_host
        delay_flow = FlowLayout(delay_host, h_spacing=4, v_spacing=4)
        self._delay_buttons: list[QPushButton] = []
        for minutes in DELAY_STEPS:
            widget = button(str(minutes))
            widget.setProperty("alarmDelay", "true")
            widget.setFixedWidth(56)
            widget.clicked.connect(lambda _checked=False, value=minutes: self.set_delay(value))
            delay_flow.addWidget(widget)
            self._delay_buttons.append(widget)
        reset = button("重置", icon="")
        reset.setProperty("alarmDelay", "true")
        reset.setFixedWidth(56)
        reset.clicked.connect(self.reset_time)
        self.reset_button = reset
        delay_flow.addWidget(reset)
        self._delay_buttons.append(reset)
        form.addWidget(delay_host, 1, 1, 1, 2)

        self.repeat = QCheckBox("重复（每天）", settings_card)
        self.repeat.setObjectName("AlarmRepeat")
        self.repeat.setChecked(self._settings.repeat)
        self.repeat.toggled.connect(self._sync_repeat)
        form.addWidget(self.repeat, 2, 1, 1, 2)
        settings_card.body.addLayout(form)
        self.add(settings_card)
        QTimer.singleShot(0, self._sync_delay_widths)

        self.add_stretch()

    def _show_status(self, text: str, tone: str) -> None:
        """更新提醒状态文字与语义色（muted / accent / success / danger）。"""
        self.status.setText(text)
        self.status.setProperty("tone", tone)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _sync_delay_widths(self) -> None:
        """让延时按钮按可用宽度均分，顶部输入区跟随其实际宽度右缘对齐。"""
        if not hasattr(self, "delay_host") or getattr(self, "_syncing_delay", False):
            return
        # 用窗口宽度算可用区，避免宽窗缩窄时读到被上一轮固定宽度撑住的旧 host 宽度。
        available = max(0, self.width() - _SIDE_RESERVE)
        if available <= 0:
            return
        self._syncing_delay = True
        try:
            group_width = fit_flow_widths(
                self.delay_host,
                self._delay_buttons,
                available,
                spacing=4,
                min_width=42,
                max_width=70,
            )
            top_width = min(available, group_width)
            if getattr(self, "_top_width", 0) != top_width:
                self._top_width = top_width
                self._top_controls.setFixedWidth(top_width)
                self._top_controls.updateGeometry()
                self._form.invalidate()
                self._form.activate()
                self.delay_host.updateGeometry()
        finally:
            self._syncing_delay = False

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_delay_widths()

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
            messages.error(self, "时间无效", str(exc))
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
        dialog.setMinimumWidth(440)
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
