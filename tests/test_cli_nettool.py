"""nettool 工具测试。

验证 ``fcmd.cli.net.nettool`` 模块：
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
import fcmd.cli.net.nettool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.net.nettool import http_get, http_head, http_post

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
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"Hello, World!"))
        assert http_get("http://example.com") == "Hello, World!"

    def test_unicode_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unicode 响应体。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen("你好世界".encode()))
        assert http_get("http://example.com") == "你好世界"

    def test_custom_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """自定义超时。"""
        mock_fn = _make_mock_urlopen(b"ok")
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", mock_fn)
        assert http_get("http://example.com", timeout=10) == "ok"

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 错误应抛出 HTTPError。"""
        error = HTTPError("http://example.com", 404, "Not Found", Message(), None)
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
        with pytest.raises(HTTPError):
            http_get("http://example.com")

    def test_url_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """网络错误应抛出 URLError。"""
        error = URLError("Name or service not known")
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
        with pytest.raises(URLError):
            http_get("http://nonexistent.invalid")


# ---------------------------------------------------------------------- #
# http_post
# ---------------------------------------------------------------------- #
class TestHttpPost:
    """``http_post`` 测试。"""

    def test_success_with_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """带数据的 POST 请求。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"posted"))
        assert http_post("http://example.com", "key=value") == "posted"

    def test_success_empty_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空数据的 POST 请求。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"ok"))
        assert http_post("http://example.com") == "ok"

    def test_unicode_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unicode 响应体。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen("已提交".encode()))
        assert http_post("http://example.com", "data") == "已提交"

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 错误应抛出 HTTPError。"""
        error = HTTPError("http://example.com", 500, "Internal Server Error", Message(), None)
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
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
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"", headers))
        result = http_head("http://example.com")
        assert result["Content-Type"] == "text/html"
        assert result["Server"] == "nginx"

    def test_empty_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空响应头。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b""))
        result = http_head("http://example.com")
        assert result == {}

    def test_http_error_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HTTP 错误应抛出 HTTPError。"""
        error = HTTPError("http://example.com", 403, "Forbidden", Message(), None)
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
        with pytest.raises(HTTPError):
            http_head("http://example.com")


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
class TestNettoolCLI:
    """``nettool`` 通过 ``run_tool`` 调用测试。"""

    def test_get_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd nettool get <url> 打印响应体。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"Hello!"))
        code = run_tool("nettool", ["get", "http://example.com"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Hello!" in out

    def test_get_error_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """GET 请求失败时打印错误。"""
        error = URLError("connection refused")
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
        code = run_tool("nettool", ["get", "http://nonexistent.invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请求失败" in out

    def test_post_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd nettool post <url> --data <data> 打印响应体。"""
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"posted"))
        code = run_tool("nettool", ["post", "http://example.com", "--data", "key=value"])
        assert code == 0
        out = capsys.readouterr().out
        assert "posted" in out

    def test_post_error_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """POST 请求失败时打印错误。"""
        error = HTTPError("http://example.com", 500, "Internal Server Error", Message(), None)
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
        code = run_tool("nettool", ["post", "http://example.com", "--data", "x"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请求失败" in out

    def test_head_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd nettool head <url> 打印响应头。"""
        headers = {"Content-Type": "application/json"}
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_mock_urlopen(b"", headers))
        code = run_tool("nettool", ["head", "http://example.com"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Content-Type" in out
        assert "application/json" in out

    def test_head_error_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """HEAD 请求失败时打印错误。"""
        error = URLError("timeout")
        monkeypatch.setattr(fcmd.cli.net.nettool, "urlopen", _make_error_urlopen(error))
        code = run_tool("nettool", ["head", "http://nonexistent.invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "请求失败" in out
