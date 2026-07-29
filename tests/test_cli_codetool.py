"""codetool 工具测试。

验证 ``fcmd.cli.codetool`` 模块：
- 工具注册与子命令结构
- Base64 / URL / Hex / ROT13 / HTML 编解码
- 通过 run_tool 调用各子命令
"""

from __future__ import annotations

import base64 as b64
import binascii

import pytest

import fcmd as fx
import fcmd.cli.codetool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.codetool import (
    decode_base64,
    decode_hex,
    decode_url,
    encode_base64,
    encode_hex,
    encode_url,
    escape_html,
    rot13,
    unescape_html,
)


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """codetool 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """codetool 应在 _TOOL_REGISTRY 中注册。"""
        assert "codetool" in _TOOL_REGISTRY, "工具 'codetool' 未注册"

    def test_codetool_subcommands(self) -> None:
        """codetool 应有 base64 / url / hex / rot13 / html 子命令。"""
        subs = fx.list_subcommands("codetool")
        assert "base64" in subs
        assert "url" in subs
        assert "hex" in subs
        assert "rot13" in subs
        assert "html" in subs


# ---------------------------------------------------------------------- #
# Base64
# ---------------------------------------------------------------------- #
class TestBase64:
    """``encode_base64`` / ``decode_base64`` 测试。"""

    def test_encode_basic(self) -> None:
        """基本编码。"""
        assert encode_base64("hello") == b64.b64encode(b"hello").decode("ascii")

    def test_decode_basic(self) -> None:
        """基本解码。"""
        assert decode_base64("aGVsbG8=") == "hello"

    def test_roundtrip(self) -> None:
        """编解码往返。"""
        assert decode_base64(encode_base64("hello world")) == "hello world"

    def test_unicode(self) -> None:
        """Unicode 编解码。"""
        text = "你好世界"
        assert decode_base64(encode_base64(text)) == text

    def test_empty(self) -> None:
        """空字符串。"""
        assert encode_base64("") == ""
        assert decode_base64("") == ""

    def test_decode_invalid_raises(self) -> None:
        """非法 Base64 应抛出异常。"""
        with pytest.raises(binascii.Error):
            decode_base64("!!!invalid!!!")


# ---------------------------------------------------------------------- #
# URL
# ---------------------------------------------------------------------- #
class TestUrl:
    """``encode_url`` / ``decode_url`` 测试。"""

    def test_encode_basic(self) -> None:
        """基本 URL 编码。"""
        assert encode_url("hello world") == "hello%20world"

    def test_decode_basic(self) -> None:
        """基本 URL 解码。"""
        assert decode_url("hello%20world") == "hello world"

    def test_roundtrip(self) -> None:
        """编解码往返。"""
        text = "hello world & <special>"
        assert decode_url(encode_url(text)) == text

    def test_special_chars(self) -> None:
        """特殊字符编码。"""
        assert encode_url("&") == "%26"
        assert encode_url("<") == "%3C"
        assert encode_url("=") == "%3D"

    def test_empty(self) -> None:
        """空字符串。"""
        assert encode_url("") == ""
        assert decode_url("") == ""

    def test_unicode(self) -> None:
        """Unicode 编解码。"""
        text = "你好"
        assert decode_url(encode_url(text)) == text


# ---------------------------------------------------------------------- #
# Hex
# ---------------------------------------------------------------------- #
class TestHex:
    """``encode_hex`` / ``decode_hex`` 测试。"""

    def test_encode_basic(self) -> None:
        """基本十六进制编码。"""
        assert encode_hex("hello") == "68656c6c6f"

    def test_decode_basic(self) -> None:
        """基本十六进制解码。"""
        assert decode_hex("68656c6c6f") == "hello"

    def test_roundtrip(self) -> None:
        """编解码往返。"""
        text = "hello world"
        assert decode_hex(encode_hex(text)) == text

    def test_unicode(self) -> None:
        """Unicode 编解码。"""
        text = "你好"
        assert decode_hex(encode_hex(text)) == text

    def test_empty(self) -> None:
        """空字符串。"""
        assert encode_hex("") == ""
        assert decode_hex("") == ""

    def test_decode_invalid_raises(self) -> None:
        """非法十六进制应抛出 ValueError。"""
        with pytest.raises(ValueError):
            decode_hex("xyz")


# ---------------------------------------------------------------------- #
# ROT13
# ---------------------------------------------------------------------- #
class TestRot13:
    """``rot13`` 测试。"""

    def test_basic(self) -> None:
        """基本 ROT13。"""
        assert rot13("hello") == "uryyb"

    def test_self_inverse(self) -> None:
        """ROT13 两次等于原文。"""
        text = "Hello, World!"
        assert rot13(rot13(text)) == text

    def test_non_alpha_unchanged(self) -> None:
        """非字母字符不变。"""
        assert rot13("123!@#") == "123!@#"

    def test_empty(self) -> None:
        """空字符串。"""
        assert rot13("") == ""

    def test_unicode(self) -> None:
        """Unicode 文本（非 ASCII 字母不变）。"""
        assert rot13("你好") == "你好"


# ---------------------------------------------------------------------- #
# HTML
# ---------------------------------------------------------------------- #
class TestHtml:
    """``escape_html`` / ``unescape_html`` 测试。"""

    def test_escape_basic(self) -> None:
        """基本 HTML 转义。"""
        assert escape_html("<a>") == "&lt;a&gt;"

    def test_unescape_basic(self) -> None:
        """基本 HTML 反转义。"""
        assert unescape_html("&lt;a&gt;") == "<a>"

    def test_roundtrip(self) -> None:
        """转义往返。"""
        text = '<a href="x">&hello</a>'
        assert unescape_html(escape_html(text)) == text

    def test_ampersand(self) -> None:
        """& 转义。"""
        assert escape_html("a&b") == "a&amp;b"

    def test_double_quote(self) -> None:
        """双引号转义。"""
        assert "&quot;" in escape_html('"')

    def test_single_quote(self) -> None:
        """单引号转义。"""
        assert "&#x27;" in escape_html("'")

    def test_empty(self) -> None:
        """空字符串。"""
        assert escape_html("") == ""
        assert unescape_html("") == ""


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
class TestCodetoolCLI:
    """``codetool`` 通过 ``run_tool`` 调用测试。"""

    def test_base64_encode_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool base64 <text> 编码。"""
        code = run_tool("codetool", ["base64", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert b64.b64encode(b"hello").decode("ascii") in out

    def test_base64_decode_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool base64 <text> --decode 解码。"""
        code = run_tool("codetool", ["base64", "aGVsbG8=", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_base64_decode_invalid_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """非法 Base64 解码应打印错误。"""
        code = run_tool("codetool", ["base64", "!!!invalid!!!", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "解码失败" in out

    def test_url_encode_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool url <text> 编码。"""
        code = run_tool("codetool", ["url", "hello world"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello%20world" in out

    def test_url_decode_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool url <text> --decode 解码。"""
        code = run_tool("codetool", ["url", "hello%20world", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello world" in out

    def test_hex_encode_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool hex <text> 编码。"""
        code = run_tool("codetool", ["hex", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert "68656c6c6f" in out

    def test_hex_decode_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool hex <text> --decode 解码。"""
        code = run_tool("codetool", ["hex", "68656c6c6f", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_hex_decode_invalid_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """非法十六进制解码应打印错误。"""
        code = run_tool("codetool", ["hex", "xyz", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "解码失败" in out

    def test_rot13_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool rot13 <text> 转换。"""
        code = run_tool("codetool", ["rot13", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert "uryyb" in out

    def test_html_escape_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool html <text> 转义。"""
        code = run_tool("codetool", ["html", "<a>"])
        assert code == 0
        out = capsys.readouterr().out
        assert "&lt;a&gt;" in out

    def test_html_unescape_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd codetool html <text> --decode 反转义。"""
        code = run_tool("codetool", ["html", "&lt;a&gt;", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "<a>" in out
