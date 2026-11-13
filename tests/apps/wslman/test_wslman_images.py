"""wslman：可用镜像的两源合并、缓存与降级。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.wslman import _images
from fcmd.apps.wslman._decode import OnlineRow
from fcmd.apps.wslman._errors import WslmanError, runtime_error

_SHA = "a" * 64

_MANIFEST = {
    "Default": "Ubuntu",
    "ModernDistributions": {
        "Ubuntu": [
            {
                "Name": "Ubuntu",
                "FriendlyName": "Ubuntu",
                "Default": True,
                "Amd64Url": {"Url": "https://e/ubuntu-amd64.wsl", "Sha256": _SHA},
                "Arm64Url": {"Url": "https://e/ubuntu-arm64.wsl", "Sha256": "0x" + "B" * 64},
            },
            {
                "Name": "Ubuntu-20.04",
                "FriendlyName": "Ubuntu 20.04 LTS",
                "Amd64Url": "https://e/u2004.wsl",
            },
        ],
        "Others": [
            {
                "Name": "Debian",
                "FriendlyName": "Debian GNU/Linux",
                "Amd64Url": {"Url": "https://e/debian.wsl", "Sha256": "not-a-digest"},
            }
        ],
    },
    "Distributions": [
        {
            "Name": "OracleLinux_9_5",
            "FriendlyName": "Oracle Linux 9.5",
            "Amd64": True,
            "Arm64": False,
            "Amd64PackageUrl": "https://e/oracle.appx",
            "Arm64PackageUrl": "https://e/oracle-arm.appx",
        },
        {
            "Name": "Ubuntu",
            "FriendlyName": "Ubuntu (store)",
            "Amd64PackageUrl": "https://e/ubuntu.appx",
        },
    ],
}


def test_parse_distribution_info_modern_and_legacy() -> None:
    entries = {entry.name: entry for entry in _images.parse_distribution_info(_MANIFEST)}

    assert entries["Ubuntu"].sources == ("manifest",)
    assert entries["Ubuntu"].arches == ("aarch64", "x86_64")
    assert entries["Ubuntu"].has_sha256 is True
    digests = {download.arch: download.sha256 for download in entries["Ubuntu"].downloads}
    assert digests["x86_64"] == _SHA
    assert digests["aarch64"] == "b" * 64  # 0x 前缀被规范化

    # 裸 URL：保留地址但没有校验和。
    assert entries["Ubuntu-20.04"].downloads[0].url == "https://e/u2004.wsl"
    assert entries["Ubuntu-20.04"].has_sha256 is False

    # 非法 sha256 当作没有，不标 verified。
    assert entries["Debian"].has_sha256 is False

    # 旧版 Store 条目：走 appx，仅含声明的架构；同名现代条目优先。
    oracle = entries["OracleLinux_9_5"]
    assert oracle.sources == ("store",)
    assert oracle.arches == ("x86_64",)
    assert oracle.has_sha256 is False
    assert entries["Ubuntu"].sources == ("manifest",)


def test_parse_distribution_info_rejects_bad_payload() -> None:
    with pytest.raises(WslmanError):
        _images.parse_distribution_info(b"{not json")
    with pytest.raises(WslmanError):
        _images.parse_distribution_info([1, 2])


def test_merge_entries_keeps_cli_order_and_backfills() -> None:
    manifest = _images.parse_distribution_info(_MANIFEST)
    rows = (
        OnlineRow("Ubuntu", "Ubuntu"),
        OnlineRow("Debian", ""),
        OnlineRow("OracleLinux_9_5", "Oracle Linux 9.5"),
    )
    merged = _images.merge_entries(rows, manifest)

    assert [entry.name for entry in merged] == ["Ubuntu", "Debian", "OracleLinux_9_5", "Ubuntu-20.04"]
    by_name = {entry.name: entry for entry in merged}
    assert by_name["Ubuntu"].sources == ("cli", "manifest")
    assert by_name["Ubuntu"].friendly_name == "Ubuntu"
    assert by_name["Debian"].friendly_name == "Debian GNU/Linux"  # CLI 无友好名时回落到清单
    assert by_name["Debian"].sources == ("cli", "manifest")
    assert by_name["OracleLinux_9_5"].sources == ("cli", "store")
    assert by_name["Ubuntu-20.04"].sources == ("manifest",)


def test_merge_entries_deduplicates_case_insensitively() -> None:
    rows = (OnlineRow("ubuntu", ""), OnlineRow("Ubuntu", "dup"))
    merged = _images.merge_entries(rows, ())
    assert [entry.name for entry in merged] == ["ubuntu"]


def test_cache_roundtrip_and_tolerates_damage(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    entries = _images.parse_distribution_info(_MANIFEST)
    _images.save_cache(entries, 1_700_000_000, path)

    loaded = _images.load_cache(path)
    assert loaded is not None
    assert loaded.fetched_at == 1_700_000_000
    assert [entry.name for entry in loaded.entries] == [entry.name for entry in entries]
    assert loaded.entries[0].downloads == entries[0].downloads

    path.write_text("{oops", encoding="utf-8")
    assert _images.load_cache(path) is None

    path.write_text(json.dumps({"schema": "other/1", "entries": [], "fetched_at": 1}), encoding="utf-8")
    assert _images.load_cache(path) is None

    assert _images.load_cache(tmp_path / "missing.json") is None


def test_collect_images_fresh_merges_and_writes_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    catalog = _images.collect_images(
        fetch_cli=lambda: (OnlineRow("Ubuntu", ""),),
        fetch_manifest=lambda: _images.parse_distribution_info(_MANIFEST),
        now=lambda: 1_700_000_000.0,
        cache_file=cache,
    )

    assert catalog.from_cache is False
    assert catalog.stale is False
    assert catalog.sources == ("cli", "manifest")
    assert catalog.fetched_at == 1_700_000_000.0
    assert [entry.name for entry in catalog.entries][:2] == ["Ubuntu", "Ubuntu-20.04"]
    assert cache.exists()


def test_collect_images_partial_failure_backfills_from_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    _images.collect_images(
        fetch_cli=lambda: (OnlineRow("Ubuntu", ""), OnlineRow("Debian", "")),
        fetch_manifest=lambda: _images.parse_distribution_info(_MANIFEST),
        now=lambda: 1_700_000_000.0,
        cache_file=cache,
    )

    catalog = _images.collect_images(
        fetch_cli=lambda: (_ for _ in ()).throw(runtime_error("down")),
        fetch_manifest=lambda: _images.parse_distribution_info(_MANIFEST),
        now=lambda: 1_700_010_000.0,
        cache_file=cache,
    )

    assert catalog.stale is True
    assert catalog.sources == ("manifest",)
    assert {entry.name for entry in catalog.entries} >= {"Ubuntu", "Debian", "Ubuntu-20.04"}
    assert any("缓存" in warning for warning in catalog.warnings)
    assert catalog.fetched_at == 1_700_000_000.0  # 不把旧数据标成刚刚获取


def test_collect_images_uses_fresh_cache_without_network(tmp_path: Path) -> None:
    """TTL 内命中缓存：两个抓取函数都不应被调用。"""
    cache = tmp_path / "cache.json"
    _images.save_cache(
        _images.parse_distribution_info(_MANIFEST),
        1_700_000_000,
        cache,
        ("cli", "manifest"),
    )

    def boom_cli() -> tuple[OnlineRow, ...]:
        raise AssertionError("TTL 内不应访问在线源")

    def boom_manifest() -> tuple[_images.ImageEntry, ...]:
        raise AssertionError("TTL 内不应访问在线源")

    catalog = _images.collect_images(
        fetch_cli=boom_cli,
        fetch_manifest=boom_manifest,
        now=lambda: 1_700_000_000.0 + _images.CACHE_TTL_SECONDS - 1,
        cache_file=cache,
    )

    assert catalog.from_cache is True
    assert catalog.stale is False
    assert catalog.sources == ("cli", "manifest")
    assert catalog.fetched_at == 1_700_000_000


def test_collect_images_all_sources_fail_uses_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    _images.save_cache(_images.parse_distribution_info(_MANIFEST), 1_600_000_000, cache)

    catalog = _images.collect_images(
        fetch_cli=lambda: (_ for _ in ()).throw(runtime_error("cli down")),
        fetch_manifest=lambda: (_ for _ in ()).throw(runtime_error("json down")),
        now=lambda: 1_700_000_000.0,
        cache_file=cache,
    )

    assert catalog.from_cache is True
    assert catalog.stale is True
    assert catalog.sources == ("cache",)
    assert len(catalog.entries) == len(_images.parse_distribution_info(_MANIFEST))
    assert any("已过期" in warning for warning in catalog.warnings)


def test_collect_images_all_sources_fail_without_cache_raises(tmp_path: Path) -> None:
    with pytest.raises(WslmanError) as excinfo:
        _images.collect_images(
            fetch_cli=lambda: (_ for _ in ()).throw(runtime_error("cli down")),
            fetch_manifest=lambda: (_ for _ in ()).throw(runtime_error("json down")),
            now=lambda: 1_700_000_000.0,
            cache_file=tmp_path / "cache.json",
        )
    assert excinfo.value.code == "RUNTIME_ERROR"


def test_cli_failure_without_cache_keeps_original_error() -> None:
    """没有缓存可降级时，CLI 失败即报错（既有退出码语义）。"""
    with pytest.raises(WslmanError) as excinfo:
        _images.collect_images(
            fetch_cli=lambda: (_ for _ in ()).throw(runtime_error("cli down")),
            fetch_manifest=lambda: _images.parse_distribution_info(_MANIFEST),
            now=lambda: 1_700_000_000.0,
            cache_file=None,
        )
    assert str(excinfo.value) == "cli down"


def test_mark_local_state_is_case_insensitive() -> None:
    entries = _images.parse_distribution_info(_MANIFEST)
    marked = _images.mark_local_state(entries, installed=["ubuntu"], backups=["UBUNTU-20.04"])
    by_name = {entry.name: entry for entry in marked}
    assert by_name["Ubuntu"].installed is True
    assert by_name["Ubuntu"].backed_up is False
    assert by_name["Ubuntu-20.04"].backed_up is True
    assert by_name["Debian"].installed is False
