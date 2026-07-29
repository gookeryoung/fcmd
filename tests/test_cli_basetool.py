"""basetool 工具测试。

验证 ``fcmd.cli.basetool`` 模块：
- 工具注册与四子命令结构（base64/url/html/hex）
- 各编解码函数的往返一致性
- 错误分支（无效输入）
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.basetool import (
    base64_decode,
    base64_encode,
    hex_decode,
    hex_encode,
    html_decode,
    html_encode,
    url_decode,
    url_encode,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """basetool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "basetool" in list_tools()

    def test_subcommands(self) -> None:
        """basetool 有 base64/url/html/hex 四个子命令。"""
        subs = list_subcommands("basetool")
        assert set(subs) == {"base64", "url", "html", "hex"}


# ============================================================================ #
# base64
# ============================================================================ #
class TestBase64:
    """base64 编解码测试。"""

    def test_encode_basic(self) -> None:
        """基本编码。"""
        assert base64_encode("hello") == "aGVsbG8="

    def test_decode_basic(self) -> None:
        """基本解码。"""
        assert base64_decode("aGVsbG8=") == "hello"

    def test_round_trip(self) -> None:
        """往返一致。"""
        for s in ["", "a", "hello", "中文测试", "hello world!"]:
            assert base64_decode(base64_encode(s)) == s

    def test_decode_invalid_raises(self) -> None:
        """无效 Base64 抛 ValueError。"""
        with pytest.raises(ValueError, match="Base64 解码失败"):
            base64_decode("!!!not base64!!!")

    def test_encode_unicode(self) -> None:
        """Unicode 编码。"""
        assert base64_encode("中") == "5Lit"


# ============================================================================ #
# url
# ============================================================================ #
class TestUrl:
    """url 编解码测试。"""

    def test_encode_space(self) -> None:
        """空格编码为 %20。"""
        assert url_encode("hello world") == "hello%20world"

    def test_decode_space(self) -> None:
        """%20 解码为空格。"""
        assert url_decode("hello%20world") == "hello world"

    def test_encode_special_chars(self) -> None:
        """特殊字符全部编码。"""
        assert url_encode("&=?") == "%26%3D%3F"

    def test_encode_safe_chars(self) -> None:
        """字母数字与 -_.~ 不编码。"""
        assert url_encode("abc-_.~123") == "abc-_.~123"

    def test_round_trip(self) -> None:
        """往返一致。"""
        for s in ["", "hello", "hello world", "a=b&c=d", "中文"]:
            assert url_decode(url_encode(s)) == s

    def test_decode_plus_as_space(self) -> None:
        """unquote 不把 + 转为空格（保持原字符）。"""
        # urllib.parse.unquote 不转换 +，与 quote 配对
        assert url_decode("a+b") == "a+b"


# ============================================================================ #
# html
# ============================================================================ #
class TestHtml:
    """html 转义测试。"""

    def test_encode_basic(self) -> None:
        """基本转义。"""
        assert html_encode("<a>") == "&lt;a&gt;"

    def test_decode_basic(self) -> None:
        """基本反转义。"""
        assert html_decode("&lt;a&gt;") == "<a>"

    def test_encode_quotes(self) -> None:
        """引号转义。"""
        assert html_encode('"hello"') == "&quot;hello&quot;"
        assert html_encode("'hello'") == "&#x27;hello&#x27;"

    def test_decode_named_entities(self) -> None:
        """命名实体反转义。"""
        assert html_decode("&amp;") == "&"
        assert html_decode("&lt;") == "<"

    def test_decode_numeric_entities(self) -> None:
        """数字实体反转义。"""
        assert html_decode("&#65;") == "A"
        assert html_decode("&#x41;") == "A"

    def test_round_trip(self) -> None:
        """往返一致。"""
        for s in ["", "hello", "<a href='x'>", "&amp;", "中文 < 测试 >"]:
            assert html_decode(html_encode(s)) == s


# ============================================================================ #
# hex
# ============================================================================ #
class TestHex:
    """hex 编解码测试。"""

    def test_encode_basic(self) -> None:
        """基本编码。"""
        assert hex_encode("hello") == "68656c6c6f"

    def test_decode_basic(self) -> None:
        """基本解码。"""
        assert hex_decode("68656c6c6f") == "hello"

    def test_encode_uppercase_output(self) -> None:
        """输出为小写。"""
        assert hex_encode("ABC") == "414243"

    def test_decode_uppercase_input(self) -> None:
        """大写输入也可解码。"""
        assert hex_decode("414243") == "ABC"
        assert hex_decode("414243".upper()) == "ABC"

    def test_round_trip(self) -> None:
        """往返一致。"""
        for s in ["", "a", "hello", "中文", "x y z"]:
            assert hex_decode(hex_encode(s)) == s

    def test_decode_invalid_hex_raises(self) -> None:
        """非十六进制字符抛 ValueError。"""
        with pytest.raises(ValueError, match="十六进制解码失败"):
            hex_decode("xyz")

    def test_decode_odd_length_raises(self) -> None:
        """奇数长度抛 ValueError。"""
        with pytest.raises(ValueError, match="十六进制解码失败"):
            hex_decode("abc")


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestBasetoolCLI:
    """``basetool`` 通过 ``run_tool`` 调用测试。"""

    def test_base64_encode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """base64 编码。"""
        code = run_tool("basetool", ["base64", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert "aGVsbG8=" in out

    def test_base64_decode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """base64 解码。"""
        code = run_tool("basetool", ["base64", "aGVsbG8=", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_base64_decode_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """base64 无效输入提示。"""
        code = run_tool("basetool", ["base64", "!!!invalid!!!", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Base64 解码失败" in out

    def test_url_encode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """url 编码。"""
        code = run_tool("basetool", ["url", "hello world"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello%20world" in out

    def test_url_decode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """url 解码。"""
        code = run_tool("basetool", ["url", "hello%20world", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello world" in out

    def test_html_encode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """html 转义。"""
        code = run_tool("basetool", ["html", "<a>"])
        assert code == 0
        out = capsys.readouterr().out
        assert "&lt;a&gt;" in out

    def test_html_decode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """html 反转义。"""
        code = run_tool("basetool", ["html", "&lt;a&gt;", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "<a>" in out

    def test_hex_encode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hex 编码。"""
        code = run_tool("basetool", ["hex", "hello"])
        assert code == 0
        out = capsys.readouterr().out
        assert "68656c6c6f" in out

    def test_hex_decode(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hex 解码。"""
        code = run_tool("basetool", ["hex", "68656c6c6f", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello" in out

    def test_hex_decode_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hex 无效输入提示。"""
        code = run_tool("basetool", ["hex", "xyz", "--decode"])
        assert code == 0
        out = capsys.readouterr().out
        assert "十六进制解码失败" in out
