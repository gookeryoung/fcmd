"""Deno 来源：官方 ``release-latest.txt`` + CDN/GitHub 的 zip 与校验值。

Deno 只有一条发布线（无 LTS）；精确版本可直接拼 URL，``latest`` 读官方
文本文件，版本前缀不展开（避免依赖 GitHub API 的速率限制）。
下载优先走 ``dl.deno.land`` 官方 CDN（国内通常比 GitHub Release 快），
GitHub Release 仅作回退；两处构件与校验清单内容一致。
"""

from __future__ import annotations

import platform
import re

from fcmd.apps.devman._errors import invalid_argument, not_found
from fcmd.apps.devman._install import ArtifactPlan
from fcmd.apps.devman._net import Fetcher
from fcmd.apps.devman._paths import Layout

__all__ = ["LATEST_URL", "detect_arch", "plan_install", "release_url", "resolve_version"]

LATEST_URL = "https://dl.deno.land/release-latest.txt"
_CDN = "https://dl.deno.land/release/v{version}/{asset}"
_GITHUB = "https://github.com/denoland/deno/releases/download/v{version}/{asset}"
_EXACT_RE = re.compile(r"^\d+\.\d+\.\d+$")


def detect_arch() -> str:
    """Deno 资产的架构段（``x86_64`` / ``aarch64``）。"""
    return "aarch64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"


def release_url(version: str, arch: str) -> str:
    """官方 CDN 地址（下载首选）。"""
    return _CDN.format(version=version, asset=f"deno-{arch}-pc-windows-msvc.zip")


def mirror_url(version: str, arch: str) -> str:
    """GitHub Release 地址（CDN 不可达时的回退）。"""
    return _GITHUB.format(version=version, asset=f"deno-{arch}-pc-windows-msvc.zip")


def resolve_version(fetcher: Fetcher, raw: str) -> str:
    """``latest`` 读官方文本，精确版本直接返回，前缀给明确提示。"""
    value = (raw or "latest").strip()
    if _EXACT_RE.fullmatch(value):
        return value
    if value.lower() == "latest":
        reported = fetcher.get_text(LATEST_URL).strip().lstrip("v")
        if not _EXACT_RE.fullmatch(reported):
            raise not_found(f"无法解析 Deno 最新版本：{reported!r}", hint="稍后重试或改用精确版本")
        return reported
    if re.fullmatch(r"\d+(\.\d+)?", value):
        raise invalid_argument(
            f"Deno 不支持版本前缀 {value!r}",
            hint="用精确版本（如 deno@2.1.4）或 latest",
            field="spec",
        )
    raise not_found(f"无法解析 Deno 版本 {raw!r}", hint="例如 deno@2.1.4、deno@latest")


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
    return ArtifactPlan(
        tool="deno",
        version=version,
        filename=f"deno-{wanted_arch}-pc-windows-msvc.zip",
        urls=(url, mirror_url(version, wanted_arch)),
        sha_url=url + ".sha256sum",
        dest=layout.version_dir("deno", version),
        cache_path=layout.cache_dir / f"deno-{wanted_arch}-pc-windows-msvc.zip",
        executable="deno.exe",
        check_args=("--version",),
        check_contains=f"deno {version}",
        expected_sha256=sha256,
        expected_verify="explicit-sha256" if sha256 else "official-shasums",
    )
