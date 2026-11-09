"""regexvalidate 工具测试。

验证 ``fcmd.tools.text.regexvalidate`` 模块：
- 工具注册
- 校验结果与正则校验器（编译失败 / 匹配 / 分组 / 转义）
- 标志构建与描述
- 正则特征分析
- 多行报告生成
- CLI 端到端（含无效正则的错误提示）
"""

from __future__ import annotations

import re

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.text import regexvalidate
from fcmd.tools.text.regexvalidate import (
    PatternValidationError,
    RegexValidator,
    ValidationResult,
    analyze_pattern,
    build_flags,
    describe_flags,
    format_report,
    format_validator_report,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """regexvalidate 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["regexvalidate"]) == {None}


# ============================================================================ #
# ValidationResult
# ============================================================================ #
class TestValidationResult:
    """校验结果对象。"""

    def test_count(self) -> None:
        assert ValidationResult(is_valid=True, matches=["a", "b"]).count == 2

    def test_summary_match(self) -> None:
        assert ValidationResult(is_valid=True, matches=["a", "b"]).summary() == "匹配：2 处"

    def test_summary_zero_matches(self) -> None:
        assert ValidationResult(is_valid=True, matches=[]).summary() == "匹配：0 处"

    def test_summary_mismatch_with_error(self) -> None:
        result = ValidationResult(is_valid=False, matches=[], error="文本与模式 'x' 不匹配")
        assert result.summary() == "不匹配（文本与模式 'x' 不匹配）"

    def test_summary_mismatch_without_error(self) -> None:
        assert ValidationResult(is_valid=False, matches=[]).summary() == "不匹配"

    def test_str_delegates_to_summary(self) -> None:
        result = ValidationResult(is_valid=True, matches=["a"])
        assert str(result) == result.summary()


# ============================================================================ #
# RegexValidator
# ============================================================================ #
class TestRegexValidator:
    """正则校验器。"""

    def test_validate_finds_all_matches(self) -> None:
        result = RegexValidator("a+").validate("aaa b aa")
        assert result.is_valid
        assert result.matches == ["aaa", "aa"]
        assert result.error is None

    def test_validate_mismatch(self) -> None:
        result = RegexValidator("a+").validate("bbb")
        assert not result.is_valid
        assert result.matches == []
        assert result.error is not None

    def test_is_valid(self) -> None:
        assert RegexValidator("\\d+").is_valid("abc123")
        assert not RegexValidator("\\d+").is_valid("abc")

    def test_find_all_matches(self) -> None:
        assert RegexValidator("\\d+").find_all_matches("a1b22") == ["1", "22"]

    def test_iterate_reports_spans(self) -> None:
        matches = RegexValidator("\\d+").iterate("a1b22")
        assert [match.span() for match in matches] == [(1, 2), (3, 5)]

    def test_ignorecase_flag(self) -> None:
        validator = RegexValidator("hello", build_flags(ignorecase=True))
        assert validator.is_valid("HELLO")

    def test_invalid_pattern_raises(self) -> None:
        with pytest.raises(PatternValidationError, match="无效的正则表达式"):
            RegexValidator("[unclosed")

    def test_invalid_pattern_is_value_error(self) -> None:
        assert issubclass(PatternValidationError, ValueError)

    def test_escape_pattern(self) -> None:
        escaped = RegexValidator.escape_pattern("a.b")
        assert escaped == "a\\.b"
        assert RegexValidator(escaped).is_valid("a.b")
        assert not RegexValidator(escaped).is_valid("axb")


# ============================================================================ #
# 标志
# ============================================================================ #
class TestFlags:
    """标志构建与描述。"""

    def test_build_none(self) -> None:
        assert build_flags() == 0

    def test_build_each(self) -> None:
        assert build_flags(ignorecase=True) == re.IGNORECASE
        assert build_flags(multiline=True) == re.MULTILINE
        assert build_flags(dotall=True) == re.DOTALL

    def test_build_combined(self) -> None:
        assert build_flags(ignorecase=True, multiline=True, dotall=True) == (re.IGNORECASE | re.MULTILINE | re.DOTALL)

    def test_describe_none(self) -> None:
        assert describe_flags(0) == "无"

    def test_describe_combined(self) -> None:
        flags = re.IGNORECASE | re.MULTILINE | re.DOTALL
        assert describe_flags(flags) == "ignorecase,multiline,dotall"

    def test_describe_partial(self) -> None:
        assert describe_flags(re.MULTILINE) == "multiline"


# ============================================================================ #
# 正则特征分析
# ============================================================================ #
class TestAnalyzePattern:
    """正则特征分析。"""

    def test_plain_pattern(self) -> None:
        assert analyze_pattern("abc") == []

    def test_capturing_group(self) -> None:
        assert "包含捕获组" in analyze_pattern("(abc)")

    def test_non_capturing_group(self) -> None:
        findings = analyze_pattern("(?:abc)")
        assert "包含捕获组" in findings
        assert "包含非捕获组" in findings

    def test_named_group(self) -> None:
        findings = analyze_pattern("(?P<word>\\w+)")
        assert "包含捕获组" in findings
        assert "包含命名组" in findings
        assert "包含量词修饰符" in findings
        assert "包含转义序列" in findings

    def test_quantifier(self) -> None:
        assert analyze_pattern("a?") == ["包含量词修饰符"]

    def test_escape_sequence(self) -> None:
        assert analyze_pattern("\\d") == ["包含转义序列"]


# ============================================================================ #
# 报告生成
# ============================================================================ #
class TestFormatReport:
    """多行报告。"""

    def test_match_report(self) -> None:
        report = format_report("(\\w)(\\d)", "a1")
        assert "结果: 匹配：1 处" in report
        assert "匹配详情:" in report
        assert "[0] 'a1' @ (0, 2)" in report
        assert "分组: ('a', '1')" in report
        assert "全匹配: 是" in report
        assert "findall（1 项）: [('a', '1')]" in report
        assert "正则分析:" in report

    def test_named_group_report(self) -> None:
        report = format_report("(?P<word>\\w+)", "hi")
        assert "命名分组: {'word': 'hi'}" in report

    def test_search_match_is_not_fullmatch(self) -> None:
        report = format_report("\\d+", "a1")
        assert "结果: 匹配：1 处" in report
        assert "全匹配: 否" in report

    def test_no_match_report(self) -> None:
        report = format_report("\\d+", "abc")
        assert "结果: 不匹配" in report
        assert "匹配详情:" not in report
        assert "全匹配: 否" in report
        assert "findall（0 项）: []" in report

    def test_report_describes_flags(self) -> None:
        report = format_report("a", "A", build_flags(ignorecase=True))
        assert "标志: ignorecase" in report

    def test_format_validator_report_matches_format_report(self) -> None:
        validator = RegexValidator("\\d+", 0)
        assert format_validator_report(validator, "a1") == format_report("\\d+", "a1")

    def test_invalid_pattern_raises(self) -> None:
        with pytest.raises(PatternValidationError):
            format_report("[bad", "x")


# ============================================================================ #
# CLI 端到端
# ============================================================================ #
class TestCli:
    """CLI 执行。"""

    def test_match(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("regexvalidate", ["\\d+", "abc123"])
        assert code == 0
        out = capsys.readouterr().out
        assert "结果: 匹配：1 处" in out
        assert "全匹配: 否" in out

    def test_ignorecase_option(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("regexvalidate", ["hello", "HELLO", "--ignorecase"])
        assert code == 0
        assert "结果: 匹配：1 处" in capsys.readouterr().out

    def test_multiline_option(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = run_tool("regexvalidate", ["^a", "b\na", "--multiline"])
        assert code == 0
        out = capsys.readouterr().out
        assert "结果: 匹配：1 处" in out
        assert "标志: multiline" in out

    def test_invalid_pattern_prints_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效正则按项目惯例输出错误行并保持退出码 0。"""
        code = run_tool("regexvalidate", ["[bad", "x"])
        assert code == 0
        assert "无效的正则表达式" in capsys.readouterr().out

    def test_validator_module_exports(self) -> None:
        """模块级常量与符号可被 GUI 复用。"""
        assert regexvalidate.FLAG_CHOICES
        assert callable(regexvalidate.format_validator_report)
