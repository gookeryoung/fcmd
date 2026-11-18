"""流式下载：统一的 ``.part`` 落盘与进度回调。

只覆盖「简单下载」：没有镜像回退、断点续传与代理，那部分属于各 app 的产品
语义（wslman 镜像探测、devman 续传、locllm 多源）。本模块提供两件可复用的
原语：

- :func:`download_file`：``url`` → 文件，先写 ``.part``，成功后原子落位；
- :func:`write_stream`：把已打开的响应流按块写入文件并回报进度。

``OSError`` / ``urllib`` 异常原样抛给调用方，由调用方决定重试或降级。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "ProgressCallback",
    "download_file",
    "write_stream",
]

ProgressCallback = Callable[[int, int], None]
"""进度回调：``(已写入字节, 总字节)``；总数未知时为 0。"""

CHUNK_SIZE = 1 << 20
"""默认分块大小（1 MiB）。"""

DEFAULT_USER_AGENT = "fcmd/1.0 (+https://github.com/fcmd)"


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
