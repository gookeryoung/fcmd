"""nettool 工具测试。

验证 ``fcmd.dsl.actions.net`` 模块：
- 工具注册与子命令结构
- http_get / http_post / http_head（mock urlopen）
- HTTPError / URLError 错误处理
- 通过 run_tool 调用 get / post / head 子命令
"""

from __future__ import annotations

from email.message import Message
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import pytest

import fcmd as fx
import fcmd.dsl.actions.net
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.dsl.actions.net import http_get, http_head, http_post

# ---------------------------------------------------------------------- #
# 辅助函数
# ---------------------------------------------------------------------- #


def _make_mock_urlopen(body: bytes = b"", headers: dict[str, str] | None = None) -> object:
    """创建模拟 urlopen 函数，返回支持上下文管理器的响应对象。"""

    resp = MagicMock()
    # with X as r: 默认让 r 指向 resp 本身（默认 __enter__ 返回新 MagicMock）
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = body
    resp.headers.items.return_value = list((headers or {}).items())

    def _urlopen(req: object, timeout: int = 30) -> MagicMock:
        return resp

    return _urlopen


def _make_error_urlopen(error: Exception) -> object:
    """创建总是抛出异常的 urlopen 函数。"""

    def _urlopen(req: object, timeout: int = 30) -> object:
        raise error

    return _urlopen


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """nettool 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """nettool 应在 _TOOL_REGISTRY 中注册。"""
        assert "nettool" in _TOOL_REGISTRY, "工具 'nettool' 未注册"

    def test_nettool_subcommands(self) -> None:
        """nettool 应有 get / post / head 子命令。"""
        subs = fx.list_subcommands("nettool")
        assert "get" in subs
        assert "post" in subs
        assert "head" in subs


# ---------------------------------------------------------------------- #
# http_get
# ---------------------------------------------------------------------- #
class TestHttpGet:
    """``http_get`` 测试。"""

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """成功 GET 请求返回响应体。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"Hello, World!"))
        assert http_get("http://example.com") == "Hello, World!"

    def test_unicode_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unicode 响应体。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen("你好世界".encode()))
        assert http_get("http://example.com") == "你好世界"

    def test_custom_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """自定义超时。"""
        mock_fn = _make_mock_urlopen(b"ok")
        monkeypatch.setattr("urllib.request.urlopen", mock_fn)
        assert http_get("http://example.com", timeout=10) == "ok"

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 错误应抛出 HTTPError。"""
        error = HTTPError("http://example.com", 404, "Not Found", Message(), None)
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        with pytest.raises(HTTPError):
            http_get("http://example.com")

    def test_url_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """网络错误应抛出 URLError。"""
        error = URLError("Name or service not known")
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        with pytest.raises(URLError):
            http_get("http://nonexistent.invalid")


# ---------------------------------------------------------------------- #
# http_post
# ---------------------------------------------------------------------- #
class TestHttpPost:
    """``http_post`` 测试。"""

    def test_success_with_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """带数据的 POST 请求。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"posted"))
        assert http_post("http://example.com", "key=value") == "posted"

    def test_success_empty_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空数据的 POST 请求。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"ok"))
        assert http_post("http://example.com") == "ok"

    def test_unicode_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unicode 响应体。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen("已提交".encode()))
        assert http_post("http://example.com", "data") == "已提交"

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 错误应抛出 HTTPError。"""
        error = HTTPError("http://example.com", 500, "Internal Server Error", Message(), None)
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        with pytest.raises(HTTPError):
            http_post("http://example.com", "data")


# ---------------------------------------------------------------------- #
# http_head
# ---------------------------------------------------------------------- #
class TestHttpHead:
    """``http_head`` 测试。"""

    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """成功 HEAD 请求返回响应头。"""
        headers = {"Content-Type": "text/html", "Server": "nginx"}
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"", headers))
        result = http_head("http://example.com")
        assert result["Content-Type"] == "text/html"
        assert result["Server"] == "nginx"

    def test_empty_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空响应头。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b""))
        result = http_head("http://example.com")
        assert result == {}

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 错误应抛出 HTTPError。"""
        error = HTTPError("http://example.com", 403, "Forbidden", Message(), None)
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        with pytest.raises(HTTPError):
            http_head("http://example.com")


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
class TestNettoolCLI:
    """``nettool`` 通过 ``run_tool`` 调用测试。"""

    def test_get_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd nettool get <url> 打印响应体。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"Hello!"))
        code = run_tool("nettool", ["get", "http://example.com"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Hello!" in out

    def test_get_error_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 请求失败时打印错误。"""
        error = URLError("connection refused")
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        code = run_tool("nettool", ["get", "http://nonexistent.invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请求失败" in out

    def test_post_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd nettool post <url> --data <data> 打印响应体。"""
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"posted"))
        code = run_tool("nettool", ["post", "http://example.com", "--data", "key=value"])
        assert code == 0
        out = capsys.readouterr().out
        assert "posted" in out

    def test_post_error_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """POST 请求失败时打印错误。"""
        error = HTTPError("http://example.com", 500, "Internal Server Error", Message(), None)
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        code = run_tool("nettool", ["post", "http://example.com", "--data", "x"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请求失败" in out

    def test_head_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd nettool head <url> 打印响应头。"""
        headers = {"Content-Type": "application/json"}
        monkeypatch.setattr("urllib.request.urlopen", _make_mock_urlopen(b"", headers))
        code = run_tool("nettool", ["head", "http://example.com"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Content-Type" in out
        assert "application/json" in out

    def test_head_error_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """HEAD 请求失败时打印错误。"""
        error = URLError("timeout")
        monkeypatch.setattr("urllib.request.urlopen", _make_error_urlopen(error))
        code = run_tool("nettool", ["head", "http://nonexistent.invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请求失败" in out


# ---------------------------------------------------------------------- #
# 镜像测速（probe_mirrors / probe_score / _probe_download）
# ---------------------------------------------------------------------- #
class _FakeProbeResponse:
    """镜像测速响应替身：支持 read(size) 与 headers.get_content_type()。"""

    def __init__(self, payload: bytes, content_type: str = "application/octet-stream") -> None:
        self._payload = payload
        self._offset = 0
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._offset
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def __enter__(self) -> _FakeProbeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class TestProbeMirrors:
    """镜像测速原语测试。"""

    def test_describe_http_error(self) -> None:
        """429 判定为限速，401/403 判定为受限，其余返回状态码。"""
        from fcmd.dsl.actions.net import _describe_http_error

        assert _describe_http_error(HTTPError("u", 429, "Too Many", Message(), None)) == "限速(429)"
        assert _describe_http_error(HTTPError("u", 403, "Forbidden", Message(), None)) == "受限(403)"
        assert _describe_http_error(HTTPError("u", 404, "Not Found", Message(), None)) == "HTTP 404"

    def test_probe_score(self) -> None:
        """不可达为 -1；大样本按吞吐；小样本退化为延迟倒数。"""
        from fcmd.dsl.actions.net import UrlProbe, probe_score

        assert probe_score(UrlProbe("u", False, 10.0, 100.0, 0)) == -1.0
        assert probe_score(UrlProbe("u", True, 50.0, 2048.0, 131072)) == 2048.0
        assert probe_score(UrlProbe("u", True, 50.0, 0.0, 100)) == pytest.approx(20.0)

    def test_probe_download_measures_throughput(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """读取采样字节并计算吞吐。"""
        from fcmd.dsl.actions.net import _probe_download

        payload = b"x" * 8192
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _FakeProbeResponse(payload))
        probe = _probe_download("https://mirror.example/file", 5.0, "pip/24.0", 8192)
        assert probe.ok is True
        assert probe.bytes_read == 8192
        assert probe.throughput_kbps > 0

    def test_probe_download_rejects_html_portal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未声明允许 HTML 时，HTML 门户页判为不可用。"""
        from fcmd.dsl.actions.net import _probe_download

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: _FakeProbeResponse(b"<html>portal</html>", "text/html"),
        )
        probe = _probe_download("https://mirror.example/missing", 5.0, "cargo/1.83.0", 2048)
        assert probe.ok is False
        assert probe.reason == "返回网页而非构件"

        allowed = _probe_download("https://mirror.example/simple/pkg/", 5.0, "pip/24.0", 2048, allow_html=True)
        assert allowed.ok is True

    def test_probe_download_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 429 返回限速原因。"""
        from fcmd.dsl.actions.net import _probe_download

        error = HTTPError("https://mirror.example", 429, "Too Many", Message(), None)
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: (_ for _ in ()).throw(error),
        )
        probe = _probe_download("https://mirror.example", 5.0, "pip/24.0", 2048)
        assert probe.ok is False
        assert probe.reason == "限速(429)"

    def test_probe_download_network_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """网络错误返回超时/网络错误原因。"""
        from fcmd.dsl.actions.net import _probe_download

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: (_ for _ in ()).throw(URLError("refused")),
        )
        probe = _probe_download("https://mirror.example", 5.0, "pip/24.0", 2048)
        assert probe.ok is False
        assert probe.reason == "超时/网络错误"

    def test_probe_mirrors_two_phase_and_sort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """阶段一并发筛选，阶段二仅对延迟最低的若干候选测吞吐并排序。"""
        from fcmd.dsl.actions.net import UrlProbe, probe_mirrors

        benchmarked: list[str] = []

        def fake_probe(
            url: str, timeout: float, user_agent: str, sample_bytes: int, allow_html: bool = False
        ) -> UrlProbe:
            if sample_bytes > 2048:  # 阶段二
                benchmarked.append(url)
                return UrlProbe(url, True, 10.0, 5000.0 if url.endswith("slow-latency") else 100.0, sample_bytes)
            if url.endswith("dead"):
                return UrlProbe(url, False, 0.0, 0.0, 0, "超时/网络错误")
            latency = 300.0 if url.endswith("slow-latency") else 20.0
            return UrlProbe(url, True, latency, 0.0, 2048)

        monkeypatch.setattr("fcmd.dsl.actions.net._probe_download", fake_probe)
        urls = ["https://a/dead", "https://b/slow-latency", "https://c/normal"]
        results = probe_mirrors(urls)

        assert [p.url for p in results] == ["https://b/slow-latency", "https://c/normal", "https://a/dead"]
        assert "https://a/dead" not in benchmarked
        assert set(benchmarked) == {"https://b/slow-latency", "https://c/normal"}

    def test_probe_mirrors_empty(self) -> None:
        """空输入返回空列表。"""
        from fcmd.dsl.actions.net import probe_mirrors

        assert probe_mirrors([]) == []
