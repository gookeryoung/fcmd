"""urlcheck 工具测试。

验证 ``fcmd.cli.net.urlcheck`` 模块：
- 工具注册与子命令结构（u / r）
- check_url 可访问 / HEAD 回退 GET / 不可达
- check_urls 并发探测 + 排序（可达优先、延迟升序）
- 通过 run_tool 调用 u / r 子命令
"""

from __future__ import annotations

import time
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

import fcmd as fx
import fcmd.cli.net.urlcheck
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.net.urlcheck import check_url, check_urls, unreachable_latency

# ---------------------------------------------------------------------- #
# 辅助函数
# ---------------------------------------------------------------------- #


def _make_ok_urlopen(delay: float = 0.0) -> Any:
    """创建模拟 urlopen：任何请求均成功（支持上下文管理器）。"""

    def _urlopen(req: object, timeout: float = 5) -> Any:
        class _Resp:
            def __enter__(self) -> _Resp:
                if delay:
                    time.sleep(delay)
                return self

            def __exit__(self, *args: object) -> bool:
                return False

        return _Resp()

    return _urlopen


def _make_error_urlopen(error: Exception) -> Any:
    """创建对所有请求均抛出异常的模拟 urlopen。"""

    def _urlopen(req: object, timeout: float = 5) -> Any:
        raise error

    return _urlopen


def _make_method_error_urlopen(head_error: Exception, get_error: Exception | None = None) -> Any:
    """创建按 HTTP 方法区分行为的模拟 urlopen。

    Parameters
    ----------
    head_error:
        HEAD 请求抛出的异常
    get_error:
        GET 请求抛出的异常；``None`` 表示 GET 成功
    """

    def _urlopen(req: Any, timeout: float = 5) -> Any:
        method = getattr(req, "method", None) or "GET"
        if method == "HEAD":
            raise head_error
        if get_error is not None:
            raise get_error
        return _make_ok_urlopen()(req, timeout)

    return _urlopen


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """urlcheck 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """urlcheck 应在 _TOOL_REGISTRY 中注册。"""
        assert "urlcheck" in _TOOL_REGISTRY, "工具 'urlcheck' 未注册"

    def test_urlcheck_subcommands(self) -> None:
        """urlcheck 应有 u / r 子命令。"""
        subs = fx.list_subcommands("urlcheck")
        assert "u" in subs
        assert "r" in subs


# ---------------------------------------------------------------------- #
# check_url
# ---------------------------------------------------------------------- #
class TestCheckUrl:
    """``check_url`` 测试。"""

    def test_reachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HEAD 成功返回可达与非负延迟。"""
        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _make_ok_urlopen())
        ok, latency = check_url("https://mirror.example.com")
        assert ok is True
        assert 0 <= latency < 5000

    def test_head_405_fallback_get(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HEAD 返回 405 时回退 GET 验证。"""
        error = HTTPError("https://mirror.example.com", 405, "Method Not Allowed", Message(), None)
        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _make_method_error_urlopen(error))
        ok, latency = check_url("https://mirror.example.com")
        assert ok is True
        assert latency >= 0

    def test_http_error_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HEAD 与 GET 均收到 HTTP 错误响应（如 WAF 全站 403）时不可访问。"""
        error = HTTPError("https://mirror.example.com", 403, "Forbidden", Message(), None)
        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _make_error_urlopen(error))
        ok, latency = check_url("https://mirror.example.com")
        assert ok is False
        assert latency == unreachable_latency()

    def test_head_ok_but_get_error_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HEAD 被拒回退 GET 后仍报 HTTP 错误时不可访问（GET 决定最终结果）。"""
        head_error = HTTPError("https://mirror.example.com", 405, "Method Not Allowed", Message(), None)
        get_error = HTTPError("https://mirror.example.com", 404, "Not Found", Message(), None)
        monkeypatch.setattr(
            fcmd.cli.net.urlcheck, "urlopen", _make_method_error_urlopen(head_error, get_error=get_error)
        )
        ok, latency = check_url("https://mirror.example.com")
        assert ok is False
        assert latency == unreachable_latency()

    def test_request_carries_user_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """探测请求携带浏览器 UA（规避镜像站 WAF 拦截默认 UA）。"""
        seen: list[dict[str, str]] = []

        def _urlopen(req: Any, timeout: float = 5) -> Any:
            seen.append(dict(req.headers))
            return _make_ok_urlopen()(req, timeout)

        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _urlopen)
        check_url("https://mirror.example.com")
        ua = next((v for k, v in seen[0].items() if k.lower() == "user-agent"), "")
        assert "fcmd-urlcheck" in ua

    def test_url_error_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DNS / 连接错误直接判定不可达（不回退 GET）。"""
        error = URLError("Name or service not known")
        calls: list[str] = []

        def _urlopen(req: Any, timeout: float = 5) -> Any:
            method = getattr(req, "method", None) or "GET"
            calls.append(method)
            raise error

        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _urlopen)
        ok, latency = check_url("https://nonexistent.invalid")
        assert ok is False
        assert latency == unreachable_latency()
        # 连接级错误仅尝试 HEAD，不回退 GET
        assert calls == ["HEAD"]


# ---------------------------------------------------------------------- #
# check_urls
# ---------------------------------------------------------------------- #
class TestCheckUrls:
    """``check_urls`` 测试。"""

    def test_empty_urls(self) -> None:
        """空列表返回空结果。"""
        assert check_urls([]) == []

    def test_sorted_by_latency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """可达项按延迟升序，不可达项排在末尾。"""
        latency_by_url = {
            "https://slow.example.com": 300.0,
            "https://fast.example.com": 20.0,
            "https://down.example.com": unreachable_latency(),
        }

        def fake_check_url(url: str, timeout: float = 5.0) -> tuple[bool, float]:
            ok = latency_by_url[url] != unreachable_latency()
            return ok, latency_by_url[url]

        monkeypatch.setattr(fcmd.cli.net.urlcheck, "check_url", fake_check_url)
        results = check_urls(list(latency_by_url))
        assert results == [
            ("https://fast.example.com", True, 20.0),
            ("https://slow.example.com", True, 300.0),
            ("https://down.example.com", False, unreachable_latency()),
        ]

    def test_all_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全部不可达时保持输入顺序（延迟相同，排序稳定）。"""

        def fake_check_url(url: str, timeout: float = 5.0) -> tuple[bool, float]:
            return False, unreachable_latency()

        monkeypatch.setattr(fcmd.cli.net.urlcheck, "check_url", fake_check_url)
        results = check_urls(["https://a.example.com", "https://b.example.com"])
        assert [r[0] for r in results] == ["https://a.example.com", "https://b.example.com"]
        assert all(not r[1] for r in results)


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
class TestUrlcheckCLI:
    """``urlcheck`` 通过 ``run_tool`` 调用测试。"""

    def test_single_check_via_run_tool(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """fcmd urlcheck u <url> 打印可访问与延迟。"""
        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _make_ok_urlopen())
        code = run_tool("urlcheck", ["u", "https://mirror.example.com"])
        assert code == 0
        out = capsys.readouterr().out
        assert "可访问" in out
        assert "延迟" in out

    def test_single_check_unreachable_via_run_tool(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """连接级错误（DNS/拒绝连接）时打印不可访问。"""
        error = URLError("connection refused")
        monkeypatch.setattr(fcmd.cli.net.urlcheck, "urlopen", _make_error_urlopen(error))
        code = run_tool("urlcheck", ["u", "https://nonexistent.invalid"])
        assert code == 0
        assert "不可访问" in capsys.readouterr().out

    def test_rank_via_run_tool(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd urlcheck r <urls...> 按速度排序打印。"""

        def fake_check_url(url: str, timeout: float = 5.0) -> tuple[bool, float]:
            if "fast" in url:
                return True, 10.0
            if "slow" in url:
                return True, 200.0
            return False, unreachable_latency()

        monkeypatch.setattr(fcmd.cli.net.urlcheck, "check_url", fake_check_url)
        code = run_tool(
            "urlcheck", ["r", "https://slow.example.com", "https://fast.example.com", "https://down.example.com"]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert out.index("fast.example.com") < out.index("slow.example.com") < out.index("down.example.com")
        assert "按访问速度排序" in out

    def test_rank_empty_direct_call(self, capsys: pytest.CaptureFixture[str]) -> None:
        """空 URL 列表直接调用时打印提示（CLI 侧由 argparse 必填参数拦截）。"""
        fcmd.cli.net.urlcheck.check_urls_cmd([])
        assert "至少一个 URL" in capsys.readouterr().out
