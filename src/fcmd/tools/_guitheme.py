"""Tk 窗口共享主题：调色板、字号阶梯、间距与 ttk 样式（不注册工具）。

设计原则：

- 只用标准库 ``tkinter`` / ``ttk``，不引入第三方 GUI 依赖。
- 窗口侧只需在 ``Tk()`` 创建后调用 :func:`apply_theme`，删掉该调用即退回
  系统默认外观，不影响任何业务逻辑。
- :func:`apply_theme` 幂等：重复调用只会覆盖同名样式与 option，不会叠加。
- 默认主题是深色科技蓝：深蓝灰载体 + 亮蓝强调，经典控件（Text / Menu /
  Listbox / Entry）通过 ``option_add`` 同步为深色。Windows 标题栏
  （``dwmapi`` / DWM 属性）不在本模块范围，后续可单独补。

文件名以下划线开头，``fcmd.tools`` 的工具发现会跳过本模块。
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable
    from tkinter import Misc

__all__ = [
    "BODY_FONT",
    "FONTS",
    "HEADING_FONT",
    "MONO_FONT",
    "PALETTE",
    "SMALL_FONT",
    "SPACING",
    "STYLE_NAMES",
    "THEME_NAME",
    "FontScale",
    "GeometryRules",
    "Palette",
    "Spacing",
    "apply_theme",
    "classic_options",
    "enable_dpi_awareness",
    "font_plan",
    "pick_font_family",
    "proportional_geometry",
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
    """深色科技蓝调色板。

    背景层次：``background``（窗口底）< ``surface``（卡片/输入/日志）<
    ``surface_hover``（hover）< ``surface_active``（按下/选中行）。
    前景层次：``text``（正文）> ``muted``（次要说明）。``accent`` 系列用于
    强调、选中和进度，``select_*`` 专门给 Text / Listbox 的文本选区。
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


PALETTE: Final[Palette] = Palette()
FONTS: Final[FontScale] = FontScale()
SPACING: Final[Spacing] = Spacing()
GEOMETRY_RULES: Final[GeometryRules] = GeometryRules()


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
    "TButton",
    "Accent.TButton",
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
"""本主题会覆盖的 ttk 样式名；单测据此校验键存在性，窗口可据此选中强调按钮。"""

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
)
"""``option_add`` 兜底的经典控件选项清单（Text / Menu / Listbox / Entry / Button）。"""


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


def ttk_style_specs(palette: Palette = PALETTE, spacing: Spacing = SPACING) -> dict[str, StyleSpec]:
    """构造全部 ttk 样式定义（纯数据，便于单测与窗口侧复用）。"""
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
                "background": palette.accent,
                "foreground": palette.on_accent,
                "bordercolor": palette.accent,
                "focuscolor": palette.on_accent,
                "font": BODY_FONT,
                "padding": (spacing.sm, spacing.xs),
                "relief": "flat",
            },
            {
                "background": [
                    ("active", palette.accent_hover),
                    ("pressed", palette.accent_pressed),
                    ("disabled", palette.border),
                ],
                "foreground": [("disabled", palette.muted)],
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
                "background": palette.border_strong,
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
    """返回 ``option_add`` 的经典控件兜底配置（Text / Menu / Listbox / Entry / Button）。

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


def apply_theme(
    root: Misc,
    *,
    palette: Palette = PALETTE,
    scale: FontScale = FONTS,
    spacing: Spacing = SPACING,
) -> None:
    """给 Tk 根窗口应用 fcmd 统一主题（幂等，窗口构建前调用）。

    参数:
        root: 已创建的 ``Tk()`` / ``Toplevel`` 根窗口。
        palette: 调色板，默认浅色主题。
        scale: 字号阶梯。
        spacing: 间距阶梯。
    """
    modules = _load_tk_modules()
    families = tuple(modules.tkfont.families(root))
    ui_family = pick_font_family(families)
    mono_family = pick_font_family(families, MONO_FONT_CANDIDATES)
    _apply_named_fonts(modules.tkfont, root, ui_family, mono_family, scale)

    style = modules.ttk.Style(root)
    with contextlib.suppress(modules.tk.TclError):
        style.theme_use(THEME_NAME)
    for name, spec in ttk_style_specs(palette, spacing).items():
        style.configure(name, **spec.configure)
        if spec.map:
            style.map(name, **spec.map)

    with contextlib.suppress(modules.tk.TclError):
        root.configure({"background": palette.background})
    for pattern, value in classic_options(palette).items():
        root.option_add(pattern, value)
