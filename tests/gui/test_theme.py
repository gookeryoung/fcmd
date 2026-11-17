"""``fcmd.gui.theme`` 的纯逻辑测试：主题注册表、解析 / 持久化、字体与亮度。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.gui import theme as _guitheme


def test_theme_names_follow_registry_order() -> None:
    assert _guitheme.theme_names() == ("dark-blue", "light", "high-contrast", "native")


def test_builtin_themes_have_complete_palettes() -> None:
    for name in _guitheme.theme_names():
        theme = _guitheme.resolve_theme(name)
        assert theme.name == name
        assert _guitheme.missing_palette_tokens(theme.palette) == ()


def test_aliases_and_unknown_names_fall_back() -> None:
    assert _guitheme.resolve_theme("dark").name == "dark-blue"
    assert _guitheme.resolve_theme("default").name == "dark-blue"
    assert _guitheme.resolve_theme("contrast").name == "high-contrast"
    assert _guitheme.resolve_theme("nope").name == _guitheme.DEFAULT_THEME_NAME
    assert _guitheme.resolve_theme(None).name == _guitheme.DEFAULT_THEME_NAME


def test_theme_config_path_uses_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert _guitheme.theme_config_path() == tmp_path / "ui" / "theme.json"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    saved = _guitheme.save_theme("light", path)
    assert saved.name == "light"
    assert _guitheme.load_theme(path).name == "light"
    assert _guitheme.read_theme_name(path) == "light"


def test_invalid_saved_theme_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "theme.json"
    path.write_text('{"theme": "missing"}', encoding="utf-8")
    assert _guitheme.load_theme(path).name == _guitheme.DEFAULT_THEME_NAME
    path.write_text("{broken", encoding="utf-8")
    assert _guitheme.load_theme(path).name == _guitheme.DEFAULT_THEME_NAME


def test_save_theme_normalizes_alias(tmp_path: Path) -> None:
    saved = _guitheme.save_theme("dark", tmp_path / "theme.json")
    assert saved.name == "dark-blue"


def test_pick_font_family_prefers_candidates() -> None:
    available = ["Noto Sans CJK SC", "DejaVu Sans"]
    assert _guitheme.pick_font_family(available) == "Noto Sans CJK SC"
    assert _guitheme.pick_font_family([]) == _guitheme.UI_FONT_CANDIDATES[0]
    assert _guitheme.pick_font_family(["CONSOLAS"], _guitheme.MONO_FONT_CANDIDATES) == "CONSOLAS"


def test_font_renders_cjk_uses_measurement() -> None:
    assert _guitheme.font_renders_cjk(len) is True
    assert _guitheme.font_renders_cjk(lambda _text: 0) is False


def test_relative_luminance_and_darkness() -> None:
    assert _guitheme.relative_luminance("#ffffff") == pytest.approx(1.0)
    assert _guitheme.relative_luminance("#000000") == pytest.approx(0.0)
    with pytest.raises(ValueError):
        _guitheme.relative_luminance("white")
    assert _guitheme.is_dark_palette(_guitheme.THEMES["dark-blue"].palette) is True
    assert _guitheme.is_dark_palette(_guitheme.THEMES["light"].palette) is False
