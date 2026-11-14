"""网络层的下载、校验与重试测试（不联网）。"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.devman import _net


class FakeResponse:
    """``urlopen`` 返回值的最小替身。"""

    def __init__(
        self, chunks: Iterable[bytes], *, content_length: str | None = None, status: int | None = None
    ) -> None:
        self._chunks = list(chunks)
        self.headers = {"Content-Length": content_length} if content_length is not None else {}
        self.status = status

    def read(self, size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    import hashlib

    target = tmp_path / "blob.bin"
    payload = b"devman" * 100_000
    target.write_bytes(payload)
    assert _net.sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_get_text_decodes_utf8_with_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse([b"hello ", b"\xff"]),
    )
    assert _net.UrllibFetcher().get_text("https://example.invalid/x") == "hello \ufffd"


def test_download_writes_part_then_replaces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse([b"abc", b"def"], content_length="6"),
    )
    progress: list[tuple[int, int]] = []
    dest = tmp_path / "cache" / "node.zip"
    _net.UrllibFetcher().download(
        "https://example.invalid/n.zip", dest, lambda done, total: progress.append((done, total))
    )
    assert dest.read_bytes() == b"abcdef"
    assert not dest.with_name(dest.name + ".part").exists()
    assert progress == [(3, 6), (6, 6)]


def test_download_without_content_length(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: FakeResponse([b"x" * 10]))
    dest = tmp_path / "f.bin"
    _net.UrllibFetcher().download("https://example.invalid/f", dest)
    assert dest.read_bytes() == b"x" * 10


def test_retryable_http_error_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[int] = []

    def flaky(request: Any, timeout: Any = None) -> FakeResponse:
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)  # type: ignore[arg-type]
        return FakeResponse([b"ok"])

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    monkeypatch.setattr(_net.time, "sleep", lambda seconds: None)
    assert _net.UrllibFetcher(retries=1).get_text("https://example.invalid/x") == "ok"
    assert len(calls) == 2


def test_non_retryable_http_error_raises_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(request: Any, timeout: Any = None) -> FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 404, "gone", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", missing)
    with pytest.raises(_net.FetchError) as info:
        _net.UrllibFetcher(retries=3).get_text("https://example.invalid/x")
    assert "404" in str(info.value)


def test_url_error_raises_fetch_error_after_retries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    attempts: list[int] = []

    def broken(request: Any, timeout: Any = None) -> FakeResponse:
        attempts.append(1)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    monkeypatch.setattr(_net.time, "sleep", lambda seconds: None)
    with pytest.raises(_net.FetchError) as info:
        _net.UrllibFetcher(retries=2).download("https://example.invalid/f", tmp_path / "f.bin")
    assert len(attempts) == 3
    assert "offline" in str(info.value)


def test_timeout_maps_to_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(request: Any, timeout: Any = None) -> FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", slow)
    with pytest.raises(_net.FetchError):
        _net.UrllibFetcher(retries=0).get_text("https://example.invalid/x")


def test_devnull_progress_accepts_anything() -> None:
    assert _net.devnull_progress(1, 2) is None


# ---------------------------------------------------------------------- #
# 断点续传
# ---------------------------------------------------------------------- #


def test_download_resumes_with_range_header(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dest = tmp_path / "cache" / "node.zip"
    partial = dest.with_name(dest.name + ".part")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"abc")
    seen: list[str | None] = []

    def resumed(request: Any, timeout: Any = None) -> FakeResponse:
        seen.append(request.get_header("Range"))
        return FakeResponse([b"def"], content_length="3", status=206)

    monkeypatch.setattr(urllib.request, "urlopen", resumed)
    progress: list[tuple[int, int]] = []
    _net.UrllibFetcher().download(
        "https://example.invalid/n.zip", dest, lambda done, total: progress.append((done, total))
    )
    assert seen == ["bytes=3-"]
    assert dest.read_bytes() == b"abcdef"
    assert not partial.exists()
    assert progress == [(6, 6)]


def test_download_without_range_support_truncates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dest = tmp_path / "n.zip"
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(b"abc")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse([b"xyz"], content_length="3", status=200),
    )
    _net.UrllibFetcher().download("https://example.invalid/n.zip", dest)
    assert dest.read_bytes() == b"xyz"


def test_download_416_discards_partial_and_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dest = tmp_path / "n.zip"
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(b"too-large")
    ranges: list[str | None] = []

    def flaky(request: Any, timeout: Any = None) -> FakeResponse:
        value = request.get_header("Range")
        ranges.append(value)
        if value:
            raise urllib.error.HTTPError(request.full_url, 416, "range", {}, None)  # type: ignore[arg-type]
        return FakeResponse([b"ok"], status=200)

    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    _net.UrllibFetcher(retries=0).download("https://example.invalid/n.zip", dest)
    assert ranges == ["bytes=9-", None]
    assert dest.read_bytes() == b"ok"
    assert not partial.exists()


# ---------------------------------------------------------------------- #
# 代理
# ---------------------------------------------------------------------- #


def test_normalize_proxy_variants() -> None:
    assert _net.normalize_proxy(None) is None
    assert _net.normalize_proxy("  ") == ""
    assert _net.normalize_proxy("OFF") == ""
    assert _net.normalize_proxy("127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert _net.normalize_proxy("https://proxy.example:8443") == "https://proxy.example:8443"


def test_proxy_opener_receives_both_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeOpener:
        def open(self, request: Any, timeout: Any = None) -> FakeResponse:
            return FakeResponse([b"ok"])

    def build(*handlers: Any) -> FakeOpener:
        captured["handler"] = handlers[0]
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert _net.UrllibFetcher(proxy="proxy.example:8080").get_text("https://example.invalid/x") == "ok"
    assert captured["handler"].proxies == {"http": "http://proxy.example:8080", "https": "http://proxy.example:8080"}


def test_proxy_off_uses_empty_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeOpener:
        def open(self, request: Any, timeout: Any = None) -> FakeResponse:
            return FakeResponse([b"ok"])

    def build(*handlers: Any) -> FakeOpener:
        captured["handler"] = handlers[0]
        return FakeOpener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert _net.UrllibFetcher(proxy="off").get_text("https://example.invalid/x") == "ok"
    assert captured["handler"].proxies == {}
