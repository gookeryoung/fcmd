"""wslmanw（P5）测试：设置往返、窗口几何、确认开关、列表/详情口径与启动入口。

Qt 窗口类不进单测（``pragma: no cover``，离屏冒烟见 ``test_wslmanw_qt.py``）；
这里只覆盖纯函数与入口接线，保证 GUI 与 CLI 共用同一套口径。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from fcmd.apps.wslman import wslmanw
from fcmd.apps.wslman._distro import Distro
from fcmd.apps.wslman._errors import not_found
from fcmd.core.toolkit import _TOOL_REGISTRY


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
    assert wslmanw.settings_from_mapping({"sidebar_collapsed": True}).sidebar_collapsed is True
    assert wslmanw.settings_from_mapping({"sidebar_collapsed": "yes"}).sidebar_collapsed is False


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
        "mirror": "auto",
        "sidebar_collapsed": False,
        "image_filter": "all",
        "image_sort": "name",
        "image_source": "all",
    }


def test_image_view_options_fall_back_on_bad_values() -> None:
    """镜像页筛选 / 排序 / 来源只接受登记过的取值，非法值逐项回落默认。"""
    settings = wslmanw.settings_from_mapping(
        {"image_filter": "installed", "image_sort": "status", "image_source": "manifest"}
    )
    assert (settings.image_filter, settings.image_sort, settings.image_source) == (
        "installed",
        "status",
        "manifest",
    )
    broken = wslmanw.settings_from_mapping({"image_filter": "bogus", "image_sort": 42, "image_source": "nope"})
    assert (broken.image_filter, broken.image_sort, broken.image_source) == ("all", "name", "all")


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
    title, subtitle, metrics = wslmanw.image_item_lines(entry)
    assert title == "Ubuntu"
    assert subtitle == "Ubuntu · aarch64/x86_64 · CLI · 清单"
    # 右侧状态列说「当前能做什么」：已安装 / 可安装（已下载）/ 可下载 / 仅 wsl --install
    assert metrics == "已安装"
    assert wslmanw.image_marks(entry) == (("已安装", "success"), ("sha256", "muted"))
    details = dict(wslmanw.image_details(entry))
    assert details["下载来源"] == "CLI 安装（wsl --install）、官方清单 URL"
    assert details["校验和"] == "有 sha256"
    assert "x86_64: https://e/ubuntu.wsl（有 sha256）" in details["下载地址"]
    assert "无 sha256" in details["下载地址"]

    store = wslmanw._images.ImageEntry(name="OracleLinux_9_5", friendly_name="", sources=("store",))
    assert wslmanw.image_item_lines(store) == ("OracleLinux_9_5", "任意架构 · Store", "仅 wsl --install")
    assert wslmanw.image_marks(store) == ()
    assert dict(wslmanw.image_details(store))["下载地址"].startswith("无直接下载地址")

    unverified = wslmanw._images.ImageEntry(
        name="Debian",
        friendly_name="Debian",
        sources=("manifest",),
        downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/debian.wsl"),),
        backed_up=True,
    )
    assert wslmanw.image_marks(unverified) == (("已备份", "accent"), ("无 sha256", "warning"))

    downloaded = wslmanw._images.ImageEntry(
        name="Ubuntu-24.04",
        friendly_name="Ubuntu 24.04",
        sources=("cli", "manifest"),
        downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/u24.wsl", "a" * 64),),
        downloaded=True,
        archive="D:/WSL/u24.wsl",
    )
    # 已下载未安装：状态列提示「可安装」，标签给「已下载」
    assert wslmanw.image_action_text(downloaded) == "可安装"
    assert wslmanw.image_marks(downloaded) == (("已下载", "accent"), ("sha256", "muted"))
    assert wslmanw.image_action_text(_image("Plain")) == "可下载"

    assert wslmanw.image_source_text(wslmanw._images.ImageEntry(name="X", friendly_name="", sources=())) == "未知"


def _image(
    name: str, *, friendly: str = "", download: bool = True, installed: bool = False
) -> wslmanw._images.ImageEntry:
    downloads = (wslmanw._images.ImageDownload("x86_64", f"https://e/{name}.wsl"),) if download else ()
    return wslmanw._images.ImageEntry(
        name=name,
        friendly_name=friendly,
        sources=("cli", "manifest") if download else ("store",),
        downloads=downloads,
        installed=installed,
    )


def test_filter_images_by_query_status_and_source() -> None:
    entries = (
        _image("Ubuntu", friendly="Ubuntu 24.04", installed=True),
        _image("Debian", friendly="Debian GNU/Linux"),
        _image("OracleLinux", friendly="Oracle Linux", download=False),
    )
    assert [entry.name for entry in wslmanw.filter_images(entries)] == ["Ubuntu", "Debian", "OracleLinux"]
    assert [entry.name for entry in wslmanw.filter_images(entries, query="gnu")] == ["Debian"]
    assert [entry.name for entry in wslmanw.filter_images(entries, filter_key="download")] == ["Ubuntu", "Debian"]
    assert [entry.name for entry in wslmanw.filter_images(entries, filter_key="install")] == ["OracleLinux"]
    assert [entry.name for entry in wslmanw.filter_images(entries, filter_key="installed")] == ["Ubuntu"]
    assert [entry.name for entry in wslmanw.filter_images(entries, source="store")] == ["OracleLinux"]
    assert wslmanw.filter_images(entries, filter_key="backed_up") == ()


def test_sort_images_and_counts() -> None:
    # 友好名与名称排序结果相反，才能区分两种排序真的生效。
    entries = (
        _image("ubuntu", friendly="Alpha"),
        _image("Debian", friendly="Zulu", installed=True),
    )
    assert [entry.name for entry in wslmanw.sort_images(entries)] == ["Debian", "ubuntu"]
    assert [entry.name for entry in wslmanw.sort_images(entries, "friendly")] == ["ubuntu", "Debian"]
    assert [entry.name for entry in wslmanw.sort_images(entries, "status")] == ["Debian", "ubuntu"]
    # 未知排序值回落按名称，不让旧设置文件把列表弄乱。
    assert [entry.name for entry in wslmanw.sort_images(entries, "legacy")] == ["Debian", "ubuntu"]

    entries = (
        _image("ubuntu", friendly="Alpha"),
        _image("Debian", friendly="Zulu"),
        _image("Oracle", download=False),
    )
    counts = wslmanw.image_counts(entries)
    assert counts == {"all": 3, "download": 2, "install": 1, "installed": 0, "backed_up": 0}
    status = wslmanw.image_status_text(entries, 2, filter_key="download", source="manifest", note="cli+清单 · 10:00")
    assert "共 3 个镜像" in status
    assert "可直链下载 2 个" in status
    assert "官方清单" in status
    assert "显示 2 个" in status
    assert status.endswith("cli+清单 · 10:00")


def test_image_status_text_explains_empty_filter_result() -> None:
    entries = (_image("Ubuntu"),)
    status = wslmanw.image_status_text(entries, 0, filter_key="installed")
    assert "没有匹配项" in status
    assert "没有匹配项" not in wslmanw.image_status_text((), 0)


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
    """侧边栏 + 三页布局的默认尺寸；最小尺寸保证表格与详情不被压扁。"""
    assert wslmanw.DEFAULT_GEOMETRY == "1160x760"
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


def test_run_gui_delegates_to_qt_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[object] = []
    fake = types.ModuleType("fcmd.apps.wslman._wslmanw_qt")
    fake.run_qt = lambda settings_path=None: calls.append(settings_path)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcmd.apps.wslman._wslmanw_qt", fake)
    path = tmp_path / "settings.json"
    wslmanw.run_gui(path)
    assert calls == [path]


def test_launch_cmd_calls_run_gui(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []
    monkeypatch.setattr(wslmanw, "run_gui", lambda: called.append(True))
    wslmanw.launch_cmd()
    assert called == [True]


def _manifest_entry(sha256: str = "a" * 64) -> wslmanw._images.ImageEntry:
    return wslmanw._images.ImageEntry(
        name="Debian",
        friendly_name="Debian GNU/Linux",
        sources=("manifest",),
        downloads=(
            wslmanw._images.ImageDownload(
                "x86_64",
                "https://salsa.debian.org/debian/WSL/-/jobs/1/artifacts/raw/Debian_WSL_AMD64_v1.26.0.0.wsl",
                sha256,
            ),
        ),
    )


def test_local_archives_filters_suffixes_and_skips_missing_dir(tmp_path: Path) -> None:
    """扫描只认可导入的归档后缀，目录不存在时返回空而不是报错。"""
    (tmp_path / "b.wsl").write_bytes(b"x")
    (tmp_path / "a.tar.gz").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    assert [path.name for path in wslmanw.local_archives(tmp_path)] == ["a.tar.gz", "b.wsl"]
    assert wslmanw.local_archives(tmp_path / "missing") == ()


def test_guess_distro_name_strips_suffix_and_arch() -> None:
    assert wslmanw.guess_distro_name(Path("Debian-x86_64.tar")) == "Debian"
    assert wslmanw.guess_distro_name(Path("Ubuntu 24.04.wsl")) == "Ubuntu_24.04"
    assert wslmanw.guess_distro_name(Path("---.tar")) == "distro"


def test_match_archive_accepts_gui_and_cli_filenames() -> None:
    """GUI 下载保留官方 URL 原名，CLI 下载是「名称-架构.tar」，两者都要能认出来。"""
    entry = _manifest_entry()
    expected = (entry, entry.downloads[0])
    assert wslmanw.match_archive(Path("Debian_WSL_AMD64_v1.26.0.0.wsl"), [entry]) == expected
    assert wslmanw.match_archive(Path("Debian-x86_64.tar"), [entry]) == expected
    assert wslmanw.match_archive(Path("My-Export.tar"), [entry]) is None


def test_inspect_archive_reports_package_info(tmp_path: Path) -> None:
    entry = _manifest_entry()
    archive = tmp_path / "Debian-x86_64.tar"
    archive.write_bytes(b"abc")
    info = wslmanw.inspect_archive(archive, [entry])
    assert (info.distro, info.arch, info.size) == ("Debian", "x86_64", 3)
    assert info.expected_sha256 == "a" * 64
    assert "官方清单" in info.source

    exported = tmp_path / "My-Export.tar"
    exported.write_bytes(b"abc")
    loose = wslmanw.inspect_archive(exported, [entry])
    assert loose.distro == "My-Export"
    assert loose.expected_sha256 is None
    assert "未匹配" in loose.source


def test_verification_label_never_calls_unverified_files_trusted() -> None:
    assert "未比对" in wslmanw.verification_label(None, None)
    assert wslmanw.verification_label("A" * 64, None) == "待校验"
    assert wslmanw.verification_label("a" * 64, "a" * 64) == "sha256 与官方清单一致"
    assert wslmanw.verification_label("a" * 64, "b" * 64) == "sha256 与官方清单不一致"


def test_mark_downloaded_matches_archives_in_directory(tmp_path: Path) -> None:
    """下载目录里的归档把条目标成「已下载」，并记住路径供行内「安装」使用。"""
    ubuntu = _image("Ubuntu")
    debian = _image("Debian")
    # 官方 URL 原名（Debian）与 CLI 命名（Ubuntu-<arch>.tar）都认
    (tmp_path / "debian.wsl").write_bytes(b"x")
    (tmp_path / "Ubuntu-x86_64.tar").write_bytes(b"x")
    marked = wslmanw.mark_downloaded((ubuntu, debian), tmp_path)

    by_name = {entry.name: entry for entry in marked}
    assert by_name["Ubuntu"].downloaded is True
    assert by_name["Ubuntu"].archive.endswith("Ubuntu-x86_64.tar")
    assert by_name["Debian"].downloaded is True
    assert by_name["Debian"].archive.endswith("debian.wsl")
    # 目录里的无关文件不影响判定
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    again = wslmanw.mark_downloaded((debian,), tmp_path)
    assert again[0].downloaded is True


def test_mark_downloaded_skips_installed_and_missing_dir(tmp_path: Path) -> None:
    """已安装的条目不需要「可安装」标记；目录不存在时一律未下载。"""
    installed = wslmanw._images.ImageEntry(
        name="Ubuntu",
        friendly_name="Ubuntu",
        sources=("cli", "manifest"),
        downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/ubuntu.wsl"),),
        installed=True,
    )
    plain = _image("Debian")
    (tmp_path / "ubuntu.wsl").write_bytes(b"x")
    (tmp_path / "debian.wsl").write_bytes(b"x")
    marked = wslmanw.mark_downloaded((installed, plain), tmp_path)
    # 归档在就算已下载，但「已安装」优先：标签只给已安装，行内也不再给安装按钮
    assert marked[0].downloaded is True
    assert wslmanw.image_marks(marked[0]) == (("已安装", "success"), ("无 sha256", "warning"))
    assert marked[1].downloaded is True

    empty = wslmanw.mark_downloaded((plain,), tmp_path / "nope")
    assert empty[0].downloaded is False
    assert empty[0].archive == ""
