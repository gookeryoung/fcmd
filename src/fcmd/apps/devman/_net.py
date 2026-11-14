"""网络访问：文本获取、构件下载、sha256 校验。

``Fetcher`` 是协议，安装流程只依赖协议；单元测试注入假实现，不联网。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

__all__ = ["FetchError", "Fetcher", "ProgressCallback", "UrllibFetcher", "sha256_file"]

ProgressCallback = Callable[[int, int], None]
_CHUNK = 1024 * 1024
_USER_AGENT = "fcmd-devman/1.0 (+https://github.com/)"


class FetchError(Exception):
    """传输层失败；由调用方翻译成 ``DOWNLOAD_FAILED`` / ``CHECKSUM_UNAVAILABLE``。"""


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


class UrllibFetcher:
    """基于标准库的实现；超时与重试来自配置。"""

    def __init__(self, *, timeout_s: int = 120, retries: int = 1) -> None:
        self._timeout = timeout_s
        self._retries = retries

    def _open(self, url: str):
        # 延迟导入：工具发现阶段不加载 ssl 等重型库
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        return urllib.request.urlopen(request, timeout=self._timeout)

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
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")

        def action() -> None:
            with self._open(url) as response, partial.open("wb") as sink:
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else 0
                done = 0
                while chunk := response.read(_CHUNK):
                    sink.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)

        self._with_retry(action)
        partial.replace(dest)


def devnull_progress(done: int, total: int) -> None:
    """占位回调（GUI 会替换成进度条）。"""
