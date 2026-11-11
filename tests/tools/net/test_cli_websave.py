"""websave 工具测试。

验证 ``fcmd.infras.net`` 与 ``fcmd.tools.net.websave``：
- 工具注册（单命令工具，无子命令）
- 同源判断与相关链接过滤（广告/静态资源/跨域）
- 链接提取与页面保存
- 深度控制、去重与错误处理
"""

from __future__ import annotations

from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.error import HTTPError

import pytest

import fcmd as fx
import fcmd.infras.net
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.net.websave import websave


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
        from fcmd.infras.net import is_same_origin

        assert is_same_origin("https://example.com", "https://example.com/page")
        assert not is_same_origin("https://example.com", "https://other.com")
        assert not is_same_origin("http://example.com", "https://example.com")

    def test_is_relevant_url(self) -> None:
        """is_relevant_url 过滤广告、静态资源与跨域链接。"""
        from fcmd.infras.net import is_relevant_url

        base = "https://example.com"
        assert is_relevant_url(base, "https://example.com/about")
        assert not is_relevant_url(base, "https://example.com/ad/banner")
        assert not is_relevant_url(base, "https://example.com/image.jpg")
        assert not is_relevant_url(base, "https://other.com/page")

    def test_extract_links(self) -> None:
        """extract_links 提取绝对链接并跳过锚点/邮件/电话链接。"""
        from fcmd.infras.net import extract_links

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
        from fcmd.infras.net import save_page

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
        monkeypatch.setattr(fcmd.infras.net, "http_get", mock)
        return mock

    def test_save_website_basic(
        self, tmp_path: Path, mock_http_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """深度 1 仅保存主页，广告链接不爬取。"""
        mock_http_get.return_value = """
        <html><body>
            <a href="/subpage">Subpage</a>
            <a href="https://ad.example.com">Ad</a>
        </body></html>
        """
        websave("https://example.com", str(tmp_path), depth=1)

        assert (tmp_path / "index.html").exists()
        assert not (tmp_path / "subpage.html").exists()
        mock_http_get.assert_called_once_with("https://example.com", timeout=30)
        assert "已保存到" in capsys.readouterr().out

    def test_save_website_with_depth(self, tmp_path: Path, mock_http_get: MagicMock) -> None:
        """深度 2 时爬取同源子页面。"""
        from fcmd.infras.net import save_website

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
        from fcmd.infras.net import save_website

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
        """请求失败时记录警告并继续。"""
        mock_http_get.side_effect = HTTPError("https://example.com", 404, "Not Found", Message(), None)
        websave("https://example.com", str(tmp_path), depth=1)

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


# ---------------------------------------------------------------------- #
# HTML/CSS 改写与资源递归下载测试
# ---------------------------------------------------------------------- #
class TestRewrite:
    """HTML/CSS 引用改写测试。"""

    def test_rewrite_html_keeps_query(self, tmp_path: Path) -> None:
        """JS 缓存规避链接（``style.css?time=``）改写后保留查询串。"""
        from fcmd.infras.net import rewrite_html

        html = (
            'document.write(\'<link rel="stylesheet" type="text/css" '
            "href=\"css/style.css?time=' + new Date().getTime() + '\">');"
        )
        page_file = tmp_path / "index.html"
        local_assets = {"https://example.com/css/style.css?time=": tmp_path / "assets" / "css" / "style_time_.css"}
        rewritten = rewrite_html(html, "https://example.com", page_file, local_assets, tmp_path)
        assert "style_time_.css?time=" in rewritten
        assert "new Date().getTime()" in rewritten

    def test_rewrite_css_urls(self, tmp_path: Path) -> None:
        """CSS ``url()`` 引用改写为本地相对路径。"""
        from fcmd.infras.net import rewrite_css

        css = (
            '@import url("base.css");\n'
            ".a { background: url(../images/bg.png); }\n"
            ".b { background: url(data:image/png;base64,xxx); }\n"
        )
        css_file = tmp_path / "assets" / "css" / "style.css"
        local_assets: dict[str, Path | None] = {
            "https://example.com/css/base.css": tmp_path / "assets" / "css" / "base.css",
            "https://example.com/images/bg.png": tmp_path / "assets" / "images" / "bg.png",
        }
        rewritten = rewrite_css(css, "https://example.com/css/style.css", css_file, local_assets)
        assert 'url("base.css")' in rewritten
        assert "url(../images/bg.png)" in rewritten
        assert "data:image/png" in rewritten


class TestDownloadAssets:
    """资源递归下载测试。"""

    @pytest.fixture
    def mock_http_get(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """模拟 http_get 函数。"""
        mock = MagicMock()
        monkeypatch.setattr(fcmd.infras.net, "http_get", mock)
        return mock

    @pytest.fixture
    def mock_download_asset(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """模拟 download_asset 函数。"""
        mock = MagicMock()
        monkeypatch.setattr(fcmd.infras.net, "download_asset", mock)
        return mock

    def test_css_recursive_download(self, tmp_path: Path, mock_download_asset: MagicMock) -> None:
        """CSS 内 ``url()`` 引用的图片一并下载，且 CSS 改写为本地引用。"""
        from fcmd.infras.net import download_assets_recursive

        def fake_download(url: str, timeout: int = 30) -> bytes:
            if url == "https://example.com/css/style.css":
                return b".a { background: url(../images/bg.png); }"
            if url == "https://example.com/images/bg.png":
                return b"\x89PNG"
            raise AssertionError(f"unexpected url {url}")

        mock_download_asset.side_effect = fake_download
        cache: dict[str, Path | None] = {}
        download_assets_recursive(["https://example.com/css/style.css"], tmp_path, cache)

        css_file = tmp_path / "assets" / "example.com" / "css" / "style.css"
        assert css_file.exists()
        # 目录结构与源站一致，相对引用无需改写即可命中本地文件
        assert "url(../images/bg.png)" in css_file.read_text(encoding="utf-8")
        assert (tmp_path / "assets" / "example.com" / "images" / "bg.png").read_bytes() == b"\x89PNG"

    def test_asset_failure_marked(self, tmp_path: Path, mock_download_asset: MagicMock) -> None:
        """下载失败以 ``None`` 标记缓存，不重复尝试。"""
        from fcmd.infras.net import download_assets_recursive

        mock_download_asset.side_effect = HTTPError("https://example.com/x.png", 404, "Not Found", Message(), None)
        cache: dict[str, Path | None] = {}
        download_assets_recursive(["https://example.com/x.png"], tmp_path, cache)
        assert cache["https://example.com/x.png"] is None
        download_assets_recursive(["https://example.com/x.png"], tmp_path, cache)
        assert mock_download_asset.call_count == 1

    def test_save_website_downloads_images(
        self, tmp_path: Path, mock_http_get: MagicMock, mock_download_asset: MagicMock
    ) -> None:
        """页面中的图片下载到 assets 并改写为本地路径。"""
        from fcmd.infras.net import save_website

        mock_http_get.return_value = (
            '<html><body><img src="/logo.png" alt="logo"><a href="/subpage">Sub</a></body></html>'
        )
        mock_download_asset.return_value = b"\x89PNG"
        save_website("https://example.com", str(tmp_path), depth=1)

        img_file = tmp_path / "assets" / "example.com" / "logo.png"
        assert img_file.read_bytes() == b"\x89PNG"
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "assets/example.com/logo.png" in html

    def test_save_website_skips_failed_asset(
        self, tmp_path: Path, mock_http_get: MagicMock, mock_download_asset: MagicMock
    ) -> None:
        """下载失败的资源保持原始引用，不写入本地映射。"""
        from fcmd.infras.net import save_website

        mock_http_get.return_value = '<img src="/broken.png">'
        mock_download_asset.side_effect = HTTPError("https://example.com/broken.png", 404, "Not Found", Message(), None)
        save_website("https://example.com", str(tmp_path), depth=1)

        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'src="/broken.png"' in html


# ---------------------------------------------------------------------- #
# URL 映射 / 资源收集 / 下载
# ---------------------------------------------------------------------- #
class TestUrlMapping:
    """URL 到本地路径的映射测试。"""

    def test_url_to_file_path_with_query(self, tmp_path: Path) -> None:
        """查询串被清洗为文件名片段并保留扩展名。"""
        from fcmd.infras.net import url_to_file_path

        page = url_to_file_path("https://example.com/a.html?x=1&y=2", tmp_path)
        assert page == tmp_path / "a_x_1_y_2.html"

        search = url_to_file_path("https://example.com/search?q=a b", tmp_path)
        assert search == tmp_path / "search_q_a_b.html"

    def test_asset_to_file_path(self, tmp_path: Path) -> None:
        """资源路径按主机分目录；空路径落为 index，查询串被清洗。"""
        from fcmd.infras.net import asset_to_file_path

        assert asset_to_file_path("https://cdn.example.com:8443/", tmp_path) == (
            tmp_path / "assets" / "cdn.example.com_8443" / "index"
        )
        assert asset_to_file_path("https://example.com/img.png?v=1", tmp_path).name == "img_v_1.png"


class TestCollectAssetUrls:
    """``collect_asset_urls`` 过滤与去重测试。"""

    def test_filters_ads_cross_origin_and_inline(self) -> None:
        from fcmd.infras.net import collect_asset_urls

        html = """
        <img src="">
        <img src="#frag">
        <img src="javascript:void(0)">
        <img src="data:image/png;base64,xxx">
        <img src="/ad.banner.png">
        <img src="/pic.png">
        <link href="https://other.com/style.css">
        <script src="/app.js"></script>
        <script src="/app.js"></script>
        <div style="background: url(/bg.png)"></div>
        <div style="background: url(data:image/png;base64,xxx)"></div>
        <div style="background: url(javascript:void(0))"></div>
        <div style="background: url(/banner-bg.png)"></div>
        <div style="background: url(/bg2.png?x=1)"></div>
        """

        assets = collect_asset_urls(html, "https://example.com/page")

        assert assets == [
            "https://example.com/pic.png",
            "https://example.com/app.js",
            "https://example.com/bg.png",
            "https://example.com/bg2.png?x=1",
        ]


class TestDownloadAsset:
    """``download_asset`` 直接下载测试。"""

    def test_download_asset_reads_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fcmd.infras.net import download_asset

        class _Response:
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *args: object) -> bool:
                return False

            def read(self) -> bytes:
                return b"PNG"

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30: _Response())
        assert download_asset("https://example.com/a.png") == b"PNG"


# ---------------------------------------------------------------------- #
# 改写分支与镜像测速重试
# ---------------------------------------------------------------------- #
class TestRewriteBranches:
    """``rewrite_html`` / ``rewrite_css`` 的边界分支。"""

    def test_rewrite_html_link_and_background_branches(self, tmp_path: Path) -> None:
        from fcmd.infras.net import rewrite_html

        html = (
            '<a href="mailto:x@y.com">m</a>'
            '<a href="#top">t</a>'
            '<a href="/p#sec">p</a>'
            '<a href="https://other.com/x">x</a>'
            '<img src="/missing.png">'
            '<div style="background: url(/bg.png?x=1)"></div>'
            '<div style="background: url(data:image/png;base64,xxx)"></div>'
            '<div style="background: url(/nope.png)"></div>'
        )
        local_assets = {"https://example.com/bg.png?x=1": tmp_path / "assets" / "bg.png"}

        rewritten = rewrite_html(
            html,
            "https://example.com",
            tmp_path / "index.html",
            local_assets,
            tmp_path,
            rewrite_links=True,
        )

        assert 'href="mailto:x@y.com"' in rewritten
        assert 'href="#top"' in rewritten
        assert 'href="p.html#sec"' in rewritten
        assert 'href="https://other.com/x"' in rewritten
        assert 'src="/missing.png"' in rewritten
        assert "url(assets/bg.png?x=1)" in rewritten
        assert "url(data:image/png" in rewritten
        assert 'url("/nope.png")' not in rewritten
        assert "url(/nope.png)" in rewritten

    def test_rewrite_css_skips_unknown_and_keeps_query(self, tmp_path: Path) -> None:
        from fcmd.infras.net import rewrite_css

        css = (
            ".a { background: url(/missing.png); }\n"
            ".b { background: url(/x.png?v=2); }\n"
            ".c { background: url(#frag); }\n"
        )
        local_assets: dict[str, Path | None] = {"https://example.com/x.png?v=2": tmp_path / "assets" / "x.png"}

        rewritten = rewrite_css(css, "https://example.com/style.css", tmp_path / "style.css", local_assets)

        assert "url(/missing.png)" in rewritten
        assert "url(assets/x.png?v=2)" in rewritten
        assert "url(#frag)" in rewritten


class TestDownloadAssetsRecursiveBranches:
    """CSS 递归下载的去重与内联过滤。"""

    def test_dedup_cached_and_inline_refs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from fcmd.infras.net import download_assets_recursive

        calls: list[str] = []

        css = (
            ".a { background: url(/new.png); }\n"
            ".b { background: url(/new.png); }\n"
            ".c { background: url(data:image/png;base64,xxx); }\n"
            ".d { background: url(#frag); }\n"
            ".e { background: url(javascript:void(0)); }\n"
            ".f { background: url(/ad.banner.png); }\n"
        )

        def _fake_download(url: str, timeout: int = 30) -> bytes:
            calls.append(url)
            if url == "https://example.com/a.css":
                return css.encode("utf-8")
            return b"PNG"

        monkeypatch.setattr("fcmd.infras.net.download_asset", _fake_download)

        cache: dict[str, Path | None] = {}
        download_assets_recursive(["https://example.com/a.css"], tmp_path, cache)

        assert calls == ["https://example.com/a.css", "https://example.com/new.png"]


class TestProbeMirrorsRetry:
    """``probe_mirrors`` 对失败项的并发重试。"""

    def test_failed_probe_is_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fcmd.core.shared import net

        calls: list[tuple[str, int]] = []

        def _fake_probe(url: str, timeout: float, user_agent: str, sample_bytes: int, allow_html: bool = False) -> Any:
            calls.append((url, sample_bytes))
            attempts = sum(1 for item in calls if item == (url, net._PROBE_QUICK_BYTES))
            ok = url == "https://ok.example" or attempts > 1
            return net.UrlProbe(
                url,
                ok,
                1.0,
                float(sample_bytes) if ok else 0.0,
                sample_bytes if ok else 0,
                "" if ok else "超时",
            )

        monkeypatch.setattr(net, "_probe_download", _fake_probe)
        results = net.probe_mirrors(["https://ok.example", "https://flaky.example"], timeout=1.0)

        assert [probe.url for probe in results] == ["https://ok.example", "https://flaky.example"]
        assert all(probe.ok for probe in results)
