"""alarmclock - 定时提醒（数字时钟 + 闹钟 + 到点闪烁提醒，Qt GUI）。

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

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry

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

_DESCRIPTION = "数字时钟 + 定时提醒（Qt）：按 HH:MM:SS 设定闹钟或按分钟快捷延时，到点弹出置顶闪烁提醒窗口，支持每天重复"

DIGITAL_FORMAT = "%H:%M:%S"
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
    return _geometry.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 纯逻辑（可单测，不依赖 GUI 工具包）
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


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.tools.system._alarmclock_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("alarmclock", help="打开定时提醒窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开定时提醒窗口。"""
    run_gui()


@fcmd.main("alarmclock")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
