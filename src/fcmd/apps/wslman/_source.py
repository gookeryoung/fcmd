"""下载源：清单解析、HTTPS 下载、续传与摘要校验。

来源分层见 ADR 0023 / D8：``auto`` 优先 ``wsl --install --no-launch``，失败落到清单，
最后才是本地文件。这里只负责清单与文件两条路径；CLI 安装由 ``wslman.py`` 调度。
"""

from __future__ import annotations

import contextlib
import json
import platform
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from fcmd._compat import override
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

ProgressCallback = Callable[[int, int | None], None]
"""下载进度回调：``(累计已写字节, 总量或 None)``；CLI 不传，GUI 用它在状态栏显示进度。"""

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
    def redirect_request(  # noqa: PLR0913, PLR0917 - urllib 回调签名
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
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


_CANCEL_POLL_S = 0.2
"""看门狗轮询间隔：取消 / 停滞的判定粒度，也是取消后最长的响应延迟。"""

_STALL_TIMEOUT_S = 30.0
"""连续多久没有收到新数据就判定连接卡死；``urlopen`` 的读超时要等满 60 秒，
而且超时后连接也不能继续用，所以用它把「卡住」提前变成明确错误。"""


class _StallWatch:
    """下载读循环的看门狗：取消或长时间无数据时主动 ``close()`` 响应。

    ``urllib`` 的 ``read`` 只受 socket 超时约束；连接卡住时读循环会一直阻塞，
    取消标记也没机会被检查到。旁路线程每 :data:`_CANCEL_POLL_S` 秒检查一次，
    一旦取消就把响应关掉，阻塞中的 ``read`` 立刻返回 / 抛错，调用方再按
    ``reason`` 区分「用户取消」与「连接停滞」。
    """

    def __init__(
        self,
        response: _Response,
        *,
        cancelled: Callable[[], bool] | None,
        stall_timeout: float,
    ) -> None:
        self._response = response
        self._cancelled = cancelled
        self._stall_timeout = max(stall_timeout, 0.0)
        self._last = time.monotonic()
        self._stop = threading.Event()
        self.reason = ""
        self._thread = threading.Thread(target=self._loop, name="wslman-download-watch", daemon=True)

    def touch(self) -> None:
        """读循环收到数据时调用，重置停滞计时。"""
        self._last = time.monotonic()

    def __enter__(self) -> _StallWatch:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _loop(self) -> None:
        while not self._stop.wait(_CANCEL_POLL_S):
            if self._cancelled is not None and self._cancelled():
                self.reason = "cancelled"
            elif time.monotonic() - self._last > self._stall_timeout:
                self.reason = "stalled"
            else:
                continue
            close = getattr(self._response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
            return


def _open_response(
    open_fn: _Opener,
    request: urllib.request.Request,
    timeout: float,
    cancelled: Callable[[], bool] | None,
) -> _Response:
    """建立连接；有取消标记时不让阻塞的 ``open`` 把取消拖到超时之后。

    ``opener.open`` 在建连 / 等响应头的阶段不受读循环控制，网络卡住时会一直
    占用到 ``timeout``。把这一步放进旁路守护线程，调用线程每
    :data:`_CANCEL_POLL_S` 秒检查一次取消；取消后不再等它，迟到的响应由线程
    自己关掉，避免半开连接泄漏。
    """
    if cancelled is None:
        return open_fn(request, timeout)
    if cancelled():
        raise KeyboardInterrupt
    box: dict[str, Any] = {}
    done = threading.Event()
    abandoned = threading.Event()

    def run() -> None:
        try:
            box["response"] = open_fn(request, timeout)
        except BaseException as exc:  # 交给调用线程按原类型重新抛出
            box["error"] = exc
        finally:
            done.set()
            response = box.get("response")
            close = getattr(response, "close", None)
            if abandoned.is_set() and callable(close):
                with contextlib.suppress(Exception):
                    close()

    threading.Thread(target=run, name="wslman-download-connect", daemon=True).start()
    while not done.wait(_CANCEL_POLL_S):
        if cancelled():
            abandoned.set()
            raise KeyboardInterrupt
    if "error" in box:
        raise box["error"]
    return box["response"]


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
    progress: ProgressCallback | None = None,
    stall_timeout: float = _STALL_TIMEOUT_S,
) -> DownloadResult:
    """下载到 ``<target>.part``，支持 Range 续传，校验后原子改名。

    摘要不匹配时删除本次 ``.part`` 并以 ``CHECK_FAILED`` 退出；用户已有文件
    （``--source file``）不会走这里，因此不会被误删。

    ``progress(done, total)`` 按约 0.1 秒的节奏回报累计字节数与总量（总量未知
    时为 ``None``），GUI 用它显示进度 / 速度；CLI 不传就保持原来的 stderr 进度。

    读循环按 ``read1`` / 小块读取，避免一次 ``read(1 MiB)`` 在慢速或卡住的连接上
    长时间不返回、进度看起来完全不动。``cancelled`` 每轮检查一次，旁路看门狗会在
    取消时关闭响应，让阻塞中的读立刻结束；连续 ``stall_timeout`` 秒没有新数据则按
    「停滞」报错，不再等 socket 自己超时。
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
    watch: _StallWatch | None = None
    try:
        response = _open_response(opener or _default_open, request, timeout, cancelled)
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
            read1 = getattr(response, "read1", None)
            read = read1 if callable(read1) else response.read
            written = start
            last_report = 0.0
            if progress is not None:
                progress(written, total)
            with _StallWatch(response, cancelled=cancelled, stall_timeout=stall_timeout) as watch:
                try:
                    with part.open("ab" if resumed else "wb") as handle:
                        while True:
                            if cancelled is not None and cancelled():
                                raise KeyboardInterrupt
                            data = read(chunk)
                            if not data:
                                break
                            watch.touch()
                            handle.write(data)
                            written += len(data)
                            if progress is not None:
                                now = time.monotonic()
                                if now - last_report >= _PROGRESS_INTERVAL_S:
                                    last_report = now
                                    progress(written, total)
                finally:
                    reporter.stop()
                # 看门狗可能刚刚关掉响应（读循环因此以 EOF / 异常收尾），这里统一定性。
                if watch.reason == "cancelled" or (cancelled is not None and cancelled()):
                    raise KeyboardInterrupt
                if watch.reason == "stalled":
                    raise runtime_error(
                        f"下载停滞：连续 {stall_timeout:g} 秒没有收到新数据",
                        hint=f"已保留 {part}；检查网络或换一个下载源后重试会尝试续传",
                    )
            if progress is not None:
                progress(written, total)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except KeyboardInterrupt:
        raise
    except WslmanError:
        raise
    except Exception as exc:
        cancelled_now = cancelled is not None and cancelled()
        if (watch is not None and watch.reason == "cancelled") or cancelled_now:
            raise KeyboardInterrupt from exc
        if (watch is not None and watch.reason == "stalled") or isinstance(exc, TimeoutError):
            raise runtime_error(
                f"下载超时：连接长时间没有响应（{stall_timeout:g} 秒无新数据）",
                hint=f"已保留 {part}；检查网络或换一个下载源后重试会尝试续传",
            ) from exc
        if isinstance(exc, urllib.error.HTTPError):
            raise runtime_error(
                f"下载失败（HTTP {exc.code}）：{url}",
                hint=f"保留的临时文件：{part}；重试或改用 --source file",
            ) from exc
        if isinstance(exc, (urllib.error.URLError, OSError)):
            raise runtime_error(
                f"下载失败：{exc}",
                hint=f"保留的临时文件：{part}；网络恢复后重试会尝试续传",
            ) from exc
        raise

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


_PROGRESS_INTERVAL_S = 0.1
"""进度回调的最小间隔：GUI 状态栏按 10 Hz 刷新足够顺滑，又不至于刷爆事件队列。"""

_MAX_RESUME_ATTEMPTS = 6
"""同一来源断流后最多续传几次；每次只要有字节推进就继续，避免坏网络下一遍遍从头下。"""

_SPEED_POLL_S = 0.5
"""下载中速度看门狗的轮询间隔；只用于「明显太慢就换源」，不参与正常进度上报。"""

_MIN_SWITCH_SPEED = 512 * 1024
"""自动下载的最低可接受速度（字节/秒）；低于它且还有其它候选时换源。

测速窗口只看前几秒，CDN 常见的「首段很快、随后限速」只能靠下载中的持续速度兜住。
"""

_SPEED_GRACE_S = 5.0
"""下载开始后先跑多久再判断速度：避开建连、TLS、首包与磁盘预分配造成的瞬时低值。

这个宽限期也是「换源前的纯等待」，对首个候选来说就是首字节之后的空转时间；
5 秒足够跨过一个窗口的限速抖动，又不会让一个坏源白占太久。
"""

_SPEED_WINDOW_S = 1.5
"""速度看门狗的统计窗口：只看最近 1.5 秒，CDN 中途限速不会被全程均值掩盖。"""


def download_mirrored(  # noqa: PLR0913
    target: Path,
    *,
    candidates: Sequence[Candidate],
    expected_sha256: str | None = None,
    label: str = "下载",
    cancelled: Callable[[], bool] | None = None,
    opener: _Opener | None = None,
    progress: ProgressCallback | None = None,
    min_speed: int = _MIN_SWITCH_SPEED,
    speed_grace: float = _SPEED_GRACE_S,
) -> DownloadResult:
    """按候选顺序下载：自动测速后的最快源优先，失败或校验不过就换下一个。

    候选来自 :func:`fcmd.apps.wslman._mirrors.candidates` /
    :func:`fcmd.apps.wslman._wslui.wslui_candidates`。每个候选可以带自己的落盘名
    （``filename``，不同格式的制品不会写进同一个文件）、自己的摘要（``sha256``）与
    ``verify`` 标记：WSLUI 的 ``tar.xz`` 与官方 ``.wsl`` 不是同一份字节，不拿官方
    sha256 校验它。``source`` 记录实际用的那个候选。

    ``min_speed`` 是下载中持续速度的下限：测速窗口只看开头几秒，源在首段之后被
    限速时，只要还有后备源就切到下一个，避免整份文件以 KB/s 慢慢拉完。
    """
    sentinel = Path(target)
    kept_part = sentinel
    failures: list[str] = []
    for index, candidate in enumerate(candidates):
        last_candidate = index == len(candidates) - 1
        candidate_target = sentinel.parent / candidate.filename if candidate.filename else sentinel
        expected = candidate.sha256 or (expected_sha256 if candidate.verify else None)

        # 测速是「下载前」的短窗口；这里再看「下载中」的持续速度。首段很快、随后
        # 被 CDN 限到 KB/s 的源，只有真正开始写盘才暴露。只对还有后备源的候选启用。
        tracker = {"done": 0.0}
        slow = threading.Event()
        stop_watch = threading.Event()

        def report(
            done: int,
            total: int | None,
            tracker: dict[str, float] = tracker,
            progress: ProgressCallback | None = progress,
        ) -> None:
            tracker["done"] = float(done)
            if progress is not None:
                progress(done, total)

        def monitor(
            slow: threading.Event = slow,
            stop_watch: threading.Event = stop_watch,
            tracker: dict[str, float] = tracker,
            speed_grace: float = speed_grace,
            min_speed: int = min_speed,
        ) -> None:
            started = time.monotonic()
            last_time = started
            last_done = 0.0
            while not stop_watch.wait(_SPEED_POLL_S):
                now = time.monotonic()
                if now - started < speed_grace:
                    continue
                if now - last_time < _SPEED_WINDOW_S:
                    continue
                done = tracker["done"]
                # 用最近一个窗口的速度，不用全程均值：首段 10 MB/s、随后限到 10 KB/s
                # 的源，全程均值会长时间保持「看起来不慢」。
                speed = (done - last_done) / (now - last_time)
                last_done = done
                last_time = now
                if speed < min_speed:
                    slow.set()
                    return

        def local_cancel(slow: threading.Event = slow, cancelled: Callable[[], bool] | None = cancelled) -> bool:
            return slow.is_set() or (cancelled is not None and cancelled())

        watch = min_speed > 0 and not last_candidate
        watcher = threading.Thread(target=monitor, name="wslman-speed-watch", daemon=True) if watch else None
        if watcher is not None:
            watcher.start()
        try:
            for _attempt in range(_MAX_RESUME_ATTEMPTS):
                if local_cancel():
                    raise KeyboardInterrupt
                part = part_path(candidate_target)
                meta_path = _meta_for(part)
                kept_part = part
                before = part.stat().st_size if part.is_file() else 0
                try:
                    result = download(
                        candidate.url,
                        candidate_target,
                        expected_sha256=expected,
                        opener=opener,
                        label=label,
                        cancelled=local_cancel if watch or cancelled is not None else None,
                        progress=report if progress is not None or watch else None,
                    )
                except KeyboardInterrupt:
                    if slow.is_set():
                        failures.append(f"{candidate.label}：持续低于 {min_speed / 1024:.0f} KB/s，已换源")
                        part.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                        break
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
        finally:
            stop_watch.set()
            if watcher is not None:
                watcher.join(timeout=1.0)
    kept = kept_part.stat().st_size if kept_part.is_file() else 0
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
