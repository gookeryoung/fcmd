"""Bun 来源：GitHub Release（zip + ``SHASUMS256.txt``）。

Bun 没有独立的版本清单站点：精确版本拼 Release URL，``latest`` 读 GitHub
API 的 latest release（带 User-Agent，遵守匿名速率限制）。
"""

from __future__ import annotations

import json
import platform
import re

from fcmd.apps.devman._errors import invalid_argument, not_found, runtime_error
from fcmd.apps.devman._install import ArtifactPlan
from fcmd.apps.devman._net import Fetcher
from fcmd.apps.devman._paths import Layout

__all__ = ["LATEST_API", "detect_arch", "plan_install", "release_url", "resolve_version"]

LATEST_API = "https://api.github.com/repos/oven-sh/bun/releases/latest"
_RELEASE = "https://github.com/oven-sh/bun/releases/download/bun-v{version}/{asset}"
_EXACT_RE = re.compile(r"^\d+\.\d+\.\d+$")


def detect_arch() -> str:
    """Bun Windows 资产只有 x64；arm64 暂无官方 zip。"""
    return "x64" if platform.machine().lower() not in {"arm64", "aarch64"} else "arm64"


def release_url(version: str, arch: str) -> str:
    return _RELEASE.format(version=version, asset=f"bun-windows-{arch}.zip")


def resolve_version(fetcher: Fetcher, raw: str) -> str:
    value = (raw or "latest").strip()
    if _EXACT_RE.fullmatch(value):
        return value
    if value.lower() == "latest":
        try:
            body = json.loads(fetcher.get_text(LATEST_API))
        except ValueError as exc:
            raise runtime_error(f"Bun 最新版本信息无法解析：{exc}", hint="稍后重试或改用精确版本") from exc
        tag = body.get("tag_name") if isinstance(body, dict) else None
        if not isinstance(tag, str):
            raise not_found("Bun 最新版本信息缺少 tag_name", hint="改用精确版本，如 bun@1.1.38")
        reported = tag.removeprefix("bun-v").lstrip("v")
        if not _EXACT_RE.fullmatch(reported):
            raise not_found(f"无法解析 Bun 版本：{tag!r}", hint="改用精确版本，如 bun@1.1.38")
        return reported
    if re.fullmatch(r"\d+(\.\d+)?", value):
        raise invalid_argument(
            f"Bun 不支持版本前缀 {value!r}",
            hint="用精确版本（如 bun@1.1.38）或 latest",
            field="spec",
        )
    raise not_found(f"无法解析 Bun 版本 {raw!r}", hint="例如 bun@1.1.38、bun@latest")


def plan_install(
    fetcher: Fetcher,
    layout: Layout,
    raw: str,
    *,
    sha256: str | None = None,
    arch: str | None = None,
) -> ArtifactPlan:
    version = resolve_version(fetcher, raw)
    wanted_arch = arch or detect_arch()
    url = release_url(version, wanted_arch)
    if sha256 is not None:
        sha256 = sha256.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise invalid_argument("--sha256 必须是 64 位十六进制", field="sha256")
    filename = f"bun-windows-{wanted_arch}.zip"
    return ArtifactPlan(
        tool="bun",
        version=version,
        filename=filename,
        urls=(url,),
        sha_url=f"https://github.com/oven-sh/bun/releases/download/bun-v{version}/SHASUMS256.txt",
        dest=layout.version_dir("bun", version),
        cache_path=layout.cache_dir / filename,
        executable="bun.exe",
        check_args=("--version",),
        check_contains=version,
        expected_sha256=sha256,
        expected_verify="explicit-sha256" if sha256 else "official-shasums",
    )
