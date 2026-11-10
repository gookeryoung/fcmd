"""日期计算 DSL 动作：calcdate。

业务实现统一在 :mod:`fcmd.tools.calc.calcdatebase`；本模块只保留 ``@action``
声明与参数转接，避免 DSL 层出现第二套日期实现。
"""

from __future__ import annotations

from fcmd.dsl.actions import action
from fcmd.tools.calc import calcdatebase

__all__: list[str] = []


# ============================================================================
# DSL 动作
# ============================================================================


@action(
    "calcdate_add",
    param_help={
        "date": "起始日期（YYYY-MM-DD）",
        "days": "偏移天数（正数加、负数减）",
    },
)
def calcdate_add(date: str, days: int) -> None:
    """日期加减天数并打印结果。"""
    try:
        result = calcdatebase.add_days(date, days)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)


@action(
    "calcdate_workdays",
    param_help={
        "start": "起始日期（YYYY-MM-DD）",
        "end": "结束日期（YYYY-MM-DD）",
    },
)
def calcdate_workdays(start: str, end: str) -> None:
    """计算两个日期之间的工作日数（排除周末）。"""
    try:
        result = calcdatebase.count_workdays(start, end)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)


@action(
    "calcdate_diff",
    param_help={
        "date1": "第一个日期（YYYY-MM-DD）",
        "date2": "第二个日期（YYYY-MM-DD）",
    },
)
def calcdate_diff(date1: str, date2: str) -> None:
    """计算两个日期的差值天数（date2 - date1）。"""
    try:
        result = calcdatebase.date_diff(date1, date2)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)


@action(
    "calcdate_compare",
    param_help={
        "date1": "第一个日期（YYYY-MM-DD）",
        "date2": "第二个日期（YYYY-MM-DD）",
    },
)
def calcdate_compare(date1: str, date2: str) -> None:
    """比较两个日期的先后关系。"""
    try:
        result = calcdatebase.compare_dates(date1, date2)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)
