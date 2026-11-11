"""llmdown 工具测试。

覆盖：
- 工具注册与 search / info / local / files / mirror / download 子命令
- ModelScope 搜索 / 详情 / 清单解析与错误映射
- 本地模型扫描与删除、文件过滤、URL 构造、源自动选优
- 下载：新建、Range 续传、服务端忽略 Range、兜底源、sha256 与大小校验
- download_files 的已存在跳过、进度回调、取消与并发下载
"""

from __future__ import annotations

import hashlib
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

import fcmd as fx
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.infras.net import UrlProbe
from fcmd.tools.llm import llmdown
from fcmd.tools.llm.llmdown import (
    DownloadCancelled,
    ModelFile,
    ModelInfo,
    ModelSearch,
    ModelSummary,
    ModelToolError,
    ProgressEvent,
    SourceSelection,
    _download_one,
    _normalize_repo,
    _ReporterProgress,
    delete_local_model,
    download_files,
    fetch_files,
    fetch_model_info,
    file_url,
    filter_files,
    format_timestamp,
    scan_local_models,
    search_models,
    select_source,
)


# ---------------------------------------------------------------------- #
# 假响应 / 假进度
# ---------------------------------------------------------------------- #
class _FakeResponse:
    """最小 urllib 响应替身：按 size 分块返回 payload。"""

    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self._offset = 0
        self.status = status

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._payload[self._offset :]
        else:
            chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _ProgressStub:
    """rich Progress 替身：记录任务创建 / 更新 / 移除。"""

    def __init__(self) -> None:
        self.tasks: list[dict[str, Any]] = []
        self.updates: list[tuple[Any, dict[str, Any]]] = []
        self.removed: list[Any] = []

    def add_task(self, description: str, **kwargs: Any) -> int:
        self.tasks.append({"description": description, **kwargs})
        return len(self.tasks) - 1

    def update(self, task: Any, **kwargs: Any) -> None:
        self.updates.append((task, kwargs))

    def remove_task(self, task: Any) -> None:
        self.removed.append(task)


def _json_urlopen(payload: Any, status: int = 200) -> Any:
    """返回固定 JSON 的 urlopen 替身。"""
    import json

    raw = json.dumps(payload).encode()

    def _urlopen(req: object, timeout: float = 30) -> _FakeResponse:
        return _FakeResponse(raw, status)

    return _urlopen


def _download_urlopen(payload_by_url: dict[str, bytes], status: int = 200, seen: list[Any] | None = None) -> Any:
    """按 URL 返回二进制内容的 urlopen 替身。"""

    def _urlopen(req: Any, timeout: float = 60) -> _FakeResponse:
        if seen is not None:
            seen.append(req)
        return _FakeResponse(payload_by_url[req.full_url], status)

    return _urlopen


def _probe(url: str, ok: bool = True, throughput: float = 100.0) -> UrlProbe:
    """构造测速结果。"""
    return UrlProbe(url, ok, 10.0 if ok else 0.0, throughput, 131072 if ok else 0, "" if ok else "超时")


# ---------------------------------------------------------------------- #
# 注册与清单解析
# ---------------------------------------------------------------------- #
class TestRegistration:
    def test_registered(self) -> None:
        assert "llmdown" in _TOOL_REGISTRY

    def test_subcommands(self) -> None:
        subs = fx.list_subcommands("llmdown")
        assert {"files", "mirror", "download"} <= set(subs)


class TestFetchFiles:
    def test_modelscope_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "Data": {
                "Files": [
                    {"Path": "b/model.safetensors", "Size": 100, "Type": "blob", "Sha256": "ABC"},
                    {"Path": "docs", "Size": 0, "Type": "tree"},
                ]
            }
        }
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen(payload))
        files = fetch_files("Qwen/demo", "modelscope")
        assert files == [ModelFile("b/model.safetensors", 100, "ABC")]

    def test_hf_parse_uses_lfs_sha256(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            {"path": "a.safetensors", "size": 10, "type": "file", "lfs": {"oid": "CD"}},
            {"path": "b.json", "size": 2, "type": "file", "oid": "sha1"},
            {"path": "sub", "type": "directory"},
        ]
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen(payload))
        files = fetch_files("Qwen/demo", "hf-mirror")
        assert files == [ModelFile("a.safetensors", 10, "CD"), ModelFile("b.json", 2, "")]

    def test_http_error_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(req: object, timeout: float = 30) -> Any:
            raise HTTPError("https://example.com", 404, "Not Found", Message(), None)

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(ModelToolError, match="HTTP 404"):
            fetch_files("Qwen/demo", "modelscope")

    def test_network_error_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(req: object, timeout: float = 30) -> Any:
            raise URLError("dns failure")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        with pytest.raises(ModelToolError, match="清单请求失败"):
            fetch_files("Qwen/demo", "hf-mirror")

    def test_invalid_json_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _urlopen(req: object, timeout: float = 30) -> _FakeResponse:
            return _FakeResponse(b"<html>")

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        with pytest.raises(ModelToolError, match="合法 JSON"):
            fetch_files("Qwen/demo", "modelscope")

    def test_empty_manifest_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen({"Data": {"Files": []}}))
        with pytest.raises(ModelToolError, match="没有文件"):
            fetch_files("Qwen/demo", "modelscope")

    def test_modelscope_format_error_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen({"Message": "boom"}))
        with pytest.raises(ModelToolError, match="格式异常: boom"):
            fetch_files("Qwen/demo", "modelscope")

    def test_modelscope_skips_invalid_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "Data": {
                "Files": [
                    "not-a-dict",
                    {"Path": "dir", "Type": "tree"},
                    {"Name": "by-name.bin", "Size": 3, "Type": "blob"},
                    {"Path": "", "Size": 0, "Type": "blob"},
                ]
            }
        }
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen(payload))
        assert fetch_files("Qwen/demo", "modelscope") == [ModelFile("by-name.bin", 3, "")]

    def test_hf_format_error_mapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen({"data": []}))
        with pytest.raises(ModelToolError, match="格式异常"):
            fetch_files("Qwen/demo", "hf-mirror")

    def test_hf_skips_invalid_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = [
            "not-a-dict",
            {"path": "sub", "type": "directory"},
            {"path": "", "type": "file"},
            {"path": "ok.bin", "size": 4, "type": "file", "lfs": None},
        ]
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen(payload))
        assert fetch_files("Qwen/demo", "hf-mirror") == [ModelFile("ok.bin", 4, "")]


# ---------------------------------------------------------------------- #
# 辅助函数
# ---------------------------------------------------------------------- #
class TestHelpers:
    def test_normalize_repo(self) -> None:
        assert _normalize_repo(" /Qwen/demo/ ") == "Qwen/demo"
        with pytest.raises(ModelToolError, match="<组织>/<模型>"):
            _normalize_repo("demo")
        with pytest.raises(ModelToolError, match="<组织>/<模型>"):
            _normalize_repo("a/b/c")

    def test_file_url_encodes_path(self) -> None:
        url = file_url("Qwen/demo", "hf-mirror", None, "sub/a b.bin")
        assert url == "https://hf-mirror.com/Qwen/demo/resolve/main/sub/a%20b.bin"
        url = file_url("Qwen/demo", "modelscope", "v1", "config.json")
        assert url == "https://www.modelscope.cn/models/Qwen/demo/resolve/v1/config.json"

    def test_filter_files(self) -> None:
        files = [ModelFile("a.safetensors", 1), ModelFile("b.json", 2), ModelFile("README.md", 3)]
        assert [f.path for f in filter_files(files, ["*.safetensors"])] == ["a.safetensors"]
        assert [f.path for f in filter_files(files, exclude=["*.md"])] == ["a.safetensors", "b.json"]
        assert filter_files(files, ["*.bin"]) == []


# ---------------------------------------------------------------------- #
# 源选择
# ---------------------------------------------------------------------- #
class TestSelectSource:
    def test_explicit_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        files = [ModelFile("a.bin", 1)]
        monkeypatch.setattr(llmdown, "fetch_files", lambda *a, **k: files)
        selection = select_source("Qwen/demo", "modelscope")
        assert selection.source == "modelscope"
        assert selection.fallback is None
        assert selection.probes == ()
        assert selection.files == tuple(files)

    def test_auto_single_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        files = [ModelFile("a.bin", 1)]

        def _fetch(repo: str, source: str, revision: str | None = None) -> list[ModelFile]:
            if source == "hf-mirror":
                raise ModelToolError("不存在")
            return files

        monkeypatch.setattr(llmdown, "fetch_files", _fetch)
        selection = select_source("Qwen/demo")
        assert selection.source == "modelscope"
        assert selection.fallback is None

    def test_auto_all_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fetch(*a: Any, **k: Any) -> list[ModelFile]:
            raise ModelToolError("不可用")

        monkeypatch.setattr(llmdown, "fetch_files", _fetch)
        with pytest.raises(ModelToolError, match="所有源均不可用"):
            select_source("Qwen/demo")

    def test_auto_probe_picks_faster(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ms_files = [ModelFile("a.bin", 100)]
        hf_files = [ModelFile("a.bin", 100)]

        def _fetch(repo: str, source: str, revision: str | None = None) -> list[ModelFile]:
            return ms_files if source == "modelscope" else hf_files

        monkeypatch.setattr(llmdown, "fetch_files", _fetch)
        monkeypatch.setattr(
            llmdown,
            "_probe_sources",
            lambda *a, **k: [
                ("hf-mirror", _probe("https://hf-mirror.com/a", throughput=900)),
                ("modelscope", _probe("https://www.modelscope.cn/a", throughput=100)),
            ],
        )
        selection = select_source("Qwen/demo")
        assert selection.source == "hf-mirror"
        assert selection.fallback == "modelscope"
        assert len(selection.probes) == 2

    def test_auto_no_common_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _fetch(repo: str, source: str, revision: str | None = None) -> list[ModelFile]:
            return [ModelFile(f"{source}.bin", 1)]

        monkeypatch.setattr(llmdown, "fetch_files", _fetch)
        monkeypatch.setattr(llmdown, "_probe_sources", lambda *a, **k: [])
        selection = select_source("Qwen/demo")
        assert selection.source == "modelscope"
        assert selection.fallback == "hf-mirror"
        assert selection.probes == ()

    def test_probe_sources_uses_common_largest_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manifests = {
            "modelscope": [ModelFile("small.bin", 1), ModelFile("big.bin", 500)],
            "hf-mirror": [ModelFile("big.bin", 500), ModelFile("other.bin", 9)],
        }
        probed: list[str] = []

        def _probe_mirrors(urls: list[str], timeout: float = 5, user_agent: str = "") -> list[UrlProbe]:
            probed.extend(urls)
            return [_probe(url, throughput=float(index + 1) * 10) for index, url in enumerate(urls)]

        monkeypatch.setattr("fcmd.infras.net.probe_mirrors", _probe_mirrors)
        result = llmdown._probe_sources("Qwen/demo", manifests, None, 3.0)

        assert [source for source, _ in result] == ["modelscope", "hf-mirror"]
        assert len(probed) == 2
        assert all("big.bin" in url for url in probed)


class TestPrintSelection:
    def test_reports_unavailable_probe(self, capsys: pytest.CaptureFixture[str]) -> None:
        probes = (_probe("https://hf-mirror.com/a", ok=False),)
        llmdown._print_selection(SourceSelection("modelscope", (ModelFile("a.bin", 1),), None, probes))
        out = capsys.readouterr().out
        assert "不可用（超时）" in out
        assert "选用源" in out


# ---------------------------------------------------------------------- #
# 单文件下载
# ---------------------------------------------------------------------- #
class TestDownloadOne:
    def test_fresh_download(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"hello world"
        seen: list[Any] = []
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content}, seen=seen),
        )
        file = ModelFile("a.bin", len(content), hashlib.sha256(content).hexdigest())
        final = tmp_path / "a.bin"
        selection = SourceSelection("modelscope", (file,))
        progress = _ProgressStub()
        _download_one("Qwen/demo", file, final, llmdown._part_path(final), selection, None, True, progress, 0, 1)

        assert final.read_bytes() == content
        assert not llmdown._part_path(final).exists()
        assert seen[0].get_header("Range") is None
        assert progress.removed  # 校验任务已移除

    def test_resume_with_range(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"hello world"
        final = tmp_path / "a.bin"
        part = llmdown._part_path(final)
        part.write_bytes(content[:6])
        seen: list[Any] = []
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen(
                {"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content[6:]}, 206, seen
            ),
        )
        file = ModelFile("a.bin", len(content), hashlib.sha256(content).hexdigest())
        selection = SourceSelection("modelscope", (file,))
        _download_one("Qwen/demo", file, final, part, selection, None, True, _ProgressStub(), 0, 1)

        assert final.read_bytes() == content
        assert seen[0].get_header("Range") == "bytes=6-"

    def test_server_ignores_range_restarts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"hello world"
        final = tmp_path / "a.bin"
        part = llmdown._part_path(final)
        part.write_bytes(content[:6])
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content}, 200),
        )
        file = ModelFile("a.bin", len(content), hashlib.sha256(content).hexdigest())
        selection = SourceSelection("modelscope", (file,))
        progress = _ProgressStub()
        _download_one("Qwen/demo", file, final, part, selection, None, True, progress, 0, 1)

        assert final.read_bytes() == content
        assert any(update.get("completed") == 0 for _, update in progress.updates)

    def test_fallback_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"fallback"
        primary = "https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin"

        def _urlopen(req: Any, timeout: float = 60) -> _FakeResponse:
            if req.full_url == primary:
                raise URLError("primary down")
            return _FakeResponse(content)

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        file = ModelFile("a.bin", len(content))
        selection = SourceSelection("modelscope", (file,), fallback="hf-mirror")
        final = tmp_path / "a.bin"
        _download_one(
            "Qwen/demo", file, final, llmdown._part_path(final), selection, None, False, _ProgressStub(), 0, 1
        )
        assert final.read_bytes() == content

    def test_sha256_mismatch_removes_part(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"bad"
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content}),
        )
        file = ModelFile("a.bin", len(content), "0" * 64)
        selection = SourceSelection("modelscope", (file,))
        final = tmp_path / "a.bin"
        with pytest.raises(ModelToolError, match="sha256"):
            _download_one(
                "Qwen/demo", file, final, llmdown._part_path(final), selection, None, True, _ProgressStub(), 0, 1
            )
        assert not final.exists()
        assert not llmdown._part_path(final).exists()

    def test_size_mismatch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": b"short"}),
        )
        file = ModelFile("a.bin", 100)
        selection = SourceSelection("modelscope", (file,))
        final = tmp_path / "a.bin"
        with pytest.raises(ModelToolError, match="大小不符"):
            _download_one(
                "Qwen/demo", file, final, llmdown._part_path(final), selection, None, False, _ProgressStub(), 0, 1
            )

    def test_all_sources_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(req: object, timeout: float = 60) -> Any:
            raise URLError("down")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        file = ModelFile("a.bin", 1)
        selection = SourceSelection("modelscope", (file,), fallback="hf-mirror")
        final = tmp_path / "a.bin"
        with pytest.raises(ModelToolError, match="下载失败"):
            _download_one(
                "Qwen/demo", file, final, llmdown._part_path(final), selection, None, False, _ProgressStub(), 0, 1
            )

    def test_oversized_part_restarts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"abc"
        final = tmp_path / "a.bin"
        part = llmdown._part_path(final)
        part.write_bytes(b"too-long")
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content}),
        )
        file = ModelFile("a.bin", len(content))
        progress = _ProgressStub()
        _download_one(
            "Qwen/demo", file, final, part, SourceSelection("modelscope", (file,)), None, False, progress, 0, 1
        )

        assert final.read_bytes() == content
        assert any(update.get("completed") == 0 for _, update in progress.updates)

    def test_complete_part_reuses_without_download(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"abc"
        final = tmp_path / "a.bin"
        part = llmdown._part_path(final)
        part.write_bytes(content)

        def _unexpected(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("不应发起下载")

        monkeypatch.setattr("urllib.request.urlopen", _unexpected)
        file = ModelFile("a.bin", len(content))
        _download_one(
            "Qwen/demo", file, final, part, SourceSelection("modelscope", (file,)), None, False, _ProgressStub(), 0, 1
        )

        assert final.read_bytes() == content

    def test_sha256_file_reports_progress(self, tmp_path: Path) -> None:
        path = tmp_path / "a.bin"
        path.write_bytes(b"x" * 10)
        progress = _ProgressStub()

        digest = llmdown._sha256_file(path, progress, 0)

        assert digest == hashlib.sha256(b"x" * 10).hexdigest()
        assert progress.updates


# ---------------------------------------------------------------------- #
# 多文件下载
# ---------------------------------------------------------------------- #
class TestDownloadFiles:
    def test_all_existing_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        final = tmp_path / "a.bin"
        final.write_bytes(b"1234")
        file = ModelFile("a.bin", 4)

        def _unexpected(*a: Any, **k: Any) -> Any:
            raise AssertionError("不应发起下载")

        monkeypatch.setattr("urllib.request.urlopen", _unexpected)
        download_files("Qwen/demo", [file], tmp_path, SourceSelection("modelscope", (file,)))
        assert "全部文件已存在" in capsys.readouterr().out

    def test_downloads_and_reports(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"abc"
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/sub/a.bin": content}),
        )
        file = ModelFile("sub/a.bin", len(content))
        download_files("Qwen/demo", [file], tmp_path, SourceSelection("modelscope", (file,)), workers=1)
        assert (tmp_path / "sub" / "a.bin").read_bytes() == content

    def test_oversized_part_restarts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"abc"
        final = tmp_path / "a.bin"
        llmdown._part_path(final).write_bytes(b"too-long")
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content}),
        )
        file = ModelFile("a.bin", len(content))
        download_files("Qwen/demo", [file], tmp_path, SourceSelection("modelscope", (file,)), workers=1)
        assert final.read_bytes() == content

    def test_failures_are_aggregated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(req: object, timeout: float = 60) -> Any:
            raise URLError("down")

        monkeypatch.setattr("urllib.request.urlopen", _raise)
        file = ModelFile("a.bin", 1)
        with pytest.raises(ModelToolError, match="1 个文件下载失败"):
            download_files("Qwen/demo", [file], tmp_path, SourceSelection("modelscope", (file,)), workers=1)


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
class TestCli:
    def test_files_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        selection = SourceSelection("modelscope", (ModelFile("a.json", 2, "ABCDEF123456"), ModelFile("b.bin", 3)))
        monkeypatch.setattr(llmdown, "select_source", lambda *a, **k: selection)
        code = run_tool("llmdown", ["files", "Qwen/demo", "--include", "*.json"])
        assert code == 0
        out = capsys.readouterr().out
        assert "a.json" in out
        assert "b.bin" not in out
        assert "合计 2 B" in out

    def test_files_empty_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        selection = SourceSelection("modelscope", (ModelFile("a.bin", 2),))
        monkeypatch.setattr(llmdown, "select_source", lambda *a, **k: selection)
        with pytest.raises(ModelToolError, match="过滤后没有文件"):
            llmdown.list_files_cmd("Qwen/demo", include=["*.json"])

    def test_mirror_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        probes = (
            _probe("https://hf-mirror.com/a", throughput=900),
            _probe("https://www.modelscope.cn/a", throughput=10),
        )
        selection = SourceSelection("hf-mirror", (ModelFile("a.bin", 1),), "modelscope", probes)
        monkeypatch.setattr(llmdown, "select_source", lambda *a, **k: selection)
        code = run_tool("llmdown", ["mirror", "Qwen/demo"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hf-mirror" in out
        assert "900 KB/s" in out
        assert "选用源" in out

    def test_mirror_no_common_file(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        selection = SourceSelection("modelscope", (ModelFile("a.bin", 1),))
        monkeypatch.setattr(llmdown, "select_source", lambda *a, **k: selection)
        llmdown.mirror_cmd("Qwen/demo")
        assert "无法同文件测速" in capsys.readouterr().out

    def test_download_default_dest(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        captured: dict[str, Any] = {}
        selection = SourceSelection("modelscope", (ModelFile("a.bin", 2),))

        def _fake_download(repo: str, files: list[ModelFile], dest: Path, *args: Any) -> None:
            captured.update(repo=repo, files=files, dest=dest)

        monkeypatch.setattr(llmdown, "select_source", lambda *a, **k: selection)
        monkeypatch.setattr(llmdown, "download_files", _fake_download)
        llmdown.download_cmd("Qwen/demo")
        assert captured["dest"] == Path("models") / "Qwen/demo"
        assert captured["files"][0].path == "a.bin"
        assert "总大小" in capsys.readouterr().out

    def test_download_empty_filter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        selection = SourceSelection("modelscope", (ModelFile("a.bin", 2),))
        monkeypatch.setattr(llmdown, "select_source", lambda *a, **k: selection)
        with pytest.raises(ModelToolError, match="过滤后没有文件"):
            llmdown.download_cmd("Qwen/demo", include=["*.json"])

    def test_search_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        result = ModelSearch(
            (
                ModelSummary(
                    repo="Qwen/demo",
                    name="demo",
                    owner="Qwen",
                    tasks=("文本生成",),
                    downloads=1234,
                    stars=5,
                    updated=1700000000,
                    storage_size=2048,
                ),
            ),
            1,
        )
        monkeypatch.setattr(llmdown, "search_models", lambda *a, **k: result)
        code = run_tool("llmdown", ["search", "--keyword", "qwen"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Qwen/demo" in out
        assert "文本生成" in out
        assert "1,234" in out

    def test_search_empty_result(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setattr(llmdown, "search_models", lambda *a, **k: ModelSearch((), 0))
        llmdown.search_cmd("nothing")
        assert "没有匹配的模型" in capsys.readouterr().out

    def test_info_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        info = ModelInfo(
            summary=ModelSummary(repo="Qwen/demo", name="demo", owner="Qwen", chinese_name="演示", downloads=7),
            description="desc",
            readme="# README\n内容",
            organization="Qwen",
            frameworks=("PyTorch",),
            architectures=("Transformer",),
            languages=("zh",),
            revision="master",
        )
        monkeypatch.setattr(llmdown, "fetch_model_info", lambda *a, **k: info)
        code = run_tool("llmdown", ["info", "Qwen/demo", "--readme", "--limit", "5"])
        assert code == 0
        out = capsys.readouterr().out
        assert "演示" in out
        assert "PyTorch" in out
        assert "已截断" in out

    def test_local_lists_models(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        model = tmp_path / "Qwen" / "demo"
        model.mkdir(parents=True)
        (model / "a.bin").write_bytes(b"1234")
        (model / "b.bin.part").write_bytes(b"1")
        llmdown.local_cmd(tmp_path)
        out = capsys.readouterr().out
        assert "Qwen/demo" in out
        assert "未完成" in out

    def test_local_empty(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        llmdown.local_cmd(tmp_path / "missing")
        assert "没有已下载的模型" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# ModelScope 搜索 / 详情
# ---------------------------------------------------------------------- #
class TestSearchModels:
    def _payload(self) -> dict[str, Any]:
        return {
            "Code": 200,
            "Data": {
                "Model": {
                    "TotalCount": 42,
                    "Models": [
                        {
                            "Name": "Qwen3-Coder",
                            "Path": "Qwen",
                            "ChineseName": "千问代码",
                            "Downloads": 123,
                            "Stars": 4,
                            "Tasks": [{"Name": "text-generation", "ChineseName": "文本生成"}, "bad-task"],
                            "Tags": ["llm", None, ""],
                            "License": "Apache-2.0",
                            "LastUpdatedTime": 1700000000,
                            "StorageSize": 4096,
                        },
                        {"Name": ""},
                    ],
                }
            },
        }

    def test_search_request_and_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def _urlopen(req: Any, timeout: float = 30) -> _FakeResponse:
            import json

            captured["method"] = req.get_method()
            captured["body"] = json.loads(req.data)
            return _FakeResponse(json.dumps(self._payload()).encode())

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        result = search_models(" qwen ", page=2, page_size=10, sort="modified")

        assert captured["method"] == "PUT"
        assert captured["body"] == {
            "PageSize": 10,
            "PageNumber": 2,
            "SortBy": "GmtModified",
            "Target": "",
            "SingleCriterion": [],
            "Name": "qwen",
        }
        assert result.total == 42
        assert result.page == 2
        assert result.page_size == 10
        assert len(result.items) == 1
        item = result.items[0]
        assert item.repo == "Qwen/Qwen3-Coder"
        assert item.chinese_name == "千问代码"
        assert item.tasks == ("文本生成",)
        assert item.tags == ("llm",)
        assert item.downloads == 123
        assert item.storage_size == 4096

    def test_invalid_pagination(self) -> None:
        with pytest.raises(ModelToolError, match="分页参数非法"):
            search_models(page=0)
        with pytest.raises(ModelToolError, match="分页参数非法"):
            search_models(page_size=0)
        with pytest.raises(ModelToolError, match="分页参数非法"):
            search_models(page_size=101)

    def test_format_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen({"Message": "boom"}))
        with pytest.raises(ModelToolError, match="模型列表格式异常: boom"):
            search_models("qwen")

    def test_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {"Data": {"Model": {"TotalCount": 0, "Models": [{"Name": ""}]}}}
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen(payload))
        result = search_models()
        assert result.items == ()
        assert result.total == 0


class TestFetchModelInfo:
    def test_parse_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {
            "Data": {
                "Name": "Qwen3-Coder",
                "Path": "Qwen",
                "ChineseName": "千问代码",
                "Description": "描述",
                "ReadMeContent": "# README",
                "Downloads": 9,
                "Stars": 3,
                "Tasks": [{"Name": "text-generation", "ChineseName": "文本生成"}],
                "Tags": ["llm"],
                "License": "Apache-2.0",
                "LastUpdatedTime": 1700000000,
                "StorageSize": 8192,
                "Organization": {"FullName": "通义千问"},
                "Frameworks": ["PyTorch"],
                "Architectures": ["Transformer"],
                "ModelType": ["text-generation"],
                "Language": ["zh", "en"],
                "Revision": "master",
                "CreatedTime": 1600000000,
                "SupportInference": "True",
            }
        }
        captured: dict[str, str] = {}

        def _urlopen(req: Any, timeout: float = 30) -> _FakeResponse:
            import json

            captured["url"] = req.full_url
            return _FakeResponse(json.dumps(payload).encode())

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        info = fetch_model_info(" Qwen/Qwen3-Coder ")

        assert captured["url"] == "https://www.modelscope.cn/api/v1/models/Qwen/Qwen3-Coder"
        assert info.summary.repo == "Qwen/Qwen3-Coder"
        assert info.organization == "通义千问"
        assert info.readme == "# README"
        assert info.frameworks == ("PyTorch",)
        assert info.architectures == ("Transformer",)
        assert info.model_types == ("text-generation",)
        assert info.languages == ("zh", "en")
        assert info.revision == "master"
        assert info.created == 1600000000
        assert info.inference == "True"

    def test_invalid_repo(self) -> None:
        with pytest.raises(ModelToolError, match="<组织>/<模型>"):
            fetch_model_info("demo")

    def test_format_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("urllib.request.urlopen", _json_urlopen({"Message": "not found"}))
        with pytest.raises(ModelToolError, match="模型详情格式异常: not found"):
            fetch_model_info("Qwen/demo")


class TestFormatTimestamp:
    def test_values(self) -> None:
        assert format_timestamp(0) == "-"
        assert format_timestamp(-1) == "-"
        assert format_timestamp(1700000000) == "2023-11-15"
        assert format_timestamp(10**20) == "-"


# ---------------------------------------------------------------------- #
# 本地模型管理
# ---------------------------------------------------------------------- #
class TestLocalModels:
    def test_scan_models(self, tmp_path: Path) -> None:
        model = tmp_path / "Qwen" / "demo"
        (model / "sub").mkdir(parents=True)
        (model / "a.bin").write_bytes(b"1234")
        (model / "sub" / "b.bin").write_bytes(b"12")
        (model / "c.bin.part").write_bytes(b"1")
        (tmp_path / "empty" / "no-files").mkdir(parents=True)

        models = scan_local_models(tmp_path)

        assert len(models) == 1
        item = models[0]
        assert item.repo == "Qwen/demo"
        assert item.path == model
        assert item.size == 7
        assert item.files == 3
        assert item.partial == 1
        assert item.complete is False

    def test_scan_missing_root(self, tmp_path: Path) -> None:
        assert scan_local_models(tmp_path / "missing") == []

    def test_delete_model(self, tmp_path: Path) -> None:
        model = tmp_path / "Qwen" / "demo"
        model.mkdir(parents=True)
        (model / "a.bin").write_bytes(b"1")

        removed = delete_local_model(tmp_path, "Qwen/demo")

        assert removed == model.resolve()
        assert not model.exists()

    def test_delete_rejects_outside_root(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside-model"
        outside.mkdir(exist_ok=True)
        with pytest.raises(ModelToolError, match="拒绝删除"):
            delete_local_model(tmp_path / "models", "../outside-model")
        assert outside.exists()

    def test_delete_missing(self, tmp_path: Path) -> None:
        with pytest.raises(ModelToolError, match="目录不存在"):
            delete_local_model(tmp_path, "Qwen/missing")


# ---------------------------------------------------------------------- #
# 进度回调与取消
# ---------------------------------------------------------------------- #
class TestReporterProgress:
    def test_events_and_clamp(self) -> None:
        events: list[ProgressEvent] = []
        progress = _ReporterProgress(events.append)
        task = progress.add_task("a.bin", total=10)
        progress.update(task, advance=4)
        progress.update(task, advance=100)
        progress.remove_task(task)

        assert [(event.task, event.state, event.completed) for event in events] == [
            ("a.bin", "start", 0),
            ("a.bin", "update", 4),
            ("a.bin", "update", 10),
            ("a.bin", "done", 10),
        ]

    def test_context_exit_finishes_remaining_tasks(self) -> None:
        events: list[ProgressEvent] = []
        with _ReporterProgress(events.append) as progress:
            progress.add_task("总进度", total=3)
        assert events[-1].state == "done"
        assert events[-1].total == 3


class TestCancellation:
    def test_sha256_stops(self, tmp_path: Path) -> None:
        path = tmp_path / "a.bin"
        path.write_bytes(b"x" * 10)
        with pytest.raises(DownloadCancelled):
            llmdown._sha256_file(path, should_stop=lambda: True)

    def test_download_files_cancel_keeps_part(self, tmp_path: Path) -> None:
        part = llmdown._part_path(tmp_path / "a.bin")
        part.write_bytes(b"abc")
        file = ModelFile("a.bin", 10)
        with pytest.raises(DownloadCancelled, match=r"保留 \.part"):
            download_files(
                "Qwen/demo",
                [file],
                tmp_path,
                SourceSelection("modelscope", (file,)),
                workers=1,
                should_stop=lambda: True,
            )
        assert part.read_bytes() == b"abc"

    def test_reporter_receives_progress(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = b"abc"
        monkeypatch.setattr(
            "urllib.request.urlopen",
            _download_urlopen({"https://www.modelscope.cn/models/Qwen/demo/resolve/master/a.bin": content}),
        )
        events: list[ProgressEvent] = []
        file = ModelFile("a.bin", len(content))
        download_files(
            "Qwen/demo",
            [file],
            tmp_path,
            SourceSelection("modelscope", (file,)),
            workers=1,
            reporter=events.append,
        )

        assert ("总进度", "start") in {(event.task, event.state) for event in events}
        assert ("a.bin", "done") in {(event.task, event.state) for event in events}
        assert any(event.task == "总进度" and event.completed == len(content) for event in events)
