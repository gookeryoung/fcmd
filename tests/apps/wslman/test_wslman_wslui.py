"""WSLUI 镜像源：解析、匹配、落盘名与自动测速择优。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _images, _wslui

SAMPLE = {
    "data": {
        "update_time": "2026-11-22T00:00:00Z",
        "distros": [
            {
                "name": "Ubuntu",
                "version": "24.04",
                "sources": [
                    {
                        "url": "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-releases/24.04/ubuntu-24.04-wsl-amd64.wsl",
                        "mirror": "tsinghua",
                        "format": "wsl",
                    },
                    {
                        "url": "https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/ubuntu-24.04-wsl-amd64.wsl",
                        "mirror": "ustc",
                        "format": "wsl",
                    },
                ],
            }
        ],
    }
}


def _entry(name: str = "Ubuntu-24.04", url: str = "https://releases.ubuntu.com/24.04/a.wsl") -> _images.ImageEntry:
    return _images.ImageEntry(
        name=name,
        friendly_name="Ubuntu 24.04 LTS",
        sources=("manifest",),
        downloads=(_images.ImageDownload("x86_64", url),),
    )


def test_parse_catalog_reads_wslui_sources() -> None:
    distros = _wslui.parse_catalog(SAMPLE)
    assert len(distros) == 1
    distro = distros[0]
    assert distro.display_name == "Ubuntu 24.04"
    assert [source.mirror for source in distro.sources] == ["tsinghua", "ustc"]
    assert distro.sources[0].label == "清华 TUNA"
    assert distro.sources[1].label == "中科大 USTC"


def test_match_sources_by_name_version_and_official_filename() -> None:
    catalog = _wslui.WsluiCatalog(_wslui.parse_catalog(SAMPLE))
    sources = _wslui.match_sources(catalog, _entry())
    assert [source.mirror for source in sources] == ["tsinghua", "ustc"]
    # 名称对不上时，官方下载文件名与镜像文件名一致也能匹配。
    other = _entry("Custom", "https://releases.ubuntu.com/24.04/ubuntu-24.04-wsl-amd64.wsl")
    assert _wslui.match_sources(catalog, other) == sources
    assert _wslui.match_sources(None, _entry()) == ()


def test_download_filename() -> None:
    catalog = _wslui.WsluiCatalog(_wslui.parse_catalog(SAMPLE))
    sources = _wslui.match_sources(catalog, _entry())
    assert _wslui.download_filename(_entry(), sources[0]) == "Ubuntu-24.04-x86_64.wsl"
    xz = _wslui.WsluiSource("https://mirrors.ustc.edu.cn/a/rootfs.tar.xz", "ustc", "tar.xz")
    assert _wslui.download_filename(_entry(), xz) == "Ubuntu-24.04-x86_64.tar.xz"


def test_rank_sources_prefers_throughput_then_latency_fallback() -> None:
    sources = tuple(_wslui.WsluiSource(f"https://{host}/f", host, "tar") for host in ("slow", "fast"))
    ranked = _wslui.rank_sources(
        sources,
        probe=lambda url: 1.0 if "fast" in url else 2.0,
        latency=lambda url: pytest.fail("有吞吐结果时不应测延迟"),
    )
    assert [source.mirror for source in ranked] == ["slow", "fast"]

    ranked = _wslui.rank_sources(
        sources,
        probe=lambda _url: 0.0,
        latency=lambda url: 5.0 if "fast" in url else 50.0,
    )
    assert [source.mirror for source in ranked] == ["fast", "slow"]


def test_load_catalog_caches_and_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    calls: list[str] = []

    def fake_http(url: str, *, timeout: float = 20.0) -> Any:
        calls.append(url)
        if "helper/install" in url:
            return {"data": {"online_distros": {"url": "https://mirror.example/list.json"}}}
        return SAMPLE

    monkeypatch.setattr(_wslui, "_http_json", fake_http)
    first = _wslui.load_catalog()
    assert first.from_cache is False
    assert first.distros
    assert json.loads(_wslui.cache_path().read_text(encoding="utf-8"))["schema"] == _wslui._CACHE_SCHEMA

    second = _wslui.load_catalog()
    assert second.from_cache is True
    assert len(calls) == 2  # 第二次直接命中新鲜缓存，不再联网

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("offline")

    monkeypatch.setattr(_wslui, "_http_json", broken)
    stale = _wslui.load_catalog(force=True)
    assert stale.from_cache is True and stale.stale is True
    assert stale.warnings


def test_match_sources_falls_back_to_name_only_for_unversioned_entries() -> None:
    catalog = _wslui.WsluiCatalog(
        (
            _wslui.WsluiDistro("Debian", "13", (_wslui.WsluiSource("https://mirror/debian.tar.xz", "tuna", "tar.xz"),)),
            _wslui.WsluiDistro("Kali", "current", (_wslui.WsluiSource("https://mirror/kali.tar.xz", "nju", "tar.xz"),)),
            _wslui.WsluiDistro(
                "AlmaLinux", "10", (_wslui.WsluiSource("https://mirror/alma10.tar.xz", "bfsu", "tar.xz"),)
            ),
        )
    )
    debian = _images.ImageEntry(
        "Debian",
        "Debian GNU/Linux",
        ("manifest",),
        (_images.ImageDownload("x86_64", "https://salsa.debian.org/Debian_WSL_AMD64_v1.26.0.0.wsl"),),
    )
    assert [source.mirror for source in _wslui.match_sources(catalog, debian)] == ["tuna"]
    kali = _images.ImageEntry(
        "kali-linux",
        "Kali Linux Rolling",
        ("manifest",),
        (_images.ImageDownload("x86_64", "https://kali.download/kali-linux.wsl"),),
    )
    assert [source.mirror for source in _wslui.match_sources(catalog, kali)] == ["nju"]
    # 带数字版本时不做前缀降级，避免 AlmaLinux-8 拿到 10 的镜像源。
    alma8 = _images.ImageEntry(
        "AlmaLinux-8",
        "AlmaLinux OS 8",
        ("manifest",),
        (_images.ImageDownload("x86_64", "https://github.com/AlmaLinux-8.wsl"),),
    )
    assert _wslui.match_sources(catalog, alma8) == ()


def test_wslui_candidates_carry_own_filename_and_skip_official_sha() -> None:
    catalog = _wslui.WsluiCatalog(_wslui.parse_catalog(SAMPLE))
    entry = _entry()
    candidates = _wslui.wslui_candidates(entry, catalog, arch="x86_64")
    assert [candidate.ident for candidate in candidates] == ["tsinghua", "ustc"]
    assert candidates[0].filename == "Ubuntu-24.04-x86_64.wsl"
    assert candidates[0].trusted is False
    assert candidates[0].verify is False
    assert candidates[0].sha256 is None
