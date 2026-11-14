"""压缩包类运行时的通用安装流水线（Go / Deno / Bun / JDK 共用）。

与 ``_node.install`` 保持同一套安全语义：缓存命中先校验、sha256 不匹配删缓存、
解压到 ``.staging-*``、跑主程序自检、原子落位、最后写标记；失败只清临时目录。
"""

from __future__ import annotations

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
    runtime_error,
)
from fcmd.apps.devman._net import Fetcher, FetchError, ProgressCallback, sha256_file
from fcmd.apps.devman._node import parse_shasums
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import MARKER_SCHEMA, now_iso, write_marker
from fcmd.apps.devman._toolchain import is_complete

__all__ = ["ArtifactPlan", "install_zip"]


@dataclass(frozen=True, slots=True)
class ArtifactPlan:
    """一个可下载的 zip 构件及其安装参数。"""

    tool: str
    version: str
    filename: str
    urls: tuple[str, ...]
    sha_url: str
    dest: Path
    cache_path: Path
    executable: str
    check_args: tuple[str, ...]
    check_contains: str
    expected_sha256: str | None = None
    expected_verify: str = "explicit-sha256"

    def to_data(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "version": self.version,
            "artifact": self.filename,
            "urls": list(self.urls),
            "sha256_url": self.sha_url or None,
            "dest": str(self.dest),
            "cache": str(self.cache_path),
        }


def _resolve_expected(plan: ArtifactPlan, fetcher: Fetcher) -> tuple[str, str]:
    if plan.expected_sha256:
        return plan.expected_sha256, plan.expected_verify
    if not plan.sha_url:
        raise runtime_error(
            f"没有可用的 {plan.tool} 校验清单",
            hint="用 --sha256 <64 位十六进制> 显式指定",
            code=CHECKSUM_UNAVAILABLE,
        )
    try:
        text = fetcher.get_text(plan.sha_url)
    except FetchError as exc:
        raise runtime_error(
            f"无法获取 {plan.tool} 校验清单：{exc}",
            hint="清单不可达时可用 --sha256 <64 位十六进制> 显式指定",
            code=CHECKSUM_UNAVAILABLE,
        ) from exc
    table = parse_shasums(text)
    expected = table.get(plan.filename)
    if expected is None:
        # Deno 等项目的 .sha256sum 是「Algorithm : SHA256 / Hash : <hex>」格式
        match = re.search(r"(?im)^\s*hash\s*:\s*([0-9a-f]{64})\s*$", text)
        if match:
            expected = match.group(1).lower()
    if expected is None:
        raise runtime_error(
            f"校验清单里没有 {plan.filename}",
            hint="确认版本号与架构；清单地址：" + plan.sha_url,
            code=CHECKSUM_UNAVAILABLE,
        )
    return expected, "official-shasums"


def _download(plan: ArtifactPlan, fetcher: Fetcher, progress: ProgressCallback | None) -> None:
    if plan.cache_path.is_file() and plan.expected_sha256 and sha256_file(plan.cache_path) == plan.expected_sha256:
        return
    problems: list[str] = []
    for url in plan.urls:
        try:
            fetcher.download(url, plan.cache_path, progress)
            return
        except FetchError as exc:
            problems.append(f"{url} → {exc}")
    raise runtime_error(
        f"{plan.tool} 构件下载失败：" + "；".join(problems),
        hint="检查网络或稍后重试",
        code=DOWNLOAD_FAILED,
    )


def install_zip(  # noqa: PLR0913 - 与 _node.install 的参数一一对应
    plan: ArtifactPlan,
    layout: Layout,
    *,
    fetcher: Fetcher,
    runner: Any,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """执行安装；失败只清临时目录，不写标记、不动既有版本。"""
    final = plan.dest
    if is_complete(layout, plan.tool, plan.version) and not force:
        return {
            "version": plan.version,
            "changed": False,
            "path": str(final),
            "message": "已安装，跳过（--force 可重装）",
            "actions": [],
        }

    expected, verify = _resolve_expected(plan, fetcher)
    plan = ArtifactPlan(
        tool=plan.tool,
        version=plan.version,
        filename=plan.filename,
        urls=plan.urls,
        sha_url=plan.sha_url,
        dest=plan.dest,
        cache_path=plan.cache_path,
        executable=plan.executable,
        check_args=plan.check_args,
        check_contains=plan.check_contains,
        expected_sha256=expected,
        expected_verify=plan.expected_verify,
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

    root = layout.tool_root(plan.tool)
    staging = root / f".staging-{plan.version}-{uuid.uuid4().hex[:8]}"
    unpacked = staging / "unpacked"
    root.mkdir(parents=True, exist_ok=True)
    try:
        extract_zip(plan.cache_path, unpacked)
        inner = single_root(unpacked) or unpacked
        staged_exe = inner / plan.executable
        if not staged_exe.is_file():
            raise runtime_error(
                f"压缩包里没有 {plan.executable}",
                hint="构件与工具不匹配，已放弃落位",
                code=DOWNLOAD_FAILED,
            )
        result = runner.run([str(staged_exe), *plan.check_args])
        reported = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        if result.returncode != 0 or plan.check_contains not in reported:
            raise runtime_error(
                f"新装的 {plan.tool} 自检失败：{reported or result.returncode}",
                hint="压缩包与版本不匹配，已放弃落位",
            )
        if final.exists():
            shutil.rmtree(final)
        inner.replace(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    source = plan.urls[0]
    write_marker(
        final,
        {
            "schema": MARKER_SCHEMA,
            "tool": plan.tool,
            "version": plan.version,
            "source": source,
            "sha256": expected,
            "verify": verify,
            "installed_at": now_iso(),
        },
    )
    return {
        "version": plan.version,
        "changed": True,
        "path": str(final),
        "message": f"已安装 {plan.tool} {plan.version}",
        "actions": [
            {"action": "download", "url": source, "sha256": expected, "verify": verify},
            {"action": "install", "path": str(final)},
        ],
    }
