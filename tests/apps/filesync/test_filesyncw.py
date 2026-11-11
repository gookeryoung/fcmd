"""filesyncw 测试：设置解析 / 持久化、模式标签转换、geometry 与启动入口。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.filesync import filesyncw
from fcmd.apps.filesync._core import COMPARE_CONTENT, COMPARE_TIME_SIZE, SYNC_MIRROR, SYNC_TWO_WAY
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.gui import window as _guiwindow


def test_tool_registration() -> None:
    """filesyncw 仅注册一个无子命令的 GUI 入口。"""
    assert set(_TOOL_REGISTRY["filesyncw"]) == {None}


def test_config_path_uses_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert filesyncw.config_path() == tmp_path / "ui" / "filesyncw.json"


def test_config_path_defaults_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FCMD_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows 的 Path.home() 走 USERPROFILE
    assert filesyncw.config_path() == tmp_path / ".fcmd" / "ui" / "filesyncw.json"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = filesyncw.FilesyncSettings(
        left="L",
        right="R",
        compare_mode=COMPARE_CONTENT,
        sync_mode=SYNC_MIRROR,
        include="*.py;*.md",
        exclude="tmp",
        skip_hidden=True,
        show_equal=True,
        geometry="1000x700+10+20",
    )
    filesyncw.save_settings(settings, path)
    assert filesyncw.load_settings(path) == settings


def test_load_missing_or_invalid_returns_defaults(tmp_path: Path) -> None:
    assert filesyncw.load_settings(tmp_path / "missing.json") == filesyncw.FilesyncSettings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert filesyncw.load_settings(broken) == filesyncw.FilesyncSettings()
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    assert filesyncw.load_settings(array) == filesyncw.FilesyncSettings()


def test_settings_from_mapping_tolerates_bad_values() -> None:
    raw: dict[str, object] = {
        "left": 123,
        "right": None,
        "compare_mode": "unknown",
        "sync_mode": "unknown",
        "include": "*.py",
        "exclude": ["bad"],
        "skip_hidden": "yes",
        "show_equal": True,
        "geometry": "not-a-geometry",
    }
    settings = filesyncw.settings_from_mapping(raw)
    assert settings.left == ""
    assert settings.right == ""
    assert settings.compare_mode == COMPARE_TIME_SIZE
    assert settings.sync_mode == SYNC_TWO_WAY
    assert settings.include == "*.py"
    assert settings.exclude == ""
    assert settings.skip_hidden is False
    assert settings.show_equal is True
    assert settings.geometry is None


def test_settings_from_mapping_keeps_base_and_normalizes_modes() -> None:
    base = filesyncw.FilesyncSettings(left="old", right="old-r", geometry="800x600")
    settings = filesyncw.settings_from_mapping(
        {"left": "new", "compare_mode": " CONTENT ", "sync_mode": " Mirror ", "geometry": "900x650+1+2"},
        base,
    )
    assert settings.left == "new"
    assert settings.right == "old-r"
    assert settings.compare_mode == COMPARE_CONTENT
    assert settings.sync_mode == SYNC_MIRROR
    assert settings.geometry == "900x650+1+2"


def test_settings_mapping_roundtrip() -> None:
    settings = filesyncw.FilesyncSettings(left="L", right="R", skip_hidden=True)
    mapping = filesyncw.settings_to_mapping(settings)
    assert mapping["left"] == "L"
    assert mapping["skip_hidden"] is True
    assert filesyncw.settings_from_mapping(mapping) == settings


def test_mode_label_and_value() -> None:
    labels = {COMPARE_TIME_SIZE: "时间与大小", COMPARE_CONTENT: "内容"}
    assert filesyncw.mode_label(COMPARE_CONTENT, labels) == "内容"
    assert filesyncw.mode_label("unknown", labels) == "时间与大小"
    assert filesyncw.mode_value("内容", labels, COMPARE_TIME_SIZE) == COMPARE_CONTENT
    assert filesyncw.mode_value(COMPARE_CONTENT, labels, COMPARE_TIME_SIZE) == COMPARE_CONTENT
    assert filesyncw.mode_value("未知", labels, COMPARE_TIME_SIZE) == COMPARE_TIME_SIZE


def test_valid_geometry_alias() -> None:
    assert filesyncw.valid_geometry("100x200") == "100x200"
    assert filesyncw.valid_geometry("bad") is None


def test_resolve_geometry_restores_saved() -> None:
    assert filesyncw.resolve_geometry("1000x700+10+20", 1920, 1080) == "1000x700+10+20"


def test_resolve_geometry_clamps_and_falls_back() -> None:
    assert filesyncw.resolve_geometry("5000x5000+9999+9999", 1920, 1080) == "1920x1080+0+0"
    assert filesyncw.resolve_geometry("garbage", 1920, 1080) == filesyncw.DEFAULT_GEOMETRY
    assert filesyncw.resolve_geometry(None, 1920, 1080) == filesyncw.DEFAULT_GEOMETRY


def test_run_gui_delegates_to_shared_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run_window(factory: object, message: str) -> None:
        recorded["factory"] = factory
        recorded["message"] = message

    monkeypatch.setattr(_guiwindow, "run_window", fake_run_window)
    filesyncw.run_gui(Path("custom.json"))
    assert callable(recorded["factory"])
    assert recorded["message"] == "文件夹比较与同步"


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(filesyncw, "run_gui", lambda: called.append(True))
    filesyncw.launch_cmd()
    assert called == [True]
