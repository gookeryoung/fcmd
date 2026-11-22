"""GUI 主题注册表：调色板 / 字号阶梯 / 间距 / 几何规则与主题持久化。

本模块只保存**主题数据与纯函数**（四套内置主题、别名、`ui/theme.json` 读写、
调色板 token 校验、字体挑选与相对亮度），不导入任何 GUI 工具包：

- Qt 侧 :mod:`fcmd.gui.qt.theme` 把 :class:`Palette` 翻译成 QSS / QPalette；
- CI（不装 PySide6）也能直接单测主题解析与持久化；
- 窗口几何与主题选择互不影响，主题文件位于
  ``${FCMD_HOME:-~/.fcmd}/ui/theme.json``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from fcmd.gui import config as _guiconfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

__all__ = [
    "CJK_TEXT_SAMPLE",
    "DEFAULT_THEME_NAME",
    "FONTS",
    "GEOMETRY_RULES",
    "LATIN_FONT_CANDIDATES",
    "MONO_FONT_CANDIDATES",
    "PALETTE",
    "SPACING",
    "THEMES",
    "THEME_ALIASES",
    "UI_FONT_CANDIDATES",
    "FontScale",
    "GeometryRules",
    "Palette",
    "Spacing",
    "Theme",
    "font_renders_cjk",
    "is_dark_palette",
    "load_theme",
    "missing_palette_tokens",
    "pick_font_family",
    "read_theme_name",
    "relative_luminance",
    "resolve_theme",
    "save_theme",
    "theme_config_path",
    "theme_names",
    "ui_font_families",
]


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


LATIN_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Cascadia Code",
    "Cascadia Mono",
)
"""拉丁（英文 / 数字 / 符号）界面字体候选：GUI 里英文一律用 Cascadia Code。

系统没装时按 :data:`MONO_FONT_CANDIDATES` 之外的正常 CJK 字体回退，中文不受影响：
Qt 6 用 :meth:`QFont.setFamilies` 组成字体链，逐字回退到 CJK 字体。
"""

UI_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI Variable Text",
    "Segoe UI",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Droid Sans Fallback",
    "WenQuanYi Bitmap Song",
    "DejaVu Sans",
    "Noto Sans",
    "Liberation Sans",
    "Ubuntu",
)
"""界面字体候选，按优先级排列；Qt 侧用 ``QFontDatabase`` 挑第一个可用族。"""

MONO_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Cascadia Code",
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "Source Code Pro",
    "Noto Sans Mono CJK SC",
    "Sarasa Mono SC",
    "WenQuanYi Micro Hei Mono",
    "WenQuanYi Zen Hei Mono",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "WenQuanYi Bitmap Song",
    "Courier New",
)
"""等宽字体候选，用于日志 / 代码类控件。"""

CJK_TEXT_SAMPLE: Final[str] = "中文测试"
"""字体中文覆盖检测样本；缺字形时宽度测量返回 0。"""

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
    ),
    native=True,
    description="浅灰系统色 + 蓝色强调，保留系统原生观感，主题化收敛到字体与强调色。",
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


def ui_font_families(available: Iterable[str]) -> tuple[str, ...]:
    """界面字体链：拉丁字体（Cascadia Code）在前，CJK 字体在后做逐字回退。

    返回值的第一个元素即 ``QFont`` 的主族（Qt 5 只认第一个），Qt 6 下整个链会通过
    ``setFamilies`` 生效，因此英文 / 数字落在 Cascadia Code、中文自动回退到 CJK 字体。
    系统没装 Cascadia系列时退化为原来的行为（只有 CJK 字体）。
    """
    lookup = {name.casefold(): name for name in available}
    chain: list[str] = []
    for candidate in LATIN_FONT_CANDIDATES:
        found = lookup.get(candidate.casefold())
        if found is not None:
            chain.append(found)
            break
    cjk = pick_font_family(available)
    if cjk not in chain:
        chain.append(cjk)
    return tuple(chain)


def pick_font_family(
    available: Iterable[str],
    candidates: tuple[str, ...] = UI_FONT_CANDIDATES,
) -> str:
    """从候选列表里挑第一个系统可用字体族，均不可用时返回首个候选。

    参数:
        available: 当前 GUI 环境报告的字体族集合。
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


def font_renders_cjk(measure: Callable[[str], int]) -> bool:
    """按字体宽度测量回调判断能否显示中文。

    缺字形时宽度测量返回 0，此时窗口里的中文会显示为方块
    （用于检测系统字体是否覆盖中文，例如 CI 的最小字体环境）。
    """
    return measure(CJK_TEXT_SAMPLE) > 0


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
