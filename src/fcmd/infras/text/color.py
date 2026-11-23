"""``fcmd.infras.text.color``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["hex_to_rgb", "hsl_to_rgb", "rgb_to_hex", "rgb_to_hsl"]


_coloraide: Any = None


def _require_coloraide() -> Any | None:
    """惰性导入 ``coloraide.Color``；缺失时返回 ``None``（由调用方提示安装）。

    coloraide 的色空间注册表较大，``import fcmd`` 与工具启动路径不应为其付费。
    """
    global _coloraide  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if _coloraide is not None:
        return _coloraide
    try:
        from coloraide import Color
    except ImportError:  # pragma: no cover - 由 extra 保证，缺库时给出安装提示
        return None
    _coloraide = Color
    return Color


def _color(*args: Any) -> Any:
    """构造 coloraide ``Color``；缺少依赖时抛 ``ValueError``。"""
    color_cls = _require_coloraide()
    if color_cls is None:
        raise ValueError("未安装 coloraide 库，请安装: pip install fcmd[color]")
    return color_cls(*args)


def _srgb_255(color: Any) -> tuple[int, int, int]:
    """取 sRGB 分量（0-1）并量化为 0-255 整数。"""
    r, g, b = color.convert("srgb").coords()[:3]
    return (round(r * 255), round(g * 255), round(b * 255))


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """十六进制颜色转 RGB 分量（0-255）。"""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"HEX 颜色须为 6 位十六进制（#RRGGBB），收到: {hex_color!r}")
    try:
        int(s, 16)  # 仅用于校验字符集，交给 coloraide 解析会抛无上下文的错误
    except ValueError as exc:
        raise ValueError(f"HEX 颜色含非十六进制字符: {hex_color!r}") from exc
    return _srgb_255(_color(f"#{s}"))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """RGB 分量转 ``#RRGGBB``。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    return _color("srgb", [r / 255, g / 255, b / 255]).to_string(hex=True)


def rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """RGB 转 HSL（h: 0-360, s/l: 0-100）。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    h, s, light = _color("srgb", [r / 255, g / 255, b / 255]).convert("hsl").coords()[:3]
    # 灰阶（无彩色）的色相无定义，coloraide 返回 nan；归零保证输出可读
    hue = 0.0 if math.isnan(h) else h
    return (round(hue, 2), round(s * 100, 2), round(light * 100, 2))


def hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    """HSL 转 RGB。"""
    if not 0 <= h <= 360:
        raise ValueError(f"h 须在 0-360 范围: {h}")
    if not 0 <= s <= 100:
        raise ValueError(f"s 须在 0-100 范围: {s}")
    if not 0 <= light <= 100:
        raise ValueError(f"l 须在 0-100 范围: {light}")
    return _srgb_255(_color("hsl", [h, s / 100, light / 100]))
