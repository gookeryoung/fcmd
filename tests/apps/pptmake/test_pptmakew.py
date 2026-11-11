"""pptmakew 测试：设置解析 / 持久化、文件名清洗、geometry 与启动入口。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import pptmakew
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.gui import window as _guiwindow


def test_tool_registration() -> None:
    assert set(_TOOL_REGISTRY["pptmakew"]) == {None}


def test_config_path_uses_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert pptmakew.config_path() == tmp_path / "ui" / "pptmakew.json"


def test_config_path_defaults_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FCMD_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert pptmakew.config_path() == tmp_path / ".fcmd" / "ui" / "pptmakew.json"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = pptmakew.PptmakeSettings(
        style=core.STYLE_GRAPHITE,
        aspect=core.ASPECT_4_3,
        decor=core.DECOR_FRAME,
        last_dir=str(tmp_path),
        geometry="1200x760+10+20",
    )
    pptmakew.save_settings(settings, path)
    assert pptmakew.load_settings(path) == settings


def test_load_missing_or_invalid_returns_defaults(tmp_path: Path) -> None:
    assert pptmakew.load_settings(tmp_path / "missing.json") == pptmakew.PptmakeSettings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert pptmakew.load_settings(broken) == pptmakew.PptmakeSettings()
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    assert pptmakew.load_settings(array) == pptmakew.PptmakeSettings()


def test_settings_from_mapping_tolerates_bad_values() -> None:
    raw: dict[str, object] = {
        "style": "neon",
        "aspect": "21:9",
        "decor": "unknown",
        "last_dir": 123,
        "geometry": "not-a-geometry",
    }
    settings = pptmakew.settings_from_mapping(raw)
    assert settings.style == core.STYLE_AURORA
    assert settings.aspect == core.ASPECT_16_9
    assert settings.decor == core.DECOR_ORBS
    assert settings.last_dir == ""
    assert settings.geometry is None


def test_settings_from_mapping_keeps_base() -> None:
    base = pptmakew.PptmakeSettings(style=core.STYLE_CORAL, aspect=core.ASPECT_4_3, last_dir="old")
    settings = pptmakew.settings_from_mapping(
        {"style": " PORCELAIN ", "aspect": "16:9", "decor": "minimal", "geometry": "1200x760+1+2"},
        base,
    )
    assert settings.style == core.STYLE_PORCELAIN
    assert settings.aspect == core.ASPECT_16_9
    assert settings.decor == core.DECOR_MINIMAL
    assert settings.last_dir == "old"
    assert settings.geometry == "1200x760+1+2"


def test_settings_mapping_roundtrip() -> None:
    settings = pptmakew.PptmakeSettings(style=core.STYLE_CORAL, last_dir="D:/slides")
    mapping = pptmakew.settings_to_mapping(settings)
    assert mapping["style"] == core.STYLE_CORAL
    assert mapping["last_dir"] == "D:/slides"
    assert pptmakew.settings_from_mapping(mapping) == settings


def test_safe_filename() -> None:
    assert pptmakew.safe_filename("品牌:模板/2026") == "品牌_模板_2026"
    assert pptmakew.safe_filename("   ") == "template"
    assert pptmakew.safe_filename("...") == "template"


def test_slide_label() -> None:
    assert pptmakew.slide_label(1, core.SlideSpec(layout=core.LAYOUT_COVER, title="短标题")) == "01  封面  短标题"
    long_label = pptmakew.slide_label(
        2,
        core.SlideSpec(layout=core.LAYOUT_BULLETS, title="这是一个非常非常长的页面标题需要被截断"),
    )
    assert long_label.startswith("02  要点  ")
    assert long_label.endswith("…")
    assert pptmakew.slide_label(3, core.SlideSpec(layout=core.LAYOUT_QUOTE)) == "03  引用  （未命名）"


def test_option_conversions() -> None:
    assert pptmakew._option_value(pptmakew.STYLE_OPTIONS, "", core.STYLE_AURORA) == core.STYLE_AURORA
    assert (
        pptmakew._option_value(pptmakew.STYLE_OPTIONS, core.STYLE_TEXT[core.STYLE_CORAL], core.STYLE_AURORA)
        == core.STYLE_CORAL
    )
    assert pptmakew._option_value(pptmakew.STYLE_OPTIONS, core.STYLE_CORAL, core.STYLE_AURORA) == core.STYLE_CORAL
    assert (
        pptmakew._option_label(pptmakew.ASPECT_OPTIONS, core.ASPECT_4_3, "fallback")
        == core.ASPECT_TEXT[core.ASPECT_4_3]
    )
    assert pptmakew._option_label(pptmakew.ASPECT_OPTIONS, "unknown", "fallback") == "fallback"


def test_resolve_geometry() -> None:
    assert pptmakew.resolve_geometry("1200x760+10+20", 1920, 1080) == "1200x760+10+20"
    assert pptmakew.resolve_geometry("5000x5000+9999+9999", 1920, 1080) == "1920x1080+0+0"
    assert pptmakew.resolve_geometry("garbage", 1920, 1080) == pptmakew.DEFAULT_GEOMETRY
    assert pptmakew.resolve_geometry(None, 1920, 1080) == pptmakew.DEFAULT_GEOMETRY


def test_run_gui_delegates_to_shared_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run_window(factory: object, message: str) -> None:
        recorded["factory"] = factory
        recorded["message"] = message

    monkeypatch.setattr(_guiwindow, "run_window", fake_run_window)
    pptmakew.run_gui(Path("custom.json"))
    assert callable(recorded["factory"])
    assert recorded["message"] == "PPT 模板设计器"


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(pptmakew, "run_gui", lambda: called.append(True))
    pptmakew.launch_cmd()
    assert called == [True]
