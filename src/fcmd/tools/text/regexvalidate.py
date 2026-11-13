r"""regexvalidate - 正则表达式校验工具（CLI）。

交互参考 ``bitool_gui.regexvalidate``：编译正则后对文本做 ``search`` /
``fullmatch`` / ``findall`` / ``finditer``，输出匹配位置、捕获组与正则特征
分析。支持忽略大小写、多行、DOTALL 三种标志。

示例
----
    fcmd regexvalidate '\d+' 'abc123'           # 统计匹配数量与位置
    fcmd regexvalidate '(?P<word>\w+)' 'Hello' --ignorecase
    fcmd regexvalidate '^a' 'b\na' --multiline  # ^ 匹配每行行首
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fcmd
from fcmd.console import print_error

__all__ = [
    "FLAG_CHOICES",
    "PatternValidationError",
    "RegexValidator",
    "ValidationResult",
    "analyze_pattern",
    "build_flags",
    "describe_flags",
    "format_report",
    "format_validator_report",
]

__version__ = "0.1.0"

_DESCRIPTION = "正则校验（匹配/全匹配/findall/分组与特征分析，支持 ignorecase/multiline/dotall 标志）"

# 标志名 → re 标志常量（界面展示与标志构建共用的唯一定义）
FLAG_CHOICES: tuple[tuple[str, int], ...] = (
    ("ignorecase", re.IGNORECASE),
    ("multiline", re.MULTILINE),
    ("dotall", re.DOTALL),
)


class PatternValidationError(ValueError):
    """正则表达式无法编译时抛出。"""


@dataclass(frozen=True)
class ValidationResult:
    """单次正则校验结果。"""

    is_valid: bool
    matches: list[str]
    error: str | None = None

    @property
    def count(self) -> int:
        """匹配数量。"""
        return len(self.matches)

    def summary(self) -> str:
        """返回中文单行结论。"""
        if not self.is_valid:
            return f"不匹配（{self.error}）" if self.error else "不匹配"
        if self.count == 0:
            return "匹配：0 处"
        return f"匹配：{self.count} 处"

    def __str__(self) -> str:
        return self.summary()


class RegexValidator:
    """封装编译后的正则，提供校验与查询。"""

    def __init__(self, pattern: str, flags: int = 0) -> None:
        self.pattern = pattern
        self.flags = flags
        try:
            self.compiled: re.Pattern[str] = re.compile(pattern, flags)
        except re.error as exc:
            raise PatternValidationError(f"无效的正则表达式 {pattern!r}：{exc}") from exc

    def validate(self, text: str) -> ValidationResult:
        """校验文本是否与模式匹配，并返回全部匹配片段。"""
        matches = self.find_all_matches(text)
        if matches or self.compiled.search(text):
            return ValidationResult(is_valid=True, matches=matches)
        return ValidationResult(is_valid=False, matches=[], error=f"文本与模式 {self.pattern!r} 不匹配")

    def is_valid(self, text: str) -> bool:
        """文本是否与模式匹配。"""
        return self.validate(text).is_valid

    def find_all_matches(self, text: str) -> list[str]:
        """返回全部非重叠匹配的完整文本。"""
        return [match.group(0) for match in self.compiled.finditer(text)]

    def iterate(self, text: str) -> list[re.Match[str]]:
        """返回全部匹配对象（展示位置、分组用）。"""
        return list(self.compiled.finditer(text))

    @staticmethod
    def escape_pattern(literal_text: str) -> str:
        """转义文本中的正则元字符，使模式按字面量匹配。"""
        return re.escape(literal_text)


def build_flags(*, ignorecase: bool = False, multiline: bool = False, dotall: bool = False) -> int:
    """按开关组合出 :mod:`re` 标志位。"""
    flags = 0
    if ignorecase:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE
    if dotall:
        flags |= re.DOTALL
    return flags


def describe_flags(flags: int) -> str:
    """把标志位渲染为逗号分隔的名称，无标志时返回 ``无``。"""
    names = [name for name, value in FLAG_CHOICES if flags & value]
    return ",".join(names) if names else "无"


def analyze_pattern(pattern: str) -> list[str]:
    """分析模式中使用的正则特征（与参考实现的判定口径一致）。"""
    findings: list[str] = []
    if "(" in pattern and ")" in pattern:
        findings.append("包含捕获组")
    if "(?:" in pattern:
        findings.append("包含非捕获组")
    if "(?P<" in pattern:
        findings.append("包含命名组")
    if "?" in pattern:
        findings.append("包含量词修饰符")
    if "\\" in pattern:
        findings.append("包含转义序列")
    return findings


def format_validator_report(validator: RegexValidator, text: str) -> str:
    """基于已编译的校验器生成多行报告（CLI 与 GUI 共用）。"""
    result = validator.validate(text)
    matches = validator.iterate(text)
    lines = [
        f"正则: {validator.pattern}",
        f"文本: {text!r}",
        f"标志: {describe_flags(validator.flags)}",
        f"结果: {result.summary()}",
    ]
    if matches:
        lines.append("匹配详情:")
        for index, match in enumerate(matches):
            lines.append(f"  [{index}] {match.group(0)!r} @ {match.span()}")
            if match.groups():
                lines.append(f"      分组: {match.groups()}")
            if match.groupdict():
                lines.append(f"      命名分组: {match.groupdict()}")
    fullmatch = validator.compiled.fullmatch(text)
    lines.append(f"全匹配: {'是' if fullmatch else '否'}")
    all_matches = validator.compiled.findall(text)
    lines.append(f"findall（{len(all_matches)} 项）: {all_matches!r}")
    findings = analyze_pattern(validator.pattern)
    if findings:
        lines.append("正则分析:")
        lines.extend(f"  - {finding}" for finding in findings)
    return "\n".join(lines)


def format_report(pattern: str, text: str, flags: int = 0) -> str:
    """编译模式并生成多行校验报告。

    Raises
    ------
    PatternValidationError
        模式无法编译。
    """
    return format_validator_report(RegexValidator(pattern, flags), text)


@fcmd.tool("regexvalidate", help="校验文本与正则是否匹配", description=_DESCRIPTION)
def check_cmd(
    pattern: str,
    text: str,
    *,
    ignorecase: bool = False,
    multiline: bool = False,
    dotall: bool = False,
) -> None:
    """校验文本与正则表达式是否匹配。

    Parameters
    ----------
    pattern:
        正则表达式
    text:
        待校验的文本
    ignorecase:
        忽略大小写（re.IGNORECASE）
    multiline:
        多行模式（re.MULTILINE，^ / $ 匹配每行）
    dotall:
        DOTALL 模式（re.DOTALL，. 匹配换行）
    """
    flags = build_flags(ignorecase=ignorecase, multiline=multiline, dotall=dotall)
    try:
        print(format_report(pattern, text, flags))
    except PatternValidationError as exc:
        print_error(
            str(exc),
            code="INVALID_ARGUMENT",
            hint="修正正则语法或去掉不支持的构造后重试",
        )


@fcmd.main("regexvalidate")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
