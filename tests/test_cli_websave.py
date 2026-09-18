"""websave 工具测试。

验证 ``fcmd.cli.net.websave`` 模块：
- 工具注册（单命令工具，无子命令）
- 同源判断与相关链接过滤（广告/静态资源/跨域）
- 链接提取与页面保存
- 深度控制、去重与错误处理
"""

from __future__ import annotations

from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

import fcmd as fx
import fcmd.cli.net.websave
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """websave 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """websave 应在 _TOOL_REGISTRY 中注册。"""
        assert "websave" in _TOOL_REGISTRY, "工具 'websave' 未注册"

    def test_websave_single_command(self) -> None:
        """websave 是单命令工具（无子命令）。"""
        subs = fx.list_subcommands("websave")
        assert subs == []


# ---------------------------------------------------------------------- #
# 辅助函数测试
# ---------------------------------------------------------------------- #
class TestHelperFunctions:
    """辅助函数测试。"""

    def test_is_same_origin(self) -> None:
        """is_same_origin 正确判断同源。"""
        from fcmd.cli.net.websave import is_same_origin

        assert is_same_origin("https://example.com", "https://example.com/page")
        assert not is_same_origin("https://example.com", "https://other.com")
        assert not is_same_origin("http://example.com", "https://example.com")

    def test_is_relevant_url(self) -> None:
        """is_relevant_url 过滤广告、静态资源与跨域链接。"""
        from fcmd.cli.net.websave import is_relevant_url

        base = "https://example.com"
        assert is_relevant_url(base, "https://example.com/about")
        assert not is_relevant_url(base, "https://example.com/ad/banner")
        assert not is_relevant_url(base, "https://example.com/image.jpg")
        assert not is_relevant_url(base, "https://other.com/page")

    def test_extract_links(self) -> None:
        """extract_links 提取绝对链接并跳过锚点/邮件/电话链接。"""
        from fcmd.cli.net.websave import extract_links

        html = """
        <a href="/page1">Page 1</a>
        <a href="http://example.com/page2">Page 2</a>
        <a href="#anchor">Anchor</a>
        <a href="mailto:test@example.com">Email</a>
        <a href="tel:123456">Tel</a>
        """
        links = extract_links(html, "https://example.com")
        assert links == ["https://example.com/page1", "http://example.com/page2"]

    def test_save_page(self, tmp_path: Path) -> None:
        """save_page 按 URL 路径映射为本地 HTML 文件。"""
        from fcmd.cli.net.websave import save_page

        save_page("https://example.com", "<h1>root</h1>", tmp_path)
        assert (tmp_path / "index.html").read_text(encoding="utf-8") == "<h1>root</h1>"

        save_page("https://example.com/about", "<h1>about</h1>", tmp_path)
        assert (tmp_path / "about.html").read_text(encoding="utf-8") == "<h1>about</h1>"

        save_page("https://example.com/docs/guide.html", "<h1>guide</h1>", tmp_path)
        assert (tmp_path / "docs" / "guide.html").read_text(encoding="utf-8") == "<h1>guide</h1>"


# ---------------------------------------------------------------------- #
# websave 工具测试
# ---------------------------------------------------------------------- #
class TestWebsave:
    """``websave`` 工具测试。"""

    @pytest.fixture
    def mock_http_get(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """模拟 http_get 函数。"""
        mock = MagicMock()
        monkeypatch.setattr(fcmd.cli.net.websave, "http_get", mock)
        return mock

    def test_save_website_basic(
        self, tmp_path: Path, mock_http_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """深度 1 仅保存主页，广告链接不爬取。"""
        from fcmd.cli.net.websave import save_website

        mock_http_get.return_value = """
        <html><body>
            <a href="/subpage">Subpage</a>
            <a href="https://ad.example.com">Ad</a>
        </body></html>
        """
        save_website("https://example.com", str(tmp_path), depth=1)

        assert (tmp_path / "index.html").exists()
        assert not (tmp_path / "subpage.html").exists()
        mock_http_get.assert_called_once_with("https://example.com", timeout=30)
        assert "已保存到" in capsys.readouterr().out

    def test_save_website_with_depth(self, tmp_path: Path, mock_http_get: MagicMock) -> None:
        """深度 2 时爬取同源子页面。"""
        from fcmd.cli.net.websave import save_website

        def fake_get(url: str, timeout: int = 30) -> str:
            if url == "https://example.com":
                return '<a href="/subpage">Sub</a><a href="https://example.com/logo.png">Logo</a>'
            if url == "https://example.com/subpage":
                return "<p>sub</p>"
            return ""

        mock_http_get.side_effect = fake_get
        save_website("https://example.com", str(tmp_path), depth=2)

        assert (tmp_path / "index.html").exists()
        assert (tmp_path / "subpage.html").exists()
        assert mock_http_get.call_count == 2

    def test_save_website_dedup(self, tmp_path: Path, mock_http_get: MagicMock) -> None:
        """同一子页面被多个父页面引用时只爬取一次。"""
        from fcmd.cli.net.websave import save_website

        pages = {
            "https://example.com": '<a href="/a">A</a><a href="/b">B</a>',
            "https://example.com/a": '<a href="/c">C</a>',
            "https://example.com/b": '<a href="/c">C</a>',
            "https://example.com/c": "<p>c</p>",
        }
        mock_http_get.side_effect = lambda url, **_: pages[url]
        save_website("https://example.com", str(tmp_path), depth=3)

        # /c 被 /a、/b 同时引用，只应请求一次
        assert mock_http_get.call_count == 4
        assert (tmp_path / "c.html").exists()

    def test_save_website_http_error(
        self, tmp_path: Path, mock_http_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """请求失败时打印警告并继续。"""
        from fcmd.cli.net.websave import save_website

        mock_http_get.side_effect = HTTPError("https://example.com", 404, "Not Found", Message(), None)
        save_website("https://example.com", str(tmp_path), depth=1)

        assert not (tmp_path / "index.html").exists()
        assert "无法获取" in capsys.readouterr().out

    def test_websave_via_run_tool(
        self, tmp_path: Path, mock_http_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """通过 run_tool 调用 websave 命令。"""
        mock_http_get.return_value = "<html><body>Test</body></html>"
        code = run_tool("websave", ["https://example.com", "--output-dir", str(tmp_path), "--depth", "1"])
        assert code == 0
        assert (tmp_path / "index.html").exists()
        assert "已保存到" in capsys.readouterr().out
