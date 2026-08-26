"""asciitool 工具测试。

验证 ``fcmd.cli.text.asciitool`` 模块：
- 工具注册与三子命令结构（char/code/table）
- ``char_to_code``/``code_to_char``/``build_ascii_table``
- 边界与错误分支
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.text.asciitool import (
    build_ascii_table,
    char_to_code,
    code_to_char,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """asciitool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "asciitool" in list_tools()

    def test_subcommands(self) -> None:
        """asciitool 有 char/code/table 三个子命令。"""
        subs = list_subcommands("asciitool")
        assert set(subs) == {"char", "code", "table"}


# ============================================================================ #
# char_to_code
# ============================================================================ #
class TestCharToCode:
    """char_to_code 查询测试。"""

    def test_uppercase_a(self) -> None:
        """A 的 ASCII 码为 65。"""
        assert char_to_code("A") == 65

    def test_lowercase_a(self) -> None:
        """a 的 ASCII 码为 97。"""
        assert char_to_code("a") == 97

    def test_digit_zero(self) -> None:
        """0 的 ASCII 码为 48。"""
        assert char_to_code("0") == 48

    def test_space(self) -> None:
        """空格的 ASCII 码为 32。"""
        assert char_to_code(" ") == 32

    def test_newline(self) -> None:
        """换行的 ASCII 码为 10。"""
        assert char_to_code("\n") == 10

    def test_empty_string_raises(self) -> None:
        """空字符串抛 ValueError。"""
        with pytest.raises(ValueError, match="char 要求单个字符"):
            char_to_code("")

    def test_multiple_chars_raises(self) -> None:
        """多字符抛 ValueError。"""
        with pytest.raises(ValueError, match="char 要求单个字符"):
            char_to_code("abc")


# ============================================================================ #
# code_to_char
# ============================================================================ #
class TestCodeToChar:
    """code_to_char 查询测试。"""

    def test_code_65(self) -> None:
        """65 对应 A。"""
        assert code_to_char(65) == "A"

    def test_code_97(self) -> None:
        """97 对应 a。"""
        assert code_to_char(97) == "a"

    def test_code_48(self) -> None:
        """48 对应 0。"""
        assert code_to_char(48) == "0"

    def test_code_32(self) -> None:
        """32 对应空格。"""
        assert code_to_char(32) == " "

    def test_code_0(self) -> None:
        """0 对应 NULL 字符。"""
        assert code_to_char(0) == "\x00"

    def test_negative_raises(self) -> None:
        """负数抛 ValueError。"""
        with pytest.raises(ValueError, match="code 超出合法范围"):
            code_to_char(-1)

    def test_too_large_raises(self) -> None:
        """超出 Unicode 范围抛 ValueError。"""
        with pytest.raises(ValueError, match="code 超出合法范围"):
            code_to_char(0x110000)

    def test_bool_rejected(self) -> None:
        """布尔值被拒绝（虽然 bool 是 int 子类）。"""
        with pytest.raises(ValueError, match="code 要求整数"):
            code_to_char(True)  # type: ignore[arg-type]


# ============================================================================ #
# build_ascii_table
# ============================================================================ #
class TestBuildAsciiTable:
    """build_ascii_table 构建测试。"""

    def test_default_range(self) -> None:
        """默认范围 0x20..0x7E（95 项）。"""
        table = build_ascii_table()
        assert len(table) == 95
        assert table[0]["code"] == "32"
        assert table[0]["char"] == " "
        assert table[-1]["code"] == "126"
        assert table[-1]["char"] == "~"

    def test_custom_range(self) -> None:
        """自定义范围 48..57（数字字符）。"""
        table = build_ascii_table(48, 57)
        assert len(table) == 10
        chars = [entry["char"] for entry in table]
        assert chars == list("0123456789")

    def test_single_entry(self) -> None:
        """单条目范围（start == end）。"""
        table = build_ascii_table(65, 65)
        assert len(table) == 1
        assert table[0]["code"] == "65"
        assert table[0]["hex"] == "0x41"
        assert table[0]["char"] == "A"

    def test_hex_format(self) -> None:
        """十六进制格式化为 2 位大写。"""
        table = build_ascii_table(10, 10)
        assert table[0]["hex"] == "0x0A"

    def test_del_char_escaped(self) -> None:
        """DEL 字符（0x7F）显示为转义。"""
        table = build_ascii_table(127, 127)
        assert table[0]["char"] == "\\x7f"

    def test_start_greater_than_end_raises(self) -> None:
        """start > end 抛 ValueError。"""
        with pytest.raises(ValueError, match="start 不能大于 end"):
            build_ascii_table(100, 50)

    def test_out_of_range_raises(self) -> None:
        """范围超出 Unicode 抛 ValueError。"""
        with pytest.raises(ValueError, match="范围超出"):
            build_ascii_table(-1, 10)
        with pytest.raises(ValueError, match="范围超出"):
            build_ascii_table(0, 0x110000)

    def test_bool_rejected(self) -> None:
        """布尔值被拒绝。"""
        with pytest.raises(ValueError, match="start 要求整数"):
            build_ascii_table(True, 10)  # type: ignore[arg-type]


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestAsciitoolCLI:
    """``asciitool`` 通过 ``run_tool`` 调用测试。"""

    def test_char(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd asciitool char A。"""
        code = run_tool("asciitool", ["char", "A"])
        assert code == 0
        out = capsys.readouterr().out
        assert "char: A" in out
        assert "code: 65" in out
        assert "hex: 0x41" in out

    def test_char_multiple_raises(self, capsys: pytest.CaptureFixture[str]) -> None:
        """char 多字符提示。"""
        code = run_tool("asciitool", ["char", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "char 要求单个字符" in out

    def test_code(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd asciitool code 65。"""
        code = run_tool("asciitool", ["code", "65"])
        assert code == 0
        out = capsys.readouterr().out
        assert "code: 65" in out
        assert "char: A" in out

    def test_code_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """code 超范围提示。"""
        code = run_tool("asciitool", ["code", "-1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "code 超出合法范围" in out

    def test_table_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd asciitool table 打印默认范围。"""
        code = run_tool("asciitool", ["table"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        # 默认范围 32..126，应包含空格和 ~
        assert any("32" in line and "0x20" in line for line in lines)
        assert any("126" in line and "0x7E" in line and "~" in line for line in lines)

    def test_table_custom_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd asciitool table --start 48 --end 57。"""
        code = run_tool("asciitool", ["table", "--start", "48", "--end", "57"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        # 数字字符 0..9
        assert any("48" in line and "0" in line for line in lines)
        assert any("57" in line and "9" in line for line in lines)
