"""窗口几何纯函数：``WxH+X+Y`` 解析 / 格式化、越界夹紧与居中计算。

不导入任何 GUI 库，CI（无 PySide6）可直接单测；各窗口骨架、
各工具窗口的设置读写都复用同一套口径，因此同一个 ``ui/*.json`` 里的
``geometry`` 字段可以在任意前端之间互读。

- 记忆位置越界、尺寸小于最小尺寸、坐标落在屏幕外时按屏幕夹紧；
- 默认 geometry 既可传固定字符串，也可传 ``(屏幕宽, 屏幕高) -> geometry``
  的回调，供按屏幕比例计算初始尺寸的窗口使用。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "centered_geometry",
    "clamp_saved_geometry",
    "format_geometry",
    "parse_geometry",
    "resolve_geometry",
    "size_from_geometry",
]

_GEOMETRY_RE = re.compile(r"^(?P<width>\d+)x(?P<height>\d+)(?:(?P<sign_x>[+-])(?P<x>\d+)(?P<sign_y>[+-])(?P<y>\d+))?$")


def valid_geometry(text: str | None) -> str | None:
    """校验 geometry 字符串（``WxH`` 或 ``WxH+X+Y``），非法时返回 ``None``。"""
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


def parse_geometry(text: str | None) -> tuple[int, int, int, int] | None:
    """解析 ``WxH+X+Y``；缺少位置时按左上角 ``0,0`` 处理，非法文本返回 ``None``。"""
    if not isinstance(text, str):
        return None
    match = _GEOMETRY_RE.fullmatch(text.strip())
    if match is None:
        return None
    width = int(match["width"])
    height = int(match["height"])
    if width <= 0 or height <= 0:
        return None
    if match["x"] is None:
        return width, height, 0, 0
    x = int(match["x"]) if match["sign_x"] == "+" else -int(match["x"])
    y = int(match["y"]) if match["sign_y"] == "+" else -int(match["y"])
    return width, height, x, y


def format_geometry(width: int, height: int, x: int, y: int) -> str:
    """把窗口矩形格式化为 ``WxH+X+Y``（负数坐标写成 ``+-`` 形式）。"""
    sign_x = "+" if x >= 0 else "-"
    sign_y = "+" if y >= 0 else "-"
    return f"{max(width, 0)}x{max(height, 0)}{sign_x}{abs(x)}{sign_y}{abs(y)}"


def size_from_geometry(text: str | None, *, default: tuple[int, int] = (900, 700)) -> tuple[int, int]:
    """从 ``WxH+X+Y`` 取宽高；缺失或非法时返回 ``default``。"""
    rect = parse_geometry(text)
    return (rect[0], rect[1]) if rect is not None else default


def clamp_saved_geometry(
    saved: str | None,
    screen_width: int,
    screen_height: int,
    *,
    min_width: int,
    min_height: int,
) -> str | None:
    """把记忆的 geometry 夹回屏幕可见范围；非法或缺失时返回 ``None``。"""
    return clamp_geometry(
        saved,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )


def resolve_geometry(  # noqa: PLR0913 - 与 clamp_geometry 参数对齐，显式传入屏幕与最小尺寸
    saved: str | None,
    screen_width: int,
    screen_height: int,
    *,
    default: str | Callable[[int, int], str],
    min_width: int,
    min_height: int,
) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（同样按屏幕夹紧）。"""
    restored = clamp_saved_geometry(
        saved,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )
    if restored is not None:
        return restored
    default_text = default(screen_width, screen_height) if callable(default) else default
    clamped = clamp_saved_geometry(
        default_text,
        screen_width,
        screen_height,
        min_width=min_width,
        min_height=min_height,
    )
    return clamped or default_text


def centered_geometry(  # noqa: PLR0913 - 纯几何计算，参数与父窗口/屏幕坐标一一对应
    width: int,
    height: int,
    *,
    parent_x: int,
    parent_y: int,
    parent_width: int,
    parent_height: int,
    screen_width: int,
    screen_height: int,
) -> str:
    """计算在父窗口内居中的 geometry，并夹回屏幕可见范围。"""
    x = parent_x + (parent_width - width) // 2
    y = parent_y + (parent_height - height) // 2
    x = min(max(x, 0), max(screen_width - width, 0))
    y = min(max(y, 0), max(screen_height - height, 0))
    return format_geometry(width, height, x, y)
