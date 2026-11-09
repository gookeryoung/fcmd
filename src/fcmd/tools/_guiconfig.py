"""GUI 工具共享的设置持久化骨架（不注册工具）。

7 个 Tk 窗口工具（img2icow / videoconvw / reshackw / checksum / alarmclock /
img2pdfw / regexvalidatew）的设置文件统一位于
``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``，读写流程与 geometry 校验完全一致。
本模块收敛这部分骨架，各工具只保留设置 dataclass 与字段级解析。

文件名以下划线开头，``fcmd.tools`` 的工具发现会跳过本模块。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["clamp_geometry", "config_path", "read_settings", "valid_geometry", "write_settings"]

_GEOMETRY_RE = re.compile(
    r"^(?P<width>\d+)x(?P<height>\d+)"
    r"(?:(?P<sign_x>[+-])(?P<x>\d+)(?P<sign_y>[+-])(?P<y>\d+))?$"
)


def config_path(name: str) -> Path:
    """返回 ``${FCMD_HOME:-~/.fcmd}/ui/<name>.json``。"""
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "ui" / f"{name}.json"


def valid_geometry(text: str | None) -> str | None:
    """校验 Tk geometry 字符串（``WxH`` 或 ``WxH+X+Y``），非法时返回 ``None``。"""
    if text is not None and _GEOMETRY_RE.fullmatch(text):
        return text
    return None


def clamp_geometry(
    text: str | None,
    screen_width: int,
    screen_height: int,
    *,
    min_width: int = 480,
    min_height: int = 360,
) -> str | None:
    """把记忆的 geometry 夹回当前屏幕可见范围；非法格式返回 ``None``。

    规则：尺寸不小于 ``min_width`` / ``min_height``，也不超过当前屏幕；位置
    限制在 ``0 <= x <= 屏幕宽 - 窗口宽``（纵坐标同理）。屏幕小于最小尺寸时
    取屏幕尺寸，避免分辨率切换后窗口被放到屏幕外或缩没。
    """
    if text is None:
        return None
    match = _GEOMETRY_RE.fullmatch(text.strip())
    if match is None:
        return None
    width = _clamp_size(int(match["width"]), min_width, screen_width)
    height = _clamp_size(int(match["height"]), min_height, screen_height)
    if match["x"] is None:
        return f"{width}x{height}"
    x = int(match["x"]) if match["sign_x"] == "+" else -int(match["x"])
    y = int(match["y"]) if match["sign_y"] == "+" else -int(match["y"])
    x = min(max(x, 0), max(screen_width - width, 0))
    y = min(max(y, 0), max(screen_height - height, 0))
    return f"{width}x{height}+{x}+{y}"


def _clamp_size(value: int, minimum: int, screen: int) -> int:
    """尺寸夹在 ``[minimum, screen]``；屏幕无效或小于最小值时取最小值/屏幕。"""
    if screen <= 0:
        return minimum
    return min(max(value, minimum), screen)


def read_settings(path: Path) -> dict[str, Any] | None:
    """读取设置 JSON；文件缺失、JSON 损坏或顶层非对象时返回 ``None``。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def write_settings(payload: Mapping[str, Any], path: Path) -> None:
    """写入设置 JSON；父目录自动创建，写入失败静默忽略。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return
