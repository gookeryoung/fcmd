"""calcdate 工具：日期加减、工作日数、日期差值与先后比较。

计算逻辑在 :mod:`fcmd.infras.calc`，本模块只做参数适配与输出。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import calc


@fx.tool(
    "calcdate",
    subcommand="add",
    help="日期加减天数",
    param_help={
        "date": "起始日期（YYYY-MM-DD）",
        "days": "偏移天数（正数加、负数减）",
    },
)
def calcdate_add(date: str, days: int) -> None:
    """日期加减天数并打印结果。"""
    try:
        result = calc.add_days(date, days)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)


@fx.tool(
    "calcdate",
    subcommand="workdays",
    help="计算工作日数",
    param_help={
        "start": "起始日期（YYYY-MM-DD）",
        "end": "结束日期（YYYY-MM-DD）",
    },
)
def calcdate_workdays(start: str, end: str) -> None:
    """计算两个日期之间的工作日数（排除周末）。"""
    try:
        result = calc.count_workdays(start, end)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)


@fx.tool(
    "calcdate",
    subcommand="diff",
    help="计算日期差值",
    param_help={
        "date1": "第一个日期（YYYY-MM-DD）",
        "date2": "第二个日期（YYYY-MM-DD）",
    },
)
def calcdate_diff(date1: str, date2: str) -> None:
    """计算两个日期的差值天数（date2 - date1）。"""
    try:
        result = calc.date_diff(date1, date2)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)


@fx.tool(
    "calcdate",
    subcommand="compare",
    help="比较日期先后",
    param_help={
        "date1": "第一个日期（YYYY-MM-DD）",
        "date2": "第二个日期（YYYY-MM-DD）",
    },
)
def calcdate_compare(date1: str, date2: str) -> None:
    """比较两个日期的先后关系。"""
    try:
        result = calc.compare_dates(date1, date2)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)
