"""网络访问：文本获取、构件下载、sha256 校验。

``Fetcher`` 是协议，安装流程只依赖协议；单元测试注入假实现，不联网。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "FetchError",
    "Fetcher",
    "ProgressCallback",
    "UrllibFetcher",
    "normalize_proxy",
    "sha256_file",
]

ProgressCallback = Callable[[int, int], None]
_CHUNK = 1024 * 1024
_USER_AGENT = "fcmd-devman/1.0 (+https://github.com/)"
_PROXY_DISABLED = {"off", "none", "direct", "false", "no"}


class FetchError(Exception):
    """传输层失败；由调用方翻译成 ``DOWNLOAD_FAILED`` / ``CHECKSUM_UNAVAILABLE``。"""


def normalize_proxy(value: str | None) -> str | None:
    """把配置里的 ``network.proxy`` 归一成三种形态。

    ``None`` 表示跟随系统（含 ``HTTP_PROXY`` 等环境变量）；空串表示显式禁用代理；
    其余返回带 scheme 的 URL（未写 scheme 时按 ``http://host:port`` 处理）。
    """
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() in _PROXY_DISABLED:
        return ""
    return text if "://" in text else f"http://{text}"


class Fetcher(Protocol):
    """安装流程需要的最小网络能力。"""

    def get_text(self, url: str) -> str:
        """读取文本（用于发布索引与校验清单）。"""

    def download(self, url: str, dest: Path, progress: ProgressCallback | None = None) -> None:
        """下载到 ``dest``；实现方负责先写 ``.part`` 再落位。"""


def sha256_file(path: Path) -> str:
    """流式计算文件 sha256（小写十六进制）。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class _RestartDownload(Exception):
    """内部信号：半成品与服务端不一致，丢弃 ``.part`` 后从头再来一次。"""


class UrllibFetcher:
    """基于标准库的实现；超时与重试来自配置。

    ``proxy`` 走 :func:`normalize_proxy`：``None`` 时不装 ``ProxyHandler``（保持
    标准库默认行为，测试也据此注入 ``urlopen``）；空串用空 ``ProxyHandler``
    显式绕过环境变量代理；其余值作为 http/https 代理。
    """

    def __init__(self, *, timeout_s: int = 120, retries: int = 1, proxy: str | None = None) -> None:
        self._timeout = timeout_s
        self._retries = retries
        self._proxy = normalize_proxy(proxy)
        self._opener: Any = None

    def _get_opener(self) -> Any:
        if self._proxy is None:
            return None
        if self._opener is None:
            import urllib.request

            proxies: dict[str, str] = {} if self._proxy == "" else {"http": self._proxy, "https": self._proxy}
            self._opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        return self._opener

    def _open(self, url: str, *, offset: int = 0):
        # 延迟导入：工具发现阶段不加载 ssl 等重型库
        import urllib.request

        headers = {"User-Agent": _USER_AGENT}
        if offset > 0:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        opener = self._get_opener()
        if opener is None:
            return urllib.request.urlopen(request, timeout=self._timeout)
        return opener.open(request, timeout=self._timeout)

    def _with_retry(self, action: Callable[[], None]) -> None:
        import urllib.error

        attempts = self._retries + 1
        for attempt in range(attempts):
            try:
                action()
                return
            except urllib.error.HTTPError as exc:
                if exc.code in {408, 429, 500, 502, 503, 504} and attempt + 1 < attempts:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise FetchError(f"HTTP {exc.code}：{exc.reason}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt + 1 < attempts:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise FetchError(str(exc)) from exc

    def get_text(self, url: str) -> str:
        chunks: list[bytes] = []

        def action() -> None:
            chunks.clear()
            with self._open(url) as response:
                while chunk := response.read(_CHUNK):
                    chunks.append(chunk)

        self._with_retry(action)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def download(self, url: str, dest: Path, progress: ProgressCallback | None = None) -> None:
        """下载到 ``dest``；先写 ``.part``，续传成功后才落位。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")

        def attempt() -> None:
            import urllib.error

            offset = partial.stat().st_size if partial.is_file() else 0
            try:
                response = self._open(url, offset=offset)
            except urllib.error.HTTPError as exc:
                # 半成品比服务端文件还大（换了构件 / 上次写坏）：丢弃后从头再来一次
                if exc.code == 416 and offset > 0:
                    partial.unlink(missing_ok=True)
                    raise _RestartDownload from exc
                raise
            with response as source:
                # 服务端不支持 Range 会返回 200，此时必须截断重写，不能追加
                if offset and getattr(source, "status", None) != 206:
                    offset = 0
                total_header = source.headers.get("Content-Length")
                length = int(total_header) if total_header and total_header.isdigit() else 0
                total = offset + length
                done = offset
                with partial.open("ab" if offset else "wb") as sink:
                    while chunk := source.read(_CHUNK):
                        sink.write(chunk)
                        done += len(chunk)
                        if progress is not None:
                            progress(done, total)

        def action() -> None:
            try:
                attempt()
            except _RestartDownload:
                attempt()

        self._with_retry(action)
        partial.replace(dest)


def devnull_progress(done: int, total: int) -> None:
    """占位回调（GUI 会替换成进度条）。"""
