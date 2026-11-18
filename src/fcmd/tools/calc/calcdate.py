"""calcdate 工具：日期加减、工作日数、日期差值与先后比较。

计算逻辑在 :mod:`fcmd.infras.calc`，本模块只做参数适配与输出。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import calc


def _run(subcommand: str, fn: Callable[..., Any], *args: object, json_output: bool = False) -> None:
    """执行日期计算并输出；``ValueError`` 转为统一错误行（退出码 3）。"""
    try:
        result = fn(*args)
    except ValueError as exc:
        report_error(
            "calcdate",
            subcommand,
            code="INVALID_ARGUMENT",
            message=str(exc),
            hint="确认日期格式为 YYYY-MM-DD 且是真实存在的日期",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("calcdate", subcommand, {"result": result})
        return
    print(result)


@fx.tool(
    "calcdate",
    subcommand="add",
    help="日期加减天数",
    param_help={
        "date": "起始日期（YYYY-MM-DD）",
        "days": "偏移天数（正数加、负数减）",
    },
)
def calcdate_add(date: str, days: int, json_output: bool = False) -> None:
    """日期加减天数并打印结果。"""
    _run("add", calc.add_days, date, days, json_output=json_output)


@fx.tool(
    "calcdate",
    subcommand="workdays",
    help="计算工作日数",
    param_help={
        "start": "起始日期（YYYY-MM-DD）",
        "end": "结束日期（YYYY-MM-DD）",
    },
)
def calcdate_workdays(start: str, end: str, json_output: bool = False) -> None:
    """计算两个日期之间的工作日数（排除周末）。"""
    _run("workdays", calc.count_workdays, start, end, json_output=json_output)


@fx.tool(
    "calcdate",
    subcommand="diff",
    help="计算日期差值",
    param_help={
        "date1": "第一个日期（YYYY-MM-DD）",
        "date2": "第二个日期（YYYY-MM-DD）",
    },
)
def calcdate_diff(date1: str, date2: str, json_output: bool = False) -> None:
    """计算两个日期的差值天数（date2 - date1）。"""
    _run("diff", calc.date_diff, date1, date2, json_output=json_output)


@fx.tool(
    "calcdate",
    subcommand="compare",
    help="比较日期先后",
    param_help={
        "date1": "第一个日期（YYYY-MM-DD）",
        "date2": "第二个日期（YYYY-MM-DD）",
    },
)
def calcdate_compare(date1: str, date2: str, json_output: bool = False) -> None:
    """比较两个日期的先后关系。"""
    _run("compare", calc.compare_dates, date1, date2, json_output=json_output)
