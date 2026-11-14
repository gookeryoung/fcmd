"""Node.js 来源：官方发布索引、``SHASUMS256.txt`` 与安装流水线。

构件可以走镜像，**校验清单一律取官方**；官方清单不可达时拒绝安装，
由调用方决定是否显式给 ``--sha256``。
"""

from __future__ import annotations

import platform
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.devman._archive import extract_zip, single_root
from fcmd.apps.devman._errors import (
    CHECKSUM_MISMATCH,
    CHECKSUM_UNAVAILABLE,
    DOWNLOAD_FAILED,
    check_failed,
    invalid_argument,
    not_found,
    runtime_error,
)
from fcmd.apps.devman._net import Fetcher, FetchError, ProgressCallback, sha256_file
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import MARKER_SCHEMA, now_iso, write_marker
from fcmd.apps.devman._toolchain import is_complete

__all__ = [
    "MIRROR_AUTO",
    "RELEASE_INDEX",
    "NodeRelease",
    "artifact_name",
    "detect_arch",
    "install",
    "latest_matching",
    "parse_release_index",
    "parse_shasums",
    "plan_install",
    "sha_url",
]

RELEASE_INDEX = "https://nodejs.org/dist/index.json"
OFFICIAL_DIST = "https://nodejs.org/dist"
MIRROR_AUTO = "auto"
_AUTO_MIRRORS = ("https://npmmirror.com/mirrors/node",)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_RE = re.compile(r"^\d+\.\d+\.\d+$")


def detect_arch() -> str:
    """Windows 架构标记（Node 的 zip 命名用 ``x64`` / ``arm64``）。"""
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return "x64"


def artifact_name(version: str, arch: str) -> str:
    return f"node-v{version}-win-{arch}.zip"


def sha_url(version: str) -> str:
    """校验清单永远取官方（镜像只提供构件字节）。"""
    return f"{OFFICIAL_DIST}/v{version}/SHASUMS256.txt"


def candidate_bases(mirror: str) -> list[str]:
    """按顺序尝试的构件来源；不含官方清单。"""
    value = (mirror or MIRROR_AUTO).strip().rstrip("/")
    if value in {"", MIRROR_AUTO}:
        return [OFFICIAL_DIST, *_AUTO_MIRRORS]
    if value == "official":
        return [OFFICIAL_DIST]
    return [value, OFFICIAL_DIST]


@dataclass(frozen=True, slots=True)
class NodeRelease:
    version: str
    lts: str | bool
    date: str | None

    def to_data(self) -> dict[str, Any]:
        return {"version": self.version, "lts": self.lts, "date": self.date}


def parse_release_index(text: str) -> list[NodeRelease]:
    """解析官方 ``index.json``；非法条目跳过而不是整体失败。"""
    import json

    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise runtime_error(f"Node 发布索引无法解析：{exc}", hint="稍后重试或检查网络代理") from exc
    if not isinstance(raw, list):
        raise runtime_error("Node 发布索引格式不符合预期（不是数组）")
    releases: list[NodeRelease] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        version = item.get("version")
        if not isinstance(version, str) or not _STABLE_RE.fullmatch(version.lstrip("v")):
            continue
        lts = item.get("lts", False)
        releases.append(
            NodeRelease(
                version=version.lstrip("v"),
                lts=lts if isinstance(lts, (str, bool)) else False,
                date=item.get("date") if isinstance(item.get("date"), str) else None,
            )
        )
    return releases


def _sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def latest_matching(releases: list[NodeRelease], raw: str) -> str:
    """在发布索引里解析 ``latest`` / ``lts`` / 版本前缀，返回具体版本号。"""
    stable = [release for release in releases if _STABLE_RE.fullmatch(release.version)]
    if raw.lower() == "lts":
        candidates = [release for release in stable if release.lts]
        if not candidates:
            raise not_found("没有可用的 Node LTS 版本", hint="检查发布索引或改用 latest")
        return max(candidates, key=lambda release: _sort_key(release.version)).version
    if raw.lower() == "latest":
        if not stable:
            raise not_found("发布索引里没有稳定版")
        return max(stable, key=lambda release: _sort_key(release.version)).version
    wanted = tuple(int(part) for part in re.findall(r"\d+", raw))
    if not wanted:
        raise not_found(f"无法解析 Node 版本前缀 {raw!r}", hint="例如 node@20、node@20.11.1")
    candidates = [
        release.version
        for release in stable
        if tuple(int(part) for part in release.version.split("."))[: len(wanted)] == wanted
    ]
    if not candidates:
        raise not_found(f"远端没有匹配 {raw} 的 Node 版本", hint="用 fcmd devman versions node --remote 查看")
    return max(candidates, key=_sort_key)


def parse_shasums(text: str) -> dict[str, str]:
    """解析 ``SHASUMS256.txt``：文件名 → 小写 sha256。"""
    table: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts
        digest = digest.lower()
        if _SHA_RE.fullmatch(digest):
            table[name.lstrip("*")] = digest
    return table


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """node 安装计划（dry-run 会原样打印）。"""

    version: str
    filename: str
    urls: tuple[str, ...]
    sha_url: str
    dest: Path
    cache_path: Path
    expected_sha256: str | None = None

    def to_data(self) -> dict[str, Any]:
        return {
            "tool": "node",
            "version": self.version,
            "artifact": self.filename,
            "urls": list(self.urls),
            "sha256_url": self.sha_url,
            "dest": str(self.dest),
            "cache": str(self.cache_path),
        }


def plan_install(
    layout: Layout,
    version: str,
    *,
    mirror: str = MIRROR_AUTO,
    arch: str | None = None,
    sha256: str | None = None,
) -> InstallPlan:
    """组装下载计划；``sha256`` 显式给出时跳过官方清单。"""
    arch = arch or detect_arch()
    filename = artifact_name(version, arch)
    urls = tuple(f"{base}/v{version}/{filename}" for base in candidate_bases(mirror))
    if sha256 is not None:
        sha256 = sha256.strip().lower()
        if not _SHA_RE.fullmatch(sha256):
            raise invalid_argument("--sha256 必须是 64 位十六进制", field="sha256")
    return InstallPlan(
        version=version,
        filename=filename,
        urls=urls,
        sha_url=sha_url(version),
        dest=layout.version_dir("node", version),
        cache_path=layout.cache_dir / filename,
        expected_sha256=sha256,
    )


def _resolve_expected(plan: InstallPlan, fetcher: Fetcher) -> tuple[str, str]:
    """返回 ``(sha256, verify)``；显式值优先，否则取官方清单。"""
    if plan.expected_sha256:
        return plan.expected_sha256, "explicit-sha256"
    try:
        table = parse_shasums(fetcher.get_text(plan.sha_url))
    except FetchError as exc:
        raise runtime_error(
            f"无法获取官方校验清单：{exc}",
            hint="官方清单不可达时可用 --sha256 <64 位十六进制> 显式指定",
            code=CHECKSUM_UNAVAILABLE,
        ) from exc
    expected = table.get(plan.filename)
    if expected is None:
        raise runtime_error(
            f"官方清单里没有 {plan.filename}",
            hint="确认版本号与架构；清单地址：" + plan.sha_url,
            code=CHECKSUM_UNAVAILABLE,
        )
    return expected, "official-shasums"


def _download(plan: InstallPlan, fetcher: Fetcher, progress: ProgressCallback | None) -> None:
    """按候选来源下载；全部失败抛 ``DOWNLOAD_FAILED``。"""
    if plan.cache_path.is_file() and plan.expected_sha256 and sha256_file(plan.cache_path) == plan.expected_sha256:
        return
    problems: list[str] = []
    for url in plan.urls:
        try:
            fetcher.download(url, plan.cache_path, progress)
            return
        except FetchError as exc:
            problems.append(f"{url} → {exc}")
            plan.cache_path.unlink(missing_ok=True)
    raise runtime_error(
        "Node 构件下载失败：" + "；".join(problems),
        hint="检查网络或配置 config.node.mirror",
        code=DOWNLOAD_FAILED,
    )


def install(  # noqa: PLR0913 - 安装流程参数与回滚语义一一对应
    plan: InstallPlan,
    layout: Layout,
    *,
    fetcher: Fetcher,
    runner: Any,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """执行安装；失败只清临时目录，不写标记、不动既有版本。"""
    final = plan.dest
    if is_complete(layout, "node", plan.version) and not force:
        return {
            "version": plan.version,
            "changed": False,
            "path": str(final),
            "message": "已安装，跳过（--force 可重装）",
            "actions": [],
        }

    expected, verify = _resolve_expected(plan, fetcher)
    plan = InstallPlan(
        version=plan.version,
        filename=plan.filename,
        urls=plan.urls,
        sha_url=plan.sha_url,
        dest=plan.dest,
        cache_path=plan.cache_path,
        expected_sha256=expected,
    )
    _download(plan, fetcher, progress)

    actual = sha256_file(plan.cache_path)
    if actual != expected:
        plan.cache_path.unlink(missing_ok=True)
        raise check_failed(
            f"sha256 校验失败：期望 {expected}，实际 {actual}",
            hint="构件可能被篡改或镜像不同步；已删除缓存文件",
            code=CHECKSUM_MISMATCH,
        )

    staging = layout.node_root / f".staging-{plan.version}-{uuid.uuid4().hex[:8]}"
    tmp_extract = staging / "unpacked"
    layout.node_root.mkdir(parents=True, exist_ok=True)
    try:
        extract_zip(plan.cache_path, tmp_extract)
        inner = single_root(tmp_extract)
        if inner is None:
            raise runtime_error("压缩包结构不符合预期（缺少顶层目录）", code=DOWNLOAD_FAILED)
        staging_exe = inner / "node.exe"
        if not staging_exe.is_file():
            raise runtime_error("压缩包里没有 node.exe", code=DOWNLOAD_FAILED)
        result = runner.run([str(staging_exe), "-v"])
        reported = (result.stdout or "").strip()
        if result.returncode != 0 or reported.lstrip("v") != plan.version:
            raise runtime_error(
                f"新装的 node 自检失败：{reported or result.stderr.strip() or result.returncode}",
                hint="压缩包与版本不匹配，已放弃落位",
            )
        if final.exists():
            shutil.rmtree(final)
        inner.replace(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    write_marker(
        final,
        {
            "schema": MARKER_SCHEMA,
            "tool": "node",
            "version": plan.version,
            "source": plan.urls[0],
            "sha256": expected,
            "verify": verify,
            "installed_at": now_iso(),
        },
    )
    return {
        "version": plan.version,
        "changed": True,
        "path": str(final),
        "message": f"已安装 node {plan.version}",
        "actions": [
            {"action": "download", "url": plan.urls[0], "sha256": expected, "verify": verify},
            {"action": "install", "path": str(final)},
        ],
    }
