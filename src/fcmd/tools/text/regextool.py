"""regextool —— 正则匹配/查找/替换/分割。"""

from __future__ import annotations

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import text as textinfra

__all__: list[str] = []

_HINT = "检查正则语法（括号、量词、转义）是否完整"


def _report(subcommand: str, error: ValueError, json_output: bool) -> None:
    """呈现正则相关错误。"""
    report_error(
        "regextool",
        subcommand,
        code="INVALID_ARGUMENT",
        message=str(error),
        field="pattern",
        expected="合法的正则表达式",
        hint=_HINT,
        json_output=json_output,
    )


@fx.tool(
    "regextool",
    subcommand="match",
    help="匹配正则表达式",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待匹配的文本",
    },
)
def match(pattern: str, text: str, json_output: bool = False) -> None:
    """在文本开头匹配正则表达式并打印结果。"""
    try:
        result = textinfra.match_pattern(pattern, text)
    except ValueError as error:
        _report("match", error, json_output)
        return
    if json_output:
        # 未匹配不是错误：``result`` 为 null，调用方可据此判定
        emit_result("regextool", "match", {"result": result})
        return
    if result is None:
        print("未匹配")
        return
    for key, value in result.items():
        print(f"{key}: {value}")


@fx.tool(
    "regextool",
    subcommand="find",
    help="查找所有匹配",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待查找的文本",
    },
)
def find(pattern: str, text: str, json_output: bool = False) -> None:
    """查找文本中所有匹配并逐行打印。"""
    try:
        matches = textinfra.find_all(pattern, text)
    except ValueError as error:
        _report("find", error, json_output)
        return
    if json_output:
        emit_result("regextool", "find", {"result": matches})
        return
    if not matches:
        print("未匹配")
        return
    for item in matches:
        print(item)


@fx.tool(
    "regextool",
    subcommand="replace",
    help="替换匹配",
    param_help={
        "pattern": "正则表达式字符串",
        "replacement": "替换字符串（支持反向引用）",
        "text": "待替换的文本",
    },
)
def replace(pattern: str, replacement: str, text: str, json_output: bool = False) -> None:
    """替换文本中所有匹配并打印结果。"""
    try:
        result = textinfra.replace_pattern(pattern, replacement, text)
    except ValueError as error:
        _report("replace", error, json_output)
        return
    if json_output:
        emit_result("regextool", "replace", {"result": result})
        return
    print(result)


@fx.tool(
    "regextool",
    subcommand="split",
    help="按正则分割",
    param_help={
        "pattern": "正则表达式字符串（分隔符模式）",
        "text": "待分割的文本",
    },
)
def split(pattern: str, text: str, json_output: bool = False) -> None:
    """按正则分割文本并逐行打印片段。"""
    try:
        parts = textinfra.split_pattern(pattern, text)
    except ValueError as error:
        _report("split", error, json_output)
        return
    if json_output:
        emit_result("regextool", "split", {"result": parts})
        return
    for part in parts:
        print(part)
