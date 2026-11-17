"""``fcmd.gui.qt.theme`` 的纯逻辑测试：QSS 生成、字体挑选与模板变量。

这些函数不导入 ``PySide6``，CI（无 Qt 环境）也能跑；控件类与真实渲染
留给离屏冒烟测试（``test_qt_window_smoke.py``）。
"""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme


@pytest.mark.parametrize("name", list(_guitheme.THEMES))
def test_stylesheet_substitutes_every_palette_token(name: str) -> None:
    theme = _guitheme.resolve_theme(name)
    text = _qt_theme.stylesheet(theme)
    assert "$" not in text
    # ``cursor`` 只用于 QPalette / 文本光标，QSS 没有对应属性
    for item in fields(theme.palette):
        if item.name == "cursor":
            continue
        assert getattr(theme.palette, item.name) in text


def test_stylesheet_contains_expected_selectors() -> None:
    theme = _guitheme.resolve_theme("dark-blue")
    text = _qt_theme.stylesheet(theme)
    for selector in (
        "QFrame#Card",
        "QLabel#CardTitle",
        "QPushButton#Primary",
        "QPushButton#Danger",
        "QPushButton#NavButton",
        "QToolButton#SidebarToggle",
    ):
        assert selector in text
    assert f"{theme.fonts.body}pt" in text


def test_template_tokens_include_fonts_and_radius() -> None:
    theme = _guitheme.resolve_theme("light")
    tokens = _qt_theme.template_tokens(theme, font_family="Fake Font", mono_family="Fake Mono")
    assert tokens["font_family"] == "Fake Font"
    assert tokens["mono_family"] == "Fake Mono"
    assert tokens["font_body"] == str(theme.fonts.body)
    assert tokens["radius"] == "8"
    assert tokens["background"] == theme.palette.background


def test_template_tokens_fall_back_to_candidate_family() -> None:
    theme = _guitheme.resolve_theme("dark-blue")
    tokens = _qt_theme.template_tokens(theme)
    assert tokens["font_family"] == _qt_theme.UI_FONT_CANDIDATES[0]
    assert tokens["mono_family"] == _qt_theme.MONO_FONT_CANDIDATES[0]


def test_template_tokens_derive_band_soft_colors() -> None:
    """评分档位的浅色底由调色板派生，保证每套主题下都是同一色系的淡色。"""
    theme = _guitheme.resolve_theme("dark-blue")
    tokens = _qt_theme.template_tokens(theme)
    for name in ("accent", "success", "warning", "danger"):
        color = getattr(theme.palette, name).lstrip("#")
        red, green, blue = (int(color[index : index + 2], 16) for index in (0, 2, 4))
        assert tokens[f"{name}_soft"] == f"rgba({red}, {green}, {blue}, 16%)"


def test_tiered_value_html_uses_theme_fonts_and_escapes() -> None:
    """分层主值取标题字号加粗、说明取次要字号与 muted 色，并转义特殊字符。"""
    theme = _guitheme.resolve_theme("dark-blue")
    html_text = _qt_theme.tiered_value_html(theme, "NVIDIA RTX <X>", "24 GB 显存")
    assert f"font-size:{theme.fonts.heading}pt; font-weight:600;" in html_text
    assert f"font-size:{theme.fonts.small}pt" in html_text
    assert f"color:{theme.palette.muted}" in html_text
    assert "NVIDIA RTX &lt;X&gt;" in html_text
    assert "24 GB 显存" in html_text
    assert _qt_theme.tiered_value_html(theme, "64 GB") == (
        f'<span style="font-size:{theme.fonts.heading}pt; font-weight:600;">64 GB</span>'
    )


def test_control_height_follows_font_metrics() -> None:
    """统一控件高度按字号换算成逻辑像素，字号变大时同比变大（不写死物理像素）。"""
    theme = _guitheme.resolve_theme("dark-blue")
    baseline = _qt_theme.control_height(theme)
    assert baseline == round(theme.fonts.body * 96 / 72) + _qt_theme.CONTROL_HEIGHT_EXTRA
    bigger = replace(theme, fonts=_guitheme.FontScale(body=theme.fonts.body + 3))
    assert _qt_theme.control_height(bigger) > baseline


def test_template_tokens_accept_measured_control_height() -> None:
    """运行时用 ``QFontMetrics`` 实测高度覆盖估算值，样式表与字体实际行高对齐。"""
    theme = _guitheme.resolve_theme("dark-blue")
    assert _qt_theme.template_tokens(theme)["control_height"] == f"{_qt_theme.control_height(theme)}px"
    assert _qt_theme.template_tokens(theme, control_height_px=30)["control_height"] == "30px"
    text = _qt_theme.stylesheet(theme, control_height_px=30)
    button_block = text.split("QPushButton {", 1)[1].split("}", 1)[0]
    input_block = text.split("QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {", 1)[1].split("}", 1)[0]
    for block in (button_block, input_block):
        assert "min-height: 30px" in block
        assert "max-height: 30px" in block


def test_pick_font_family_prefers_available_candidate_case_insensitively() -> None:
    available = {"consolas", "Microsoft YaHei UI"}
    assert _qt_theme.pick_font_family(available) == "Microsoft YaHei UI"
    assert _qt_theme.pick_font_family({"segoe ui"}) == "segoe ui"


def test_pick_font_family_falls_back_to_first_candidate() -> None:
    assert _qt_theme.pick_font_family(set()) == _qt_theme.UI_FONT_CANDIDATES[0]
    assert _qt_theme.pick_font_family(set(), ("Mono",)) == "Mono"


def test_theme_names_matches_tk_registry() -> None:
    assert _qt_theme.theme_names() == _guitheme.theme_names()


def test_load_and_save_share_tk_settings_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    saved = _qt_theme.save_theme("light")
    assert saved.name == "light"
    assert _qt_theme.load_theme().name == "light"
    assert (tmp_path / "ui" / "theme.json").is_file()


def test_card_uses_plain_frame_and_title_label() -> None:
    """卡片标题必须是普通 QLabel：QGroupBox::title 的背景 / 定位在 QSS 里不可控。"""
    text = _qt_theme.stylesheet(_guitheme.resolve_theme("dark-blue"))
    assert "QGroupBox#Card" not in text
    card_block = text.split("QFrame#Card {", 1)[1].split("}", 1)[0]
    assert "border-radius" in card_block
    title_block = text.split("QLabel#CardTitle {", 1)[1].split("}", 1)[0]
    assert "font-weight: 600" in title_block
    assert "background" not in title_block


def test_tab_pane_and_nested_tables_are_flat() -> None:
    """页签内容区与卡片内表格不再叠加边框：外层留给 Card / 行卡片表达层次。"""
    text = _qt_theme.stylesheet(_guitheme.resolve_theme("dark-blue"))
    pane_block = text.split("QTabWidget::pane {", 1)[1].split("}", 1)[0]
    assert "border: none" in pane_block
    assert "background: transparent" in pane_block
    for selector in (
        "QFrame#Card QTableView",
        "QFrame#Card QTreeView",
        "QFrame#Card QListView",
        "QFrame#Card QTreeWidget",
        "QFrame#Card QTableWidget",
    ):
        assert selector in text
    nested_block = text.split("QFrame#Card QTreeWidget, QFrame#Card QTableWidget {", 1)[1].split("}", 1)[0]
    assert "border: none" in nested_block
    assert "background: transparent" in nested_block


def test_installed_and_converted_buttons_use_success_color() -> None:
    theme = _guitheme.resolve_theme("dark-blue")
    text = _qt_theme.stylesheet(theme)
    block = text.split("QPushButton#InstalledButton, QPushButton#ConvertedButton {", 1)[1].split("}", 1)[0]
    assert f"color: {theme.palette.success}" in block
    assert f"border: 1px solid {theme.palette.success}" in block
    tag_block = text.split("QLabel#QuantTag {", 1)[1].split("}", 1)[0]
    assert f"color: {theme.palette.accent}" in tag_block
    assert f"background: {theme.palette.accent}" not in tag_block


def test_mode_choice_tone_colors_are_themed() -> None:
    theme = _guitheme.resolve_theme("dark-blue")
    text = _qt_theme.stylesheet(theme)
    for tone, color in (
        ("danger", theme.palette.danger),
        ("accent", theme.palette.accent),
        ("success", theme.palette.success),
        ("warning", theme.palette.warning),
    ):
        block = text.split(f'QPushButton#ChoiceCard[tone="{tone}"] {{', 1)[1].split("}", 1)[0]
        assert f"border-color: {color}" in block


def test_secondary_buttons_keep_visible_border() -> None:
    """次要（Ghost）按钮也要有可见边框，否则在卡片里看起来像纯文字标签。"""
    text = _qt_theme.stylesheet(_guitheme.resolve_theme("dark-blue"))
    ghost_block = text.split("QPushButton#Ghost {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid" in ghost_block
    hover_block = text.split("QPushButton#Ghost:hover {", 1)[1].split("}", 1)[0]
    assert "border-color" in hover_block
    assert "border-color: transparent" not in text
