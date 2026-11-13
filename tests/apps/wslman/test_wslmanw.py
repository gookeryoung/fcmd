"""wslmanw（P5）测试：设置往返、窗口几何、确认开关、列表/详情口径与启动入口。

真实 Tk 窗口不在单测范围内（窗口类标 ``pragma: no cover``）；这里只覆盖
纯函数与入口接线，保证 GUI 与 CLI 共用同一套口径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.wslman import wslmanw
from fcmd.apps.wslman._distro import Distro
from fcmd.apps.wslman._errors import not_found
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.gui import window as _guiwindow


def _distro(**overrides: object) -> Distro:
    base: dict[str, object] = {
        "name": "Ubuntu",
        "state": "running",
        "state_raw": "Running",
        "version": 2,
        "default": True,
        "base_path": r"C:\WSL\Ubuntu",
        "default_uid": 1000,
        "registered": True,
    }
    base.update(overrides)
    return Distro(**base)  # type: ignore[arg-type]


def test_tool_registration() -> None:
    """wslmanw 是独立工具，只注册一个无子命令的 GUI 入口。"""
    assert set(_TOOL_REGISTRY["wslmanw"]) == {None}


def test_config_path_uses_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert wslmanw.config_path() == tmp_path / "ui" / "wslmanw.json"


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    settings = wslmanw.WslmanwSettings(
        geometry="1100x720+20+30",
        download_dir=r"D:\WSL\downloads",
        manifest_url="https://example.com/manifest.json",
        download_source="manifest",
    )
    wslmanw.save_settings(settings, path)
    assert wslmanw.load_settings(path) == settings


def test_load_missing_or_broken_returns_defaults(tmp_path: Path) -> None:
    assert wslmanw.load_settings(tmp_path / "missing.json") == wslmanw.WslmanwSettings()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert wslmanw.load_settings(broken) == wslmanw.WslmanwSettings()


def test_settings_from_mapping_tolerates_bad_values() -> None:
    raw: dict[str, object] = {
        "geometry": "not-a-geometry",
        "download_dir": 42,
        "manifest_url": None,
        "download_source": "ftp",
    }
    settings = wslmanw.settings_from_mapping(raw)
    assert settings == wslmanw.WslmanwSettings(geometry=None, download_source="auto")

    assert wslmanw.settings_from_mapping({"download_source": "file"}).download_source == "file"


def test_settings_mapping_is_json_friendly() -> None:
    settings = wslmanw.WslmanwSettings(geometry="900x600", download_source="cli")
    assert wslmanw.settings_to_mapping(settings) == {
        "geometry": "900x600",
        "download_dir": "",
        "manifest_url": "",
        "download_source": "cli",
    }


def test_resolve_geometry_clamps_to_screen_and_minimum() -> None:
    assert wslmanw.resolve_geometry(None, 1920, 1080) == wslmanw.DEFAULT_GEOMETRY
    assert wslmanw.resolve_geometry("600x400+0+0", 1920, 1080) == "900x600+0+0"
    assert wslmanw.resolve_geometry("1400x900+5000+5000", 1920, 1080) == "1400x900+520+180"


def test_unregister_confirm_requires_exact_typed_name() -> None:
    assert wslmanw.unregister_confirm_enabled("Ubuntu", "Ubuntu") is True
    assert wslmanw.unregister_confirm_enabled("  ubuntu  ", "Ubuntu") is True
    assert wslmanw.unregister_confirm_enabled("Ubunt", "Ubuntu") is False
    assert wslmanw.unregister_confirm_enabled("", "Ubuntu") is False
    assert wslmanw.unregister_confirm_enabled("Ubuntu", "") is False


def test_shutdown_confirm_requires_checkbox() -> None:
    assert wslmanw.shutdown_confirm_enabled(True) is True
    assert wslmanw.shutdown_confirm_enabled(False) is False


def test_distro_row_and_detail_lines() -> None:
    distro = _distro()
    assert wslmanw.distro_row(distro, 2048, False) == ("Ubuntu", "运行中", "2", "2.0 KiB", "是")
    assert wslmanw.distro_row(_distro(default=False, version=None), None, False)[3:] == ("未知", "")

    lines = wslmanw.detail_lines(distro, None, False)
    assert "名称: Ubuntu" in lines
    assert "默认: 是" in lines
    assert r"虚拟磁盘: C:\WSL\Ubuntu\ext4.vhdx" in lines
    assert "默认用户: 1000" in lines


def test_detail_lines_handles_missing_registry_data() -> None:
    lines = wslmanw.detail_lines(_distro(base_path=None, default_uid=None, registered=False), 10, True)
    assert "BasePath: 未知" in lines
    assert "虚拟磁盘: 未知" in lines
    assert "默认用户: 未知" in lines
    assert "来源: wsl --list" in lines
    assert "≈" in next(line for line in lines if line.startswith("占用:"))


def test_error_text_formats_wslman_errors() -> None:
    assert wslmanw._error_text(not_found("没有", hint="先安装")) == "没有（先安装）"
    assert wslmanw._error_text(not_found("没有")) == "没有"
    assert wslmanw._error_text(RuntimeError("boom")) == "RuntimeError: boom"


def test_run_gui_delegates_to_shared_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run_window(factory: object, message: str) -> None:
        recorded["factory"] = factory
        recorded["message"] = message

    monkeypatch.setattr(_guiwindow, "run_window", fake_run_window)
    wslmanw.run_gui()
    assert recorded["factory"] is wslmanw.WslmanWindow
    assert recorded["message"] == "WSL 管理"


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(wslmanw, "run_gui", lambda: called.append(True))
    wslmanw.launch_cmd()
    assert called == [True]
