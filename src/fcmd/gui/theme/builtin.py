"""四套内置主题与别名。"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from .palette import PALETTE, Palette, Theme

DEFAULT_THEME_NAME: Final[str] = "dark-blue"


_DARK_BLUE = Theme(
    name=DEFAULT_THEME_NAME,
    label="深色科技蓝",
    palette=PALETTE,
    description="深蓝灰载体 + 亮蓝强调，适合夜间与长时间开发。",
)


_LIGHT = Theme(
    name="light",
    label="浅色",
    palette=Palette(
        background="#f5f7fa",
        surface="#ffffff",
        surface_hover="#eef3f9",
        surface_active="#e2eaf3",
        border="#c3cedc",
        border_strong="#9fb0c4",
        text="#1f2937",
        muted="#5f6f82",
        accent="#1f6feb",
        accent_hover="#3b82f6",
        accent_pressed="#1a56c4",
        on_accent="#ffffff",
        select_bg="#1f6feb",
        select_fg="#ffffff",
        cursor="#1f6feb",
        log_bg="#ffffff",
        log_fg="#1f2937",
        success="#15803d",
        warning="#b45309",
        danger="#b91c1c",
        stripe="#edf1f7",
        disabled_bg="#f7f9fc",
        disabled_fg="#84909f",
        disabled_border="#dde4ed",
    ),
    description="浅色底 + 蓝色强调，适合白天办公、投影与浅色桌面。",
)


_HIGH_CONTRAST = Theme(
    name="high-contrast",
    label="高对比（无障碍）",
    palette=Palette(
        background="#000000",
        surface="#0a0a0a",
        surface_hover="#1f1f1f",
        surface_active="#333333",
        border="#ffffff",
        border_strong="#ffffff",
        text="#ffffff",
        muted="#d0d0d0",
        accent="#ffd400",
        accent_hover="#ffff66",
        accent_pressed="#cccc00",
        on_accent="#000000",
        select_bg="#ffd400",
        select_fg="#000000",
        cursor="#ffd400",
        log_bg="#000000",
        log_fg="#ffffff",
        success="#00ff88",
        warning="#ffb000",
        danger="#ff5555",
        stripe="#1e1e1e",
        disabled_bg="#0a0a0a",
        disabled_fg="#8a8a8a",
        disabled_border="#5a5a5a",
    ),
    description="纯黑底 + 白字 + 黄色强调，优先低视力与强光环境可读性。",
)


_NATIVE = Theme(
    name="native",
    label="接近原生",
    palette=Palette(
        background="#f0f0f0",
        surface="#ffffff",
        surface_hover="#e8e8e8",
        surface_active="#dcdcdc",
        border="#a0a0a0",
        border_strong="#707070",
        text="#1a1a1a",
        muted="#5a5a5a",
        accent="#005fb8",
        accent_hover="#1a6fc4",
        accent_pressed="#004a91",
        on_accent="#ffffff",
        select_bg="#cce4f7",
        select_fg="#000000",
        cursor="#000000",
        log_bg="#ffffff",
        log_fg="#1a1a1a",
        success="#107c10",
        warning="#9d5d00",
        danger="#c42b1c",
        stripe="#eef1f5",
        disabled_bg="#f4f4f4",
        disabled_fg="#8a8a8a",
        disabled_border="#d8d8d8",
    ),
    native=True,
    description="浅灰系统色 + 蓝色强调，保留系统原生观感，主题化收敛到字体与强调色。",
)


THEMES: Final[Mapping[str, Theme]] = MappingProxyType(
    {theme.name: theme for theme in (_DARK_BLUE, _LIGHT, _HIGH_CONTRAST, _NATIVE)}
)


THEME_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"dark": "dark-blue", "default": "dark-blue", "contrast": "high-contrast"}
)
