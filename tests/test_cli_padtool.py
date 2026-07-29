"""padtool 工具测试。

验证 ``fcmd.cli.padtool`` 模块：
- 工具注册与四子命令结构（left/right/center/justify）
- ``align_left``/``align_right``/``align_center``/``align_justify``
- 边界场景与错误分支
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.padtool import (
    align_center,
    align_justify,
    align_left,
    align_right,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """padtool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "padtool" in list_tools()

    def test_subcommands(self) -> None:
        """padtool 有 left/right/center/justify 四个子命令。"""
        subs = list_subcommands("padtool")
        assert set(subs) == {"left", "right", "center", "justify"}


# ============================================================================ #
# align_left
# ============================================================================ #
class TestAlignLeft:
    """align_left 左对齐测试。"""

    def test_short_text(self) -> None:
        """短文本填充空格到目标宽度。"""
        result = align_left("hi", 5)
        assert result == "hi   "
        assert len(result) == 5

    def test_exact_width(self) -> None:
        """文本长度等于宽度时不变。"""
        assert align_left("hello", 5) == "hello"

    def test_long_text(self) -> None:
        """长文本（超过宽度）原样返回。"""
        assert align_left("hello world", 5) == "hello world"

    def test_empty_text(self) -> None:
        """空文本填充全部空格。"""
        assert align_left("", 3) == "   "

    def test_zero_width(self) -> None:
        """零宽度返回原文。"""
        assert align_left("hi", 0) == "hi"

    def test_negative_width_raises(self) -> None:
        """负宽度抛 ValueError。"""
        with pytest.raises(ValueError, match="width 要求非负数"):
            align_left("hi", -1)


# ============================================================================ #
# align_right
# ============================================================================ #
class TestAlignRight:
    """align_right 右对齐测试。"""

    def test_short_text(self) -> None:
        """短文本左侧填充空格。"""
        assert align_right("hi", 5) == "   hi"

    def test_exact_width(self) -> None:
        """文本长度等于宽度时不变。"""
        assert align_right("hello", 5) == "hello"

    def test_long_text(self) -> None:
        """长文本原样返回。"""
        assert align_right("hello world", 5) == "hello world"

    def test_empty_text(self) -> None:
        """空文本填充全部空格。"""
        assert align_right("", 3) == "   "

    def test_zero_width(self) -> None:
        """零宽度返回原文。"""
        assert align_right("hi", 0) == "hi"

    def test_negative_width_raises(self) -> None:
        """负宽度抛 ValueError。"""
        with pytest.raises(ValueError, match="width 要求非负数"):
            align_right("hi", -1)


# ============================================================================ #
# align_center
# ============================================================================ #
class TestAlignCenter:
    """align_center 居中测试。"""

    def test_short_text(self) -> None:
        """短文本两侧填充空格。"""
        result = align_center("hi", 6)
        assert result == "  hi  "
        assert len(result) == 6

    def test_uneven_padding(self) -> None:
        """奇数空格分配（额外空格在左侧，Python str.center 行为）。"""
        # str.center 行为：左侧 = ceil(marg/2)=2，右侧 = floor(marg/2)=1
        assert align_center("hi", 5) == "  hi "

    def test_exact_width(self) -> None:
        """文本长度等于宽度时不变。"""
        assert align_center("hello", 5) == "hello"

    def test_long_text(self) -> None:
        """长文本原样返回。"""
        assert align_center("hello world", 5) == "hello world"

    def test_empty_text(self) -> None:
        """空文本填充全部空格。"""
        assert align_center("", 3) == "   "

    def test_zero_width(self) -> None:
        """零宽度返回原文。"""
        assert align_center("hi", 0) == "hi"

    def test_negative_width_raises(self) -> None:
        """负宽度抛 ValueError。"""
        with pytest.raises(ValueError, match="width 要求非负数"):
            align_center("hi", -1)


# ============================================================================ #
# align_justify
# ============================================================================ #
class TestAlignJustify:
    """align_justify 两端对齐测试。"""

    def test_single_line_justified(self) -> None:
        """单行两端对齐到指定宽度。"""
        # "the quick" 长度 9，宽度 11，需补 2 空格，单间隙 → 全补到该间隙
        result = align_justify("the quick", 11)
        assert result == "the   quick"
        assert len(result) == 11

    def test_multiple_gaps(self) -> None:
        """多间隙两端对齐（左侧优先分配额外空格）。"""
        # "a b c" 词长度 3，宽度 10，需补 7 空格，2 间隙 → base=3, extra=1
        # 第 1 间隙 4 空格，第 2 间隙 3 空格
        result = align_justify("a b c", 10)
        assert result == "a    b   c"
        assert len(result) == 10

    def test_multi_line_last_line_left_aligned(self) -> None:
        """多行末行保持左对齐。"""
        text = "the quick brown\nfox jumps"
        result = align_justify(text, 20)
        lines = result.splitlines()
        # 末行左对齐（不两端对齐）
        assert lines[1] == "fox jumps".ljust(20)

    def test_single_word_line(self) -> None:
        """单行单词无法两端对齐，左对齐。"""
        result = align_justify("hello", 10)
        assert result == "hello     "

    def test_empty_line_in_multiline_preserved(self) -> None:
        """多行文本中的空行左对齐为全空格。"""
        result = align_justify("hello\n\nworld", 10)
        lines = result.splitlines()
        # 中间空行左对齐为 10 个空格
        assert lines[1] == "          "

    def test_empty_text(self) -> None:
        """空文本左对齐为全空格。"""
        assert align_justify("", 5) == "     "

    def test_negative_width_raises(self) -> None:
        """负宽度抛 ValueError。"""
        with pytest.raises(ValueError, match="width 要求非负数"):
            align_justify("hi", -1)

    def test_width_too_narrow(self) -> None:
        """宽度不足以容纳单词 + 单空格分隔时左对齐。"""
        # "a b" 长度 3，宽度 2，无法两端对齐
        result = align_justify("a b", 2)
        assert result == "a b".ljust(2)  # 原文长度已超过 2

    def test_last_line_with_single_word(self) -> None:
        """末行单单词保持左对齐。"""
        text = "the quick\nfox"
        result = align_justify(text, 20)
        lines = result.splitlines()
        assert lines[1] == "fox".ljust(20)


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestPadtoolCLI:
    """``padtool`` 通过 ``run_tool`` 调用测试。"""

    def test_left(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd padtool left "hi" --width 5。"""
        code = run_tool("padtool", ["left", "hi", "--width", "5"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "hi   " in lines

    def test_left_default_width(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd padtool left "hi" 使用默认宽度 20。"""
        code = run_tool("padtool", ["left", "hi"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert any(line.startswith("hi") and len(line) == 20 for line in lines)

    def test_right(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd padtool right "hi" --width 5。"""
        code = run_tool("padtool", ["right", "hi", "--width", "5"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "   hi" in lines

    def test_center(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd padtool center "hi" --width 6。"""
        code = run_tool("padtool", ["center", "hi", "--width", "6"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "  hi  " in lines

    def test_justify(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd padtool justify "the quick" --width 11。"""
        code = run_tool("padtool", ["justify", "the quick", "--width", "11"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "the   quick" in lines

    def test_negative_width(self, capsys: pytest.CaptureFixture[str]) -> None:
        """负宽度提示。"""
        code = run_tool("padtool", ["left", "hi", "--width", "-1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "width 要求非负数" in out
