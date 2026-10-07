"""日期计算 DSL 动作：calcdate。

原模块 ``fcmd.cli.calc.calcdate`` 为纯 Python 实现（datetime 标准库），
无子进程调用。迁移后由 ``@action`` 装饰器注册。

注意：``fcmd/dsl/actions/calc.py`` 已有 randtool/stattool/timetool，
本模块仅承载 calcdate 四子命令，勿误改 calc.py。
"""

from __future__ import annotations

from datetime import date, timedelta

from fcmd.dsl.actions import action

__all__: list[str] = []


# ============================================================================
# 公共函数
# ============================================================================


def parse_date(date_str: str) -> date:
    """解析 ISO 8601 日期字符串（``YYYY-MM-DD``）。"""
    return date.fromisoformat(date_str)


def add_days(date_str: str, days: int) -> str:
    """日期加减天数，返回新日期字符串。"""
    d = parse_date(date_str)
    result = d + timedelta(days=days)
    return result.isoformat()


def count_workdays(start_str: str, end_str: str) -> int:
    """计算两个日期之间的工作日数（含首尾，排除周六周日）。"""
    start = parse_date(start_str)
    end = parse_date(end_str)
    if start > end:
        start, end = end, start
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def date_diff(date1_str: str, date2_str: str) -> int:
    """计算两个日期的差值（``date2 - date1`` 的天数）。"""
    d1 = parse_date(date1_str)
    d2 = parse_date(date2_str)
    return (d2 - d1).days


def compare_dates(date1_str: str, date2_str: str) -> str:
    """比较两个日期的先后关系，返回 before/equal/after。"""
    d1 = parse_date(date1_str)
    d2 = parse_date(date2_str)
    if d1 < d2:
        return "before"
    if d1 > d2:
        return "after"
    return "equal"


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
        result = add_days(date, days)
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
        result = count_workdays(start, end)
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
        result = date_diff(date1, date2)
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
        result = compare_dates(date1, date2)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(result)
