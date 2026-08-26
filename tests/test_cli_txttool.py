"""txttool 工具测试。

验证 ``fcmd.cli.text.txttool`` 模块：
- 工具注册与子命令结构
- count_text 统计行/词/字符数
- sort_lines 排序文本行
- unique_lines 去重文本行
- convert_case 大小写转换
- 通过 run_tool 调用 count/sort/unique/case 子命令
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.text.txttool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.text.txttool import convert_case, count_text, sort_lines, unique_lines


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """txttool 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """txttool 应在 _TOOL_REGISTRY 中注册。"""
        assert "txttool" in _TOOL_REGISTRY, "工具 'txttool' 未注册"

    def test_txttool_subcommands(self) -> None:
        """txttool 应有 count / sort / unique / case 子命令。"""
        subs = fx.list_subcommands("txttool")
        assert "count" in subs
        assert "sort" in subs
        assert "unique" in subs
        assert "case" in subs


# ---------------------------------------------------------------------- #
# count_text
# ---------------------------------------------------------------------- #
class TestCountText:
    """``count_text`` 统计测试。"""

    def test_empty_text(self) -> None:
        """空文本应全部为 0。"""
        stats = count_text("")
        assert stats == {"lines": 0, "words": 0, "chars": 0}

    def test_single_line(self) -> None:
        """单行无换行。"""
        stats = count_text("hello world")
        assert stats["lines"] == 1
        assert stats["words"] == 2
        assert stats["chars"] == 11

    def test_multi_line(self) -> None:
        """多行文本。"""
        text = "line1\nline2\nline3"
        stats = count_text(text)
        assert stats["lines"] == 3
        assert stats["words"] == 3
        assert stats["chars"] == len(text)

    def test_multi_line_with_trailing_newline(self) -> None:
        """末尾换行的多行文本。"""
        text = "line1\nline2\n"
        stats = count_text(text)
        assert stats["lines"] == 2
        assert stats["words"] == 2
        assert stats["chars"] == len(text)

    def test_unicode_text(self) -> None:
        """Unicode 文本。"""
        text = "你好世界\n第二行"
        stats = count_text(text)
        assert stats["lines"] == 2
        assert stats["words"] == 2
        assert stats["chars"] == len(text)

    def test_whitespace_only(self) -> None:
        """纯空白文本。"""
        stats = count_text("   \n\t\n  ")
        assert stats["lines"] == 3
        assert stats["words"] == 0


# ---------------------------------------------------------------------- #
# sort_lines
# ---------------------------------------------------------------------- #
class TestSortLines:
    """``sort_lines`` 排序测试。"""

    def test_basic_sort(self) -> None:
        """基本升序排序。"""
        result = sort_lines("banana\napple\ncherry")
        assert result == "apple\nbanana\ncherry"

    def test_reverse_sort(self) -> None:
        """逆序排序。"""
        result = sort_lines("apple\nbanana\ncherry", reverse=True)
        assert result == "cherry\nbanana\napple"

    def test_empty_text(self) -> None:
        """空文本排序返回空串。"""
        assert sort_lines("") == ""

    def test_single_line(self) -> None:
        """单行文本排序不变。"""
        assert sort_lines("only") == "only"

    def test_already_sorted(self) -> None:
        """已排序文本不变。"""
        text = "a\nb\nc"
        assert sort_lines(text) == text


# ---------------------------------------------------------------------- #
# unique_lines
# ---------------------------------------------------------------------- #
class TestUniqueLines:
    """``unique_lines`` 去重测试。"""

    def test_basic_dedup(self) -> None:
        """基本去重。"""
        result = unique_lines("a\nb\na\nc\nb")
        assert result == "a\nb\nc"

    def test_all_duplicates(self) -> None:
        """全部重复。"""
        result = unique_lines("x\nx\nx")
        assert result == "x"

    def test_empty_text(self) -> None:
        """空文本去重返回空串。"""
        assert unique_lines("") == ""

    def test_no_duplicates(self) -> None:
        """无重复时不变。"""
        text = "a\nb\nc"
        assert unique_lines(text) == text

    def test_preserves_order(self) -> None:
        """保持首次出现顺序。"""
        result = unique_lines("c\na\nb\na\nc\nb")
        assert result == "c\na\nb"


# ---------------------------------------------------------------------- #
# convert_case
# ---------------------------------------------------------------------- #
class TestConvertCase:
    """``convert_case`` 大小写转换测试。"""

    def test_upper(self) -> None:
        """转大写。"""
        assert convert_case("hello World", "upper") == "HELLO WORLD"

    def test_lower(self) -> None:
        """转小写。"""
        assert convert_case("Hello WORLD", "lower") == "hello world"

    def test_title(self) -> None:
        """转标题大小写。"""
        assert convert_case("hello world", "title") == "Hello World"

    def test_capitalize(self) -> None:
        """转句首大写。"""
        assert convert_case("hello world", "capitalize") == "Hello world"

    def test_swapcase(self) -> None:
        """交换大小写。"""
        assert convert_case("Hello World", "swapcase") == "hELLO wORLD"

    def test_default_upper(self) -> None:
        """默认模式为 upper。"""
        assert convert_case("hello") == "HELLO"

    def test_invalid_mode_raises(self) -> None:
        """无效模式应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不支持的模式"):
            convert_case("hello", "invalid")

    def test_unicode_case(self) -> None:
        """Unicode 大小写转换。"""
        assert convert_case("你好", "upper") == "你好"


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
class TestTxttoolCLI:
    """``txttool`` 通过 ``run_tool`` 调用测试。"""

    def test_count_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool count <file> 统计行/词/字符数。"""
        f = tmp_path / "a.txt"
        f.write_text("hello world\nfoo bar", encoding="utf-8")
        code = run_tool("txttool", ["count", str(f)])
        assert code == 0
        out = capsys.readouterr().out
        assert "行数: 2" in out
        assert "词数: 4" in out
        assert "字符数: 19" in out

    def test_count_empty_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空文件统计全部为 0。"""
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        code = run_tool("txttool", ["count", str(f)])
        assert code == 0
        out = capsys.readouterr().out
        assert "行数: 0" in out
        assert "词数: 0" in out
        assert "字符数: 0" in out

    def test_sort_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool sort <file> 排序行。"""
        f = tmp_path / "a.txt"
        f.write_text("banana\napple\ncherry", encoding="utf-8")
        code = run_tool("txttool", ["sort", str(f)])
        assert code == 0
        out = capsys.readouterr().out
        assert "apple\nbanana\ncherry" in out

    def test_sort_reverse_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool sort <file> --reverse 逆序排序。"""
        f = tmp_path / "a.txt"
        f.write_text("apple\nbanana\ncherry", encoding="utf-8")
        code = run_tool("txttool", ["sort", str(f), "--reverse"])
        assert code == 0
        out = capsys.readouterr().out
        assert "cherry\nbanana\napple" in out

    def test_unique_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool unique <file> 去重行。"""
        f = tmp_path / "a.txt"
        f.write_text("a\nb\na\nc\nb", encoding="utf-8")
        code = run_tool("txttool", ["unique", str(f)])
        assert code == 0
        out = capsys.readouterr().out
        assert "a\nb\nc" in out

    def test_case_upper_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool case <file> 默认转大写。"""
        f = tmp_path / "a.txt"
        f.write_text("hello world", encoding="utf-8")
        code = run_tool("txttool", ["case", str(f)])
        assert code == 0
        out = capsys.readouterr().out
        assert "HELLO WORLD" in out

    def test_case_lower_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool case <file> --mode lower 转小写。"""
        f = tmp_path / "a.txt"
        f.write_text("Hello WORLD", encoding="utf-8")
        code = run_tool("txttool", ["case", str(f), "--mode", "lower"])
        assert code == 0
        out = capsys.readouterr().out
        assert "hello world" in out

    def test_case_title_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd txttool case <file> --mode title 转标题大小写。"""
        f = tmp_path / "a.txt"
        f.write_text("hello world", encoding="utf-8")
        code = run_tool("txttool", ["case", str(f), "--mode", "title"])
        assert code == 0
        out = capsys.readouterr().out
        assert "Hello World" in out

    def test_case_invalid_mode_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """无效模式应打印错误提示。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        code = run_tool("txttool", ["case", str(f), "--mode", "invalid"])
        assert code == 0
        out = capsys.readouterr().out
        assert "不支持的模式" in out

    def test_count_file_not_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在时打印提示。"""
        code = run_tool("txttool", ["count", str(tmp_path / "nonexistent")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_sort_file_not_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """排序时文件不存在应打印提示。"""
        code = run_tool("txttool", ["sort", str(tmp_path / "nonexistent")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_unique_file_not_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """去重时文件不存在应打印提示。"""
        code = run_tool("txttool", ["unique", str(tmp_path / "nonexistent")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_case_file_not_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """大小写转换时文件不存在应打印提示。"""
        code = run_tool("txttool", ["case", str(tmp_path / "nonexistent")])
        assert code == 0
        out = capsys.readouterr().out
        assert "文件不存在" in out
