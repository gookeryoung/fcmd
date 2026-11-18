"""infras.download 的流式写入与落盘行为测试。"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pytest

from fcmd.infras.download import download_file, write_stream


class _FakeResponse:
    """带上下文管理协议的响应替身。"""

    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stream.close()

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


def test_write_stream_reports_cumulative_progress() -> None:
    """按块写入并回报累计字节数。"""
    seen: list[tuple[int, int]] = []
    written = write_stream(io.BytesIO(b"abcdef"), io.BytesIO(), progress=lambda d, t: seen.append((d, t)), total=6)
    assert written == 6
    assert seen == [(6, 6)]


def test_write_stream_resumes_from_done() -> None:
    """续传时从已有字节数继续累加。"""
    sink = io.BytesIO()
    assert write_stream(io.BytesIO(b"cd"), sink, done=2, total=4) == 4
    assert sink.getvalue() == b"cd"


def test_download_file_writes_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功后目标文件就位，且不留下 .part。"""
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        requests.append(request)
        return _FakeResponse(b"font-bytes")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "nested" / "font.ttf"
    download_file("https://example.com/font.ttf", target, user_agent="fcmd-test/1.0")
    assert target.read_bytes() == b"font-bytes"
    assert not target.with_name("font.ttf.part").exists()
    assert requests[0].get_header("User-agent") == "fcmd-test/1.0"


def test_download_file_cleans_partial_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载失败时删除 .part，不产生半成品。"""

    def fake_urlopen(_request: urllib.request.Request, timeout: float) -> _FakeResponse:
        raise OSError("连接中断")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "font.ttf"
    with pytest.raises(OSError):
        download_file("https://example.com/font.ttf", target)
    assert list(tmp_path.iterdir()) == []
