"""日期计算公共实现：calcdate（基于 datetime 标准库，无子进程调用）。

业务实现统一在本模块；DSL 层 :mod:`fcmd.dsl.actions.calcdate` 只保留
``@action`` 声明与参数转接，避免出现第二套日期实现。
"""

from __future__ import annotations

from datetime import date, timedelta

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
