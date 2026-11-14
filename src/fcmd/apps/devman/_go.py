"""Go 来源：官方 ``dl/?mode=json`` 索引、zip 构件与校验值。

Go 官方索引同时提供版本、文件名与 sha256，因此不需要单独的校验清单；
架构只有 ``amd64`` / ``arm64`` 两种。
"""

from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from typing import Any

from fcmd.apps.devman._errors import invalid_argument, not_found, runtime_error
from fcmd.apps.devman._install import ArtifactPlan
from fcmd.apps.devman._net import Fetcher
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._toolchain import sort_key

__all__ = ["DIST", "INDEX", "GoFile", "GoRelease", "detect_arch", "latest_matching", "parse_index", "plan_install"]

INDEX = "https://go.dev/dl/?mode=json&include=all"
DIST = "https://go.dev/dl"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def detect_arch() -> str:
    """Go 的 Windows 架构标记（``amd64`` / ``arm64``）。"""
    return "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"


@dataclass(frozen=True, slots=True)
class GoFile:
    filename: str
    os: str
    arch: str
    sha256: str
    size: int

    def to_data(self) -> dict[str, Any]:
        return {"filename": self.filename, "os": self.os, "arch": self.arch, "size": self.size}


@dataclass(frozen=True, slots=True)
class GoRelease:
    version: str
    stable: bool
    files: tuple[GoFile, ...]

    def to_data(self) -> dict[str, Any]:
        return {"version": self.version, "stable": self.stable}


def parse_index(text: str) -> list[GoRelease]:
    """解析官方索引；非法条目跳过而不是整体失败。"""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise runtime_error(f"Go 发布索引无法解析：{exc}", hint="稍后重试或检查网络代理") from exc
    if not isinstance(raw, list):
        raise runtime_error("Go 发布索引格式不符合预期（不是数组）")
    releases: list[GoRelease] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        version = item.get("version")
        if not isinstance(version, str) or not re.fullmatch(r"go\d+\.\d+(\.\d+)?", version):
            continue
        files: list[GoFile] = []
        for entry in item.get("files") or []:
            if not isinstance(entry, dict):
                continue
            filename = entry.get("filename")
            digest = entry.get("sha256")
            if not isinstance(filename, str) or not isinstance(digest, str) or not _SHA_RE.fullmatch(digest.lower()):
                continue
            files.append(
                GoFile(
                    filename=filename,
                    os=str(entry.get("os", "")),
                    arch=str(entry.get("arch", "")),
                    sha256=digest.lower(),
                    size=int(entry.get("size") or 0),
                )
            )
        releases.append(
            GoRelease(
                version=version.removeprefix("go"),
                stable=bool(item.get("stable")),
                files=tuple(files),
            )
        )
    return releases


def latest_matching(releases: list[GoRelease], raw: str) -> str:
    """在索引里解析 ``latest`` / 精确版本 / 版本前缀。"""
    stable = [release for release in releases if release.stable]
    if raw.lower() == "latest":
        if not stable:
            raise not_found("Go 索引里没有稳定版")
        return max(stable, key=lambda release: sort_key(release.version)).version
    wanted = tuple(int(part) for part in re.findall(r"\d+", raw))
    if not wanted:
        raise not_found(f"无法解析 Go 版本 {raw!r}", hint="例如 go@1.23.4、go@1.23、go@latest")
    candidates = [
        release.version
        for release in stable
        if tuple(int(part) for part in release.version.split("."))[: len(wanted)] == wanted
    ]
    if not candidates:
        raise not_found(f"远端没有匹配 {raw} 的 Go 版本", hint="用 fcmd devman versions go --remote 查看")
    return max(candidates, key=sort_key)


def plan_install(
    fetcher: Fetcher,
    layout: Layout,
    raw: str,
    *,
    sha256: str | None = None,
    arch: str | None = None,
) -> ArtifactPlan:
    """读取官方索引，组装 Go 安装计划（dry-run 会原样打印）。"""
    releases = parse_index(fetcher.get_text(INDEX))
    version = latest_matching(releases, raw)
    release = next(item for item in releases if item.version == version)
    wanted_arch = arch or detect_arch()
    artifact = next(
        (
            item
            for item in release.files
            if item.os == "windows" and item.arch == wanted_arch and item.filename.endswith(".zip")
        ),
        None,
    )
    if artifact is None:
        raise not_found(
            f"Go {version} 没有 windows/{wanted_arch} 的 zip 构件",
            hint="换一个版本或检查官方索引",
        )
    if sha256 is not None:
        sha256 = sha256.strip().lower()
        if not _SHA_RE.fullmatch(sha256):
            raise invalid_argument("--sha256 必须是 64 位十六进制", field="sha256")
    return ArtifactPlan(
        tool="go",
        version=version,
        filename=artifact.filename,
        urls=(f"{DIST}/{artifact.filename}",),
        sha_url="",
        dest=layout.version_dir("go", version),
        cache_path=layout.cache_dir / artifact.filename,
        executable="bin/go.exe",
        check_args=("version",),
        check_contains=f"go{version}",
        expected_sha256=sha256 or artifact.sha256,
        expected_verify="explicit-sha256" if sha256 else "official-index",
    )
