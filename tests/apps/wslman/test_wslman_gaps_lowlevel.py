"""wslman 底层模块的边界分支补充：镜像、解码、磁盘、注册表、传输与镜像清单缓存。

这些用例只走纯函数或显式注入的假实现，不执行真实 ``wsl.exe``、不读写用户目录。
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

from fcmd.apps.wslman import _decode, _disk, _images, _mirrors, _transfer
from fcmd.apps.wslman._errors import WslmanError
from fcmd.apps.wslman._registry import WindowsRegistryReader

# ---------------------------------------------------------------------- #
# _mirrors
# ---------------------------------------------------------------------- #


def test_mirror_labels_and_trusted_ids() -> None:
    assert _mirrors.label(_mirrors.AUTO) == "自动（优先国内镜像）"
    assert _mirrors.label("ustc") == "中科大 USTC"
    assert _mirrors.label("huawei") == "华为云"
    assert _mirrors.label("custom") == "custom"
    assert _mirrors.trusted_ids() == ("official", "ustc", "aliyun", "huawei")
    assert _mirrors.identifiers() == ("auto", "official", "ustc", "aliyun", "huawei")


def test_candidates_respects_verifiability_and_rewrites_mirror_urls() -> None:
    url = "https://releases.ubuntu.com/24.04/ubuntu-24.04-wsl-amd64.wsl"

    assert [candidate.ident for candidate in _mirrors.candidates(url, verifiable=False)] == ["official"]
    assert [candidate.ident for candidate in _mirrors.candidates(url, policy="official")] == ["official"]

    picked = _mirrors.candidates(url)
    assert [candidate.ident for candidate in picked] == ["ustc", "aliyun", "huawei", "official"]
    assert picked[0].url == "https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/ubuntu-24.04-wsl-amd64.wsl"
    assert picked[0].trusted is True
    assert picked[-1].url == url
    assert picked[0].to_data() == {
        "id": "ustc",
        "label": "中科大 USTC",
        "url": "https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/ubuntu-24.04-wsl-amd64.wsl",
        "trusted": True,
    }


# ---------------------------------------------------------------------- #
# _decode
# ---------------------------------------------------------------------- #


def test_decode_console_handles_single_byte_payload() -> None:
    # 单字节时 UTF-16 采样单元数为 0，直接按 UTF-8 解码。
    assert _decode.decode_console(b"x") == "x"


def test_parse_distro_list_skips_blank_lines() -> None:
    rows = _decode.parse_distro_list("\n   \n* Ubuntu Running 2\n")
    assert [(row.name, row.state, row.version, row.default) for row in rows] == [("Ubuntu", "running", 2, True)]


def test_parse_wsl_version_keeps_scanning_after_line_without_number() -> None:
    assert _decode.parse_wsl_version("WSL 环境\n1.2.3") == "1.2.3"


# ---------------------------------------------------------------------- #
# _disk
# ---------------------------------------------------------------------- #


def test_distro_disk_bytes_without_vhdx_or_rootfs(tmp_path: Path) -> None:
    base = tmp_path / "distro"
    base.mkdir()
    assert _disk.distro_disk_bytes(str(base), 2) == (None, False)


def test_walk_size_counts_nested_directories(tmp_path: Path) -> None:
    root = tmp_path / "rootfs"
    (root / "nested").mkdir(parents=True)
    (root / "a.bin").write_bytes(b"12345")
    (root / "nested" / "b.bin").write_bytes(b"123")

    assert _disk._walk_size(root, budget_s=30.0) == (8, False)


def test_walk_size_ignores_entries_that_are_neither_file_nor_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Entry:
        def is_dir(self, follow_symlinks: bool = False) -> bool:
            return False

        def is_file(self, follow_symlinks: bool = False) -> bool:
            return False

    class _Scan:
        def __enter__(self) -> list[_Entry]:
            return [_Entry()]

        def __exit__(self, *exc_info: object) -> bool:
            return False

    monkeypatch.setattr(_disk.os, "scandir", lambda path: _Scan())
    root = tmp_path / "rootfs"
    root.mkdir()

    assert _disk._walk_size(root, budget_s=30.0) == (0, False)


def test_walk_size_stops_at_file_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_disk, "_WALK_FILE_LIMIT", 1)
    root = tmp_path / "rootfs"
    root.mkdir()
    (root / "a.bin").write_bytes(b"123")

    assert _disk._walk_size(root, budget_s=30.0) == (3, True)


# ---------------------------------------------------------------------- #
# _registry
# ---------------------------------------------------------------------- #


def test_registry_import_failure_degrades_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "winreg", None)
    snapshot = WindowsRegistryReader(platform="win32").snapshot()

    assert snapshot.available is False
    assert "winreg" in (snapshot.error or "")


# ---------------------------------------------------------------------- #
# _transfer
# ---------------------------------------------------------------------- #


def test_volume_filesystem_failure_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    def boom(size: int) -> object:
        raise OSError("kernel32 不可用")

    monkeypatch.setattr(_transfer.os, "name", "nt")
    monkeypatch.setattr(ctypes, "create_unicode_buffer", boom)

    assert _transfer._volume_filesystem(tmp_path) is None


class _StopAfterOne:
    """``wait`` 第一次返回 False（进循环体），之后返回 True（退出循环）。"""

    def __init__(self) -> None:
        self.calls = 0

    def wait(self, timeout: float) -> bool:
        self.calls += 1
        return self.calls > 1


def test_progress_reporter_stop_without_start_and_loop_prints(tmp_path: Path) -> None:
    target = tmp_path / "a.tar"
    stream = io.StringIO()
    reporter = _transfer.ProgressReporter(target, None, label="下载", stream=stream)

    reporter.stop()  # 未 start 时 stop 不应报错，也不写任何内容
    assert stream.getvalue() == ""

    target.write_bytes(b"x" * 10)
    reporter._stop = _StopAfterOne()  # type: ignore[assignment]
    reporter._loop()

    assert stream.getvalue() == "[下载] 已写 10 B\n"


def test_transfer_lock_exit_without_acquire_and_busy_without_pid(tmp_path: Path) -> None:
    lock = _transfer.TransferLock("Ubuntu", directory=tmp_path)
    lock.__exit__(None, None, None)
    assert not lock.path.exists()

    lock.path.write_text(json.dumps({"pid": "not-int"}), encoding="utf-8")
    with pytest.raises(WslmanError) as excinfo, lock:
        pass

    assert excinfo.value.code == "RUNTIME_ERROR"
    assert "已有传输在进行" in str(excinfo.value)
    assert "PID" not in (excinfo.value.hint or "")


# ---------------------------------------------------------------------- #
# _images
# ---------------------------------------------------------------------- #


def test_sha256_rejects_non_string_and_normalises_prefix() -> None:
    assert _images._sha256(123) is None
    assert _images._sha256(" sha256:" + "A" * 64 + " ") == "a" * 64


def test_parse_distribution_info_skips_malformed_sections_and_duplicates() -> None:
    entries = _images.parse_distribution_info(
        {
            "ModernDistributions": {
                "Bad": "not-a-list",
                "Mixed": [1, {"Name": "   "}, {"Name": "Ubuntu"}, {"Name": "ubuntu"}],
            },
            "Distributions": [
                {"FriendlyName": "无名"},
                {"Name": "   "},
                {"Name": "Store", "Amd64PackageUrl": "https://example.com/store.appx"},
            ],
        }
    )

    assert [entry.name for entry in entries] == ["Ubuntu", "Store"]
    assert entries[1].sources == ("store",)
    assert [download.arch for download in entries[1].downloads] == ["x86_64"]


def test_load_cache_rejects_wrong_shapes_and_skips_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps({"schema": _images.CACHE_SCHEMA, "entries": "x", "fetched_at": 1}),
        encoding="utf-8",
    )
    assert _images.load_cache(path) is None

    path.write_text(
        json.dumps({"schema": _images.CACHE_SCHEMA, "entries": [], "fetched_at": "x"}),
        encoding="utf-8",
    )
    assert _images.load_cache(path) is None

    path.write_text(
        json.dumps(
            {
                "schema": _images.CACHE_SCHEMA,
                "fetched_at": 1.0,
                "sources": ["cli"],
                "entries": [
                    "bad",
                    {"name": ""},
                    {
                        "name": "Ubuntu",
                        "downloads": [
                            "bad",
                            {"arch": "x86_64"},
                            {"arch": "x86_64", "url": "https://example.com/u.wsl"},
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = _images.load_cache(path)

    assert loaded is not None
    assert [entry.name for entry in loaded.entries] == ["Ubuntu"]
    assert [download.url for download in loaded.entries[0].downloads] == ["https://example.com/u.wsl"]
    assert loaded.sources == ("cli",)


def test_save_cache_swallows_write_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Path, *args: object, **kwargs: object) -> int:
        raise OSError("只读目录")

    monkeypatch.setattr(Path, "write_text", boom)

    _images.save_cache((), 1.0, tmp_path / "cache.json")
    assert not (tmp_path / "cache.json").exists()


def test_age_text_units() -> None:
    assert _images._age_text(120) == "2 分钟"
    assert _images._age_text(7200) == "2.0 小时"
    assert _images._age_text(172800) == "2.0 天"


def test_backfill_from_cache_without_cache_and_filling_both_sides() -> None:
    assert _images._backfill_from_cache(None, None, None) == ((), ())

    cache = _images.ImageCache(
        entries=(
            _images.ImageEntry("Ubuntu", "Ubuntu", ("cli",)),
            _images.ImageEntry("Debian", "Debian", ("manifest",)),
        ),
        fetched_at=1.0,
        sources=("cli", "manifest"),
    )
    rows, manifest = _images._backfill_from_cache(None, None, cache)
    assert [row.name for row in rows] == ["Ubuntu"]
    assert [entry.name for entry in manifest] == ["Debian"]

    kept_rows = (_decode.OnlineRow(name="Ubuntu", friendly_name="Ubuntu"),)
    kept_manifest = (_images.ImageEntry("Debian", "Debian", ("manifest",)),)
    assert _images._backfill_from_cache(kept_rows, kept_manifest, cache) == (kept_rows, kept_manifest)


def test_default_open_uses_https_only_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    from fcmd.apps.wslman import _source

    response = object()

    class _Opener:
        def __init__(self) -> None:
            self.calls: list[tuple[urllib.request.Request, float]] = []

        def open(self, request: urllib.request.Request, timeout: float) -> object:
            self.calls.append((request, timeout))
            return response

    opener = _Opener()
    captured: list[tuple[object, ...]] = []

    def build_opener(*handlers: object) -> _Opener:
        captured.append(handlers)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    request = urllib.request.Request("https://example.com/a.tar")

    assert _source._default_open(request, 7.5) is response
    assert opener.calls == [(request, 7.5)]
    assert isinstance(captured[0][0], _source._HTTPSOnlyRedirect)


def test_https_redirect_handler_allows_secure_target() -> None:
    import urllib.request

    from fcmd.apps.wslman import _source

    request = urllib.request.Request("https://example.com/a.tar")
    redirected = _source._HTTPSOnlyRedirect().redirect_request(
        request, io.BytesIO(b""), 302, "Found", {}, "https://example.com/b.tar"
    )

    assert redirected.full_url == "https://example.com/b.tar"


def test_https_redirect_handler_rejects_plain_http() -> None:
    import urllib.request

    from fcmd.apps.wslman import _source

    request = urllib.request.Request("https://example.com/a.tar")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _source._HTTPSOnlyRedirect().redirect_request(
            request, io.BytesIO(b""), 302, "Found", {}, "http://example.com/b.tar"
        )

    assert excinfo.value.code == 302
    assert "只允许 HTTPS 重定向" in str(excinfo.value)
