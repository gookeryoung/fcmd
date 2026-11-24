"""wslmanw（P5）测试：设置往返、窗口几何、确认开关、列表/详情口径与启动入口。

Qt 窗口类不进单测（``pragma: no cover``，离屏冒烟见 ``test_wslmanw_qt.py``）；
这里只覆盖纯函数与入口接线，保证 GUI 与 CLI 共用同一套口径。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from fcmd.apps.wslman import _configfiles, _initenv, _open, wslmanw
from fcmd.apps.wslman._distro import Distro, DistroView
from fcmd.apps.wslman._errors import not_found
from fcmd.apps.wslman._registry import RegistrySnapshot
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
        active_page="images",
        download_dir=r"D:\WSL\downloads",
        backup_dir=r"D:/WSL/backups",
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
    assert settings.backup_dir == ""


def test_settings_mapping_is_json_friendly() -> None:
    settings = wslmanw.WslmanwSettings(geometry="900x600", download_dir=r"D:\WSL")
    assert wslmanw.settings_to_mapping(settings) == {
        "geometry": "900x600",
        "work_dir": "",
        "work_dirs": [],
        "download_dir": r"D:\WSL",
        "backup_dir": "",
        "mirror": "auto",
        "max_downloads": 2,
        "sidebar_collapsed": False,
        "active_page": "distros",
        "image_filter": "all",
        "image_sort": "name",
        "image_source": "all",
        "distro_terminal_dirs": {},
        "dialog_geometry": {},
        "distro_init_tasks": {},
        "distro_package_mirrors": {},
        "distro_env_status": {},
        "distro_config_status": {},
        "distro_config_restore": {},
        "distro_opener_paths": {},
        "openers": {
            "ide": [_open.to_mapping(item) for item in _open.default_openers(_open.KIND_IDE)],
            "agent": [_open.to_mapping(item) for item in _open.default_openers(_open.KIND_AGENT)],
            "selected": {"ide": "vscode", "agent": "opencode"},
            "hidden": {},
        },
    }


def test_env_status_and_opener_paths_cache_roundtrip() -> None:
    """探测结果与 WSL 内工具路径要能持久化，实例停止时直接回显。"""
    status = _initenv.EnvStatus(
        user="me",
        home="/home/me",
        os_name="Ubuntu 24.04",
        mirror="mirrors.aliyun.com",
        ssh_public_key="ssh-ed25519 AAAA",
        python="3.12.3",
        uv="0.5.14",
        node="22.11.0",
        nvm="yes",
        bun="1.2.0",
        rust="1.80.0",
        pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
        uv_index="https://pypi.tuna.tsinghua.edu.cn/simple",
        npm_registry="https://registry.npmmirror.com",
        bun_registry="https://registry.npmmirror.com",
    )
    settings = wslmanw.with_env_status(wslmanw.WslmanwSettings(), "Ubuntu", status)
    settings = wslmanw.with_opener_paths(settings, "Ubuntu", {"opencode": "/usr/bin/opencode"})
    restored = wslmanw.settings_from_mapping(wslmanw.settings_to_mapping(settings))
    assert wslmanw.env_status_for(restored, "Ubuntu") == status
    assert wslmanw.opener_paths_for(restored, "Ubuntu") == {"opencode": "/usr/bin/opencode"}

    cleared = wslmanw.with_opener_paths(restored, "Ubuntu", {})
    assert wslmanw.opener_paths_for(cleared, "Ubuntu") == {}
    assert wslmanw.env_status_for(cleared, "Missing") is None


def test_config_status_cache_roundtrip() -> None:
    """「迁移配置文件」上次扫描状态随设置文件往返，打开配置直接回显。"""
    snapshot = _configfiles.build_snapshot(
        (_configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),),
        _configfiles.ConfigTarget(
            home="/home/me",
            files=(_configfiles.RemoteFile(".ssh/id_ed25519", "600", 10),),
            dirs={".ssh": "700"},
        ),
        _configfiles.ConfigSource(_configfiles.SOURCE_WINDOWS, "", "C:/Users/me"),
        stamp=1700000000.0,
    )
    settings = wslmanw.with_config_status(wslmanw.WslmanwSettings(), "Ubuntu", snapshot)
    restored = wslmanw.settings_from_mapping(wslmanw.settings_to_mapping(settings))
    assert wslmanw.config_status_for(restored, "Ubuntu") == snapshot
    assert wslmanw.config_status_for(restored, "Missing") is None
    # 旧调用名与新字段同样可用。
    assert wslmanw.ssh_status_for(restored, "Ubuntu") == snapshot
    # 旧设置文件里的 distro_ssh_status 读入时迁移到新字段，不丢缓存。
    legacy = wslmanw.settings_from_mapping(
        {"distro_ssh_status": {"Ubuntu": _configfiles.snapshot_to_mapping(snapshot)}}
    )
    assert wslmanw.config_status_for(legacy, "Ubuntu") == snapshot
    # 坏行（不是映射 / 缺标签）按「没有缓存」处理，不影响打开配置。
    broken = wslmanw.settings_from_mapping({"distro_config_status": {"Ubuntu": "nope", "Debian": {}}})
    assert wslmanw.config_status_for(broken, "Ubuntu") is None
    assert wslmanw.config_status_for(broken, "Debian") is None


def test_config_restore_record_roundtrip_and_clear() -> None:
    """还原记录随设置文件往返；清空后返回 ``None``，坏行按无记录处理。"""
    record = _configfiles.RestoreRecord(
        "2026-01-01 12:00:00",
        "Windows 宿主",
        (
            _configfiles.RestoreEntry(".gitconfig", ".fcmd-config-backup-1/.gitconfig", 42),
            _configfiles.RestoreEntry(".ssh/id_ed25519", "", 100),
        ),
    )
    settings = wslmanw.with_config_restore(wslmanw.WslmanwSettings(), "Ubuntu", record)
    restored = wslmanw.settings_from_mapping(wslmanw.settings_to_mapping(settings))
    assert wslmanw.config_restore_for(restored, "Ubuntu") == record
    assert wslmanw.config_restore_for(restored, "Debian") is None

    cleared = wslmanw.with_config_restore(restored, "Ubuntu", None)
    assert wslmanw.config_restore_for(cleared, "Ubuntu") is None
    broken = wslmanw.settings_from_mapping(
        {"distro_config_restore": {"Ubuntu": "nope", "Debian": {"stamp": "", "entries": "[]"}}}
    )
    assert wslmanw.config_restore_for(broken, "Ubuntu") is None
    assert wslmanw.config_restore_for(broken, "Debian") is None


def test_terminal_cwd_roundtrip_and_default(tmp_path: Path) -> None:
    """实例终端目录默认 ~，可覆盖并随设置文件持久化；回落 ~ 时删除该键。"""
    assert wslmanw.terminal_cwd(wslmanw.WslmanwSettings(), "Ubuntu") == "~"
    settings = wslmanw.with_terminal_cwd(wslmanw.WslmanwSettings(), "Ubuntu", "/home/me/project")
    assert wslmanw.terminal_cwd(settings, "ubuntu") == "/home/me/project"

    path = tmp_path / "settings.json"
    wslmanw.save_settings(settings, path)
    assert wslmanw.load_settings(path) == settings

    reset = wslmanw.with_terminal_cwd(settings, "UBUNTU", "~")
    assert wslmanw.terminal_cwd(reset, "Ubuntu") == "~"
    assert reset.distro_terminal_dirs == {}


def test_terminal_dirs_tolerate_bad_values() -> None:
    settings = wslmanw.settings_from_mapping(
        {"distro_terminal_dirs": {"Ubuntu": "/srv/app", "": "/x", "Debian": 42, "Alpine": "~"}}
    )
    assert settings.distro_terminal_dirs == {"Ubuntu": "/srv/app", "Alpine": "~"}
    assert wslmanw.settings_from_mapping({"distro_terminal_dirs": "bogus"}).distro_terminal_dirs == {}


def test_dialog_geometry_roundtrip_and_casefold_lookup() -> None:
    """对话框几何按名字（大小写不敏感）保存；非法几何不写入。"""
    settings = wslmanw.with_dialog_geometry(wslmanw.WslmanwSettings(), "distro_config", "720x480+10+20")
    assert wslmanw.dialog_geometry(settings, "DISTRO_CONFIG") == "720x480+10+20"
    assert wslmanw.dialog_geometry(settings, "image_manager") is None

    cleared = wslmanw.with_dialog_geometry(settings, "distro_config", "bogus")
    assert cleared.dialog_geometry == {}

    raw = wslmanw.settings_to_mapping(settings)
    assert wslmanw.settings_from_mapping(raw).dialog_geometry == {"distro_config": "720x480+10+20"}
    dirty = wslmanw.settings_from_mapping({"dialog_geometry": {"ok": "640x400+0+0", "bad": "nope", 42: "x"}})
    assert dirty.dialog_geometry == {"ok": "640x400+0+0"}


def test_init_task_keys_roundtrip_and_validation() -> None:
    """初始化勾选按实例保存：忽略未知 key，按任务固定顺序输出，未记录时返回 None。"""
    settings = wslmanw.with_init_task_keys(wslmanw.WslmanwSettings(), "Ubuntu", ["node", "python", "node", "bogus"])
    assert wslmanw.init_task_keys(settings, "ubuntu") == ("python", "node")
    assert wslmanw.init_task_keys(settings, "Debian") is None

    raw = wslmanw.settings_to_mapping(settings)
    assert raw["distro_init_tasks"] == {"Ubuntu": ["python", "node"]}
    assert wslmanw.settings_from_mapping(raw).distro_init_tasks == {"Ubuntu": ("python", "node")}
    dirty = wslmanw.settings_from_mapping({"distro_init_tasks": {"Alt": ["rust", 1], "Bad": "python"}})
    assert dirty.distro_init_tasks == {"Alt": ("rust",)}


def test_package_mirror_roundtrip_and_validation() -> None:
    """软件包源按实例保存：自动值不落盘，非法值回落 auto，序列化可往返。"""
    settings = wslmanw.WslmanwSettings()
    assert wslmanw.package_mirror_for(settings, "Ubuntu") == "auto"
    settings = wslmanw.with_package_mirror(settings, "Ubuntu", "mirrors.ustc.edu.cn")
    settings = wslmanw.with_package_mirror(settings, "Debian", "official")
    assert wslmanw.package_mirror_for(settings, "ubuntu") == "mirrors.ustc.edu.cn"
    assert wslmanw.package_mirror_for(settings, "Debian") == "official"
    assert wslmanw.package_mirror_for(settings, "Kali") == "auto"

    raw = wslmanw.settings_to_mapping(settings)
    assert raw["distro_package_mirrors"] == {"Ubuntu": "mirrors.ustc.edu.cn", "Debian": "official"}
    assert wslmanw.settings_from_mapping(raw).distro_package_mirrors == {
        "Ubuntu": "mirrors.ustc.edu.cn",
        "Debian": "official",
    }

    cleared = wslmanw.with_package_mirror(settings, "ubuntu", "auto")
    assert wslmanw.package_mirror_for(cleared, "Ubuntu") == "auto"
    dirty = wslmanw.settings_from_mapping(
        {"distro_package_mirrors": {"A": "bogus", "B": "mirrors.ustc.edu.cn", 3: "official"}}
    )
    assert dirty.distro_package_mirrors == {"B": "mirrors.ustc.edu.cn"}


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


def test_mirror_sources_order_wslui_then_verified_then_official() -> None:
    """展开区的镜像源：WSLUI 多源 → 可校验国内镜像 → 官方源，顺序即可尝试顺序。"""
    from fcmd.apps.wslman._wslui import WsluiSource

    url = "https://releases.ubuntu.com/24.04/ubuntu-24.04-wsl-amd64.wsl"
    entry = wslmanw._images.ImageEntry(
        "Ubuntu",
        "Ubuntu 24.04 LTS",
        ("manifest",),
        (wslmanw._images.ImageDownload("x86_64", url, "a" * 64),),
    )
    wslui = (WsluiSource("https://mirrors.nju.edu.cn/rootfs.tar.xz", "nju", "tar.xz"),)

    pairs = wslmanw.mirror_sources(entry, wslui)

    assert [label for label, _url in pairs] == [
        "南京大学",
        "中科大 USTC",
        "阿里云",
        "华为云",
        "南京大学",
        "官方源",
    ]
    lines = wslmanw.mirror_lines(pairs).splitlines()
    assert lines[0] == "南京大学 · https://mirrors.nju.edu.cn/rootfs.tar.xz"
    assert lines[-1] == f"官方源 · {url}"


def test_mirror_sources_skip_verified_mirrors_without_sha256() -> None:
    """没有官方 sha256 就不认镜像：列表里只剩官方源，不把未校验的镜像算进数量。"""
    from fcmd.apps.wslman._wslui import WsluiSource

    url = "https://releases.ubuntu.com/24.04/ubuntu-24.04-wsl-amd64.wsl"
    entry = wslmanw._images.ImageEntry(
        "Ubuntu", "Ubuntu 24.04 LTS", ("manifest",), (wslmanw._images.ImageDownload("x86_64", url),)
    )
    wslui = (WsluiSource("https://mirrors.nju.edu.cn/rootfs.tar.xz", "nju", "tar.xz"),)

    # WSLUI 源自带未校验口径，仍然列出且不会和官方源混在一起去重。
    assert wslmanw.mirror_sources(entry, wslui) == (("南京大学", wslui[0].url), ("官方源", url))
    assert wslmanw.mirror_sources(entry, ()) == (("官方源", url),)


def test_mirror_sources_drop_other_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机装不了的架构不充数：官方按 arch 字段过滤，WSLUI 按 URL 里的架构过滤。"""
    from fcmd.apps.wslman._wslui import WsluiSource

    amd = "https://e/ubuntu-amd64.wsl"
    arm = "https://e/ubuntu-arm64.wsl"
    entry = wslmanw._images.ImageEntry(
        "Ubuntu",
        "Ubuntu 24.04 LTS",
        ("manifest",),
        (
            wslmanw._images.ImageDownload("x86_64", amd, "a" * 64),
            wslmanw._images.ImageDownload("aarch64", arm, "b" * 64),
        ),
    )
    wslui = (
        WsluiSource("https://mirrors.nju.edu.cn/lxc-images/amd64/rootfs.tar.xz", "nju", "tar.xz"),
        WsluiSource("https://mirrors.nju.edu.cn/lxc-images/arm64/rootfs.tar.xz", "nju", "tar.xz"),
        WsluiSource("https://mirrors.nju.edu.cn/no-arch/rootfs.tar.xz", "nju", "tar.xz"),
    )

    monkeypatch.setattr(wslmanw.platform, "machine", lambda: "AMD64")
    assert [url for _label, url in wslmanw.mirror_sources(entry, wslui)] == [
        "https://mirrors.nju.edu.cn/lxc-images/amd64/rootfs.tar.xz",
        "https://mirrors.nju.edu.cn/no-arch/rootfs.tar.xz",
        amd,
    ]

    # ARM 机器上反过来：WSLUI 的 amd64 rootfs 装了也跑不了，官方只留 aarch64。
    monkeypatch.setattr(wslmanw.platform, "machine", lambda: "ARM64")
    assert [url for _label, url in wslmanw.mirror_sources(entry, wslui)] == [
        "https://mirrors.nju.edu.cn/lxc-images/arm64/rootfs.tar.xz",
        "https://mirrors.nju.edu.cn/no-arch/rootfs.tar.xz",
        arm,
    ]


def test_mirror_sources_keep_official_when_arch_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """官方条目只有别的架构时不清空列表（宁可展示不可用的源，也不变成 0 个）。"""
    monkeypatch.setattr(wslmanw.platform, "machine", lambda: "AMD64")
    arm = "https://e/ubuntu-arm64.wsl"
    entry = wslmanw._images.ImageEntry("Ubuntu", "", ("manifest",), (wslmanw._images.ImageDownload("aarch64", arm),))
    assert wslmanw.mirror_sources(entry) == (("官方源", arm),)


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


def _image(  # noqa: PLR0913 - 测试夹具，状态位按需组合
    name: str,
    *,
    friendly: str = "",
    download: bool = True,
    installed: bool = False,
    downloaded: bool = False,
    backed_up: bool = False,
) -> wslmanw._images.ImageEntry:
    downloads = (wslmanw._images.ImageDownload("x86_64", f"https://e/{name}.wsl"),) if download else ()
    return wslmanw._images.ImageEntry(
        name=name,
        friendly_name=friendly,
        sources=("cli", "manifest") if download else ("store",),
        downloads=downloads,
        installed=installed,
        downloaded=downloaded,
        backed_up=backed_up,
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
    # 「仅安装」已合并进「全部」：没有直链归档的条目仍在这里，靠行内文案区分。
    assert [entry.name for entry in wslmanw.filter_images(entries, filter_key="all")] == [
        "Ubuntu",
        "Debian",
        "OracleLinux",
    ]
    assert wslmanw.filter_images(entries, filter_key="install") == entries
    assert [entry.name for entry in wslmanw.filter_images(entries, filter_key="installed")] == ["Ubuntu"]
    assert [entry.name for entry in wslmanw.filter_images(entries, source="store")] == ["OracleLinux"]
    assert wslmanw.filter_images(entries, filter_key="backed_up") == ()


def test_image_pin_rank_puts_local_first() -> None:
    """置顶档位：已安装 0 / 已下载 1 / 仅在线或仅备份 2。"""
    assert wslmanw.image_pin_rank(_image("A", installed=True)) == 0
    assert wslmanw.image_pin_rank(_image("A", installed=True, downloaded=True)) == 0
    assert wslmanw.image_pin_rank(_image("A", downloaded=True)) == 1
    assert wslmanw.image_pin_rank(_image("A", backed_up=True)) == 2
    assert wslmanw.image_pin_rank(_image("A")) == 2


def test_sort_images_and_counts() -> None:
    # 已安装 / 已下载永远置顶，排序口径只在同档位内生效。
    entries = (
        _image("ubuntu", friendly="Alpha"),
        _image("Debian", friendly="Zulu", installed=True),
        _image("Kali", friendly="Beta", downloaded=True),
        _image("Zbackup", friendly="Delta", backed_up=True),
        _image("Alpine", friendly="Gamma"),
    )
    # 置顶档位：已安装 → 已下载 → 其它；``name`` / ``friendly`` 只重排同档位。
    assert [entry.name for entry in wslmanw.sort_images(entries)] == [
        "Debian",
        "Kali",
        "Alpine",
        "ubuntu",
        "Zbackup",
    ]
    assert [entry.name for entry in wslmanw.sort_images(entries, "friendly")] == [
        "Debian",
        "Kali",
        "ubuntu",
        "Zbackup",
        "Alpine",
    ]
    # ``status`` 在同档位内先排已备份。
    assert [entry.name for entry in wslmanw.sort_images(entries, "status")] == [
        "Debian",
        "Kali",
        "Zbackup",
        "Alpine",
        "ubuntu",
    ]
    # 未知排序值回落按名称，不让旧设置文件把列表弄乱。
    assert [entry.name for entry in wslmanw.sort_images(entries, "legacy")] == [
        "Debian",
        "Kali",
        "Alpine",
        "ubuntu",
        "Zbackup",
    ]

    entries = (
        _image("ubuntu", friendly="Alpha"),
        _image("Debian", friendly="Zulu"),
        _image("Oracle", download=False),
    )
    counts = wslmanw.image_counts(entries)
    assert counts == {"all": 3, "download": 2, "installed": 0, "backed_up": 0}
    status = wslmanw.image_status_text(entries, 2, filter_key="download", source="manifest", note="cli+清单 · 10:00")
    assert "共 3 个镜像" in status
    assert "可下载 2 个" in status
    assert "已备份 0" in status
    assert "官方清单" in status
    assert "显示 2 个" in status
    assert status.endswith("cli+清单 · 10:00")


def test_image_status_text_explains_empty_filter_result() -> None:
    entries = (_image("Ubuntu"),)
    status = wslmanw.image_status_text(entries, 0, filter_key="installed")
    assert "没有匹配项" in status
    assert "没有匹配项" not in wslmanw.image_status_text((), 0)


def test_download_progress_text_shows_percent_speed_and_eta() -> None:
    """状态栏下载文案：百分比 + 已下载 / 总量 + 速度 + 剩余时间；总量未知时只给已下载。"""
    text = wslmanw.download_progress_text("Ubuntu", 512, 1024, 256.0)
    assert text == "下载 Ubuntu · 50% · 512 B / 1.0 KiB · 256 B/s · 剩余 2s"
    # 刚开始 / 断流（速度 0）不估算剩余时间，速度显示 --
    assert wslmanw.download_progress_text("Ubuntu", 0, 1024, 0.0) == ("下载 Ubuntu · 0% · 0 B / 1.0 KiB · --")
    # 总量未知：只给已下载字节与速度
    assert wslmanw.download_progress_text("Ubuntu", 2048, None, 1024.0) == ("下载 Ubuntu · 2.0 KiB · 1.00 KB/s")
    # 已下完：100% 且不再写剩余时间
    done = wslmanw.download_progress_text("Ubuntu", 1024, 1024, 512.0)
    assert "100%" in done
    assert "剩余" not in done


def test_export_progress_text_uses_same_shape_as_download() -> None:
    """导出状态栏文案：百分比 / 已写 / 估算总量 / 速度 / 剩余，前缀是「导出」。"""
    text = wslmanw.export_progress_text("Ubuntu", 512, 1024, 256.0)
    assert text == "导出 Ubuntu · 50% · 512 B / 1.0 KiB · 256 B/s · 剩余 2s"
    assert wslmanw.export_progress_text("Ubuntu", 2048, None, 1024.0) == ("导出 Ubuntu · 2.0 KiB · 1.00 KB/s")


def test_download_limit_clamps_to_safe_range() -> None:
    """并发上限：默认 2 个，非法值回落默认，越界收敛到 1–4。"""
    assert wslmanw.DEFAULT_MAX_DOWNLOADS == 2
    assert wslmanw.download_limit(None) == 2
    assert wslmanw.download_limit("not-a-number") == 2
    assert wslmanw.download_limit(0) == wslmanw.MIN_DOWNLOADS
    assert wslmanw.download_limit(99) == wslmanw.MAX_DOWNLOADS
    assert wslmanw.download_limit("3") == 3
    settings = wslmanw.with_max_downloads(wslmanw.WslmanwSettings(), 4)
    assert settings.max_downloads == 4
    assert wslmanw.with_max_downloads(settings, 0).max_downloads == wslmanw.MIN_DOWNLOADS


def test_max_downloads_roundtrips_through_settings() -> None:
    """并发上限随设置文件读写；坏值读入时收敛而不是抛错。"""
    settings = wslmanw.WslmanwSettings(max_downloads=3)
    assert wslmanw.settings_from_mapping(wslmanw.settings_to_mapping(settings)).max_downloads == 3
    assert wslmanw.settings_from_mapping({"max_downloads": 42}).max_downloads == wslmanw.MAX_DOWNLOADS
    assert wslmanw.settings_from_mapping({"max_downloads": "x"}).max_downloads == wslmanw.DEFAULT_MAX_DOWNLOADS


def test_download_metrics_text_handles_known_and_unknown_total() -> None:
    """行内进度：有总量给百分比，无总量给已下载量，刚开始给「下载中…」。"""
    assert wslmanw.download_metrics_text(512, 1024, 256.0) == "下载 50% · 256 B/s"
    assert wslmanw.download_metrics_text(2048, None) == "下载 2.0 KiB"
    assert wslmanw.download_metrics_text(0, None) == "下载中…"
    assert wslmanw.download_metrics_text(0, 0) == "下载中…"


def test_download_queue_text_reports_concurrency_and_backlog() -> None:
    """镜像页状态行：并发几项 / 上限 / 排队几项；没有任务时为空串。"""
    assert wslmanw.download_queue_text(0, 0, 2) == ""
    assert wslmanw.download_queue_text(2, 0, 2) == "正在下载 2 项 · 上限 2"
    text = wslmanw.download_queue_text(2, 3, 2)
    assert "正在下载 2 项" in text
    assert "排队 3 项" in text


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
    """侧边栏 + 四页布局的默认尺寸；最小尺寸保证行卡列表不被压扁。"""
    assert wslmanw.DEFAULT_GEOMETRY == "1160x760"
    assert (wslmanw.MIN_WIDTH, wslmanw.MIN_HEIGHT) == (900, 600)


def test_detail_fields_labels_match_inline_layout() -> None:
    """行内详情网格的字段顺序与数据字段必须一致，避免布局漂移。"""
    fields = wslmanw.detail_fields(_distro(), 2048, False)
    assert tuple(label for label, _value in fields) == wslmanw._DETAIL_LABELS
    assert dict(fields)["虚拟磁盘"] == r"C:\WSL\Ubuntu\ext4.vhdx"

    missing = dict(wslmanw.detail_fields(_distro(base_path=None, default_uid=None, registered=False), 10, True))
    assert missing["BasePath"] == "未知"
    assert missing["虚拟磁盘"] == "未知"
    assert missing["默认用户"] == "未知"
    assert missing["来源"] == "wsl --list"
    assert "≈" in missing["占用"]


def test_summary_text_keeps_full_distro_name() -> None:
    """状态栏摘要保留完整发行版名（行卡标题才做省略）。"""
    long_name = "Ubuntu-Preview-Extremely-Long-Distro-Name"
    summary = wslmanw.summary_text(_distro(name=long_name), 0, False)
    assert summary.startswith(long_name)
    assert "运行中" in summary and "v2" in summary


def test_unregister_confirm_requires_exact_typed_name() -> None:
    assert wslmanw.unregister_confirm_enabled("Ubuntu", "Ubuntu") is True
    assert wslmanw.unregister_confirm_enabled("  ubuntu  ", "Ubuntu") is True
    assert wslmanw.unregister_confirm_enabled("Ubunt", "Ubuntu") is False
    assert wslmanw.unregister_confirm_enabled("", "Ubuntu") is False
    assert wslmanw.unregister_confirm_enabled("Ubuntu", "") is False


def test_shutdown_confirm_requires_checkbox() -> None:
    assert wslmanw.shutdown_confirm_enabled(True) is True
    assert wslmanw.shutdown_confirm_enabled(False) is False


def test_detail_fields_cover_identity_and_storage() -> None:
    """行内详情同时给出身份信息与存储信息。"""
    fields = dict(wslmanw.detail_fields(_distro(), None, False))
    assert fields["名称"] == "Ubuntu"
    assert fields["状态"] == "运行中"
    assert fields["版本"] == "2"
    assert fields["默认"] == "是"
    assert fields["默认用户"] == "1000"
    assert fields["虚拟磁盘"].endswith("ext4.vhdx")


def test_error_text_formats_wslman_errors() -> None:
    assert wslmanw._error_text(not_found("没有", hint="先安装")) == "没有（先安装）"
    assert wslmanw._error_text(not_found("没有")) == "没有"
    assert wslmanw._error_text(RuntimeError("boom")) == "RuntimeError: boom"


def test_work_dir_derives_download_install_and_backup(tmp_path: Path) -> None:
    settings = wslmanw.WslmanwSettings(work_dir=str(tmp_path / "root"))
    assert wslmanw.work_dir_path(settings) == tmp_path / "root"
    assert wslmanw.derived_dirs(settings) == {
        "download": tmp_path / "root" / "download",
        "install": tmp_path / "root" / "install",
        "backup": tmp_path / "root" / "backup",
    }


def test_empty_work_dir_falls_back_to_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    assert wslmanw.work_dir_path(wslmanw.WslmanwSettings()) == tmp_path / "home" / "wsl"


def test_with_work_dir_clears_legacy_overrides() -> None:
    settings = wslmanw.WslmanwSettings(download_dir=r"D:\old", backup_dir=r"D:\bak")
    updated = wslmanw.with_work_dir(settings, r"E:\new")
    assert updated.work_dir == r"E:\new"
    assert updated.download_dir == ""
    assert updated.backup_dir == ""


def test_legacy_dir_note_mentions_old_paths() -> None:
    assert wslmanw.legacy_dir_note(wslmanw.WslmanwSettings()) == ""
    note = wslmanw.legacy_dir_note(wslmanw.WslmanwSettings(download_dir=r"D:\dl"))
    assert r"D:\dl" in note
    assert "工作目录" in note


def test_legacy_single_work_dir_migrates_to_one_default(tmp_path: Path) -> None:
    """旧版单目录平滑迁成一条记录并设为默认。"""
    settings = wslmanw.settings_from_mapping({"work_dir": str(tmp_path / "old")})
    assert settings.work_dir == str(tmp_path / "old")
    assert settings.work_dirs == (str(tmp_path / "old"),)
    assert wslmanw.work_dir_paths(settings) == (tmp_path / "old",)

    raw = wslmanw.settings_to_mapping(settings)
    assert raw["work_dirs"] == [str(tmp_path / "old")]


def test_work_dir_paths_keep_default_first_and_dedupe(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    settings = wslmanw.WslmanwSettings(work_dir=str(second), work_dirs=(str(first), str(second), str(first)))
    assert wslmanw.work_dir_paths(settings) == (second, first)
    assert wslmanw.work_dir_path(settings) == second

    updated = wslmanw.with_work_dirs(settings, (first, second), second)
    assert updated.work_dirs == (str(second), str(first))
    assert updated.work_dir == str(second)
    # 再切默认：列表顺序跟着变，下载 / 安装 / 备份按新默认推演。
    switched = wslmanw.with_work_dirs(updated, (updated.work_dirs[1], updated.work_dirs[0]), first)
    assert switched.work_dirs == (str(first), str(second))
    assert wslmanw.derived_dirs(switched)["install"] == first / "install"


def test_work_dir_of_matches_registry_base_path(tmp_path: Path) -> None:
    root = tmp_path / "wsl"
    other = tmp_path / "other"
    settings = wslmanw.WslmanwSettings(work_dir=str(root), work_dirs=(str(root), str(other)))
    assert wslmanw.work_dir_of(settings, str(root / "install" / "Ubuntu")) == root
    # wsl --import-in-place 写出的 BasePath 带 \\?\ 扩展前缀，也要能归属到工作目录。
    prefix = chr(92) * 2 + "?" + chr(92)
    assert wslmanw.work_dir_of(settings, prefix + str(root / "install" / "Ubuntu")) == root
    assert wslmanw.work_dir_of(settings, str(other / "x")) == other
    assert wslmanw.work_dir_of(settings, str(tmp_path / "loose" / "Debian")) is None
    assert wslmanw.work_dir_of(settings, None) is None


def test_scan_install_dirs_finds_moved_instances(tmp_path: Path) -> None:
    """刷新时的目录扫描：只认含 vhdx / rootfs 的 ``install/<实例>``。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    settings = wslmanw.with_work_dirs(wslmanw.WslmanwSettings(), [first, second], first)

    ubuntu = second / "install" / "Ubuntu"
    ubuntu.mkdir(parents=True)
    (ubuntu / "ext4.vhdx").write_bytes(b"x")
    debian = first / "install" / "Debian"
    debian.mkdir(parents=True)
    (debian / "rootfs").mkdir()
    empty = first / "install" / "Empty"
    empty.mkdir(parents=True)

    found = wslmanw.scan_install_dirs(settings)
    assert found == {"ubuntu": ubuntu, "debian": debian}
    assert wslmanw.has_distro_files(str(ubuntu)) is True
    assert wslmanw.has_distro_files(str(empty)) is False
    assert wslmanw.has_distro_files(None) is False


def test_reconcile_distros_marks_stale_entries_for_repair(tmp_path: Path) -> None:
    """刷新对比：BasePath 失效的实例仍列出，但标「需修复」；同名磁盘填进修复路径。"""
    root = tmp_path / "wsl"
    moved = root / "install" / "Ubuntu"
    moved.mkdir(parents=True)
    (moved / "ext4.vhdx").write_bytes(b"x")
    settings = wslmanw.with_work_dirs(wslmanw.WslmanwSettings(), [root], root)
    snapshot = RegistrySnapshot(
        available=False,
        default_distribution=None,
        default_version=None,
        distros=(),
        error=None,
    )
    stale_ubuntu = Distro("Ubuntu", "stopped", "Stopped", 2, False, base_path=str(root / "old" / "Ubuntu"))
    missing = Distro("Debian", "stopped", "Stopped", 2, False, base_path=str(root / "old" / "Debian"))
    view = DistroView((stale_ubuntu, missing), "Ubuntu", (), snapshot)

    reconciled, needs_repair, orphans = wslmanw.reconcile_distros(view, settings)
    assert needs_repair == ("Ubuntu", "Debian")
    assert orphans == {"Ubuntu": moved}
    assert [item.name for item in reconciled.distros] == ["Ubuntu", "Debian"]
    assert all(item.needs_repair for item in reconciled.distros)
    assert reconciled.distros[0].repair_vhd == str(moved / "ext4.vhdx")
    assert reconciled.distros[1].repair_vhd is None
    assert any("需修复注册" in warning for warning in reconciled.warnings)
    assert any("同名残余磁盘" in warning and str(moved) in warning for warning in reconciled.warnings)


def test_reconcile_distros_keeps_unknown_base_path(tmp_path: Path) -> None:
    """注册表不可读导致 BasePath 为空时不能误删，仍可启动的实例保持原样。"""
    snapshot = RegistrySnapshot(
        available=False,
        default_distribution=None,
        default_version=None,
        distros=(),
        error=None,
    )
    distro = Distro("Ubuntu", "running", "Running", 2, True, base_path=None)
    view = DistroView((distro,), "Ubuntu", (), snapshot)
    reconciled, needs_repair, orphans = wslmanw.reconcile_distros(view, wslmanw.WslmanwSettings())
    assert reconciled is view
    assert needs_repair == ()
    assert orphans == {}


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
    (tmp_path / "c.vhdx").write_bytes(b"x")
    (tmp_path / "d.zip").write_bytes(b"x")
    (tmp_path / "e.txt").write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    assert [path.name for path in wslmanw.local_archives(tmp_path)] == [
        "a.tar.gz",
        "b.wsl",
        "c.vhdx",
        "d.zip",
    ]
    assert wslmanw.local_archives(tmp_path / "missing") == ()


def test_guess_distro_name_strips_suffix_and_arch() -> None:
    assert wslmanw.guess_distro_name(Path("Debian-x86_64.tar")) == "Debian"
    assert wslmanw.guess_distro_name(Path("Ubuntu 24.04.wsl")) == "Ubuntu_24.04"
    assert wslmanw.guess_distro_name(Path("kali.ext4.vhdx")) == "kali.ext4"
    assert wslmanw.guess_distro_name(Path("Debian.zip")) == "Debian"
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


def test_match_distro_image_by_name_and_friendly_name() -> None:
    """发行版名 → 在线镜像：名称全等、友好名全等都能命中（大小写不敏感）。"""
    ubuntu = _image("Ubuntu-24.04", friendly="Ubuntu 24.04 LTS")
    debian = _image("Debian", friendly="Debian GNU/Linux")

    assert wslmanw.match_distro_image(_distro(name="ubuntu-24.04"), (ubuntu, debian)) is ubuntu
    assert wslmanw.match_distro_image(_distro(name="Ubuntu 24.04 LTS"), (ubuntu, debian)) is ubuntu
    assert wslmanw.match_distro_image(_distro(name="Debian"), (ubuntu, debian)) is debian
    assert wslmanw.match_distro_image(_distro(name="Arch"), (ubuntu, debian)) is None
    # 清单为空时不误报「本地导入」
    assert wslmanw.match_distro_image(_distro(name="Ubuntu"), ()) is None


def test_match_distro_image_prefers_longest_word_prefix() -> None:
    """发行版名是镜像名的「词首前缀」时也算命中，多个候选取最长的那个。"""
    ubuntu = _image("Ubuntu")
    ubuntu2404 = _image("Ubuntu-24.04")
    entries = (ubuntu, ubuntu2404)

    assert wslmanw.match_distro_image(_distro(name="Ubuntu-24.04-custom"), entries) is ubuntu2404
    assert wslmanw.match_distro_image(_distro(name="Ubuntu-Preview"), entries) is ubuntu
    # 非词首前缀（UbuntuX）不算命中，避免把无关发行版归到 Ubuntu
    assert wslmanw.match_distro_image(_distro(name="UbuntuX"), entries) is None


def test_distro_image_text_reports_match_and_local_import() -> None:
    """行内说明：命中给友好名，未命中给「本地导入」，清单未知时留空。"""
    ubuntu = _image("Ubuntu-24.04", friendly="Ubuntu 24.04 LTS")
    assert wslmanw.distro_image_text(_distro(name="Ubuntu-24.04"), (ubuntu,)) == "镜像 Ubuntu 24.04 LTS"
    # 没有友好名时退回镜像名
    plain = _image("Alpine")
    assert wslmanw.distro_image_text(_distro(name="Alpine"), (plain,)) == "镜像 Alpine"
    assert wslmanw.distro_image_text(_distro(name="MyDistro"), (ubuntu,)) == "本地导入"
    assert wslmanw.distro_image_text(_distro(name="MyDistro"), (), known=False) == ""


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


def test_mark_downloaded_recognizes_wslui_source_filename(tmp_path: Path) -> None:
    """WSLUI 自动择优下载落盘为 ``<实例>-<arch>.<格式>``，重扫时也要标成已下载。"""
    ubuntu = _image("Ubuntu-24.04")
    (tmp_path / "Ubuntu-24.04-x86_64.tar.xz").write_bytes(b"x")
    marked = wslmanw.mark_downloaded((ubuntu,), tmp_path)
    assert marked[0].downloaded is True
    assert marked[0].archive.endswith("Ubuntu-24.04-x86_64.tar.xz")


def test_mark_downloaded_marks_all_entries_sharing_an_archive(tmp_path: Path) -> None:
    """同名下载项指向同一个归档时，相关条目的「已下载」状态保持一致。"""
    url = "https://releases.ubuntu.com/26.04.1/ubuntu-26.04.1-wsl-amd64.wsl"
    download = wslmanw._images.ImageDownload("x86_64", url)
    generic = wslmanw._images.ImageEntry(name="Ubuntu", friendly_name="Ubuntu", sources=("cli",), downloads=(download,))
    versioned = wslmanw._images.ImageEntry(
        name="Ubuntu-26.04", friendly_name="Ubuntu 26.04 LTS", sources=("cli", "manifest"), downloads=(download,)
    )
    (tmp_path / "ubuntu-26.04.1-wsl-amd64.wsl").write_bytes(b"x")

    by_name = {entry.name: entry for entry in wslmanw.mark_downloaded((generic, versioned), tmp_path)}

    assert by_name["Ubuntu"].downloaded is True
    assert by_name["Ubuntu-26.04"].downloaded is True
    assert by_name["Ubuntu"].archive.endswith("ubuntu-26.04.1-wsl-amd64.wsl")
    assert by_name["Ubuntu-26.04"].archive.endswith("ubuntu-26.04.1-wsl-amd64.wsl")


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


def test_backup_names_scans_all_work_dirs(tmp_path: Path) -> None:
    """「已备份」按工作目录的 backup 子目录判定，不依赖 CLI 的 backups.dir。"""
    first = tmp_path / "a" / "backup"
    second = tmp_path / "b" / "backup"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "Ubuntu.tar").write_bytes(b"x")
    (second / "Kali.tar.gz").write_bytes(b"x")
    (second / "notes.txt").write_bytes(b"x")
    (second / "Debian.tgz").write_bytes(b"x")
    assert wslmanw.backup_names((first, second, tmp_path / "missing")) == frozenset({"ubuntu", "kali", "debian"})


def test_copy_defaults_and_name_validation(tmp_path: Path) -> None:
    """复制实例默认名称依次避让、默认目录落在源实例所属工作目录的 install 下。"""
    assert wslmanw.copy_default_name("Ubuntu", ("Ubuntu",)) == "Ubuntu-copy"
    assert wslmanw.copy_default_name("Ubuntu", ("Ubuntu", "Ubuntu-copy")) == "Ubuntu-copy2"
    assert wslmanw.copy_name_problem("Ubuntu-copy", ("Ubuntu",)) is None
    assert wslmanw.copy_name_problem("  ", ("Ubuntu",)) == "实例名称不能为空"
    assert "已存在" in str(wslmanw.copy_name_problem("ubuntu", ("Ubuntu",)))
    assert "路径分隔符" in str(wslmanw.copy_name_problem("a/b", ("Ubuntu",)))

    settings = wslmanw.WslmanwSettings(work_dir=str(tmp_path / "wsl"))
    target = wslmanw.copy_default_dir(settings, str(tmp_path / "wsl" / "install" / "Ubuntu"), "Ubuntu-copy")
    assert target == tmp_path / "wsl" / "install" / "Ubuntu-copy"
    outside = wslmanw.copy_default_dir(settings, str(tmp_path / "elsewhere"), "X")
    assert outside == tmp_path / "wsl" / "install" / "X"
