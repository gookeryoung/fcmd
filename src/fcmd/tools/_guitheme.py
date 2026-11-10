"""Tk 窗口共享主题：主题注册表、调色板、字号阶梯、间距与 ttk 样式（不注册工具）。

设计原则：

- 只用标准库 ``tkinter`` / ``ttk``，不引入第三方 GUI 依赖。
- 窗口侧只需在 ``Tk()`` 创建后调用 :func:`apply_theme`，删掉该调用即退回
  系统默认外观，不影响任何业务逻辑。
- :func:`apply_theme` 幂等：重复调用只会覆盖同名样式与 option，不会叠加。
- 内置四套主题：深色科技蓝（默认）、浅色、高对比、接近原生。主题选择
  持久化到 ``${FCMD_HOME:-~/.fcmd}/ui/theme.json``，窗口启动时读取；
  文件缺失、JSON 损坏、主题名非法或调色板缺 token 都安全回退默认主题。
- 经典控件（Text / Menu / Listbox / Entry / Canvas）通过 ``option_add``
  同步主题色。Windows 标题栏（``dwmapi`` / DWM 属性）不在本模块范围。

文件名以下划线开头，``fcmd.tools`` 的工具发现会跳过本模块。
"""

from __future__ import annotations

import contextlib
import re
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, cast

from fcmd.tools import _guiconfig

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from tkinter import Misc

__all__ = [
    "BODY_FONT",
    "DEFAULT_THEME_NAME",
    "FONTS",
    "GEOMETRY_RULES",
    "HEADING_FONT",
    "MONO_FONT",
    "PALETTE",
    "SMALL_FONT",
    "SPACING",
    "STYLE_NAMES",
    "THEMES",
    "THEME_ALIASES",
    "THEME_NAME",
    "FontScale",
    "GeometryRules",
    "Palette",
    "Spacing",
    "Theme",
    "apply_theme",
    "classic_options",
    "enable_dpi_awareness",
    "font_plan",
    "install_theme_switcher",
    "load_theme",
    "missing_palette_tokens",
    "pick_font_family",
    "proportional_geometry",
    "read_theme_name",
    "resolve_theme",
    "save_theme",
    "theme_config_path",
    "theme_names",
    "ttk_style_specs",
]

THEME_NAME: Final[str] = "clam"
"""ttk 主题名：``clam`` 允许自定义背景/边框，系统主题（vista/aqua）会忽略这些选项。"""

UI_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI Variable Text",
    "Segoe UI",
    "PingFang SC",
    "Noto Sans CJK SC",
    "DejaVu Sans",
)
"""界面字体候选，按优先级排列；Windows 中文环境首选微软雅黑 UI。"""

MONO_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "JetBrains Mono",
    "Source Code Pro",
    "DejaVu Sans Mono",
    "Courier New",
)
"""等宽字体候选，用于日志/代码类控件。"""

BODY_FONT: Final[str] = "FcmdBodyFont"
SMALL_FONT: Final[str] = "FcmdSmallFont"
HEADING_FONT: Final[str] = "FcmdHeadingFont"
MONO_FONT: Final[str] = "FcmdMonoFont"


@dataclass(frozen=True, slots=True)
class Palette:
    """一套完整语义调色板。

    背景层次：``background``（窗口底）< ``surface``（卡片/输入/日志）<
    ``surface_hover``（hover）< ``surface_active``（按下/选中行）。
    前景层次：``text``（正文）> ``muted``（次要说明）。``accent`` 系列用于
    强调、选中和进度，``select_*`` 专门给 Text / Listbox 的文本选区，
    ``log_bg`` / ``log_fg`` 给日志与代码文本。
    """

    background: str = "#0b1220"
    surface: str = "#121c2b"
    surface_hover: str = "#1a2839"
    surface_active: str = "#22344a"
    border: str = "#2a3a4f"
    border_strong: str = "#3b5370"
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
class Theme:
    """一套可切换主题：稳定 id、显示名与完整调色板。"""

    name: str
    label: str
    palette: Palette
    fonts: FontScale = FontScale()
    spacing: Spacing = Spacing()
    native: bool = False
    description: str = ""


PALETTE: Final[Palette] = Palette()
FONTS: Final[FontScale] = FontScale()
SPACING: Final[Spacing] = Spacing()
GEOMETRY_RULES: Final[GeometryRules] = GeometryRules()

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
        border="#d5dde8",
        border_strong="#b9c6d6",
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
    ),
    native=True,
    description="浅灰系统色 + 蓝色强调，保留系统 ttk 观感，主题化收敛到字体与强调色。",
)

THEMES: Final[Mapping[str, Theme]] = MappingProxyType(
    {theme.name: theme for theme in (_DARK_BLUE, _LIGHT, _HIGH_CONTRAST, _NATIVE)}
)
"""内置主题注册表：id 即持久化值与菜单值。"""

THEME_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"dark": "dark-blue", "default": "dark-blue", "contrast": "high-contrast"}
)
"""兼容别名，读取配置时归一化到正式 id。"""

_THEME_SETTINGS_KEY: Final[str] = "theme"
_HEX_COLOR_RE: Final[re.Pattern[str]] = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True, slots=True)
class FontSpec:
    """一个具名字体（Tk named font）的目标配置。"""

    name: str
    size: int
    weight: str = "normal"
    mono: bool = False


@dataclass(frozen=True, slots=True)
class StyleSpec:
    """单个 ttk 样式的 ``configure`` 选项与状态映射。"""

    configure: dict[str, object]
    map: dict[str, list[tuple[str, object]]] = field(default_factory=dict)


STYLE_NAMES: Final[tuple[str, ...]] = (
    "TFrame",
    "TLabelframe",
    "TLabelframe.Label",
    "TLabel",
    "Title.TLabel",
    "Muted.TLabel",
    "Hint.TLabel",
    "TButton",
    "Accent.TButton",
    "Danger.TButton",
    "TEntry",
    "TCombobox",
    "TCheckbutton",
    "TRadiobutton",
    "TProgressbar",
    "TScrollbar",
    "Treeview",
    "Treeview.Heading",
    "TNotebook",
    "TNotebook.Tab",
    "TSeparator",
    "Toolbutton",
)
"""本主题会覆盖的 ttk 样式名；单测据此校验键存在性，窗口可据此选中强调/危险按钮。"""

CLASSIC_OPTION_PATTERNS: Final[tuple[str, ...]] = (
    "*Background",
    "*Foreground",
    "*Font",
    "*selectBackground",
    "*selectForeground",
    "*insertBackground",
    "*highlightBackground",
    "*highlightColor",
    "*Text.background",
    "*Text.foreground",
    "*Text.insertBackground",
    "*Text.selectBackground",
    "*Text.selectForeground",
    "*Text.font",
    "*Text.relief",
    "*Text.borderWidth",
    "*Text.highlightThickness",
    "*Menu.background",
    "*Menu.foreground",
    "*Menu.activeBackground",
    "*Menu.activeForeground",
    "*Menu.selectColor",
    "*Menu.font",
    "*Menu.borderWidth",
    "*Menu.relief",
    "*Listbox.background",
    "*Listbox.foreground",
    "*Listbox.selectBackground",
    "*Listbox.selectForeground",
    "*Button.font",
    "*Entry.background",
    "*Entry.foreground",
    "*Entry.insertBackground",
    "*Entry.selectBackground",
    "*Entry.selectForeground",
    "*Canvas.background",
    "*Canvas.highlightBackground",
    "*Canvas.highlightColor",
)
"""``option_add`` 兜底的经典控件选项清单（Text / Menu / Listbox / Entry / Button / Canvas）。"""


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


def load_theme(path: Path | None = None) -> Theme:
    """按持久化配置加载主题；读取失败或值非法时返回默认主题。"""
    return resolve_theme(read_theme_name(path))


def save_theme(name: str | Theme, path: Path | None = None) -> Theme:
    """保存主题选择并返回规范化的主题；非法名写默认主题，写入失败静默。"""
    theme = resolve_theme(name if isinstance(name, str) else name.name)
    _guiconfig.write_settings({_THEME_SETTINGS_KEY: theme.name}, path or theme_config_path())
    return theme


def pick_font_family(
    available: Iterable[str],
    candidates: tuple[str, ...] = UI_FONT_CANDIDATES,
) -> str:
    """从候选列表里挑第一个系统可用字体族，均不可用时返回首个候选。

    参数:
        available: 当前 Tk 环境报告的字体族集合（``font.families()``）。
        candidates: 按优先级排列的候选字体族。

    返回:
        选中的字体族名；大小写不敏感匹配，保留系统报告的真实写法。
    """
    lookup = {name.casefold(): name for name in available}
    for candidate in candidates:
        found = lookup.get(candidate.casefold())
        if found is not None:
            return found
    return candidates[0]


def font_plan(scale: FontScale = FONTS) -> tuple[FontSpec, ...]:
    """返回需要配置的具名字体清单（自定义字体 + Tk 默认字体）。"""
    return (
        FontSpec(BODY_FONT, scale.body),
        FontSpec(SMALL_FONT, scale.small),
        FontSpec(HEADING_FONT, scale.heading, weight="bold"),
        FontSpec(MONO_FONT, scale.body, mono=True),
        FontSpec("TkDefaultFont", scale.body),
        FontSpec("TkTextFont", scale.body),
        FontSpec("TkMenuFont", scale.body),
        FontSpec("TkHeadingFont", scale.heading, weight="bold"),
    )


def ttk_style_specs(
    palette: Palette = PALETTE,
    spacing: Spacing = SPACING,
    *,
    native: bool = False,
) -> dict[str, StyleSpec]:
    """构造全部 ttk 样式定义（纯数据，便于单测与窗口侧复用）。

    ``native`` 为真表示沿用系统 ttk 主题（Windows 上是 ``vista``）。该主题的
    ``Button.button`` 由系统原生绘制，``background`` / ``bordercolor`` 等选项
    全部失效，只有 ``foreground`` 生效；因此强调 / 危险按钮若继续按填充色配
    ``on_accent`` 前景，会退化成「系统浅色底 + 白字」。此时改为系统按钮底色 +
    语义色文字，保证可读且保留原生观感；其他主题仍使用填充式按钮。
    """
    accent_bg = palette.surface if native else palette.accent
    accent_fg = palette.accent if native else palette.on_accent
    accent_active_bg = palette.surface_hover if native else palette.accent_hover
    accent_pressed_bg = palette.surface_active if native else palette.accent_pressed
    accent_fg_map: list[tuple[str, object]] = (
        [("active", palette.accent_hover), ("pressed", palette.accent_pressed), ("disabled", palette.muted)]
        if native
        else [("disabled", palette.muted)]
    )
    danger_bg = palette.surface if native else palette.danger
    danger_fg = palette.danger if native else palette.on_accent
    return {
        "TFrame": StyleSpec({"background": palette.background}),
        "TLabelframe": StyleSpec(
            {
                "background": palette.background,
                "bordercolor": palette.border,
                "borderwidth": 1,
                "relief": "solid",
            }
        ),
        "TLabelframe.Label": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.muted,
                "font": HEADING_FONT,
            }
        ),
        "TLabel": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.text,
                "font": BODY_FONT,
            }
        ),
        "Title.TLabel": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.text,
                "font": HEADING_FONT,
            }
        ),
        "Muted.TLabel": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.muted,
                "font": BODY_FONT,
            }
        ),
        "Hint.TLabel": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.muted,
                "font": SMALL_FONT,
            }
        ),
        "TButton": StyleSpec(
            {
                "background": palette.surface,
                "foreground": palette.text,
                "bordercolor": palette.border,
                "focuscolor": palette.accent,
                "font": BODY_FONT,
                "padding": (spacing.sm, spacing.xs),
                "relief": "flat",
            },
            {
                "background": [("active", palette.surface_hover), ("pressed", palette.surface_active)],
                "foreground": [("disabled", palette.muted)],
            },
        ),
        "Accent.TButton": StyleSpec(
            {
                "background": accent_bg,
                "foreground": accent_fg,
                "bordercolor": palette.accent,
                "focuscolor": palette.accent if native else palette.on_accent,
                "font": BODY_FONT,
                "padding": (spacing.sm, spacing.xs),
                "relief": "flat",
            },
            {
                "background": [
                    ("active", accent_active_bg),
                    ("pressed", accent_pressed_bg),
                    ("disabled", palette.surface),
                ],
                "foreground": accent_fg_map,
            },
        ),
        "Danger.TButton": StyleSpec(
            {
                "background": danger_bg,
                "foreground": danger_fg,
                "bordercolor": palette.danger,
                "focuscolor": palette.danger if native else palette.on_accent,
                "font": BODY_FONT,
                "padding": (spacing.sm, spacing.xs),
                "relief": "flat",
            },
            {
                "background": [
                    ("active", palette.surface_active),
                    ("pressed", palette.surface_active),
                    ("disabled", palette.surface),
                ],
                "foreground": [("active", palette.danger), ("pressed", palette.danger), ("disabled", palette.muted)],
            },
        ),
        "TEntry": StyleSpec(
            {
                "fieldbackground": palette.surface,
                "foreground": palette.text,
                "insertcolor": palette.cursor,
                "bordercolor": palette.border,
                "lightcolor": palette.surface,
                "darkcolor": palette.surface,
                "padding": (spacing.sm, spacing.xs),
            },
            {
                "bordercolor": [("focus", palette.accent)],
                "fieldbackground": [("readonly", palette.background)],
            },
        ),
        "TCombobox": StyleSpec(
            {
                "fieldbackground": palette.surface,
                "background": palette.surface,
                "foreground": palette.text,
                "arrowcolor": palette.muted,
                "bordercolor": palette.border,
                "padding": (spacing.sm, spacing.xs),
            },
            {
                "bordercolor": [("focus", palette.accent)],
                "fieldbackground": [("readonly", palette.surface)],
                "foreground": [("disabled", palette.muted)],
            },
        ),
        "TCheckbutton": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.text,
                "focuscolor": palette.accent,
                "indicatorbackground": palette.surface,
                "indicatorforeground": palette.text,
                "upperbordercolor": palette.border_strong,
                "lowerbordercolor": palette.border,
                "font": BODY_FONT,
            },
            {
                "background": [("active", palette.background)],
                "foreground": [("active", palette.accent_hover), ("disabled", palette.muted)],
                "indicatorbackground": [
                    ("disabled", palette.background),
                    ("selected", palette.accent),
                    ("pressed", palette.surface_active),
                ],
                "indicatorforeground": [
                    ("disabled", palette.muted),
                    ("selected", palette.on_accent),
                ],
            },
        ),
        "TRadiobutton": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.text,
                "focuscolor": palette.accent,
                "indicatorbackground": palette.surface,
                "indicatorforeground": palette.text,
                "upperbordercolor": palette.border_strong,
                "lowerbordercolor": palette.border,
                "font": BODY_FONT,
            },
            {
                "background": [("active", palette.background)],
                "foreground": [("active", palette.accent_hover), ("disabled", palette.muted)],
                "indicatorbackground": [
                    ("disabled", palette.background),
                    ("selected", palette.accent),
                    ("pressed", palette.surface_active),
                ],
                "indicatorforeground": [
                    ("disabled", palette.muted),
                    ("selected", palette.on_accent),
                ],
            },
        ),
        "TProgressbar": StyleSpec(
            {
                "background": palette.accent,
                "troughcolor": palette.surface,
                "bordercolor": palette.border,
                "thickness": spacing.sm,
            }
        ),
        "TScrollbar": StyleSpec(
            {
                "background": palette.muted,
                "troughcolor": palette.background,
                "bordercolor": palette.background,
                "arrowcolor": palette.muted,
                "gripcount": 0,
            },
            {"background": [("active", palette.accent)]},
        ),
        "Treeview": StyleSpec(
            {
                "background": palette.surface,
                "fieldbackground": palette.surface,
                "foreground": palette.text,
                "font": BODY_FONT,
                "rowheight": spacing.lg + spacing.xs,
                "bordercolor": palette.border,
            },
            {
                "background": [("selected", palette.select_bg)],
                "foreground": [("selected", palette.select_fg)],
            },
        ),
        "Treeview.Heading": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.muted,
                "font": BODY_FONT,
                "relief": "flat",
                "padding": (spacing.sm, spacing.xs),
            }
        ),
        "TNotebook": StyleSpec({"background": palette.background, "bordercolor": palette.border}),
        "TNotebook.Tab": StyleSpec(
            {
                "background": palette.background,
                "foreground": palette.muted,
                "font": BODY_FONT,
                "padding": (spacing.md, spacing.xs),
            },
            {
                "background": [("selected", palette.surface)],
                "foreground": [("selected", palette.text), ("active", palette.accent_hover)],
            },
        ),
        "TSeparator": StyleSpec({"background": palette.border}),
        "Toolbutton": StyleSpec(
            {
                "background": palette.surface,
                "foreground": palette.text,
                "bordercolor": palette.surface,
                "focuscolor": palette.accent,
                "font": BODY_FONT,
                "padding": (spacing.sm, spacing.xs),
                "relief": "flat",
                "anchor": "w",
            },
            {
                "background": [("active", palette.surface_hover), ("pressed", palette.surface_active)],
                "foreground": [("disabled", palette.muted)],
            },
        ),
    }


def classic_options(palette: Palette = PALETTE) -> dict[str, str]:
    """返回 ``option_add`` 的经典控件兜底配置（Text / Menu / Listbox / Entry / Button / Canvas）。

    值为 Tk option 字符串：字体用具名字体，颜色用调色板色值。
    """
    return {
        "*Background": palette.background,
        "*Foreground": palette.text,
        "*Font": BODY_FONT,
        "*selectBackground": palette.select_bg,
        "*selectForeground": palette.select_fg,
        "*insertBackground": palette.cursor,
        "*highlightBackground": palette.border,
        "*highlightColor": palette.accent,
        "*Text.background": palette.log_bg,
        "*Text.foreground": palette.log_fg,
        "*Text.insertBackground": palette.cursor,
        "*Text.selectBackground": palette.select_bg,
        "*Text.selectForeground": palette.select_fg,
        "*Text.font": MONO_FONT,
        "*Text.relief": "flat",
        "*Text.borderWidth": "0",
        "*Text.highlightThickness": "0",
        "*Menu.background": palette.surface,
        "*Menu.foreground": palette.text,
        "*Menu.activeBackground": palette.accent,
        "*Menu.activeForeground": palette.on_accent,
        "*Menu.selectColor": palette.accent,
        "*Menu.font": BODY_FONT,
        "*Menu.borderWidth": "1",
        "*Menu.relief": "flat",
        "*Listbox.background": palette.surface,
        "*Listbox.foreground": palette.text,
        "*Listbox.selectBackground": palette.select_bg,
        "*Listbox.selectForeground": palette.select_fg,
        "*Button.font": BODY_FONT,
        "*Entry.background": palette.surface,
        "*Entry.foreground": palette.text,
        "*Entry.insertBackground": palette.cursor,
        "*Entry.selectBackground": palette.select_bg,
        "*Entry.selectForeground": palette.select_fg,
        "*Canvas.background": palette.surface,
        "*Canvas.highlightBackground": palette.border,
        "*Canvas.highlightColor": palette.accent,
    }


def proportional_geometry(
    screen_width: int,
    screen_height: int,
    rules: GeometryRules = GEOMETRY_RULES,
) -> tuple[int, int]:
    """按屏幕分辨率与比例计算窗口初始宽高，并夹在 ``min`` / ``max`` 之间。

    参数:
        screen_width: 屏幕可用宽度（像素）。
        screen_height: 屏幕可用高度（像素）。
        rules: 比例与最小/最大像素限制，默认见 :data:`GEOMETRY_RULES`。

    返回:
        ``(宽, 高)``，均为夹紧后的整数像素。
    """
    width = min(max(round(screen_width * rules.width_ratio), rules.min_width), rules.max_width)
    height = min(max(round(screen_height * rules.height_ratio), rules.min_height), rules.max_height)
    return width, height


DPI_AWARENESS_PER_MONITOR: Final[int] = 2


def enable_dpi_awareness() -> bool:
    """在创建 ``Tk()`` 之前声明进程 DPI 感知；成功应用返回 ``True``。

    只在 Windows 生效：优先 ``SetProcessDpiAwareness``（Per-Monitor），失败
    再退回 ``SetProcessDPIAware``（System）。非 Windows、缺少 API 或调用失败
    都静默返回 ``False``，窗口照常启动，不抛异常。
    """
    if sys.platform != "win32":
        return False
    return _apply_win32_dpi_awareness(_load_windll())


def _load_windll() -> Any | None:
    """惰性加载 ``ctypes.windll``；非 Windows 或缺少 ctypes 时返回 ``None``。"""
    try:
        import ctypes
    except ImportError:  # pragma: no cover - CPython 全平台都自带 ctypes
        return None
    return getattr(ctypes, "windll", None)


def _apply_win32_dpi_awareness(windll: Any | None) -> bool:
    """依次尝试 shcore / user32 两种 DPI 声明方式，任一成功即返回 ``True``。"""
    if windll is None:
        return False
    with contextlib.suppress(AttributeError, OSError):
        if windll.shcore.SetProcessDpiAwareness(DPI_AWARENESS_PER_MONITOR) == 0:
            return True
    try:
        return bool(windll.user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False


@dataclass(frozen=True, slots=True)
class _TkModules:
    """惰性导入的 tkinter 三件套，便于单测注入替身。"""

    tk: Any
    ttk: Any
    tkfont: Any


def _load_tk_modules() -> _TkModules:
    """惰性导入 tkinter / ttk / font，避免无显示环境下的顶层导入开销。"""
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import ttk

    return _TkModules(tk=tk, ttk=ttk, tkfont=tkfont)


def _apply_named_fonts(
    tkfont: Any,
    root: Misc,
    ui_family: str,
    mono_family: str,
    scale: FontScale,
) -> None:
    """创建或更新主题具名字体；已存在的（Tk 默认字体）就地 configure。"""
    existing = set(tkfont.names(root))
    for spec in font_plan(scale):
        options = {
            "family": mono_family if spec.mono else ui_family,
            "size": spec.size,
            "weight": spec.weight,
        }
        if spec.name in existing:
            tkfont.nametofont(spec.name, root=root).configure(**options)
        else:
            tkfont.Font(root=root, name=spec.name, **options)


def _resolve_requested_theme(
    theme: str | Theme | None,
    palette: Palette | None,
    scale: FontScale | None,
    spacing: Spacing | None,
) -> Theme:
    """把 ``apply_theme`` 的入参归一化为最终主题（含显式覆盖）。"""
    resolved = load_theme() if theme is None else (theme if isinstance(theme, Theme) else resolve_theme(theme))
    if palette is not None or scale is not None or spacing is not None:
        resolved = replace(
            resolved,
            palette=palette or resolved.palette,
            fonts=scale or resolved.fonts,
            spacing=spacing or resolved.spacing,
        )
    return resolved


def apply_theme(
    root: Misc,
    theme: str | Theme | None = None,
    *,
    palette: Palette | None = None,
    scale: FontScale | None = None,
    spacing: Spacing | None = None,
) -> Theme:
    """给 Tk 根窗口应用 fcmd 主题（幂等，窗口构建前调用），返回最终主题。

    参数:
        root: 已创建的 ``Tk()`` / ``Toplevel`` 根窗口。
        theme: 主题 id、:class:`Theme` 或 ``None``（读取持久化配置）。
        palette: 显式覆盖调色板（测试或特殊窗口使用）。
        scale: 显式覆盖字号阶梯。
        spacing: 显式覆盖间距阶梯。

    返回:
        实际应用的主题；窗口侧据此获取 ``palette`` / ``spacing``。
    """
    resolved = _resolve_requested_theme(theme, palette, scale, spacing)
    modules = _load_tk_modules()
    families = tuple(modules.tkfont.families(root))
    ui_family = pick_font_family(families)
    mono_family = pick_font_family(families, MONO_FONT_CANDIDATES)
    _apply_named_fonts(modules.tkfont, root, ui_family, mono_family, resolved.fonts)

    style = modules.ttk.Style(root)
    if not resolved.native:
        with contextlib.suppress(modules.tk.TclError):
            style.theme_use(THEME_NAME)
    for name, spec in ttk_style_specs(resolved.palette, resolved.spacing, native=resolved.native).items():
        style.configure(name, **spec.configure)
        if spec.map:
            style.map(name, **spec.map)

    with contextlib.suppress(modules.tk.TclError):
        root.configure({"background": resolved.palette.background})
    for pattern, value in classic_options(resolved.palette).items():
        root.option_add(pattern, value)
    return resolved


def install_theme_switcher(root: Misc) -> None:  # pragma: no cover - 需要真实 Tk 窗口
    """在窗口菜单栏追加“主题”菜单；选择后保存配置并提示重启生效。"""
    import tkinter as tk
    from tkinter import messagebox

    try:
        menu_path = str(root.cget("menu"))
    except tk.TclError:
        menu_path = ""
    if menu_path:
        menubar = root.nametowidget(menu_path)
    else:
        menubar = tk.Menu(root)
        cast(Any, root).configure(menu=menubar)

    try:
        end = menubar.index("end")
    except tk.TclError:
        end = None
    if end is not None:
        for index in range(int(end) + 1):
            try:
                if menubar.entrycget(index, "label") == "主题":
                    return
            except tk.TclError:
                continue

    current = tk.StringVar(master=root, value=read_theme_name())
    theme_menu = tk.Menu(menubar, tearoff=False)

    def _choose() -> None:
        chosen = save_theme(current.get())
        messagebox.showinfo(
            "主题已保存",
            f"已选择「{chosen.label}」。\n请重新打开窗口以应用新主题。",
            parent=root,
        )

    for theme in THEMES.values():
        theme_menu.add_radiobutton(label=theme.label, value=theme.name, variable=current, command=_choose)
    menubar.add_cascade(label="主题", menu=theme_menu)
    view = cast(Any, root)
    view._fcmd_theme_var = current
    view._fcmd_theme_menu = theme_menu
