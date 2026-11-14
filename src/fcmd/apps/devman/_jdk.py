"""JDK 来源：Adoptium（Eclipse Temurin）v3 API。

只取 Windows 的 JDK zip；校验值由 API 的 ``binary.package.checksum`` 提供，
不依赖 GitHub Release 的清单文件。版本号保留 Adoptium 的 semver（含 ``+build``）。
"""

from __future__ import annotations

import json
import platform
import re
import urllib.parse
from typing import Any

from fcmd.apps.devman._errors import invalid_argument, not_found, runtime_error
from fcmd.apps.devman._install import ArtifactPlan
from fcmd.apps.devman._net import Fetcher
from fcmd.apps.devman._paths import Layout

__all__ = [
    "AVAILABLE",
    "RELEASES",
    "detect_arch",
    "plan_install",
    "release_names",
    "resolve_version",
]

API = "https://api.adoptium.net/v3"
AVAILABLE = f"{API}/info/available_releases"
RELEASES = f"{API}/info/release_names?architecture={{arch}}&image_type=jdk&os=windows&vendor=eclipse&release_type=ga&sort_order=DESC&page_size=20"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


def detect_arch() -> str:
    return "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"


def _assets_url(feature: str, arch: str) -> str:
    return (
        f"{API}/assets/latest/{feature}/hotspot?architecture={arch}"
        "&image_type=jdk&os=windows&vendor=eclipse"
    )


def _version_url(version: str, arch: str) -> str:
    encoded = urllib.parse.quote(version, safe="")
    return (
        f"{API}/assets/version/{encoded}?architecture={arch}"
        "&image_type=jdk&os=windows&vendor=eclipse"
    )


def _load_list(text: str, what: str) -> list[Any]:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise runtime_error(f"{what}无法解析：{exc}", hint="稍后重试或检查网络代理") from exc
    if not isinstance(raw, list):
        raise runtime_error(f"{what}格式不符合预期（不是数组）")
    return raw


def _pick_asset(rows: list[Any], what: str) -> tuple[str, dict[str, Any]]:
    for row in rows:
        if not isinstance(row, dict):
            continue
        version = row.get("version")
        binary = row.get("binary")
        package = binary.get("package") if isinstance(binary, dict) else None
        if not isinstance(version, dict) or not isinstance(package, dict):
            continue
        semver = version.get("semver")
        link = package.get("link")
        checksum = package.get("checksum")
        name = package.get("name")
        if not (isinstance(semver, str) and semver):
            continue
        if not (isinstance(link, str) and link):
            continue
        if not (isinstance(checksum, str) and checksum):
            continue
        if not (isinstance(name, str) and name):
            continue
        if _SHA_RE.fullmatch(checksum.lower()) and name.endswith(".zip"):
            return semver, package
    raise not_found(f"{what}里没有可用的 Windows JDK zip", hint="换一个 feature 版本或稍后重试")


def _latest_feature(fetcher: Fetcher, raw: str) -> str:
    if raw not in {"latest", "lts"}:
        return raw
    try:
        info = json.loads(fetcher.get_text(AVAILABLE))
    except ValueError as exc:
        raise runtime_error(f"Adoptium 版本信息无法解析：{exc}", hint="稍后重试或检查网络代理") from exc
    key = "most_recent_lts" if raw == "lts" else "most_recent_feature_release"
    feature = info.get(key) if isinstance(info, dict) else None
    if not isinstance(feature, int):
        raise not_found("Adoptium 没有返回可用的 feature 版本", hint="改用 jdk@21 这类明确版本")
    return str(feature)


def _resolve_asset(fetcher: Fetcher, raw: str, arch: str) -> tuple[str, dict[str, Any]]:
    value = (raw or "latest").strip()
    if value in {"latest", "lts"} or value.isdigit():
        return _pick_asset(
            _load_list(fetcher.get_text(_assets_url(_latest_feature(fetcher, value), arch)), "Adoptium 构件索引"),
            f"Adoptium {value}",
        )
    if _SEMVER_RE.match(value):
        return _pick_asset(
            _load_list(fetcher.get_text(_version_url(value, arch)), "Adoptium 构件索引"),
            f"Adoptium {value}",
        )
    raise not_found(f"无法解析 JDK 版本 {raw!r}", hint="例如 jdk@21、jdk@21.0.5+11、jdk@lts")


def resolve_version(fetcher: Fetcher, raw: str) -> str:
    return _resolve_asset(fetcher, raw, detect_arch())[0]


def plan_install(
    fetcher: Fetcher,
    layout: Layout,
    raw: str,
    *,
    sha256: str | None = None,
    arch: str | None = None,
) -> ArtifactPlan:
    wanted_arch = arch or detect_arch()
    version, package = _resolve_asset(fetcher, raw, wanted_arch)
    if sha256 is not None:
        sha256 = sha256.strip().lower()
        if not _SHA_RE.fullmatch(sha256):
            raise invalid_argument("--sha256 必须是 64 位十六进制", field="sha256")
    filename = package["name"]
    return ArtifactPlan(
        tool="jdk",
        version=version,
        filename=filename,
        urls=(package["link"],),
        sha_url="",
        dest=layout.version_dir("jdk", version),
        cache_path=layout.cache_dir / filename,
        executable="bin/java.exe",
        check_args=("-version",),
        check_contains=version.split("+")[0],
        expected_sha256=sha256 or package["checksum"].lower(),
        expected_verify="explicit-sha256" if sha256 else "official-api",
    )


def release_names(fetcher: Fetcher, arch: str | None = None) -> list[str]:
    """远端可用版本（``jdk-21.0.5+11`` → ``21.0.5+11``），供 ``versions --remote``。"""
    text = fetcher.get_text(RELEASES.format(arch=arch or detect_arch()))
    rows = _load_list(text, "Adoptium 版本列表")
    names: list[str] = []
    for row in rows:
        if isinstance(row, str) and row.startswith("jdk-"):
            names.append(row.removeprefix("jdk-"))
    return names
