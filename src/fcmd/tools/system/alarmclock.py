"""alarmclock - Tk 定时提醒（数字时钟 + 闹钟 + 到点闪烁提醒）。

交互参考 ``bitool_gui.alarmclock``：大号数字时钟每秒刷新并轮换边框颜色，
可按 ``HH:MM:SS`` 设定闹钟，或用「延时」按钮快速设定 N 分钟后的提醒；
到点弹出置顶提醒窗口并轮流闪烁背景色，勾选「重复」则每天同一时间再次提醒。

设置（上次闹钟时间 / 重复开关 / 窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/alarmclock.json``，下次启动自动恢复。

示例
----
    fcmd alarmclock        # 打开定时提醒窗口
    alarmclock             # 全局命令等价入口
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "AlarmSettings",
    "AlarmState",
    "config_path",
    "format_duration",
    "load_settings",
    "next_border_color",
    "next_occurrence",
    "parse_clock",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = "数字时钟 + 定时提醒（Tk）：按 HH:MM:SS 设定闹钟或按分钟快捷延时，到点弹出置顶闪烁提醒窗口，支持每天重复"

DIGITAL_FORMAT = "%H:%M:%S"
CLOCK_FONT = ("Consolas", 44, "bold italic")
DIGITAL_BORDER_COLORS = ("#00aa00", "#eecc00", "#aa00aa", "#c0e0b0")
BLINK_BG_COLORS = ("#baf1ba", "#f8ccc3", "#aab4f0", "#efaec0")
BLINK_INTERVAL = 300
DELAY_STEPS = (1, 5, 10, 15, 30, 60)
REMINDER_TITLE = "闹钟提醒！"
REMINDER_TEXT = "⏰ 时间到！"
DEFAULT_GEOMETRY = "480x400+300+200"
MIN_WIDTH = 420
MIN_HEIGHT = 360


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/alarmclock.json``。"""
    return _guiconfig.config_path("alarmclock")


@dataclass
class AlarmSettings:
    """定时提醒的持久化设置。"""

    alarm: str | None = None
    repeat: bool = False
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> AlarmSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return AlarmSettings()

    alarm = raw.get("alarm")
    repeat = raw.get("repeat")
    geometry = raw.get("geometry")
    settings = AlarmSettings(
        alarm=alarm if isinstance(alarm, str) else None,
        repeat=repeat if isinstance(repeat, bool) else False,
        geometry=geometry if isinstance(geometry, str) else None,
    )
    if settings.alarm is not None:
        try:
            parse_clock(settings.alarm)
        except ValueError:
            settings.alarm = None
    return settings


def save_settings(settings: AlarmSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {"alarm": settings.alarm, "repeat": settings.repeat, "geometry": settings.geometry}
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 纯逻辑（可单测，不依赖 Tk）
# ---------------------------------------------------------------------- #
def parse_clock(text: str) -> time:
    """解析 ``HH:MM:SS``（也接受 ``HH:MM``）为 :class:`datetime.time`。

    Raises
    ------
    ValueError
        格式不是冒号分隔的数字，或时 / 分 / 秒超出范围。
    """
    parts = text.strip().split(":")
    if len(parts) == 2:  # HH:MM 省略秒
        parts.append("0")
    if len(parts) != 3 or not all(part.strip().isdigit() for part in parts):
        raise ValueError("时间格式应为 HH:MM:SS")
    hour, minute, second = (int(part) for part in parts)
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError("时间超出范围：HH 0-23，MM/SS 0-59")
    return time(hour, minute, second)


def next_occurrence(target: time, now: datetime) -> datetime:
    """返回 ``target`` 的下一次触发时刻（已过则顺延到明天）。"""
    now_second = now.replace(microsecond=0)
    candidate = now.replace(hour=target.hour, minute=target.minute, second=target.second, microsecond=0)
    if candidate < now_second:
        return candidate + timedelta(days=1)
    return candidate


def format_duration(delta: timedelta) -> str:
    """把剩余时长格式化为 ``HH:MM:SS``（负数按 0 处理）。"""
    total = max(0, int(delta.total_seconds()))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def next_border_color(current: str, rng: random.Random | None = None) -> str:
    """从 ``DIGITAL_BORDER_COLORS`` 中随机挑一个与 ``current`` 不同的颜色。"""
    colors = [color for color in DIGITAL_BORDER_COLORS if color != current] or list(DIGITAL_BORDER_COLORS)
    return (rng or random).choice(colors)


@dataclass
class AlarmState:
    """闹钟状态机：设定 / 取消 / 到点判定 / 关闭后顺延（重复）。"""

    target: datetime | None = None
    repeat: bool = False

    @property
    def armed(self) -> bool:
        """是否已设定闹钟。"""
        return self.target is not None

    def arm(self, clock: time, now: datetime) -> datetime:
        """按 ``clock`` 设定下一次触发时刻并返回。"""
        self.target = next_occurrence(clock, now)
        return self.target

    def cancel(self) -> None:
        """取消闹钟。"""
        self.target = None

    def is_due(self, now: datetime) -> bool:
        """当前是否已到触发时刻。"""
        return self.target is not None and now >= self.target

    def remaining(self, now: datetime) -> timedelta | None:
        """剩余时长；未设定闹钟时返回 ``None``，已过点返回 0。"""
        if self.target is None:
            return None
        return max(self.target - now, timedelta(0))

    def dismiss(self, now: datetime) -> None:
        """关闭提醒：不重复则解除，重复则顺延到下一次未来时刻。"""
        if self.target is None:
            return
        if not self.repeat:
            self.target = None
            return
        target = self.target + timedelta(days=1)
        while target <= now:
            target += timedelta(days=1)
        self.target = target


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class AlarmClockWindow:
    """Tk 定时提醒主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._state = AlarmState(repeat=self._settings.repeat)
        self._border_color = DIGITAL_BORDER_COLORS[0]
        self._reminder: Any | None = None
        self._reminder_label: Any | None = None
        self._blink_timer: str | None = None
        self._tick_timer: str | None = None

        self.root, self._theme = _guiwindow.create_root(
            title=f"定时提醒 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._clock_var = tk.StringVar(value=datetime.now().strftime(DIGITAL_FORMAT))
        self._time_var = tk.StringVar(value=self._initial_alarm_text())
        self._repeat_var = tk.BooleanVar(value=self._settings.repeat)
        self._status_var = tk.StringVar(value="未设置闹钟")
        # 主题正文字体族名：局部自定义字号/字重时复用，避免写死 Windows 字体导致乱码。
        self._ui_family = _guitheme.ui_font_family(self.root)

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_clock(datetime.now())
        self._schedule_tick()

    def _initial_alarm_text(self) -> str:
        """初始时间输入框内容：恢复上次设定，否则为 1 分钟后。"""
        if self._settings.alarm:
            return self._settings.alarm
        return (datetime.now() + timedelta(minutes=DELAY_STEPS[0])).strftime(DIGITAL_FORMAT)

    def _build_widgets(self) -> None:
        tk = self._tk
        ttk = self._ttk
        palette = self._theme.palette
        # 大号时钟保留经典 Label：需要每秒直改 highlight 边框颜色，ttk 样式不支持逐控件边框动画
        self._clock_label = tk.Label(
            self.root,
            textvariable=self._clock_var,
            font=CLOCK_FONT,
            fg=palette.text,
            bg=palette.log_bg,
            padx=self._theme.spacing.md,
            pady=self._theme.spacing.md,
            highlightthickness=2,
            highlightbackground=self._border_color,
            highlightcolor=self._border_color,
        )
        self._clock_label.pack(
            fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.md, self._theme.spacing.sm)
        )

        box = ttk.LabelFrame(self.root, text="时间设置", padding=self._theme.spacing.sm)
        box.pack(fill="x", padx=self._theme.spacing.md, pady=self._theme.spacing.xs)

        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Label(row, text="闹钟时间：").pack(side="left")
        ttk.Entry(row, textvariable=self._time_var, width=12, justify="center").pack(
            side="left", padx=(0, self._theme.spacing.sm)
        )
        self._set_button = ttk.Button(row, text="设置闹钟", command=self.toggle_alarm, style="Accent.TButton")
        self._set_button.pack(side="right")

        delay = ttk.Frame(box)
        delay.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        ttk.Label(delay, text="延时(分钟)：").pack(side="left")
        for minutes in DELAY_STEPS:
            button = ttk.Button(delay, text=str(minutes), width=3, command=lambda value=minutes: self.set_delay(value))
            button.pack(side="left", padx=(0, self._theme.spacing.xs))
        ttk.Button(delay, text="重置", command=self.reset_time).pack(side="left")

        options = ttk.Frame(box)
        options.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        ttk.Checkbutton(options, text="重复（每天）", variable=self._repeat_var, command=self._sync_repeat).pack(
            side="left"
        )

        self._status_label = ttk.Label(
            self.root,
            textvariable=self._status_var,
            foreground=palette.danger,
            font=(self._ui_family, 10, "bold italic underline"),
        )
        self._status_label.pack(
            fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.sm, self._theme.spacing.xxs)
        )
        ttk.Label(
            self.root,
            text="到点后弹窗置顶闪烁，点击「关闭提醒」结束本次提醒。",
            foreground=palette.muted,
        ).pack(fill="x", padx=self._theme.spacing.md, pady=(0, self._theme.spacing.md))

    # ------------------------------------------------------------------ #
    # 时钟与状态刷新
    # ------------------------------------------------------------------ #
    def _schedule_tick(self) -> None:
        self._tick_timer = self.root.after(1000, self._on_tick)

    def _on_tick(self) -> None:
        now = datetime.now()
        self._refresh_clock(now)
        if self._state.is_due(now) and self._reminder is None:
            self._open_reminder()
        self._schedule_tick()

    def _refresh_clock(self, now: datetime) -> None:
        self._clock_var.set(now.strftime(DIGITAL_FORMAT))
        self._border_color = next_border_color(self._border_color)
        self._clock_label.configure(highlightbackground=self._border_color, highlightcolor=self._border_color)
        self._refresh_status(now)

    def _refresh_status(self, now: datetime) -> None:
        if not self._state.armed:
            return
        assert self._state.target is not None
        remaining = self._state.remaining(now) or timedelta(0)
        self._status_var.set(
            f"闹钟已设置: {self._state.target.strftime(DIGITAL_FORMAT)}（剩余 {format_duration(remaining)}）"
        )

    # ------------------------------------------------------------------ #
    # 闹钟操作
    # ------------------------------------------------------------------ #
    def toggle_alarm(self) -> None:
        """设置 / 取消闹钟。"""
        if self._state.armed:
            self._cancel_alarm()
        else:
            self._arm_alarm()

    def _arm_alarm(self) -> None:
        try:
            clock = parse_clock(self._time_var.get())
        except ValueError as exc:
            self._messagebox.showerror("时间无效", str(exc))
            return
        self._state.repeat = bool(self._repeat_var.get())
        target = self._state.arm(clock, datetime.now())
        self._time_var.set(target.strftime(DIGITAL_FORMAT))
        self._set_button.configure(text="取消闹钟", style="Danger.TButton")
        self._status_var.set(f"已设置闹钟: {target.strftime(DIGITAL_FORMAT)}")

    def _cancel_alarm(self) -> None:
        self._state.cancel()
        self._set_button.configure(text="设置闹钟", style="Accent.TButton")
        self._status_var.set("闹钟已取消")

    def set_delay(self, minutes: int) -> None:
        """把闹钟时间设为当前时间 + ``minutes`` 分钟。"""
        self._time_var.set((datetime.now() + timedelta(minutes=minutes)).strftime(DIGITAL_FORMAT))

    def reset_time(self) -> None:
        """把闹钟时间重置为当前时刻。"""
        self._time_var.set(datetime.now().strftime(DIGITAL_FORMAT))

    def _sync_repeat(self) -> None:
        self._state.repeat = bool(self._repeat_var.get())

    # ------------------------------------------------------------------ #
    # 提醒窗口
    # ------------------------------------------------------------------ #
    def _open_reminder(self) -> None:
        tk = self._tk
        ttk = self._ttk
        self.root.bell()
        window = tk.Toplevel(self.root)
        window.title(REMINDER_TITLE)
        window.attributes("-topmost", True)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self.close_reminder)
        self._blink_color = random.choice(list(BLINK_BG_COLORS))
        window.configure(bg=self._blink_color)

        label = tk.Label(
            window,
            text=REMINDER_TEXT,
            font=(self._ui_family, 24, "bold"),
            fg="red",
            bg=self._blink_color,
        )
        label.pack(
            padx=self._theme.spacing.xl + self._theme.spacing.lg,
            pady=(self._theme.spacing.xl + self._theme.spacing.xs + self._theme.spacing.xxs, self._theme.spacing.md),
        )
        ttk.Button(window, text="关闭提醒", command=self.close_reminder).pack(
            pady=(0, self._theme.spacing.lg + self._theme.spacing.xs)
        )
        window.update_idletasks()
        _guiwindow.center_over_parent(window, self.root)
        window.grab_set()
        window.focus_force()

        self._reminder = window
        self._reminder_label = label
        self._blink_timer = window.after(BLINK_INTERVAL, self._blink)
        self._status_var.set("⏰ 闹钟响了！")

    def _blink(self) -> None:
        if self._reminder is None:
            return
        choices = [color for color in BLINK_BG_COLORS if color != self._blink_color] or list(BLINK_BG_COLORS)
        self._blink_color = random.choice(choices)
        self._reminder.configure(bg=self._blink_color)
        if self._reminder_label is not None:
            self._reminder_label.configure(bg=self._blink_color)
        self._blink_timer = self._reminder.after(BLINK_INTERVAL, self._blink)

    def close_reminder(self) -> None:
        """关闭提醒窗口，并按「重复」设置决定是否顺延。"""
        if self._reminder is None:
            return
        if self._blink_timer is not None:
            self._reminder.after_cancel(self._blink_timer)
            self._blink_timer = None
        self._reminder.grab_release()
        self._reminder.destroy()
        self._reminder = None
        self._reminder_label = None

        self._state.repeat = bool(self._repeat_var.get())
        self._state.dismiss(datetime.now())
        if self._state.armed:
            assert self._state.target is not None
            self._time_var.set(self._state.target.strftime(DIGITAL_FORMAT))
            self._status_var.set(f"下次提醒: {self._state.target.strftime(DIGITAL_FORMAT)}")
        else:
            self._set_button.configure(text="设置闹钟", style="Accent.TButton")
            self._status_var.set("闹钟已关闭")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口。"""
        if self._tick_timer is not None:
            self.root.after_cancel(self._tick_timer)
            self._tick_timer = None
        self.close_reminder()
        alarm = (
            self._state.target.strftime(DIGITAL_FORMAT)
            if self._state.target is not None
            else (self._time_var.get().strip() or None)
        )
        save_settings(
            AlarmSettings(alarm=alarm, repeat=bool(self._repeat_var.get()), geometry=self.root.winfo_geometry()),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: AlarmClockWindow(settings_path), "定时提醒窗口")


@fcmd.tool("alarmclock", help="打开定时提醒窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开定时提醒窗口。"""
    run_gui()


@fcmd.main("alarmclock")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
