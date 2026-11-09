"""``fcmd.tools._guitheme`` 主题模块测试。

覆盖纯逻辑：调色板/字号/间距配置、字体族挑选、ttk 样式键、经典控件
``option_add`` 兜底、响应式 geometry、DPI 回退。全部用替身模块，不创建
真实 ``Tk()``（CI 无显示环境）。
"""

from __future__ import annotations

import ctypes
import types
from dataclasses import replace
from typing import Any, cast

import pytest

from fcmd.tools import _guitheme


class _FakeTclError(Exception):
    """替身 TclError。"""


class _FakeFont:
    def __init__(self, **options: object) -> None:
        self.options: dict[str, object] = dict(options)

    def configure(self, **options: object) -> None:
        self.options.update(options)


class _FakeFontModule:
    """替身 ``tkinter.font``，记录字体创建与配置。"""

    def __init__(self, families: tuple[str, ...], existing: tuple[str, ...] = ()) -> None:
        self._families = families
        self._existing = set(existing)
        self.created: dict[str, _FakeFont] = {name: _FakeFont() for name in existing}
        self.created_calls = 0
        self.configure_calls = 0

    def families(self, root: object | None = None) -> list[str]:
        return list(self._families)

    def names(self, root: object | None = None) -> list[str]:
        return sorted(self._existing)

    def nametofont(self, name: str, root: object | None = None) -> _FakeFont:
        self.configure_calls += 1
        return self.created[name]

    def Font(self, root: object | None = None, name: str | None = None, **options: object) -> _FakeFont:
        self.created_calls += 1
        font = _FakeFont(**options)
        assert name is not None
        self.created[name] = font
        return font


class _FakeStyle:
    """替身 ``ttk.Style``，记录 configure/map/theme_use 调用。"""

    def __init__(self, *, fail_theme: bool = False) -> None:
        self.theme: str | None = None
        self.configured: dict[str, dict[str, object]] = {}
        self.mapped: dict[str, dict[str, object]] = {}
        self._fail_theme = fail_theme

    def theme_use(self, name: str | None = None) -> None:
        if self._fail_theme:
            raise _FakeTclError(name or "")
        self.theme = name

    def configure(self, name: str, **options: object) -> None:
        self.configured[name] = options

    def map(self, name: str, **options: object) -> None:
        self.mapped[name] = options


class _FakeRoot:
    """替身 Tk 根窗口，记录 option_add 与 configure。"""

    def __init__(self, *, fail_configure: bool = False) -> None:
        self.options: dict[str, str] = {}
        self.configured: dict[str, object] = {}
        self._fail_configure = fail_configure

    def option_add(self, pattern: str, value: str) -> None:
        self.options[pattern] = value

    def configure(self, cnf: dict[str, object] | None = None, **options: object) -> None:
        if self._fail_configure:
            raise _FakeTclError("configure failed")
        if cnf:
            self.configured.update(cnf)
        self.configured.update(options)


def _fake_modules(
    *,
    families: tuple[str, ...] = ("Segoe UI", "Consolas"),
    existing: tuple[str, ...] = (),
    fail_theme: bool = False,
) -> tuple[_guitheme._TkModules, _FakeStyle, _FakeFontModule]:
    style = _FakeStyle(fail_theme=fail_theme)
    fonts = _FakeFontModule(families, existing)
    modules = _guitheme._TkModules(
        tk=types.SimpleNamespace(TclError=_FakeTclError),
        ttk=types.SimpleNamespace(Style=lambda root=None: style),
        tkfont=fonts,
    )
    return modules, style, fonts


def _relative_luminance(color: str) -> float:
    """按 WCAG 2 计算相对亮度（``#rrggbb``）。"""

    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast(foreground: str, background: str) -> float:
    """两色对比度，1–21。"""
    lighter, darker = sorted((_relative_luminance(foreground), _relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# ============================================================================ #
# 字体族与字号阶梯
# ============================================================================ #
class TestFontFamily:
    def test_prefers_first_available_candidate(self) -> None:
        assert _guitheme.pick_font_family(["DejaVu Sans", "Microsoft YaHei UI"]) == "Microsoft YaHei UI"

    def test_matches_case_insensitively_and_keeps_real_case(self) -> None:
        assert _guitheme.pick_font_family(["segoe ui"]) == "segoe ui"

    def test_falls_back_to_first_candidate(self) -> None:
        assert _guitheme.pick_font_family(["Unknown Font"]) == _guitheme.UI_FONT_CANDIDATES[0]

    def test_mono_candidates(self) -> None:
        assert _guitheme.pick_font_family(["Courier New", "Consolas"], _guitheme.MONO_FONT_CANDIDATES) == "Consolas"


class TestFontPlan:
    def test_includes_custom_and_tk_default_fonts(self) -> None:
        plan = {spec.name: spec for spec in _guitheme.font_plan()}
        assert set(plan) >= {
            _guitheme.BODY_FONT,
            _guitheme.SMALL_FONT,
            _guitheme.HEADING_FONT,
            _guitheme.MONO_FONT,
            "TkDefaultFont",
            "TkTextFont",
            "TkMenuFont",
            "TkHeadingFont",
        }

    def test_uses_scale_and_flags(self) -> None:
        scale = _guitheme.FontScale(small=8, body=11, heading=14)
        plan = {spec.name: spec for spec in _guitheme.font_plan(scale)}
        assert plan[_guitheme.BODY_FONT].size == 11
        assert plan[_guitheme.SMALL_FONT].size == 8
        assert plan[_guitheme.HEADING_FONT].size == 14
        assert plan[_guitheme.HEADING_FONT].weight == "bold"
        assert plan[_guitheme.MONO_FONT].mono is True


# ============================================================================ #
# ttk 样式与经典控件兜底
# ============================================================================ #
class TestStyleSpecs:
    def test_covers_every_declared_style_name(self) -> None:
        specs = _guitheme.ttk_style_specs()
        assert set(specs) == set(_guitheme.STYLE_NAMES)
        assert all(spec.configure for spec in specs.values())

    def test_button_variants_follow_palette(self) -> None:
        palette = replace(_guitheme.PALETTE, accent="#123456", accent_hover="#654321")
        specs = _guitheme.ttk_style_specs(palette)
        assert specs["TButton"].configure["background"] == palette.surface
        assert ("active", palette.surface_hover) in specs["TButton"].map["background"]
        assert specs["Accent.TButton"].configure["background"] == "#123456"
        assert ("active", "#654321") in specs["Accent.TButton"].map["background"]

    def test_entry_and_combobox_use_surface_and_cursor(self) -> None:
        specs = _guitheme.ttk_style_specs()
        assert specs["TEntry"].configure["fieldbackground"] == _guitheme.PALETTE.surface
        assert specs["TEntry"].configure["insertcolor"] == _guitheme.PALETTE.cursor
        assert specs["TCombobox"].configure["arrowcolor"] == _guitheme.PALETTE.muted
        assert ("focus", _guitheme.PALETTE.accent) in specs["TEntry"].map["bordercolor"]

    def test_button_states_cover_hover_pressed_disabled(self) -> None:
        palette = _guitheme.PALETTE
        specs = _guitheme.ttk_style_specs()
        assert ("active", palette.surface_hover) in specs["TButton"].map["background"]
        assert ("pressed", palette.surface_active) in specs["TButton"].map["background"]
        assert ("disabled", palette.muted) in specs["TButton"].map["foreground"]
        accent = specs["Accent.TButton"].map
        assert ("pressed", palette.accent_pressed) in accent["background"]
        assert ("disabled", palette.border) in accent["background"]

    def test_progress_and_scrollbar_use_accent_states(self) -> None:
        palette = _guitheme.PALETTE
        specs = _guitheme.ttk_style_specs()
        assert specs["TProgressbar"].configure["background"] == palette.accent
        assert specs["TProgressbar"].configure["troughcolor"] == palette.surface
        assert ("active", palette.accent) in specs["TScrollbar"].map["background"]

    def test_check_and_radio_indicator_states_are_visible(self) -> None:
        palette = _guitheme.PALETTE
        specs = _guitheme.ttk_style_specs()
        for name in ("TCheckbutton", "TRadiobutton"):
            spec = specs[name]
            assert spec.configure["indicatorbackground"] == palette.surface
            assert spec.configure["indicatorforeground"] == palette.text
            assert ("selected", palette.accent) in spec.map["indicatorbackground"]
            assert ("selected", palette.on_accent) in spec.map["indicatorforeground"]
            assert ("disabled", palette.background) in spec.map["indicatorbackground"]
            assert ("disabled", palette.muted) in spec.map["indicatorforeground"]
        # 勾选标记（深色）与选中底色（亮蓝）、未选中标记（浅色）与深色底都必须可辨。
        assert _contrast(palette.on_accent, palette.accent) >= 4.5
        assert _contrast(palette.text, palette.surface) >= 4.5

    def test_disabled_states_remain_legible(self) -> None:
        palette = _guitheme.PALETTE
        specs = _guitheme.ttk_style_specs()
        assert ("disabled", palette.border) in specs["Accent.TButton"].map["background"]
        assert _contrast(palette.muted, palette.border) >= 3
        assert _contrast(palette.muted, palette.surface) >= 3

    def test_respects_spacing(self) -> None:
        spacing = _guitheme.Spacing(lg=20, xs=5)
        specs = _guitheme.ttk_style_specs(spacing=spacing)
        assert specs["Treeview"].configure["rowheight"] == 25
        assert specs["TButton"].configure["padding"] == (spacing.sm, spacing.xs)

    def test_toolbutton_uses_surface_and_hover(self) -> None:
        palette = _guitheme.PALETTE
        spec = _guitheme.ttk_style_specs()["Toolbutton"]
        assert spec.configure["background"] == palette.surface
        assert spec.configure["foreground"] == palette.text
        assert ("active", palette.surface_hover) in spec.map["background"]


class TestClassicOptions:
    def test_covers_declared_patterns(self) -> None:
        assert set(_guitheme.classic_options()) == set(_guitheme.CLASSIC_OPTION_PATTERNS)

    def test_text_colors_cover_background_foreground_cursor_and_selection(self) -> None:
        options = _guitheme.classic_options()
        palette = _guitheme.PALETTE
        assert options["*Text.background"] == palette.log_bg
        assert options["*Text.foreground"] == palette.log_fg
        assert options["*Text.insertBackground"] == palette.cursor
        assert options["*Text.selectBackground"] == palette.select_bg
        assert options["*Text.selectForeground"] == palette.select_fg
        assert options["*Text.font"] == _guitheme.MONO_FONT

    def test_menu_colors(self) -> None:
        options = _guitheme.classic_options()
        palette = _guitheme.PALETTE
        assert options["*Menu.background"] == palette.surface
        assert options["*Menu.foreground"] == palette.text
        assert options["*Menu.activeBackground"] == palette.accent
        assert options["*Menu.activeForeground"] == palette.on_accent
        assert options["*Menu.font"] == _guitheme.BODY_FONT

    def test_follows_custom_palette(self) -> None:
        palette = replace(_guitheme.PALETTE, log_bg="#010203", text="#040506")
        options = _guitheme.classic_options(palette)
        assert options["*Text.background"] == "#010203"
        assert options["*Menu.foreground"] == "#040506"


# ============================================================================ #
# 深色科技蓝配色
# ============================================================================ #
class TestPaletteContrast:
    def test_text_contrast_is_readable(self) -> None:
        palette = _guitheme.PALETTE
        assert _contrast(palette.text, palette.background) >= 7
        assert _contrast(palette.text, palette.surface) >= 7
        assert _contrast(palette.muted, palette.background) >= 4.5
        assert _contrast(palette.muted, palette.surface) >= 4.5
        assert _contrast(palette.log_fg, palette.log_bg) >= 7
        assert _contrast(palette.on_accent, palette.accent) >= 4.5
        assert _contrast(palette.select_fg, palette.select_bg) >= 4.5

    def test_background_layers_are_dark_and_distinct(self) -> None:
        palette = _guitheme.PALETTE
        layers = [palette.background, palette.surface, palette.surface_hover, palette.surface_active]
        assert len(set(layers)) == len(layers)
        assert all(_relative_luminance(color) < 0.1 for color in layers)

    def test_semantic_colors_are_visible_on_text_and_tree_backgrounds(self) -> None:
        palette = _guitheme.PALETTE
        for color in (palette.success, palette.warning, palette.danger):
            assert _contrast(color, palette.log_bg) >= 4.5
            assert _contrast(color, palette.surface) >= 4.5


# ============================================================================ #
# 响应式 geometry
# ============================================================================ #
class TestProportionalGeometry:
    def test_default_on_1080p(self) -> None:
        assert _guitheme.proportional_geometry(1920, 1080) == (806, 778)

    def test_clamps_to_minimum_on_small_screen(self) -> None:
        assert _guitheme.proportional_geometry(800, 600) == (560, 520)

    def test_clamps_to_maximum_on_4k(self) -> None:
        assert _guitheme.proportional_geometry(3840, 2160) == (980, 920)

    def test_custom_rules(self) -> None:
        rules = _guitheme.GeometryRules(
            width_ratio=0.5,
            height_ratio=0.5,
            min_width=100,
            min_height=100,
            max_width=2000,
            max_height=2000,
        )
        assert _guitheme.proportional_geometry(1000, 800, rules) == (500, 400)


# ============================================================================ #
# DPI 感知
# ============================================================================ #
class _Shcore:
    def __init__(self, result: int = 0, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[int] = []

    def SetProcessDpiAwareness(self, level: int) -> int:
        self.calls.append(level)
        if self._error is not None:
            raise self._error
        return self._result


class _User32:
    def __init__(self, result: int = 1, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls = 0

    def SetProcessDPIAware(self) -> int:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class TestDpiAwareness:
    def test_non_windows_returns_false_without_loading_windll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_guitheme.sys, "platform", "linux")
        monkeypatch.setattr(_guitheme, "_load_windll", lambda: pytest.fail("非 Windows 不应加载 windll"))
        assert _guitheme.enable_dpi_awareness() is False

    def test_windows_uses_per_monitor_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_guitheme.sys, "platform", "win32")
        shcore, user32 = _Shcore(), _User32()
        monkeypatch.setattr(_guitheme, "_load_windll", lambda: types.SimpleNamespace(shcore=shcore, user32=user32))
        assert _guitheme.enable_dpi_awareness() is True
        assert shcore.calls == [_guitheme.DPI_AWARENESS_PER_MONITOR]
        assert user32.calls == 0

    def test_windows_returns_false_without_windll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_guitheme.sys, "platform", "win32")
        monkeypatch.setattr(_guitheme, "_load_windll", lambda: None)
        assert _guitheme.enable_dpi_awareness() is False

    def test_none_windll(self) -> None:
        assert _guitheme._apply_win32_dpi_awareness(None) is False

    def test_falls_back_to_user32_when_shcore_fails(self) -> None:
        shcore = _Shcore(error=AttributeError("no shcore"))
        user32 = _User32(result=1)
        windll = types.SimpleNamespace(shcore=shcore, user32=user32)
        assert _guitheme._apply_win32_dpi_awareness(windll) is True
        assert user32.calls == 1

    def test_user32_failure_returns_false(self) -> None:
        shcore = _Shcore(result=1)
        user32 = _User32(error=OSError("denied"))
        windll = types.SimpleNamespace(shcore=shcore, user32=user32)
        assert _guitheme._apply_win32_dpi_awareness(windll) is False

    def test_missing_apis_return_false(self) -> None:
        assert _guitheme._apply_win32_dpi_awareness(types.SimpleNamespace()) is False

    def test_load_windll_returns_ctypes_attribute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        monkeypatch.setattr(ctypes, "windll", sentinel, raising=False)
        assert _guitheme._load_windll() is sentinel

    def test_load_windll_none_without_attribute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(ctypes, "windll", raising=False)
        assert _guitheme._load_windll() is None


# ============================================================================ #
# apply_theme
# ============================================================================ #
class TestApplyTheme:
    def test_configures_theme_styles_and_classic_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        modules, style, fonts = _fake_modules()
        root = _FakeRoot()
        monkeypatch.setattr(_guitheme, "_load_tk_modules", lambda: modules)

        _guitheme.apply_theme(cast(Any, root))

        specs = _guitheme.ttk_style_specs()
        assert style.theme == _guitheme.THEME_NAME
        assert set(style.configured) == set(_guitheme.STYLE_NAMES)
        assert set(style.mapped) == {name for name, spec in specs.items() if spec.map}
        assert root.options == _guitheme.classic_options()
        assert root.configured == {"background": _guitheme.PALETTE.background}
        assert fonts.created[_guitheme.BODY_FONT].options["family"] == "Segoe UI"
        assert fonts.created[_guitheme.MONO_FONT].options["family"] == "Consolas"
        assert fonts.created[_guitheme.HEADING_FONT].options["weight"] == "bold"

    def test_reuses_existing_named_fonts_and_survives_theme_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        names = tuple(spec.name for spec in _guitheme.font_plan())
        modules, style, fonts = _fake_modules(existing=names, fail_theme=True)
        monkeypatch.setattr(_guitheme, "_load_tk_modules", lambda: modules)

        _guitheme.apply_theme(cast(Any, _FakeRoot()))

        assert fonts.created_calls == 0
        assert fonts.configure_calls == len(names)
        assert fonts.created["TkDefaultFont"].options["family"] == "Segoe UI"
        assert style.theme is None
        assert set(style.configured) == set(_guitheme.STYLE_NAMES)

    def test_survives_root_configure_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        modules, _style, _fonts = _fake_modules()
        root = _FakeRoot(fail_configure=True)
        monkeypatch.setattr(_guitheme, "_load_tk_modules", lambda: modules)

        _guitheme.apply_theme(cast(Any, root))

        assert root.options == _guitheme.classic_options()

    def test_load_tk_modules_real_tkinter(self) -> None:
        pytest.importorskip("tkinter")
        modules = _guitheme._load_tk_modules()
        assert hasattr(modules.ttk, "Style")
        assert hasattr(modules.tkfont, "families")
