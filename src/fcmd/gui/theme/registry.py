"""主题注册表查询 / 持久化 / 覆盖与亮度判断。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, Final

from fcmd.gui import config as _guiconfig

from .builtin import DEFAULT_THEME_NAME, THEME_ALIASES, THEMES
from .palette import (
    METRIC_KEYS,
    PALETTE,
    PALETTE_TOKENS,
    THEME_APPS_KEY,
    THEME_FONT_KEYS,
    THEME_OVERRIDES_KEY,
    Palette,
    Theme,
)

_THEME_SETTINGS_KEY: Final[str] = "theme"


_HEX_COLOR_RE: Final[re.Pattern[str]] = re.compile(r"^#[0-9a-fA-F]{6}$")


_PAD_RE: Final[re.Pattern[str]] = re.compile(r"^\d+(?:\.\d+)?(?:em|px)$")


def theme_names() -> tuple[str, ...]:
    """返回注册表顺序的主题 id。"""
    return tuple(THEMES)


def missing_palette_tokens(palette: Palette) -> tuple[str, ...]:
    """返回调色板中缺失、为空或不是 ``#rrggbb`` 的 token 名。"""
    missing: list[str] = []
    for item in fields(Palette):
        try:
            value = getattr(palette, item.name)
        except AttributeError:
            missing.append(item.name)
            continue
        if not isinstance(value, str) or _HEX_COLOR_RE.fullmatch(value) is None:
            missing.append(item.name)
    return tuple(missing)


def resolve_theme(name: str | None) -> Theme:
    """把主题名归一化为注册表里的 :class:`Theme`，未知/缺 token 时回退默认。"""
    key = name.strip().casefold() if isinstance(name, str) else ""
    key = THEME_ALIASES.get(key, key)
    theme = THEMES.get(key)
    if theme is None or missing_palette_tokens(theme.palette):
        return THEMES[DEFAULT_THEME_NAME]
    return theme


def theme_config_path() -> Path:
    """返回主题配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/theme.json``。"""
    return _guiconfig.config_path("theme")


def read_theme_name(path: Path | None = None) -> str:
    """读取持久化主题名；缺文件、JSON 损坏或值非法时返回默认主题 id。"""
    raw = _guiconfig.read_settings(path or theme_config_path())
    if raw is None:
        return DEFAULT_THEME_NAME
    value = raw.get(_THEME_SETTINGS_KEY)
    if not isinstance(value, str):
        return DEFAULT_THEME_NAME
    return resolve_theme(value).name


def load_theme(path: Path | None = None, *, app: str | None = None) -> Theme:
    """按持久化配置加载主题；读取失败或值非法时返回默认主题。

    ``app`` 非空时额外应用 ``apps[app]`` 段（按窗口的调色板 / 度量覆盖）；
    覆盖值非法时逐项忽略，不影响主题本身。
    """
    raw = _guiconfig.read_settings(path or theme_config_path()) or {}
    theme = resolve_theme(raw.get(_THEME_SETTINGS_KEY))
    overrides = merge_theme_overrides(raw, app)
    return apply_theme_overrides(theme, overrides) if overrides else theme


def save_theme(name: str | Theme, path: Path | None = None) -> Theme:
    """保存主题选择并返回规范化的主题；非法名写默认主题，写入失败静默。

    只改 ``theme`` 字段，保留 ``overrides`` / ``apps`` 等手写覆盖段；
    否则用户切一次主题就会把自定义度量冲掉。
    """
    theme = resolve_theme(name if isinstance(name, str) else name.name)
    target = path or theme_config_path()
    raw = _guiconfig.read_settings(target) or {}
    raw[_THEME_SETTINGS_KEY] = theme.name
    _guiconfig.write_settings(raw, target)
    return theme


def merge_theme_overrides(raw: Mapping[str, object] | None, app: str | None = None) -> dict[str, object]:
    """把 ``overrides`` 与 ``apps[app]`` 合并成扁平覆盖表（应用段优先）。

    只做结构合并，值是否可用交给 :func:`apply_theme_overrides` 逐项校验；
    非映射段直接忽略，不抛异常。
    """
    merged: dict[str, object] = {}
    if not isinstance(raw, Mapping):
        return merged
    global_section = raw.get(THEME_OVERRIDES_KEY)
    if isinstance(global_section, Mapping):
        merged.update(global_section)
    app_section = raw.get(THEME_APPS_KEY)
    if app and isinstance(app_section, Mapping):
        scoped = app_section.get(app)
        if isinstance(scoped, Mapping):
            merged.update(scoped)
    return merged


def apply_theme_overrides(theme: Theme, overrides: Mapping[str, object] | None) -> Theme:
    """在主题上应用调色板 / 字号 / 度量覆盖，返回新 :class:`Theme`。

    覆盖键与 :class:`Palette` 字段、``font_*``、:class:`Metrics` 字段同名；
    非法值（非 ``#rrggbb``、类型不符、超出合理范围）逐项忽略，始终返回可用主题。
    """
    if not isinstance(overrides, Mapping) or not overrides:
        return theme
    palette_values = {item.name: getattr(theme.palette, item.name) for item in fields(Palette)}
    font_values: dict[str, int] = {}
    metric_values: dict[str, Any] = {}
    for key, value in overrides.items():
        if not isinstance(key, str):
            continue
        name = key.strip()
        if name in PALETTE_TOKENS:
            if isinstance(value, str) and _HEX_COLOR_RE.fullmatch(value.strip()):
                palette_values[name] = value.strip()
            continue
        if name in THEME_FONT_KEYS:
            point = _coerce_int(value, low=5, high=32)
            if point is not None:
                font_values[THEME_FONT_KEYS[name]] = point
            continue
        if name in METRIC_KEYS:
            validated = _coerce_metric(name, value)
            if validated is not None:
                metric_values[name] = validated
    if (
        not font_values
        and not metric_values
        and palette_values == {item.name: getattr(theme.palette, item.name) for item in fields(Palette)}
    ):
        return theme
    return replace(
        theme,
        palette=replace(theme.palette, **palette_values),
        fonts=replace(theme.fonts, **font_values),
        metrics=replace(theme.metrics, **metric_values),
    )


def _coerce_int(value: object, *, low: int, high: int) -> int | None:
    """把配置值收敛成 ``low..high`` 的整数；布尔 / 越界 / 非整数返回 ``None``。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if low <= value <= high else None


def _coerce_metric(name: str, value: object) -> object | None:
    """按度量字段校验覆盖值：整数度量限 0..40，内边距只接受 em / px 长度。"""
    if name in {"radius", "control_height_extra", "run_control_height_extra"}:
        return _coerce_int(value, low=0, high=40)
    if name in {"button_pad_x", "input_pad_x"} and isinstance(value, str) and _PAD_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def relative_luminance(color: str) -> float:
    """按 WCAG 2 计算 ``#rrggbb`` 的相对亮度（0–1）；非法色值抛 ``ValueError``。"""
    if _HEX_COLOR_RE.fullmatch(color) is None:
        raise ValueError(f"不是 #rrggbb 颜色: {color!r}")

    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def is_dark_palette(palette: Palette = PALETTE) -> bool:
    """调色板背景是否属于深色（决定 Windows 标题栏用深色还是浅色）。"""
    try:
        return relative_luminance(palette.background) < 0.5
    except ValueError:
        return False
