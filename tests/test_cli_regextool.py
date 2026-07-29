"""regextool 工具测试。

验证 ``fcmd.cli.regextool`` 模块：
- 工具注册与四子命令结构（match/find/replace/split）
- ``match_pattern``/``find_all``/``replace_pattern``/``split_pattern``
- 正则错误分支与边界场景
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.regextool import (
    find_all,
    match_pattern,
    replace_pattern,
    split_pattern,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """regextool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "regextool" in list_tools()

    def test_subcommands(self) -> None:
        """regextool 有 match/find/replace/split 四个子命令。"""
        subs = list_subcommands("regextool")
        assert set(subs) == {"match", "find", "replace", "split"}


# ============================================================================ #
# match_pattern
# ============================================================================ #
class TestMatchPattern:
    """match_pattern 匹配测试。"""

    def test_match_digits(self) -> None:
        """匹配开头的数字串。"""
        result = match_pattern(r"\d+", "123abc")
        assert result is not None
        assert result["match"] == "123"
        assert result["start"] == "0"
        assert result["end"] == "3"
        assert result["groups"] == ""

    def test_match_with_groups(self) -> None:
        """带捕获组的匹配。"""
        result = match_pattern(r"(\w+)@(\w+)", "user@example rest")
        assert result is not None
        assert result["match"] == "user@example"
        assert result["groups"] == "user,example"

    def test_match_no_match(self) -> None:
        """不匹配返回 None。"""
        assert match_pattern(r"\d+", "abc") is None

    def test_match_empty_text(self) -> None:
        """空文本与可匹配空的模式。"""
        # \d* 可匹配空串
        result = match_pattern(r"\d*", "")
        assert result is not None
        assert result["match"] == ""

    def test_match_invalid_pattern_raises(self) -> None:
        """无效正则抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的正则表达式"):
            match_pattern(r"[unclosed", "abc")

    def test_match_anchored_at_start(self) -> None:
        """match 仅在开头匹配（非全文本搜索）。"""
        assert match_pattern(r"\d+", "abc123") is None


# ============================================================================ #
# find_all
# ============================================================================ #
class TestFindAll:
    """find_all 查找测试。"""

    def test_find_digits(self) -> None:
        """查找所有数字串。"""
        result = find_all(r"\d+", "a1b22c333")
        assert result == ["1", "22", "333"]

    def test_find_with_groups(self) -> None:
        """有组时返回组元组转字符串。"""
        result = find_all(r"(\w)(\d)", "a1b2c3")
        # findall 有组时返回组列表
        assert result == ["('a', '1')", "('b', '2')", "('c', '3')"]

    def test_find_no_match(self) -> None:
        """无匹配返回空列表。"""
        assert find_all(r"\d+", "abc") == []

    def test_find_empty_text(self) -> None:
        """空文本查找返回空列表。"""
        assert find_all(r"\d+", "") == []

    def test_find_invalid_pattern_raises(self) -> None:
        """无效正则抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的正则表达式"):
            find_all(r"[unclosed", "abc")

    def test_find_non_overlapping(self) -> None:
        """查找不重叠（默认非重叠匹配）。"""
        # "aaa" 中 "aa" 非重叠匹配只匹配 1 次（位置 0-1，剩余 "a" 不足）
        result = find_all(r"aa", "aaa")
        assert result == ["aa"]
        # "aaaa" 中 "aa" 非重叠匹配 2 次（位置 0-1 和 2-3）
        assert find_all(r"aa", "aaaa") == ["aa", "aa"]


# ============================================================================ #
# replace_pattern
# ============================================================================ #
class TestReplacePattern:
    """replace_pattern 替换测试。"""

    def test_replace_digits(self) -> None:
        """数字替换为 #。"""
        assert replace_pattern(r"\d+", "#", "a1b22") == "a#b#"

    def test_replace_with_backref(self) -> None:
        """反向引用替换。"""
        assert replace_pattern(r"(\w+)", r"[\1]", "hi") == "[hi]"

    def test_replace_no_match(self) -> None:
        """无匹配返回原文。"""
        assert replace_pattern(r"\d+", "#", "abc") == "abc"

    def test_replace_empty_text(self) -> None:
        """空文本替换返回空串。"""
        assert replace_pattern(r"\d+", "#", "") == ""

    def test_replace_invalid_pattern_raises(self) -> None:
        """无效正则抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的正则表达式"):
            replace_pattern(r"[unclosed", "#", "abc")


# ============================================================================ #
# split_pattern
# ============================================================================ #
class TestSplitPattern:
    """split_pattern 分割测试。"""

    def test_split_comma(self) -> None:
        """按逗号分割。"""
        assert split_pattern(r",", "a,b,c") == ["a", "b", "c"]

    def test_split_digits(self) -> None:
        """按数字分割。"""
        assert split_pattern(r"\d+", "a1b22c") == ["a", "b", "c"]

    def test_split_no_match(self) -> None:
        """无分隔符返回原文列表。"""
        assert split_pattern(r",", "abc") == ["abc"]

    def test_split_empty_text(self) -> None:
        """空文本分割返回单元素空串列表。"""
        assert split_pattern(r",", "") == [""]

    def test_split_leading_separator(self) -> None:
        """开头分隔符产生前导空串。"""
        assert split_pattern(r",", ",a,b") == ["", "a", "b"]

    def test_split_invalid_pattern_raises(self) -> None:
        """无效正则抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的正则表达式"):
            split_pattern(r"[unclosed", "abc")


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestRegextoolCLI:
    """``regextool`` 通过 ``run_tool`` 调用测试。"""

    def test_match_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd regextool match \\d+ 123abc。"""
        code = run_tool("regextool", ["match", r"\d+", "123abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "match: 123" in out
        assert "start: 0" in out

    def test_match_no_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        """match 无匹配提示。"""
        code = run_tool("regextool", ["match", r"\d+", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "未匹配" in out

    def test_match_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """match 无效正则提示。"""
        code = run_tool("regextool", ["match", r"[unclosed", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的正则表达式" in out

    def test_find_results(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd regextool find \\d+ a1b22c333。"""
        code = run_tool("regextool", ["find", r"\d+", "a1b22c333"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "1" in lines
        assert "22" in lines
        assert "333" in lines

    def test_find_no_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        """find 无匹配提示。"""
        code = run_tool("regextool", ["find", r"\d+", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "未匹配" in out

    def test_replace(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd regextool replace \\d+ # a1b22。"""
        code = run_tool("regextool", ["replace", r"\d+", "#", "a1b22"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "a#b#" in lines

    def test_split(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd regextool split , a,b,c。"""
        code = run_tool("regextool", ["split", ",", "a,b,c"])
        assert code == 0
        out = capsys.readouterr().out
        lines = out.splitlines()
        assert "a" in lines
        assert "b" in lines
        assert "c" in lines

    def test_split_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """split 无效正则提示。"""
        code = run_tool("regextool", ["split", r"[unclosed", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的正则表达式" in out

    def test_find_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """find 无效正则异常提示。"""
        code = run_tool("regextool", ["find", r"[unclosed", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的正则表达式" in out

    def test_replace_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """replace 无效正则异常提示。"""
        code = run_tool("regextool", ["replace", r"[unclosed", "#", "abc"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的正则表达式" in out
