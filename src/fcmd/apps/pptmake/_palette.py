"""pptmake 风格预设与调色板：纯数据，供模板模型与版式引擎共用。

拆出本模块是为了让 :mod:`fcmd.apps.pptmake._core` 保持在模块行数预算内；
对外仍通过 ``fcmd.apps.pptmake._core`` 重导出，调用方无需改 import。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = [
    "DEFAULT_BODY_FONT",
    "DEFAULT_HEADING_FONT",
    "IMAGE_FITS",
    "IMAGE_FIT_CONTAIN",
    "IMAGE_FIT_COVER",
    "IMAGE_FIT_TEXT",
    "PALETTES",
    "PALETTE_FIELDS",
    "STYLES",
    "STYLE_AURORA",
    "STYLE_CORAL",
    "STYLE_CUSTOM",
    "STYLE_GRAPHITE",
    "STYLE_PORCELAIN",
    "STYLE_TEXT",
    "Palette",
]

STYLE_AURORA: Final[str] = "aurora"
STYLE_PORCELAIN: Final[str] = "porcelain"
STYLE_GRAPHITE: Final[str] = "graphite"
STYLE_CORAL: Final[str] = "coral"
STYLE_CUSTOM: Final[str] = "custom"
STYLES: Final[tuple[str, ...]] = (STYLE_AURORA, STYLE_PORCELAIN, STYLE_GRAPHITE, STYLE_CORAL)
STYLE_TEXT: Final[dict[str, str]] = {
    STYLE_AURORA: "极光蓝",
    STYLE_PORCELAIN: "素瓷青",
    STYLE_GRAPHITE: "石墨金",
    STYLE_CORAL: "珊瑚暖",
    STYLE_CUSTOM: "自定义",
}

IMAGE_FIT_COVER: Final[str] = "cover"
IMAGE_FIT_CONTAIN: Final[str] = "contain"
IMAGE_FITS: Final[tuple[str, ...]] = (IMAGE_FIT_COVER, IMAGE_FIT_CONTAIN)
IMAGE_FIT_TEXT: Final[dict[str, str]] = {
    IMAGE_FIT_COVER: "裁剪填满",
    IMAGE_FIT_CONTAIN: "完整显示",
}

PALETTE_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("background", "背景"),
    ("surface", "卡片"),
    ("accent", "强调"),
    ("secondary", "辅助"),
    ("text", "正文"),
    ("muted", "次要"),
)

DEFAULT_HEADING_FONT: Final[str] = "Microsoft YaHei UI"
DEFAULT_BODY_FONT: Final[str] = "Microsoft YaHei UI"


@dataclass(frozen=True, slots=True)
class Palette:
    """模板调色板：背景 / 卡片 / 强调 / 辅助 / 正文 / 次要。"""

    background: str = "#0B1020"
    surface: str = "#151C33"
    accent: str = "#5B8CFF"
    secondary: str = "#8B5CF6"
    text: str = "#F4F6FB"
    muted: str = "#9AA4BF"


PALETTES: Final[dict[str, Palette]] = {
    STYLE_AURORA: Palette(
        background="#0B1020",
        surface="#151C33",
        accent="#5B8CFF",
        secondary="#8B5CF6",
        text="#F4F6FB",
        muted="#9AA4BF",
    ),
    STYLE_PORCELAIN: Palette(
        background="#F7F4EE",
        surface="#FFFFFF",
        accent="#1F8A7A",
        secondary="#D97757",
        text="#1D2A2A",
        muted="#6B7A78",
    ),
    STYLE_GRAPHITE: Palette(
        background="#17181C",
        surface="#22242B",
        accent="#E9B44C",
        secondary="#6C8EBF",
        text="#F2F2F0",
        muted="#9A9CA5",
    ),
    STYLE_CORAL: Palette(
        background="#FFF7F2",
        surface="#FFFFFF",
        accent="#E4572E",
        secondary="#2A6F97",
        text="#2B211D",
        muted="#8A746B",
    ),
}
