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
    }
    assert wslmanw.settings_from_mapping(raw) == wslmanw.WslmanwSettings(geometry=None)


def test_removed_fields_are_ignored() -> None:
    """旧版写过 auto_refresh / poll_interval_ms / manifest_url / download_source：按未知字段忽略。"""
    settings = wslmanw.settings_from_mapping(
        {
            "geometry": "900x600",
            "auto_refresh": False,
            "poll_interval_ms": 10000,
            "manifest_url": "https://example.com/manifest.json",
            "download_source": "cli",
        }
    )
    assert settings == wslmanw.WslmanwSettings(geometry="900x600")
    mapping = wslmanw.settings_to_mapping(settings)
    for field in ("auto_refresh", "poll_interval_ms", "manifest_url", "download_source"):
        assert field not in mapping


def test_old_settings_file_without_new_fields_still_loads(tmp_path: Path) -> None:
    """旧版设置文件（只有 geometry）与含未知字段的文件都要能读。"""
    path = tmp_path / "old.json"
    path.write_text('{"geometry": "1024x700+10+10", "legacy_field": true}', encoding="utf-8")
    settings = wslmanw.load_settings(path)
    assert settings.geometry == "1024x700+10+10"
    assert settings.download_dir == ""


def test_settings_mapping_is_json_friendly() -> None:
    settings = wslmanw.WslmanwSettings(geometry="900x600", download_dir=r"D:\WSL")
    assert wslmanw.settings_to_mapping(settings) == {
        "geometry": "900x600",
        "download_dir": r"D:\WSL",
    }


def test_poll_interval_is_fixed_three_seconds() -> None:
    """轮询间隔是模块级常量，不提供开关或设置项。"""
    assert wslmanw.POLL_INTERVAL_MS == 3000
    assert not hasattr(wslmanw.WslmanwSettings(), "auto_refresh")
    assert not hasattr(wslmanw.WslmanwSettings(), "poll_interval_ms")


def test_image_rows_and_details() -> None:
    entry = wslmanw._images.ImageEntry(
        name="Ubuntu",
        friendly_name="Ubuntu",
        sources=("cli", "manifest"),
        downloads=(
            wslmanw._images.ImageDownload("x86_64", "https://e/ubuntu.wsl", "a" * 64),
            wslmanw._images.ImageDownload("aarch64", "https://e/ubuntu-arm.wsl"),
        ),
        installed=True,
    )
    assert wslmanw.image_row(entry) == ("Ubuntu", "Ubuntu", "aarch64/x86_64", "sha256 · 已安装")
    details = dict(wslmanw.image_details(entry))
    assert details["下载来源"] == "CLI 安装（wsl --install）、官方清单 URL"
    assert details["校验和"] == "有 sha256"
    assert "x86_64: https://e/ubuntu.wsl（有 sha256）" in details["下载地址"]
    assert "无 sha256" in details["下载地址"]

    store = wslmanw._images.ImageEntry(name="OracleLinux_9_5", friendly_name="", sources=("store",))
    assert wslmanw.image_row(store) == ("OracleLinux_9_5", "-", "任意", "")
    assert dict(wslmanw.image_details(store))["下载地址"].startswith("无直接下载地址")


def test_pick_download_prefers_matching_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = wslmanw._images.ImageEntry(
        name="Ubuntu",
        friendly_name="Ubuntu",
        sources=("manifest",),
        downloads=(
            wslmanw._images.ImageDownload("x86_64", "https://e/amd64.wsl"),
            wslmanw._images.ImageDownload("aarch64", "https://e/arm64.wsl"),
        ),
    )
    monkeypatch.setattr(wslmanw.platform, "machine", lambda: "AMD64")
    assert wslmanw.pick_download(entry).arch == "x86_64"
    monkeypatch.setattr(wslmanw.platform, "machine", lambda: "ARM64")
    assert wslmanw.pick_download(entry).arch == "aarch64"

    only_arm = wslmanw._images.ImageEntry(
        name="X", friendly_name="", sources=("manifest",), downloads=(entry.downloads[1],)
    )
    monkeypatch.setattr(wslmanw.platform, "machine", lambda: "AMD64")
    assert wslmanw.pick_download(only_arm).arch == "aarch64"


def test_image_filename_prefers_official_basename() -> None:
    download = wslmanw._images.ImageDownload("x86_64", "https://e/releases/Ubuntu-24.04.wsl")
    assert wslmanw.image_filename("Ubuntu-24.04", download) == "Ubuntu-24.04.wsl"

    no_name = wslmanw._images.ImageDownload("aarch64", "https://aka.ms/wslubuntu2404arm64")
    assert wslmanw.image_filename("Ubuntu-24.04", no_name) == "Ubuntu-24.04-aarch64.tar"


def test_action_states_follow_selected_state() -> None:
    """运行中只能停止；已停止只能启动；未选中两个都禁用。"""
    assert wslmanw.action_states("running") == (False, True)
    assert wslmanw.action_states("stopped") == (True, False)
    assert wslmanw.action_states("unknown") == (True, False)
    assert wslmanw.action_states(None) == (False, False)


def test_resolve_geometry_clamps_to_screen_and_minimum() -> None:
    assert wslmanw.resolve_geometry(None, 1920, 1080) == wslmanw.DEFAULT_GEOMETRY
    assert wslmanw.resolve_geometry("600x400+0+0", 1920, 1080) == "900x600+0+0"
    assert wslmanw.resolve_geometry("1400x900+5000+5000", 1920, 1080) == "1400x900+520+180"


def test_default_window_matches_new_layout() -> None:
    """列表占满宽度、详情移入笔记本后的默认与最小尺寸。"""
    assert wslmanw.DEFAULT_GEOMETRY == "980x660"
    assert (wslmanw.MIN_WIDTH, wslmanw.MIN_HEIGHT) == (900, 600)


def test_text_units_counts_wide_chars_as_two() -> None:
    assert wslmanw.text_units("Ubuntu") == 6
    assert wslmanw.text_units("发行版") == 6
    assert wslmanw.text_units("") == 0


def test_elide_name_truncates_long_names_only() -> None:
    assert wslmanw.elide_name("Ubuntu") == "Ubuntu"
    short = "a" * 24
    assert wslmanw.elide_name(short) == short

    long_name = "Ubuntu-Preview-Extremely-Long-Distro-Name"
    elided = wslmanw.elide_name(long_name)
    assert elided.endswith("…")
    assert wslmanw.text_units(elided) <= 24
    assert long_name.startswith(elided[:-1])

    wide = wslmanw.elide_name("发行版" * 10)
    assert wide.endswith("…")
    assert wslmanw.text_units(wide) <= 24


def test_column_widths_respect_minimum_and_content() -> None:
    minimums = {"name": 160, "state": 72, "version": 56, "disk": 96, "default": 50}
    maximums = {"name": 360, "state": 120, "version": 84, "disk": 180, "default": 80}
    assert wslmanw.column_widths([]) == minimums

    rows = [("Ubuntu-24.04-Desktop-LTS", "运行中", "2", "≈ 12.3 GiB（估算）", "是")]
    widths = wslmanw.column_widths(rows)
    assert widths["name"] > minimums["name"]
    assert widths["disk"] > minimums["disk"]

    huge = [("x" * 100, "y" * 100, "z" * 100, "w" * 100, "v" * 100)]
    assert wslmanw.column_widths(huge) == maximums


def test_column_widths_distribute_slack_by_ratio_not_last_column() -> None:
    """整幅宽度富余时按比例分摊，且不越过每列上限。"""
    rows = [("Ubuntu", "运行中", "2", "12.3 GiB", "是")]
    base = wslmanw.column_widths(rows)
    wide = wslmanw.column_widths(rows, available=1400)
    assert all(wide[key] >= base[key] for key in base)
    assert wide == {"name": 360, "state": 120, "version": 84, "disk": 180, "default": 80}
    # 富余全部被上限挡住时留在右侧，不会堆给最后一列。
    assert sum(wide.values()) < 1400

    partial = wslmanw.column_widths(rows, available=600)
    assert sum(partial.values()) <= 600
    assert all(partial[key] >= base[key] for key in base)

    narrow = wslmanw.column_widths(rows, available=200)
    assert narrow == base


def test_detail_fields_labels_match_layout() -> None:
    """详情页静态字段顺序与实际数据字段必须一致，避免布局漂移。"""
    fields = wslmanw.detail_fields(_distro(), 2048, False)
    assert tuple(label for label, _value in fields) == wslmanw._DETAIL_LABELS
    assert dict(fields)["虚拟磁盘"] == r"C:\WSL\Ubuntu\ext4.vhdx"
    assert wslmanw.detail_lines(_distro(), 2048, False) == [f"{label}: {value}" for label, value in fields]


def test_distro_row_truncates_long_name_but_summary_keeps_it() -> None:
    long_name = "Ubuntu-Preview-Extremely-Long-Distro-Name"
    distro = _distro(name=long_name)
    assert wslmanw.distro_row(distro, 0, False)[0].endswith("…")
    assert wslmanw.summary_text(distro, 0, False).startswith(long_name)


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
