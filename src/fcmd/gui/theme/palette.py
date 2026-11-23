"""主题数据结构：Palette / FontScale / Metrics / Theme 与默认值。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class Palette:
    """一套完整语义调色板。

    背景层次：``background``（窗口底）< ``surface``（卡片/输入/日志）<
    ``surface_hover``（hover）< ``surface_active``（按下/选中行）。
    前景层次：``text``（正文）> ``muted``（次要说明）。``accent`` 系列用于
    强调、选中和进度，``select_*`` 专门给 Text / Listbox 的文本选区，
    ``log_bg`` / ``log_fg`` 给日志与代码文本。

    禁用态单独占一组 token（``disabled_bg`` / ``disabled_fg`` /
    ``disabled_border``）：只把 ``muted`` + ``surface`` 拿来当禁用色时，浅色主题里
    禁用按钮会比可用按钮更亮、深色主题里文字仍偏亮，两种状态都不容易分辨。
    这组色按「比常态更平、更退后」单独调，各主题可以各自拉开对比。
    """

    background: str = "#0b1220"
    surface: str = "#121c2b"
    surface_hover: str = "#1a2839"
    surface_active: str = "#22344a"
    border: str = "#35496a"
    border_strong: str = "#4d6a8f"
    text: str = "#e8eef7"
    muted: str = "#9aa9ba"
    accent: str = "#38a8ff"
    accent_hover: str = "#5cbcff"
    accent_pressed: str = "#1f86d6"
    on_accent: str = "#06121d"
    select_bg: str = "#2f6fa8"
    select_fg: str = "#ffffff"
    cursor: str = "#7cd0ff"
    log_bg: str = "#0d1622"
    log_fg: str = "#d8e4f2"
    success: str = "#4ade80"
    warning: str = "#fbbf24"
    danger: str = "#f87171"
    stripe: str = "#1c2940"
    disabled_bg: str = "#0e1724"
    disabled_fg: str = "#5d6d80"
    disabled_border: str = "#26364c"


@dataclass(frozen=True, slots=True)
class FontScale:
    """字号阶梯（磅），与 :class:`Palette` 一起构成主题基线。"""

    small: int = 8
    body: int = 9
    heading: int = 11
    title: int = 13


@dataclass(frozen=True, slots=True)
class Spacing:
    """间距阶梯（像素），用于 padding 与行高。"""

    hairline: int = 1
    xxs: int = 2
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24


@dataclass(frozen=True, slots=True)
class GeometryRules:
    """窗口初始尺寸规则：按屏幕比例计算，再夹在最小/最大像素之间。"""

    width_ratio: float = 0.42
    height_ratio: float = 0.72
    min_width: int = 560
    min_height: int = 520
    max_width: int = 980
    max_height: int = 920


@dataclass(frozen=True, slots=True)
class Metrics:
    """控件度量（圆角 / 控件高度补偿 / 水平内边距）。

    与 :class:`Palette` 一起构成主题基线：QSS 里的圆角与单行控件高度都从这里取，
    主题或窗口可以在 ``ui/theme.json`` 覆盖其中任意一项，而不必改样式表。
    """

    radius: int = 8
    control_height_extra: int = 9
    run_control_height_extra: int = 6
    button_pad_x: str = "0.9em"
    input_pad_x: str = "0.45em"


@dataclass(frozen=True, slots=True)
class Theme:
    """一套可切换主题：稳定 id、显示名与完整调色板。"""

    name: str
    label: str
    palette: Palette
    fonts: FontScale = FontScale()
    spacing: Spacing = Spacing()
    metrics: Metrics = Metrics()
    native: bool = False
    description: str = ""


PALETTE: Final[Palette] = Palette()


FONTS: Final[FontScale] = FontScale()


SPACING: Final[Spacing] = Spacing()


METRICS: Final[Metrics] = Metrics()


GEOMETRY_RULES: Final[GeometryRules] = GeometryRules()


PALETTE_TOKENS: Final[tuple[str, ...]] = tuple(item.name for item in fields(Palette))


THEME_FONT_KEYS: Final[Mapping[str, str]] = MappingProxyType(
    {"font_small": "small", "font_body": "body", "font_heading": "heading", "font_title": "title"}
)


METRIC_KEYS: Final[tuple[str, ...]] = tuple(item.name for item in fields(Metrics))


THEME_OVERRIDES_KEY: Final[str] = "overrides"


THEME_APPS_KEY: Final[str] = "apps"


THEME_APP_KEY: Final[str] = "app"
