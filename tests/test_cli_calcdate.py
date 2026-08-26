"""calcdate 工具测试：公共函数 + CLI 子命令。"""

from __future__ import annotations

from datetime import date

import pytest

from fcmd.apis.toolkit import list_subcommands, list_tools, run_tool
from fcmd.cli.calc.calcdate import (
    add_days,
    compare_dates,
    count_workdays,
    date_diff,
    parse_date,
)

# 模块导入即注册工具（@fcmd.tool 装饰器在导入时执行）


# ---------------------------------------------------------------------- #
# 工具注册
# ---------------------------------------------------------------------- #
def test_tool_registered() -> None:
    """calcdate 工具已注册，且包含全部 4 个子命令。"""
    assert "calcdate" in list_tools()
    subs = list_subcommands("calcdate")
    assert set(subs) == {"add", "workdays", "diff", "compare"}


# ---------------------------------------------------------------------- #
# parse_date
# ---------------------------------------------------------------------- #
def test_parse_date_valid() -> None:
    """正常解析 ISO 日期。"""
    assert parse_date("2026-01-15") == date(2026, 1, 15)
    assert parse_date("2026-12-31") == date(2026, 12, 31)
    assert parse_date("2000-02-29") == date(2000, 2, 29)  # 闰年


def test_parse_date_invalid_format() -> None:
    """非 ISO 格式抛 ValueError。"""
    with pytest.raises(ValueError):
        parse_date("2026/01/15")
    with pytest.raises(ValueError):
        parse_date("2026-1-5")  # 非零填充
    with pytest.raises(ValueError):
        parse_date("not-a-date")


def test_parse_date_invalid_value() -> None:
    """非法日期值抛 ValueError。"""
    with pytest.raises(ValueError):
        parse_date("2026-02-30")  # 2 月无 30 日
    with pytest.raises(ValueError):
        parse_date("2026-13-01")  # 无 13 月


# ---------------------------------------------------------------------- #
# add_days
# ---------------------------------------------------------------------- #
def test_add_days_positive() -> None:
    """加正天数。"""
    assert add_days("2026-01-15", 30) == "2026-02-14"
    assert add_days("2026-12-31", 1) == "2027-01-01"  # 跨年


def test_add_days_negative() -> None:
    """加负天数（减）。"""
    assert add_days("2026-01-15", -7) == "2026-01-08"
    assert add_days("2026-01-01", -1) == "2025-12-31"  # 跨年


def test_add_days_zero() -> None:
    """加 0 天返回原日期。"""
    assert add_days("2026-01-15", 0) == "2026-01-15"


def test_add_days_leap_year() -> None:
    """闰年 2 月 29 日加减。"""
    assert add_days("2024-02-28", 1) == "2024-02-29"  # 闰年
    assert add_days("2024-02-29", 1) == "2024-03-01"
    assert add_days("2026-02-28", 1) == "2026-03-01"  # 平年无 29 日


def test_add_days_invalid_date() -> None:
    """非法日期抛 ValueError。"""
    with pytest.raises(ValueError):
        add_days("invalid", 5)


# ---------------------------------------------------------------------- #
# count_workdays
# ---------------------------------------------------------------------- #
def test_count_workdays_normal() -> None:
    """正常区间工作日数。

    2026-01-01(周四) ~ 2026-01-05(周一): 周四五日一二 = 3 个工作日
    （周六 01-03、周日 01-04 排除）
    """
    assert count_workdays("2026-01-01", "2026-01-05") == 3


def test_count_workdays_full_week() -> None:
    """完整周（周一到周日）= 5 个工作日。

    2026-01-05(周一) ~ 2026-01-11(周日)
    """
    assert count_workdays("2026-01-05", "2026-01-11") == 5


def test_count_workdays_same_day_weekday() -> None:
    """同一天且为工作日 = 1。"""
    # 2026-01-05 是周一
    assert count_workdays("2026-01-05", "2026-01-05") == 1


def test_count_workdays_same_day_weekend() -> None:
    """同一天且为周末 = 0。"""
    # 2026-01-03 是周六
    assert count_workdays("2026-01-03", "2026-01-03") == 0


def test_count_workdays_reversed_order() -> None:
    """起止颠倒自动交换，结果一致。"""
    assert count_workdays("2026-01-05", "2026-01-01") == 3


def test_count_workdays_all_weekend() -> None:
    """全周末区间 = 0。

    2026-01-03(周六) ~ 2026-01-04(周日)
    """
    assert count_workdays("2026-01-03", "2026-01-04") == 0


def test_count_workdays_invalid_date() -> None:
    """非法日期抛 ValueError。"""
    with pytest.raises(ValueError):
        count_workdays("invalid", "2026-01-01")


# ---------------------------------------------------------------------- #
# date_diff
# ---------------------------------------------------------------------- #
def test_date_diff_positive() -> None:
    """date2 > date1 返回正差值。"""
    assert date_diff("2026-01-01", "2026-02-01") == 31
    assert date_diff("2026-01-01", "2026-12-31") == 364


def test_date_diff_negative() -> None:
    """date2 < date1 返回负差值。"""
    assert date_diff("2026-02-01", "2026-01-01") == -31


def test_date_diff_zero() -> None:
    """相同日期差值为 0。"""
    assert date_diff("2026-01-15", "2026-01-15") == 0


def test_date_diff_invalid_date() -> None:
    """非法日期抛 ValueError。"""
    with pytest.raises(ValueError):
        date_diff("invalid", "2026-01-01")


# ---------------------------------------------------------------------- #
# compare_dates
# ---------------------------------------------------------------------- #
def test_compare_dates_before() -> None:
    """date1 < date2 返回 'before'。"""
    assert compare_dates("2026-01-01", "2026-02-01") == "before"


def test_compare_dates_after() -> None:
    """date1 > date2 返回 'after'。"""
    assert compare_dates("2026-02-01", "2026-01-01") == "after"


def test_compare_dates_equal() -> None:
    """相同日期返回 'equal'。"""
    assert compare_dates("2026-01-01", "2026-01-01") == "equal"


def test_compare_dates_invalid_date() -> None:
    """非法日期抛 ValueError。"""
    with pytest.raises(ValueError):
        compare_dates("invalid", "2026-01-01")


# ---------------------------------------------------------------------- #
# CLI 子命令测试
# ---------------------------------------------------------------------- #
def test_cli_add(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI add 子命令加天数。"""
    code = run_tool("calcdate", ["add", "2026-01-15", "30"])
    captured = capsys.readouterr()
    assert code == 0
    assert "2026-02-14" in captured.out


def test_cli_add_negative(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI add 子命令支持负天数。"""
    code = run_tool("calcdate", ["add", "2026-01-15", "-7"])
    captured = capsys.readouterr()
    assert code == 0
    assert "2026-01-08" in captured.out


def test_cli_add_invalid_date(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI add 非法日期打印错误（捕获异常后 return，退出码 0）。"""
    code = run_tool("calcdate", ["add", "invalid", "5"])
    captured = capsys.readouterr()
    assert code == 0  # 现有工具模式：捕获异常后 return，framework 返回 0
    assert "错误" in captured.out


def test_cli_workdays(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI workdays 子命令计算工作日数。"""
    code = run_tool("calcdate", ["workdays", "2026-01-05", "2026-01-11"])
    captured = capsys.readouterr()
    assert code == 0
    assert "5" in captured.out


def test_cli_workdays_invalid_date(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI workdays 非法日期打印错误。"""
    code = run_tool("calcdate", ["workdays", "invalid", "2026-01-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "错误" in captured.out


def test_cli_diff(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI diff 子命令计算日期差值。"""
    code = run_tool("calcdate", ["diff", "2026-01-01", "2026-02-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "31" in captured.out


def test_cli_diff_negative(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI diff 子命令返回负差值。"""
    code = run_tool("calcdate", ["diff", "2026-02-01", "2026-01-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "-31" in captured.out


def test_cli_diff_invalid_date(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI diff 非法日期打印错误。"""
    code = run_tool("calcdate", ["diff", "invalid", "2026-01-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "错误" in captured.out


def test_cli_compare_before(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI compare 子命令返回 'before'。"""
    code = run_tool("calcdate", ["compare", "2026-01-01", "2026-02-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "before" in captured.out


def test_cli_compare_equal(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI compare 子命令返回 'equal'。"""
    code = run_tool("calcdate", ["compare", "2026-01-01", "2026-01-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "equal" in captured.out


def test_cli_compare_after(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI compare 子命令返回 'after'。"""
    code = run_tool("calcdate", ["compare", "2026-02-01", "2026-01-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "after" in captured.out


def test_cli_compare_invalid_date(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI compare 非法日期打印错误。"""
    code = run_tool("calcdate", ["compare", "invalid", "2026-01-01"])
    captured = capsys.readouterr()
    assert code == 0
    assert "错误" in captured.out
