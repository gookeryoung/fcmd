"""urltool 工具测试。

验证 ``fcmd.cli.urltool`` 模块：
- 工具注册与四子命令结构（parse/query/addquery/baseurl）
- ``parse_url``/``get_query_param``/``add_query_param``/``get_base_url``
- 错误分支
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.urltool import (
    add_query_param,
    get_base_url,
    get_query_param,
    parse_url,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """urltool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "urltool" in list_tools()

    def test_subcommands(self) -> None:
        """urltool 有 parse/query/addquery/baseurl 四个子命令。"""
        subs = list_subcommands("urltool")
        assert set(subs) == {"parse", "query", "addquery", "baseurl"}


# ============================================================================ #
# parse_url
# ============================================================================ #
class TestParseUrl:
    """parse_url 解析测试。"""

    def test_full_url(self) -> None:
        """完整 URL 解析。"""
        parts = parse_url("https://example.com/path?key=value#frag")
        assert parts["scheme"] == "https"
        assert parts["netloc"] == "example.com"
        assert parts["path"] == "/path"
        assert parts["query"] == "key=value"
        assert parts["fragment"] == "frag"

    def test_with_port(self) -> None:
        """带端口。"""
        parts = parse_url("http://localhost:8080/api")
        assert parts["scheme"] == "http"
        assert parts["netloc"] == "localhost:8080"
        assert parts["path"] == "/api"

    def test_no_query(self) -> None:
        """无查询参数。"""
        parts = parse_url("https://example.com/path")
        assert parts["query"] == ""
        assert parts["fragment"] == ""

    def test_no_path(self) -> None:
        """无路径。"""
        parts = parse_url("https://example.com")
        assert parts["path"] == ""

    def test_multiple_params(self) -> None:
        """多个查询参数。"""
        parts = parse_url("https://example.com?a=1&b=2&c=3")
        assert parts["query"] == "a=1&b=2&c=3"

    def test_empty_url_raises(self) -> None:
        """空 URL 抛 ValueError。"""
        with pytest.raises(ValueError, match="URL 不能为空"):
            parse_url("")


# ============================================================================ #
# get_query_param
# ============================================================================ #
class TestGetQueryParam:
    """get_query_param 查询参数提取测试。"""

    def test_existing_param(self) -> None:
        """参数存在。"""
        assert get_query_param("https://example.com?a=1&b=2", "a") == "1"
        assert get_query_param("https://example.com?a=1&b=2", "b") == "2"

    def test_missing_param(self) -> None:
        """参数不存在返回 None。"""
        assert get_query_param("https://example.com?a=1", "z") is None

    def test_no_query_string(self) -> None:
        """无查询字符串返回 None。"""
        assert get_query_param("https://example.com/path", "a") is None

    def test_url_encoded_value(self) -> None:
        """URL 编码的参数值。"""
        assert get_query_param("https://example.com?q=hello%20world", "q") == "hello world"

    def test_multi_value_returns_first(self) -> None:
        """多值参数取第一个。"""
        assert get_query_param("https://example.com?a=1&a=2", "a") == "1"

    def test_empty_url_raises(self) -> None:
        """空 URL 抛 ValueError。"""
        with pytest.raises(ValueError, match="URL 不能为空"):
            get_query_param("", "a")


# ============================================================================ #
# add_query_param
# ============================================================================ #
class TestAddQueryParam:
    """add_query_param 添加查询参数测试。"""

    def test_add_to_no_query(self) -> None:
        """无原查询参数。"""
        result = add_query_param("https://example.com/path", "key", "value")
        assert "key=value" in result
        assert "https://example.com/path" in result

    def test_add_to_existing_query(self) -> None:
        """保留原参数并追加。"""
        result = add_query_param("https://example.com?a=1", "b", "2")
        assert "a=1" in result
        assert "b=2" in result

    def test_add_duplicate_key(self) -> None:
        """重复键追加（不覆盖）。"""
        result = add_query_param("https://example.com?a=1", "a", "2")
        # 两个 a 参数都应存在
        assert "a=1" in result
        assert "a=2" in result

    def test_special_chars_encoded(self) -> None:
        """特殊字符被编码。"""
        result = add_query_param("https://example.com", "q", "hello world")
        assert "q=hello+world" in result or "q=hello%20world" in result

    def test_preserves_fragment(self) -> None:
        """保留 fragment。"""
        result = add_query_param("https://example.com/path#section", "k", "v")
        assert "#section" in result

    def test_empty_url_raises(self) -> None:
        """空 URL 抛 ValueError。"""
        with pytest.raises(ValueError, match="URL 不能为空"):
            add_query_param("", "k", "v")


# ============================================================================ #
# get_base_url
# ============================================================================ #
class TestGetBaseUrl:
    """get_base_url 基础 URL 提取测试。"""

    def test_basic(self) -> None:
        """基本提取。"""
        assert get_base_url("https://example.com/path?q=1#f") == "https://example.com"

    def test_with_port(self) -> None:
        """带端口。"""
        assert get_base_url("http://localhost:8080/api/x") == "http://localhost:8080"

    def test_no_extra_parts(self) -> None:
        """无路径/查询/片段。"""
        assert get_base_url("https://example.com") == "https://example.com"

    def test_empty_url_raises(self) -> None:
        """空 URL 抛 ValueError。"""
        with pytest.raises(ValueError, match="URL 不能为空"):
            get_base_url("")

    def test_missing_scheme_raises(self) -> None:
        """缺少 scheme 抛 ValueError。"""
        with pytest.raises(ValueError, match="URL 缺少 scheme"):
            get_base_url("example.com/path")

    def test_missing_netloc_raises(self) -> None:
        """缺少 netloc 抛 ValueError。"""
        with pytest.raises(ValueError, match="URL 缺少 netloc"):
            get_base_url("https:///path")


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestUrltoolCLI:
    """``urltool`` 通过 ``run_tool`` 调用测试。"""

    def test_parse(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd urltool parse。"""
        code = run_tool("urltool", ["parse", "https://example.com/path?key=value#frag"])
        assert code == 0
        out = capsys.readouterr().out
        assert "scheme: https" in out
        assert "netloc: example.com" in out
        assert "path: /path" in out
        assert "query: key=value" in out
        assert "fragment: frag" in out

    def test_parse_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """parse 空 URL 提示。"""
        code = run_tool("urltool", ["parse", ""])
        assert code == 0
        out = capsys.readouterr().out
        assert "URL 不能为空" in out

    def test_query_existing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd urltool query 已存在参数。"""
        code = run_tool("urltool", ["query", "https://example.com?a=1&b=2", "a"])
        assert code == 0
        out = capsys.readouterr().out
        assert "1" in out

    def test_query_missing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """query 不存在参数提示。"""
        code = run_tool("urltool", ["query", "https://example.com?a=1", "z"])
        assert code == 0
        out = capsys.readouterr().out
        assert "参数不存在" in out

    def test_addquery(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd urltool addquery。"""
        code = run_tool("urltool", ["addquery", "https://example.com", "foo", "bar"])
        assert code == 0
        out = capsys.readouterr().out
        assert "foo=bar" in out

    def test_baseurl(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd urltool baseurl。"""
        code = run_tool("urltool", ["baseurl", "https://example.com/path?key=value"])
        assert code == 0
        out = capsys.readouterr().out
        assert "https://example.com" in out

    def test_baseurl_missing_scheme(self, capsys: pytest.CaptureFixture[str]) -> None:
        """baseurl 缺少 scheme 提示。"""
        code = run_tool("urltool", ["baseurl", "example.com/path"])
        assert code == 0
        out = capsys.readouterr().out
        assert "URL 缺少 scheme" in out

    def test_query_empty_url(self, capsys: pytest.CaptureFixture[str]) -> None:
        """query 空 URL 异常提示。"""
        code = run_tool("urltool", ["query", "", "a"])
        assert code == 0
        out = capsys.readouterr().out
        assert "URL 不能为空" in out

    def test_addquery_empty_url(self, capsys: pytest.CaptureFixture[str]) -> None:
        """addquery 空 URL 异常提示。"""
        code = run_tool("urltool", ["addquery", "", "k", "v"])
        assert code == 0
        out = capsys.readouterr().out
        assert "URL 不能为空" in out
