"""calcdate - 日期计算工具。

基于标准库 ``datetime`` 提供日期加减、工作日计算、日期差值与比较。

日期格式：``YYYY-MM-DD``（ISO 8601），用 ``date.fromisoformat`` 解析。

示例
----
    fcmd calcdate add 2026-01-15 30             # 加 30 天
    fcmd calcdate add 2026-01-15 -7             # 减 7 天
    fcmd calcdate workdays 2026-01-01 2026-01-31  # 计算工作日数
    fcmd calcdate diff 2026-01-01 2026-02-01    # 日期差值
    fcmd calcdate compare 2026-01-01 2026-02-01  # 比较日期
"""

from __future__ import annotations

from datetime import date, timedelta

import fcmd
from fcmd.console import get_console

__all__ = [
    "add_days",
    "compare_dates",
    "count_workdays",
    "date_diff",
    "parse_date",
]


# ============================================================================
# 公共函数
# ============================================================================


def parse_date(date_str: str) -> date:
    """解析 ISO 8601 日期字符串（``YYYY-MM-DD``）。

    Parameters
    ----------
    date_str:
        日期字符串（如 ``"2026-01-15"``）

    Returns
    -------
    date
        解析后的 ``date`` 对象

    Raises
    ------
    ValueError
        日期格式无效
    """
    return date.fromisoformat(date_str)


def add_days(date_str: str, days: int) -> str:
    """日期加减天数，返回新日期字符串。

    Parameters
    ----------
    date_str:
        起始日期（``YYYY-MM-DD``）
    days:
        偏移天数（正数加、负数减）

    Returns
    -------
    str
        计算后的日期（``YYYY-MM-DD``）

    Raises
    ------
    ValueError
        日期格式无效
    """
    d = parse_date(date_str)
    result = d + timedelta(days=days)
    return result.isoformat()


def count_workdays(start_str: str, end_str: str) -> int:
    """计算两个日期之间的工作日数（含首尾，排除周六周日）。

    Parameters
    ----------
    start_str:
        起始日期（``YYYY-MM-DD``）
    end_str:
        结束日期（``YYYY-MM-DD``）

    Returns
    -------
    int
        工作日数

    Raises
    ------
    ValueError
        日期格式无效
    """
    start = parse_date(start_str)
    end = parse_date(end_str)
    if start > end:
        start, end = end, start
    count = 0
    current = start
    while current <= end:
        # weekday(): 周一=0 ... 周日=6，周一至周五为工作日
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def date_diff(date1_str: str, date2_str: str) -> int:
    """计算两个日期的差值（``date2 - date1`` 的天数）。

    Parameters
    ----------
    date1_str:
        第一个日期（``YYYY-MM-DD``）
    date2_str:
        第二个日期（``YYYY-MM-DD``）

    Returns
    -------
    int
        差值天数（``date2 - date1``，可为负）

    Raises
    ------
    ValueError
        日期格式无效
    """
    d1 = parse_date(date1_str)
    d2 = parse_date(date2_str)
    return (d2 - d1).days


def compare_dates(date1_str: str, date2_str: str) -> str:
    """比较两个日期的先后关系。

    Parameters
    ----------
    date1_str:
        第一个日期（``YYYY-MM-DD``）
    date2_str:
        第二个日期（``YYYY-MM-DD``）

    Returns
    -------
    str
        ``"before"``（date1 < date2）/ ``"equal"`` / ``"after"``（date1 > date2）

    Raises
    ------
    ValueError
        日期格式无效
    """
    d1 = parse_date(date1_str)
    d2 = parse_date(date2_str)
    if d1 < d2:
        return "before"
    if d1 > d2:
        return "after"
    return "equal"


# ============================================================================
# CLI 子命令
# ============================================================================


def _print_error(exc: Exception) -> None:
    """统一错误输出格式。"""
    get_console().print(f"[red]错误:[/red] {exc}")


@fcmd.tool("calcdate", subcommand="add", help="日期加减天数")
def calcdate_add_cmd(date: str, days: int) -> None:
    """日期加减天数并打印结果。

    Parameters
    ----------
    date:
        起始日期（``YYYY-MM-DD``）
    days:
        偏移天数（正数加、负数减）
    """
    try:
        result = add_days(date, days)
    except ValueError as exc:
        _print_error(exc)
        return
    print(result)


@fcmd.tool("calcdate", subcommand="workdays", help="计算工作日数")
def calcdate_workdays_cmd(start: str, end: str) -> None:
    """计算两个日期之间的工作日数（排除周末）。

    Parameters
    ----------
    start:
        起始日期（``YYYY-MM-DD``）
    end:
        结束日期（``YYYY-MM-DD``）
    """
    try:
        result = count_workdays(start, end)
    except ValueError as exc:
        _print_error(exc)
        return
    print(result)


@fcmd.tool("calcdate", subcommand="diff", help="计算日期差值")
def calcdate_diff_cmd(date1: str, date2: str) -> None:
    """计算两个日期的差值天数（``date2 - date1``）。

    Parameters
    ----------
    date1:
        第一个日期（``YYYY-MM-DD``）
    date2:
        第二个日期（``YYYY-MM-DD``）
    """
    try:
        result = date_diff(date1, date2)
    except ValueError as exc:
        _print_error(exc)
        return
    print(result)


@fcmd.tool("calcdate", subcommand="compare", help="比较日期先后")
def calcdate_compare_cmd(date1: str, date2: str) -> None:
    """比较两个日期的先后关系。

    Parameters
    ----------
    date1:
        第一个日期（``YYYY-MM-DD``）
    date2:
        第二个日期（``YYYY-MM-DD``）
    """
    try:
        result = compare_dates(date1, date2)
    except ValueError as exc:
        _print_error(exc)
        return
    print(result)


@fcmd.main("calcdate")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
