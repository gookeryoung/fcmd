"""pptmakew 测试：设置解析 / 持久化、文件名清洗、geometry 与启动入口。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import pptmakew
from fcmd.core.toolkit import _TOOL_REGISTRY


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
        "sidebar_collapsed": "yes",
        "active_page": "missing",
    }
    settings = pptmakew.settings_from_mapping(raw)
    assert settings.style == core.STYLE_AURORA
    assert settings.aspect == core.ASPECT_16_9
    assert settings.decor == core.DECOR_ORBS
    assert settings.last_dir == ""
    assert settings.geometry is None
    assert settings.sidebar_collapsed is False
    assert settings.active_page == pptmakew.PAGE_KEYS[0]


def test_settings_parse_sidebar_page() -> None:
    settings = pptmakew.settings_from_mapping(
        {
            "sidebar_collapsed": True,
            "active_page": "style",
            "design_splitter": [240, 600, 320],
            "style_splitter": [360, 720],
        }
    )
    assert settings.sidebar_collapsed is True
    assert settings.active_page == "style"
    assert settings.design_splitter == (240, 600, 320)
    assert settings.style_splitter == (360, 720)


def test_settings_splitter_tolerates_bad_values() -> None:
    settings = pptmakew.settings_from_mapping(
        {"design_splitter": [0, -1], "style_splitter": "wide"},
        pptmakew.PptmakeSettings(design_splitter=(250, 600, 300)),
    )
    assert settings.design_splitter == (250, 600, 300)
    assert settings.style_splitter == ()


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
    settings = pptmakew.PptmakeSettings(
        style=core.STYLE_CORAL,
        last_dir="D:/slides",
        sidebar_collapsed=True,
        active_page="style",
        design_splitter=(240, 600, 320),
        style_splitter=(360, 720),
    )
    mapping = pptmakew.settings_to_mapping(settings)
    assert mapping["style"] == core.STYLE_CORAL
    assert mapping["last_dir"] == "D:/slides"
    assert mapping["sidebar_collapsed"] is True
    assert mapping["active_page"] == "style"
    assert mapping["design_splitter"] == [240, 600, 320]
    assert mapping["style_splitter"] == [360, 720]
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


def test_run_gui_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 Qt 前端时提示安装并退出。"""
    monkeypatch.setitem(sys.modules, "fcmd.apps.pptmake._pptmakew_qt", None)
    with pytest.raises(SystemExit, match="Qt"):
        pptmakew.run_gui()


def test_run_gui_delegates_to_qt_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常路径把启动交给 Qt 入口，并转发自定义设置路径。"""
    calls: list[Path | None] = []
    fake = types.ModuleType("fcmd.apps.pptmake._pptmakew_qt")
    fake.run_qt = lambda settings_path=None: calls.append(settings_path)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcmd.apps.pptmake._pptmakew_qt", fake)
    pptmakew.run_gui(Path("custom.json"))
    assert calls == [Path("custom.json")]


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(pptmakew, "run_gui", lambda: called.append(True))
    pptmakew.launch_cmd()
    assert called == [True]
