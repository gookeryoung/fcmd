"""下载源：清单解析、HTTPS 下载、续传与摘要校验。

来源分层见 ADR 0023 / D8：``auto`` 优先 ``wsl --install --no-launch``，失败落到清单，
最后才是本地文件。这里只负责清单与文件两条路径；CLI 安装由 ``wslman.py`` 调度。
"""

from __future__ import annotations

import json
import platform
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, override

from fcmd.apps.wslman._errors import WslmanError, check_failed, invalid_argument, not_found, runtime_error
from fcmd.apps.wslman._mirrors import Candidate
from fcmd.apps.wslman._transfer import ProgressReporter, atomic_replace, part_path, sha256_file

__all__ = [
    "DownloadResult",
    "Manifest",
    "ManifestEntry",
    "current_arch",
    "download",
    "download_mirrored",
    "download_name",
    "fetch_manifest",
    "file_source",
    "find_entry",
    "parse_manifest",
]

_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_CHUNK = 1024 * 1024
_APPX_SUFFIXES = (".appx", ".appxbundle")
_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
}


class _Response(Protocol):
    status: int
    headers: Any

    def read(self, size: int = -1) -> bytes: ...


class _Opener(Protocol):
    def __call__(self, request: urllib.request.Request, timeout: float) -> _Response: ...


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    name: str
    url: str
    sha256: str | None = None
    size: int | None = None
    arch: str | None = None

    def to_data(self) -> dict[str, object]:
        return {"name": self.name, "url": self.url, "sha256": self.sha256, "size": self.size, "arch": self.arch}


@dataclass(frozen=True, slots=True)
class Manifest:
    arch: str
    entries: tuple[ManifestEntry, ...]
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    sha256: str
    size: int
    verified: bool
    resumed: bool
    source: str = "official"
    commands: tuple[tuple[str, ...], ...] = ()

    def to_data(self) -> dict[str, object]:
        return {
            "file": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size,
            "verified": self.verified,
            "resumed": self.resumed,
            "source": self.source,
            "commands": [list(argv) for argv in self.commands],
        }


def current_arch() -> str:
    """把 ``platform.machine()`` 归一到清单里的架构名。"""
    raw = platform.machine().strip().casefold()
    return _ARCH_ALIASES.get(raw, raw or "unknown")


def download_name(name: str, url: str | None, arch: str | None = None) -> str:
    """为下载结果挑一个稳定的文件名；URL 无扩展名时按名称与架构生成。"""
    if url:
        candidate = Path(urllib.parse.urlparse(url).path).name
        if candidate.casefold().endswith((".tar", ".tar.gz", ".tgz")):
            return candidate
    suffix = "tar.gz" if (url or "").casefold().endswith(".gz") else "tar"
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in name) or "distro"
    return f"{safe}-{arch or current_arch()}.{suffix}"


def _normalise_arch(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().casefold()
    return _ARCH_ALIASES.get(raw, raw)


def _digest(entry: Mapping[str, Any]) -> str | None:
    for key in ("sha256", "checksum", "sha256sum", "digest"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            digest = value.strip().casefold()
            if digest.startswith("sha256:"):
                digest = digest.split(":", 1)[1]
            return digest
    return None


def _size(entry: Mapping[str, Any]) -> int | None:
    for key in ("size", "size_bytes", "bytes"):
        value = entry.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _entries_of(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, Mapping):
        for key in ("distros", "items", "releases", "entries"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        # 兼容 ``{"Ubuntu": {...}}`` 这种以名称作键的写法。
        return [{"name": key, **value} for key, value in raw.items() if isinstance(value, Mapping)]
    return []


def parse_manifest(raw: bytes | str | Any, arch: str | None = None) -> Manifest:
    """解析清单，只保留与当前架构匹配、且不是 Store 包的条目。"""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise runtime_error(f"清单不是合法 JSON：{exc}", hint="检查 --manifest-url 指向的内容") from exc
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise runtime_error(f"清单不是合法 JSON：{exc}", hint="检查 --manifest-url 指向的内容") from exc

    wanted = (_normalise_arch(arch) or current_arch()) if arch else current_arch()
    entries: list[ManifestEntry] = []
    unsupported: list[str] = []
    for item in _entries_of(raw):
        if not isinstance(item, Mapping):
            continue
        name = item.get("name") or item.get("distro") or item.get("id")
        url = item.get("url") or item.get("download_url") or item.get("href")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        name = name.strip()
        url = url.strip()
        if not name or not url:
            continue
        entry_arch = _normalise_arch(item.get("arch") or item.get("architecture"))
        if entry_arch is not None and entry_arch != wanted:
            continue
        if urllib.parse.urlparse(url).path.casefold().endswith(_APPX_SUFFIXES):
            unsupported.append(name)
            continue
        entries.append(ManifestEntry(name=name, url=url, sha256=_digest(item), size=_size(item), arch=entry_arch))
    return Manifest(arch=wanted, entries=tuple(entries), unsupported=tuple(unsupported))


def find_entry(manifest: Manifest, name: str) -> ManifestEntry:
    """按名称取条目；Store 包给出改走 ``--source cli`` 的明确指引。"""
    key = name.casefold()
    for entry in manifest.entries:
        if entry.name.casefold() == key:
            return entry
    if any(candidate.casefold() == key for candidate in manifest.unsupported):
        raise runtime_error(
            f"清单中的 {name} 只有 .appx/.AppxBundle 包，格式不可直接导入",
            hint=f"改用 --source cli（wsl --install -d {name} --no-launch），或手工下载 tar 后运行 fcmd wslman import",
        )
    hint = (
        f"先手工下载 {name} 的 tar，再执行：fcmd wslman import {name} <导入文件> --dir <安装目录>\n"
        f"或改用系统安装：fcmd wslman download {name} --source cli"
    )
    raise not_found(f"清单中没有可导入的 {name}（架构 {manifest.arch}）", hint=hint)


class _HTTPSOnlyRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    @override
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if urllib.parse.urlparse(newurl).scheme.casefold() != "https":
            raise urllib.error.HTTPError(newurl, code, "只允许 HTTPS 重定向", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_open(request: urllib.request.Request, timeout: float) -> _Response:
    opener = urllib.request.build_opener(_HTTPSOnlyRedirect())
    return opener.open(request, timeout=timeout)


def _require_https(url: str, *, field: str) -> str:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.casefold() != "https" or not parsed.netloc:
        raise invalid_argument(f"只支持 HTTPS 地址：{url}", hint="换成 https:// 开头的下载地址", field=field)
    return url.strip()


def fetch_manifest(
    url: str,
    *,
    opener: _Opener | None = None,
    timeout: float = 30.0,
    max_bytes: int = _MAX_MANIFEST_BYTES,
) -> bytes:
    """读取清单；限制大小与重定向次数，错误统一转成带提示的运行错误。"""
    target = _require_https(url, field="manifest_url")
    request = urllib.request.Request(target, headers={"User-Agent": "fcmd-wslman/0.1"})
    try:
        response = (opener or _default_open)(request, timeout)
        try:
            payload = response.read(max_bytes + 1)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except WslmanError:
        raise
    except urllib.error.HTTPError as exc:
        raise runtime_error(
            f"清单不可达（HTTP {exc.code}）：{target}",
            hint="检查网络或 --manifest-url；离线时改用 --source file",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise runtime_error(
            f"清单不可达：{target}（{exc}）",
            hint="检查网络或代理；离线时改用 --source file 并手工下载 tar",
        ) from exc
    if len(payload) > max_bytes:
        raise runtime_error(f"清单超过 {max_bytes} 字节上限", hint="换一个更小的清单或改用 --source file")
    return payload


def _response_total(response: _Response, start: int) -> int | None:
    headers = response.headers
    if start and response.status == 206:
        content_range = headers.get("Content-Range") or ""
        match = re.search(r"/(\d+)\s*$", content_range)
        if match:
            return int(match.group(1))
        return None
    length = headers.get("Content-Length")
    if length is None:
        return None
    try:
        return start + int(length)
    except ValueError:
        return None


def _meta_for(part: Path) -> Path:
    return part.with_name(part.name + ".json")


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def download(  # noqa: PLR0912, PLR0913
    url: str,
    target: Path,
    *,
    expected_sha256: str | None = None,
    opener: _Opener | None = None,
    timeout: float = 60.0,
    chunk: int = _CHUNK,
    label: str = "下载",
    cancelled: Callable[[], bool] | None = None,
) -> DownloadResult:
    """下载到 ``<target>.part``，支持 Range 续传，校验后原子改名。

    摘要不匹配时删除本次 ``.part`` 并以 ``CHECK_FAILED`` 退出；用户已有文件
    （``--source file``）不会走这里，因此不会被误删。
    """
    target = Path(target)
    part = part_path(target)
    meta_path = _meta_for(part)
    part.parent.mkdir(parents=True, exist_ok=True)

    start = part.stat().st_size if part.is_file() else 0
    meta = _read_meta(meta_path) if start else {}
    headers = {"User-Agent": "fcmd-wslman/0.1"}
    if start:
        headers["Range"] = f"bytes={start}-"
        validator = meta.get("etag") or meta.get("last_modified")
        if isinstance(validator, str) and validator:
            headers["If-Range"] = validator

    request = urllib.request.Request(_require_https(url, field="url"), headers=headers)
    resumed = False
    try:
        response = (opener or _default_open)(request, timeout)
        try:
            total = _response_total(response, start)
            if start and response.status != 206:
                start = 0
                total = _response_total(response, 0)
            resumed = start > 0
            response_headers = response.headers
            meta_path.write_text(
                json.dumps(
                    {
                        "url": url,
                        "etag": response_headers.get("ETag"),
                        "last_modified": response_headers.get("Last-Modified"),
                        "total": total,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            reporter = ProgressReporter(part, total, label=label)
            reporter.start()
            try:
                with part.open("ab" if resumed else "wb") as handle:
                    while True:
                        if cancelled is not None and cancelled():
                            raise KeyboardInterrupt
                        data = response.read(chunk)
                        if not data:
                            break
                        handle.write(data)
            finally:
                reporter.stop()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except KeyboardInterrupt:
        raise
    except WslmanError:
        raise
    except urllib.error.HTTPError as exc:
        raise runtime_error(
            f"下载失败（HTTP {exc.code}）：{url}",
            hint=f"保留的临时文件：{part}；重试或改用 --source file",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise runtime_error(
            f"下载失败：{exc}",
            hint=f"保留的临时文件：{part}；网络恢复后重试会尝试续传",
        ) from exc

    written = part.stat().st_size
    if total is not None and written < total:
        # 连接中途断开时 read() 直接返回 EOF，不能当成功：保留 .part 供续传，
        # 否则会把截断的文件误报成「sha256 不匹配」并丢掉全部进度。
        raise runtime_error(
            f"下载中断：只收到 {written} / {total} 字节",
            hint=f"已保留 {part}，网络恢复后重试会自动续传",
        )
    digest = sha256_file(part)
    if expected_sha256 is not None and digest != expected_sha256.strip().casefold():
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
        raise check_failed(
            f"下载文件 sha256 不匹配：期望 {expected_sha256}，实际 {digest}",
            hint="已删除本次下载的 .part；请核对摘要或来源后重试",
        )

    size = part.stat().st_size
    atomic_replace(part, target)
    meta_path.unlink(missing_ok=True)
    return DownloadResult(
        path=target,
        sha256=digest,
        size=size,
        verified=expected_sha256 is not None,
        resumed=resumed,
    )


_MAX_RESUME_ATTEMPTS = 6
"""同一来源断流后最多续传几次；每次只要有字节推进就继续，避免坏网络下一遍遍从头下。"""


def download_mirrored(  # noqa: PLR0913
    target: Path,
    *,
    candidates: Sequence[Candidate],
    expected_sha256: str | None = None,
    label: str = "下载",
    cancelled: Callable[[], bool] | None = None,
    opener: _Opener | None = None,
) -> DownloadResult:
    """按候选顺序下载：国内镜像优先，失败或校验不过就换下一个，官方源兜底。

    候选来自 :func:`fcmd.apps.wslman._mirrors.candidates`，每个候选都用同一个官方
    sha256 校验，所以「换了源」不会降低可信度；``source`` 记录实际用的那个。
    """
    part = part_path(Path(target))
    meta_path = _meta_for(part)
    failures: list[str] = []
    for index, candidate in enumerate(candidates):
        last_candidate = index == len(candidates) - 1
        for _attempt in range(_MAX_RESUME_ATTEMPTS):
            before = part.stat().st_size if part.is_file() else 0
            try:
                result = download(
                    candidate.url,
                    target,
                    expected_sha256=expected_sha256,
                    opener=opener,
                    label=label,
                    cancelled=cancelled,
                )
            except KeyboardInterrupt:
                raise
            except WslmanError as exc:
                failures.append(f"{candidate.label}：{exc}")
                after = part.stat().st_size if part.is_file() else 0
                # 断流/超时只要还在推进就同源续传，最多 _MAX_RESUME_ATTEMPTS 次；
                # 校验不过说明内容不同，直接换源。
                if exc.code != "CHECK_FAILED" and after > before:
                    continue
                if not last_candidate:
                    # 换源必须从头下：不同来源的 .part 拼在一起会得到错误字节。
                    part.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                break
            return replace(result, source=candidate.ident)
    kept = part.stat().st_size if part.is_file() else 0
    resume = f"；已保留 {kept} 字节，重试会从断点续传" if kept else ""
    raise runtime_error(
        "所有下载源均失败：" + "；".join(failures),
        hint=f"检查网络后重试{resume}，或改用 --source file 指定本地归档",
    )


def file_source(path: Path, *, expected_sha256: str | None = None) -> DownloadResult:
    """校验用户提供的本地归档；不复制、不删除用户文件。"""
    target = Path(path)
    if not target.is_file():
        raise not_found(f"本地文件不存在：{target}", hint="检查 --file 路径后重试")
    digest = sha256_file(target)
    if expected_sha256 is not None and digest != expected_sha256.strip().casefold():
        raise check_failed(
            f"文件 sha256 不匹配：期望 {expected_sha256}，实际 {digest}",
            hint="文件未被删除；请核对摘要或换一个文件",
        )
    return DownloadResult(
        path=target,
        sha256=digest,
        size=target.stat().st_size,
        verified=expected_sha256 is not None,
        resumed=False,
        source="file",
    )
