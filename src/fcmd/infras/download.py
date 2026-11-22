"""流式下载：统一的 ``.part`` 落盘与进度回调。

只覆盖「简单下载」：没有镜像回退、断点续传与代理，那部分属于各 app 的产品
语义（wslman 镜像探测、llm 多源）。本模块提供两件可复用的
原语：

- :func:`download_file`：``url`` → 文件，先写 ``.part``，成功后原子落位；
- :func:`write_stream`：把已打开的响应流按块写入文件并回报进度。

``OSError`` / ``urllib`` 异常原样抛给调用方，由调用方决定重试或降级。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "ProgressCallback",
    "SpeedMeter",
    "download_file",
    "format_eta",
    "format_speed",
    "write_stream",
]

ProgressCallback = Callable[[int, int], None]
"""进度回调：``(已写入字节, 总字节)``；总数未知时为 0。"""

CHUNK_SIZE = 1 << 20
"""默认分块大小（1 MiB）。"""

DEFAULT_USER_AGENT = "fcmd/1.0 (+https://github.com/fcmd)"


class SpeedMeter:
    """按时间采样估算下载速度（指数平滑），供 GUI 显示速度与剩余时间。

    只依赖单调时钟，便于单测传入 ``now``；多次 ``update`` 使用平滑系数，
    避免网络抖动导致数字乱跳。进度回退（切换文件 / 重新下载）会自动重置。
    """

    def __init__(self, *, smoothing: float = 0.35, min_interval: float = 0.2) -> None:
        self._smoothing = min(max(smoothing, 0.0), 1.0)
        self._min_interval = max(min_interval, 0.0)
        self._last_done = 0
        self._last_time: float | None = None
        self._speed = 0.0

    def reset(self, *, done: int = 0) -> None:
        """重置采样（开始新任务 / 进度回退时调用）。"""
        self._last_done = max(0, done)
        self._last_time = None
        self._speed = 0.0

    @property
    def speed(self) -> float:
        """当前平滑速度（字节/秒）。"""
        return self._speed

    def update(self, done: int, *, now: float | None = None) -> float:
        """喂入累计字节数，返回最新速度；时间间隔太短时保持上一次估算。"""
        current = time.monotonic() if now is None else now
        if done < self._last_done:
            self.reset(done=done)
        if self._last_time is None:
            self._last_time = current
            self._last_done = done
            return self._speed
        elapsed = current - self._last_time
        if elapsed < self._min_interval:
            return self._speed
        instant = max(0.0, (done - self._last_done) / elapsed)
        self._speed = instant if self._speed <= 0 else self._smoothing * instant + (1 - self._smoothing) * self._speed
        self._last_done = done
        self._last_time = current
        return self._speed

    def eta(self, total: int, *, done: int | None = None) -> float | None:
        """估算剩余秒数；速度未知 / 已完成时返回 ``None``。"""
        if self._speed <= 0 or total <= 0:
            return None
        remaining = total - (self._last_done if done is None else done)
        return max(0.0, remaining / self._speed)


def _format_bytes(size: int) -> str:
    """字节数 → 人类可读（infras 不反向依赖 apps 层的 human_size）。"""
    value = float(max(0, size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


def format_speed(bytes_per_second: float) -> str:
    """速度文案：``12.3 MB/s``；未知速度显示 ``--``。"""
    if bytes_per_second <= 0:
        return "--"
    return f"{_format_bytes(int(bytes_per_second))}/s"


def format_eta(seconds: float | None) -> str:
    """剩余时间文案：``12s`` / ``3m05s`` / ``1h02m``；未知时为空串。"""
    if seconds is None:
        return ""
    value = max(0, int(seconds))
    if value < 60:
        return f"{value}s"
    minutes, secs = divmod(value, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def write_stream(
    source: BinaryIO,
    sink: BinaryIO,
    *,
    progress: ProgressCallback | None = None,
    done: int = 0,
    total: int = 0,
) -> int:
    """把 ``source`` 分块写入 ``sink``，返回累计写入字节数。"""
    while chunk := source.read(CHUNK_SIZE):
        sink.write(chunk)
        done += len(chunk)
        if progress is not None:
            progress(done, total)
    return done


def download_file(
    url: str,
    dest: Path,
    *,
    timeout: float = 60.0,
    user_agent: str = DEFAULT_USER_AGENT,
    progress: ProgressCallback | None = None,
) -> None:
    """流式下载 ``url`` 到 ``dest``：先写 ``dest.part``，成功后原子替换。"""
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as sink:
            write_stream(response, sink, progress=progress)
        partial.replace(dest)
    except OSError:
        partial.unlink(missing_ok=True)
        raise
