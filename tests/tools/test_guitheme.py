"""``fcmd.tools._guitheme`` 主题模块测试。

覆盖纯逻辑：主题注册表、四套调色板完整性/对比度、持久化回退、字体族
挑选、ttk 样式键、经典控件 ``option_add`` 兜底、响应式 geometry、DPI
回退。全部用替身模块，不创建真实 ``Tk()``（CI 无显示环境）。
"""

from __future__ import annotations

import ctypes
import json
import types
from dataclasses import fields, replace
from typing import Any, cast

import pytest

from fcmd.tools import _guitheme

_THEME_IDS = list(_guitheme.THEMES)


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
# 主题注册表
# ============================================================================ #
class TestThemeRegistry:
    def test_contains_four_builtin_themes(self) -> None:
        assert _guitheme.theme_names() == ("dark-blue", "light", "high-contrast", "native")

    def test_default_is_dark_blue(self) -> None:
        assert _guitheme.DEFAULT_THEME_NAME == "dark-blue"
        assert _guitheme.resolve_theme(None).name == "dark-blue"

    def test_unknown_name_falls_back_to_default(self) -> None:
        assert _guitheme.resolve_theme("does-not-exist") is _guitheme.THEMES["dark-blue"]
        assert _guitheme.resolve_theme("") is _guitheme.THEMES["dark-blue"]

    def test_aliases_and_case_folding(self) -> None:
        assert _guitheme.resolve_theme("dark").name == "dark-blue"
        assert _guitheme.resolve_theme("DEFAULT").name == "dark-blue"
        assert _guitheme.resolve_theme(" contrast ").name == "high-contrast"
        assert _guitheme.resolve_theme("LIGHT").name == "light"

    def test_every_theme_has_complete_valid_palette(self) -> None:
        for theme in _guitheme.THEMES.values():
            assert _guitheme.missing_palette_tokens(theme.palette) == ()
            assert theme.label
            assert theme.description
            for item in fields(_guitheme.Palette):
                assert getattr(theme.palette, item.name).startswith("#")

    def test_native_flag_only_on_native_theme(self) -> None:
        assert _guitheme.THEMES["native"].native is True
        assert all(not _guitheme.THEMES[name].native for name in _THEME_IDS if name != "native")

    def test_broken_theme_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = _guitheme.Theme(
            name="broken",
            label="损坏主题",
            palette=object.__new__(_guitheme.Palette),
        )
        monkeypatch.setattr(
            _guitheme,
            "THEMES",
            {"dark-blue": _guitheme.THEMES["dark-blue"], "broken": broken},
        )
        assert _guitheme.resolve_theme("broken").name == "dark-blue"

    def test_missing_palette_tokens_reports_unset_slots(self) -> None:
        empty = object.__new__(_guitheme.Palette)
        assert set(_guitheme.missing_palette_tokens(empty)) == {item.name for item in fields(_guitheme.Palette)}

    def test_missing_palette_tokens_reports_bad_color(self) -> None:
        assert _guitheme.missing_palette_tokens(replace(_guitheme.PALETTE, text="not-a-color")) == ("text",)


# ============================================================================ #
# 主题持久化
# ============================================================================ #
class TestThemePersistence:
    def test_config_path_respects_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert _guitheme.theme_config_path() == tmp_path / "ui" / "theme.json"

    def test_missing_file_returns_default(self, tmp_path: Any) -> None:
        assert _guitheme.read_theme_name(tmp_path / "absent.json") == "dark-blue"
        assert _guitheme.load_theme(tmp_path / "absent.json").name == "dark-blue"

    def test_corrupt_json_returns_default(self, tmp_path: Any) -> None:
        path = tmp_path / "theme.json"
        path.write_text("{not json", encoding="utf-8")
        assert _guitheme.read_theme_name(path) == "dark-blue"

    def test_non_object_or_non_string_value_returns_default(self, tmp_path: Any) -> None:
        path = tmp_path / "theme.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert _guitheme.read_theme_name(path) == "dark-blue"
        path.write_text(json.dumps({"theme": 3}), encoding="utf-8")
        assert _guitheme.read_theme_name(path) == "dark-blue"

    def test_invalid_name_returns_default(self, tmp_path: Any) -> None:
        path = tmp_path / "theme.json"
        path.write_text(json.dumps({"theme": "solarized"}), encoding="utf-8")
        assert _guitheme.read_theme_name(path) == "dark-blue"

    def test_roundtrip_and_alias_normalization(self, tmp_path: Any) -> None:
        path = tmp_path / "theme.json"
        assert _guitheme.save_theme("light", path).name == "light"
        assert json.loads(path.read_text(encoding="utf-8")) == {"theme": "light"}
        assert _guitheme.read_theme_name(path) == "light"
        assert _guitheme.load_theme(path).name == "light"

        assert _guitheme.save_theme("dark", path).name == "dark-blue"
        assert _guitheme.read_theme_name(path) == "dark-blue"

    def test_save_accepts_theme_object(self, tmp_path: Any) -> None:
        path = tmp_path / "theme.json"
        assert _guitheme.save_theme(_guitheme.THEMES["native"], path).name == "native"
        assert _guitheme.read_theme_name(path) == "native"

    def test_save_invalid_name_writes_default(self, tmp_path: Any) -> None:
        path = tmp_path / "theme.json"
        assert _guitheme.save_theme("nope", path).name == "dark-blue"
        assert _guitheme.read_theme_name(path) == "dark-blue"

    def test_save_write_failure_is_silent(self, tmp_path: Any) -> None:
        # 把文件路径指向目录，write_text 会抛 OSError，由 write_settings 吞掉。
        assert _guitheme.save_theme("light", tmp_path).name == "light"


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

    def test_prefers_linux_cjk_font_over_latin(self) -> None:
        families = ["DejaVu Sans", "Noto Sans CJK SC"]
        assert _guitheme.pick_font_family(families) == "Noto Sans CJK SC"

    def test_wenquanyi_bitmap_song_covers_x11_core_fonts(self) -> None:
        # 未启用 Xft 的 Tk 只能看到 X11 核心字体，此时靠文泉驿位图字体显示中文。
        families = ["nimbus sans l", "wenquanyi bitmap song"]
        assert _guitheme.pick_font_family(families) == "wenquanyi bitmap song"
        assert _guitheme.pick_font_family(families, _guitheme.MONO_FONT_CANDIDATES) == "wenquanyi bitmap song"


class TestCjkDetection:
    def test_positive_width_means_supported(self) -> None:
        assert _guitheme.font_renders_cjk(lambda text: len(text) * 12) is True

    def test_zero_width_means_missing_glyphs(self) -> None:
        assert _guitheme.font_renders_cjk(lambda text: 0) is False


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

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_every_theme_produces_complete_styles(self, theme_name: str) -> None:
        theme = _guitheme.THEMES[theme_name]
        specs = _guitheme.ttk_style_specs(theme.palette, theme.spacing, native=theme.native)
        assert set(specs) == set(_guitheme.STYLE_NAMES)
        assert all(spec.configure for spec in specs.values())
        assert specs["Title.TLabel"].configure["font"] == _guitheme.HEADING_FONT
        assert specs["Muted.TLabel"].configure["foreground"] == theme.palette.muted
        assert specs["Hint.TLabel"].configure["font"] == _guitheme.SMALL_FONT

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
        assert ("disabled", palette.surface) in accent["background"]

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_danger_button_is_distinct_in_every_theme(self, theme_name: str) -> None:
        theme = _guitheme.THEMES[theme_name]
        palette = theme.palette
        spec = _guitheme.ttk_style_specs(palette, theme.spacing, native=theme.native)["Danger.TButton"]
        # native 主题的系统按钮不响应 background，危险语义只能落在文字色上。
        expected_bg = palette.surface if theme.native else palette.danger
        expected_fg = palette.danger if theme.native else palette.on_accent
        assert spec.configure["background"] == expected_bg
        assert spec.configure["foreground"] == expected_fg
        assert ("active", palette.surface_active) in spec.map["background"]
        assert ("pressed", palette.surface_active) in spec.map["background"]
        assert ("disabled", palette.surface) in spec.map["background"]
        assert ("active", palette.danger) in spec.map["foreground"]
        assert ("disabled", palette.muted) in spec.map["foreground"]
        assert _contrast(expected_fg, expected_bg) >= 4.5

    def test_native_buttons_use_semantic_text_instead_of_fill(self) -> None:
        theme = _guitheme.THEMES["native"]
        palette = theme.palette
        specs = _guitheme.ttk_style_specs(palette, theme.spacing, native=True)
        accent = specs["Accent.TButton"]
        assert accent.configure["background"] == palette.surface
        assert accent.configure["foreground"] == palette.accent
        assert ("active", palette.accent_hover) in accent.map["foreground"]
        assert ("pressed", palette.accent_pressed) in accent.map["foreground"]
        assert ("disabled", palette.muted) in accent.map["foreground"]
        # 系统原生按钮背景近似 palette.surface，语义色文字必须与它可辨。
        assert _contrast(palette.accent, palette.surface) >= 4.5

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_scrollbar_thumb_is_visible_against_trough(self, theme_name: str) -> None:
        palette = _guitheme.THEMES[theme_name].palette
        spec = _guitheme.ttk_style_specs(palette)["TScrollbar"]
        assert spec.configure["background"] == palette.muted
        assert spec.configure["troughcolor"] == palette.background
        assert ("active", palette.accent) in spec.map["background"]
        assert _contrast(palette.muted, palette.background) >= 4.5

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

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_disabled_states_remain_legible(self, theme_name: str) -> None:
        palette = _guitheme.THEMES[theme_name].palette
        specs = _guitheme.ttk_style_specs(palette)
        assert ("disabled", palette.surface) in specs["Accent.TButton"].map["background"]
        assert ("disabled", palette.muted) in specs["Accent.TButton"].map["foreground"]
        assert _contrast(palette.muted, palette.surface) >= 4.5

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

    def test_canvas_colors_follow_theme(self) -> None:
        palette = _guitheme.PALETTE
        options = _guitheme.classic_options()
        assert options["*Canvas.background"] == palette.surface
        assert options["*Canvas.highlightBackground"] == palette.border
        assert options["*Canvas.highlightColor"] == palette.accent

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_classic_options_follow_each_theme(self, theme_name: str) -> None:
        theme = _guitheme.THEMES[theme_name]
        palette = theme.palette
        options = _guitheme.classic_options(palette)
        assert options["*Text.background"] == palette.log_bg
        assert options["*Text.foreground"] == palette.log_fg
        assert options["*Menu.background"] == palette.surface
        assert options["*Listbox.background"] == palette.surface
        assert options["*Entry.background"] == palette.surface
        assert options["*Canvas.background"] == palette.surface

    def test_follows_custom_palette(self) -> None:
        palette = replace(_guitheme.PALETTE, log_bg="#010203", text="#040506")
        options = _guitheme.classic_options(palette)
        assert options["*Text.background"] == "#010203"
        assert options["*Menu.foreground"] == "#040506"


# ============================================================================ #
# 四套主题对比度
# ============================================================================ #
class TestThemeContrast:
    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_text_and_muted_are_readable(self, theme_name: str) -> None:
        palette = _guitheme.THEMES[theme_name].palette
        assert _contrast(palette.text, palette.background) >= 7
        assert _contrast(palette.text, palette.surface) >= 7
        assert _contrast(palette.muted, palette.background) >= 4.5
        assert _contrast(palette.muted, palette.surface) >= 4.5

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_accent_selection_and_log_combinations(self, theme_name: str) -> None:
        palette = _guitheme.THEMES[theme_name].palette
        assert _contrast(palette.log_fg, palette.log_bg) >= 7
        assert _contrast(palette.on_accent, palette.accent) >= 4.5
        assert _contrast(palette.select_fg, palette.select_bg) >= 4.5

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_semantic_colors_are_visible(self, theme_name: str) -> None:
        palette = _guitheme.THEMES[theme_name].palette
        for color in (palette.success, palette.warning, palette.danger):
            assert _contrast(color, palette.log_bg) >= 4.5
            assert _contrast(color, palette.surface) >= 4.5

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_background_layers_are_distinct(self, theme_name: str) -> None:
        palette = _guitheme.THEMES[theme_name].palette
        layers = [palette.background, palette.surface, palette.surface_hover, palette.surface_active]
        assert len(set(layers)) == len(layers)

    def test_dark_theme_layers_are_dark(self) -> None:
        palette = _guitheme.THEMES["dark-blue"].palette
        layers = [palette.background, palette.surface, palette.surface_hover, palette.surface_active]
        assert all(_relative_luminance(color) < 0.1 for color in layers)

    def test_light_and_native_backgrounds_are_light(self) -> None:
        for name in ("light", "native"):
            assert _relative_luminance(_guitheme.THEMES[name].palette.background) > 0.5


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
    def _apply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        theme: str | _guitheme.Theme | None = _guitheme.DEFAULT_THEME_NAME,
        root: _FakeRoot | None = None,
        fail_theme: bool = False,
        **kwargs: Any,
    ) -> tuple[_guitheme.Theme, _FakeStyle, _FakeFontModule, _FakeRoot]:
        modules, style, fonts = _fake_modules(fail_theme=fail_theme)
        view = root or _FakeRoot()
        monkeypatch.setattr(_guitheme, "_load_tk_modules", lambda: modules)
        result = _guitheme.apply_theme(cast(Any, view), theme, **kwargs)
        return result, style, fonts, view

    def test_configures_theme_styles_and_classic_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result, style, fonts, root = self._apply(monkeypatch)

        assert result.name == "dark-blue"
        specs = _guitheme.ttk_style_specs()
        assert style.theme == _guitheme.THEME_NAME
        assert set(style.configured) == set(_guitheme.STYLE_NAMES)
        assert set(style.mapped) == {name for name, spec in specs.items() if spec.map}
        assert root.options == _guitheme.classic_options()
        assert root.configured == {"background": _guitheme.PALETTE.background}
        assert fonts.created[_guitheme.BODY_FONT].options["family"] == "Segoe UI"
        assert fonts.created[_guitheme.MONO_FONT].options["family"] == "Consolas"
        assert fonts.created[_guitheme.HEADING_FONT].options["weight"] == "bold"

    def test_keeps_created_font_objects_alive_on_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # tkinter 的 Font 对象被回收会删除具名字体，必须挂在 root 上持有引用。
        _result, _style, _fonts, root = self._apply(monkeypatch)
        kept = root.__dict__["_fcmd_named_fonts"]
        assert {font.options["family"] for font in kept} == {"Segoe UI", "Consolas"}

    @pytest.mark.parametrize("theme_name", _THEME_IDS)
    def test_applies_each_theme(self, monkeypatch: pytest.MonkeyPatch, theme_name: str) -> None:
        theme = _guitheme.THEMES[theme_name]
        _result, style, _fonts, root = self._apply(monkeypatch, theme=theme_name)
        assert style.theme == (None if theme.native else _guitheme.THEME_NAME)
        assert root.options == _guitheme.classic_options(theme.palette)
        assert root.configured == {"background": theme.palette.background}

    def test_native_keeps_system_ttk_theme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result, style, _fonts, _root = self._apply(monkeypatch, theme="native")
        assert result.native is True
        assert style.theme is None

    def test_native_buttons_configure_semantic_foreground(self, monkeypatch: pytest.MonkeyPatch) -> None:
        palette = _guitheme.THEMES["native"].palette
        _result, style, _fonts, _root = self._apply(monkeypatch, theme="native")
        assert style.configured["Accent.TButton"]["foreground"] == palette.accent
        assert style.configured["Accent.TButton"]["background"] == palette.surface
        assert style.configured["Danger.TButton"]["foreground"] == palette.danger
        assert style.configured["Danger.TButton"]["background"] == palette.surface

    def test_none_reads_persisted_theme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_guitheme, "load_theme", lambda: _guitheme.THEMES["light"])
        result, _style, _fonts, root = self._apply(monkeypatch, theme=None)
        assert result.name == "light"
        assert root.options == _guitheme.classic_options(_guitheme.THEMES["light"].palette)

    def test_explicit_overrides_are_applied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        palette = replace(_guitheme.PALETTE, accent="#123456")
        scale = _guitheme.FontScale(body=10)
        spacing = _guitheme.Spacing(md=14)
        result, _style, _fonts, _root = self._apply(
            monkeypatch,
            theme="light",
            palette=palette,
            scale=scale,
            spacing=spacing,
        )
        assert result.palette.accent == "#123456"
        assert result.fonts.body == 10
        assert result.spacing.md == 14

    def test_reuses_existing_named_fonts_and_survives_theme_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        names = tuple(spec.name for spec in _guitheme.font_plan())
        modules, style, fonts = _fake_modules(existing=names, fail_theme=True)
        monkeypatch.setattr(_guitheme, "_load_tk_modules", lambda: modules)

        _guitheme.apply_theme(cast(Any, _FakeRoot()), _guitheme.DEFAULT_THEME_NAME)

        assert fonts.created_calls == 0
        assert fonts.configure_calls == len(names)
        assert fonts.created["TkDefaultFont"].options["family"] == "Segoe UI"
        assert style.theme is None
        assert set(style.configured) == set(_guitheme.STYLE_NAMES)

    def test_survives_root_configure_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        modules, _style, _fonts = _fake_modules()
        root = _FakeRoot(fail_configure=True)
        monkeypatch.setattr(_guitheme, "_load_tk_modules", lambda: modules)

        _guitheme.apply_theme(cast(Any, root), _guitheme.DEFAULT_THEME_NAME)

        assert root.options == _guitheme.classic_options()

    def test_load_tk_modules_real_tkinter(self) -> None:
        pytest.importorskip("tkinter")
        modules = _guitheme._load_tk_modules()
        assert hasattr(modules.ttk, "Style")
        assert hasattr(modules.tkfont, "families")
