"""``_source`` 的下载与清单边界分支：响应缺少 close、网络错误、截断与多源回退。"""

from __future__ import annotations

import hashlib
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from fcmd.apps.wslman import _mirrors, _source, _transfer
from fcmd.apps.wslman._errors import WslmanError, check_failed, runtime_error


class _BareResponse:
    """只有 ``read`` / ``status`` / ``headers`` 的响应，覆盖 ``close`` 缺失的分支。"""

    def __init__(self, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def _raising(exc: BaseException) -> _source._Opener:
    def opener(request: urllib.request.Request, timeout: float) -> _source._Response:
        raise exc

    return opener


def test_manifest_entry_to_data() -> None:
    entry = _source.ManifestEntry("Ubuntu", "https://example.com/u.tar", "a" * 64, 10, "x86_64")

    assert entry.to_data() == {
        "name": "Ubuntu",
        "url": "https://example.com/u.tar",
        "sha256": "a" * 64,
        "size": 10,
        "arch": "x86_64",
    }


def test_download_name_without_url_uses_name_and_arch() -> None:
    assert _source.download_name("Ubuntu 24.04", None, "aarch64") == "Ubuntu_24.04-aarch64.tar"


def test_fetch_manifest_tolerates_response_without_close() -> None:
    response = _BareResponse(b'{"ModernDistributions": {}}')

    payload = _source.fetch_manifest("https://example.com/m.json", opener=lambda request, timeout: response)

    assert payload == b'{"ModernDistributions": {}}'


def test_fetch_manifest_reraises_wslman_error() -> None:
    boom = runtime_error("清单不可达")

    with pytest.raises(WslmanError) as excinfo:
        _source.fetch_manifest("https://example.com/m.json", opener=_raising(boom))

    assert excinfo.value is boom


def test_response_total_ignores_unparsable_or_missing_length() -> None:
    assert _source._response_total(_BareResponse(b"", status=206, headers={"Content-Range": "bytes 0-1/*"}), 1) is None
    assert _source._response_total(_BareResponse(b""), 0) is None
    assert _source._response_total(_BareResponse(b"", headers={"Content-Length": "abc"}), 0) is None
    assert _source._response_total(_BareResponse(b"", status=206, headers={"Content-Range": "bytes 0-4/5"}), 1) == 5


def test_download_works_without_close_and_verifies(tmp_path: Path) -> None:
    body = b"ubuntu"
    response = _BareResponse(body, headers={"Content-Length": str(len(body))})
    target = tmp_path / "u.tar"

    result = _source.download(
        "https://example.com/u.tar",
        target,
        expected_sha256=hashlib.sha256(body).hexdigest(),
        opener=lambda request, timeout: response,
    )

    assert result.path == target
    assert result.size == len(body)
    assert result.verified is True
    assert target.read_bytes() == body


def test_download_reraises_wslman_error_and_wraps_network_error(tmp_path: Path) -> None:
    boom = runtime_error("下载被拒绝")
    with pytest.raises(WslmanError) as excinfo:
        _source.download("https://example.com/u.tar", tmp_path / "a.tar", opener=_raising(boom))
    assert excinfo.value is boom

    with pytest.raises(WslmanError) as excinfo:
        _source.download(
            "https://example.com/u.tar",
            tmp_path / "b.tar",
            opener=_raising(urllib.error.URLError("no route")),
        )
    assert excinfo.value.code == "RUNTIME_ERROR"
    assert "下载失败" in str(excinfo.value)
    assert "重试会尝试续传" in (excinfo.value.hint or "")


def test_download_reports_truncated_body_and_keeps_part(tmp_path: Path) -> None:
    response = _BareResponse(b"abc", headers={"Content-Length": "10"})
    target = tmp_path / "u.tar"

    with pytest.raises(WslmanError) as excinfo:
        _source.download("https://example.com/u.tar", target, opener=lambda request, timeout: response)

    assert "下载中断：只收到 3 / 10 字节" in str(excinfo.value)
    assert not target.exists()
    assert _transfer.part_path(target).read_bytes() == b"abc"


def _candidates() -> tuple[_mirrors.Candidate, ...]:
    return (
        _mirrors.Candidate("ustc", "中科大 USTC", "https://mirror.example.com/u.tar", True),
        _mirrors.Candidate("official", "官方源", "https://official.example.com/u.tar", True),
    )


def test_download_mirrored_retries_until_attempts_exhaust_then_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_source, "_MAX_RESUME_ATTEMPTS", 1)
    target = tmp_path / "u.tar"
    part = _transfer.part_path(target)
    sizes = iter([1, 2])

    def flaky(url: str, path: Path, **kwargs: object) -> _source.DownloadResult:
        part.write_bytes(b"x" * next(sizes))
        raise runtime_error("断流")

    monkeypatch.setattr(_source, "download", flaky)

    with pytest.raises(WslmanError) as excinfo:
        _source.download_mirrored(target, candidates=_candidates())

    assert "所有下载源均失败" in str(excinfo.value)
    assert "中科大 USTC" in str(excinfo.value)
    assert "已保留 2 字节" in (excinfo.value.hint or "")


def test_download_mirrored_keeps_part_when_last_candidate_fails_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "u.tar"
    part = _transfer.part_path(target)

    def fake(url: str, path: Path, **kwargs: object) -> _source.DownloadResult:
        part.write_bytes(b"bad-bytes")
        raise check_failed("sha256 不匹配")

    monkeypatch.setattr(_source, "download", fake)

    with pytest.raises(WslmanError) as excinfo:
        _source.download_mirrored(target, candidates=_candidates()[:1])

    assert "所有下载源均失败" in str(excinfo.value)
    assert "已保留 9 字节" in (excinfo.value.hint or "")
    assert part.read_bytes() == b"bad-bytes"


def test_download_mirrored_switches_source_and_drops_part_on_check_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_source, "_MAX_RESUME_ATTEMPTS", 1)
    target = tmp_path / "u.tar"
    part = _transfer.part_path(target)
    meta = part.with_name(part.name + ".json")

    def fake(url: str, path: Path, **kwargs: object) -> _source.DownloadResult:
        if url.startswith("https://mirror."):
            part.write_bytes(b"mirror-bytes")
            meta.write_text("{}", encoding="utf-8")
            raise check_failed("sha256 不匹配")
        return _source.DownloadResult(path=path, sha256="a" * 64, size=14, verified=True, resumed=False)

    monkeypatch.setattr(_source, "download", fake)

    result = _source.download_mirrored(target, candidates=_candidates(), expected_sha256="a" * 64)

    assert result.source == "official"
    assert result.path == target
    assert not part.exists()
    assert not meta.exists()
