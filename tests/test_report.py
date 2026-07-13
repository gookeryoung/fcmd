"""RunReport 测试：覆盖 report.py 的全部公共 API。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from fcmd.report import RunReport
from fcmd.task import TaskResult, TaskSpec, TaskStatus


def _fn() -> int:
    """构造 TaskSpec 用的占位 fn。"""
    return 1


def _make_result(  # noqa: PLR0913
    name: str = "a",
    status: TaskStatus = TaskStatus.SUCCESS,
    value: Any = 42,
    duration: float | None = 0.5,
    attempts: int = 1,
    reason: str | None = None,
) -> TaskResult[Any]:
    """构造测试用 TaskResult 实例。

    用 timedelta 精确表达秒数，避免 int() 截断小数。
    """
    spec: TaskSpec[Any] = TaskSpec(name=name, fn=_fn)
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = start + timedelta(seconds=duration) if duration is not None else None
    return TaskResult(
        spec=spec,
        status=status,
        value=value,
        attempts=attempts,
        started_at=start,
        finished_at=end,
        reason=reason,
    )


# ---------------------------------------------------------------------- #
# 类型化访问
# ---------------------------------------------------------------------- #
def test_run_report_getitem_returns_value() -> None:
    """report[name] 应返回任务结果值（而非 TaskResult）。"""
    report = RunReport()
    report.results["a"] = _make_result("a", value=7)
    assert report["a"] == 7


def test_run_report_getitem_missing_raises_keyerror() -> None:
    """缺失任务抛 KeyError。"""
    report = RunReport()
    with pytest.raises(KeyError):
        _ = report["nonexistent"]


def test_run_report_result_of_returns_full_result() -> None:
    """result_of 应返回完整的 TaskResult 对象。"""
    report = RunReport()
    r = _make_result("a")
    report.results["a"] = r
    assert report.result_of("a") is r


def test_run_report_contains() -> None:
    """in 运算符应正确判断任务是否存在。"""
    report = RunReport()
    report.results["a"] = _make_result("a")
    assert "a" in report
    assert "b" not in report


def test_run_report_iter_and_len() -> None:
    """应支持迭代任务名并返回任务数量。"""
    report = RunReport()
    report.results["a"] = _make_result("a")
    report.results["b"] = _make_result("b")
    assert list(report) == ["a", "b"]
    assert len(report) == 2


# ---------------------------------------------------------------------- #
# 汇总
# ---------------------------------------------------------------------- #
def test_run_report_summary() -> None:
    """summary 应返回含 run_id/success/total_tasks/by_status/total_duration_seconds 的字典。"""
    report = RunReport()
    report.results["a"] = _make_result("a", status=TaskStatus.SUCCESS, duration=0.5)
    report.results["b"] = _make_result("b", status=TaskStatus.FAILED, duration=0.25, value=None)
    report.results["c"] = _make_result("c", status=TaskStatus.SKIPPED, duration=None, value=None)
    report.success = False

    summary = report.summary()
    assert summary["run_id"] == report.run_id
    assert summary["success"] is False
    assert summary["total_tasks"] == 3
    assert summary["by_status"] == {"success": 1, "failed": 1, "skipped": 1}
    # 0.5 + 0.25 = 0.75（SKIPPED 无 duration）
    assert summary["total_duration_seconds"] == 0.75


def test_run_report_failed_tasks() -> None:
    """failed_tasks 应仅返回 FAILED 状态的任务名。"""
    report = RunReport()
    report.results["a"] = _make_result("a", status=TaskStatus.SUCCESS)
    report.results["b"] = _make_result("b", status=TaskStatus.FAILED, value=None)
    report.results["c"] = _make_result("c", status=TaskStatus.SKIPPED, value=None)
    assert report.failed_tasks() == ["b"]


def test_run_report_succeeded_tasks() -> None:
    """succeeded_tasks 应仅返回 SUCCESS 状态的任务名。"""
    report = RunReport()
    report.results["a"] = _make_result("a", status=TaskStatus.SUCCESS)
    report.results["b"] = _make_result("b", status=TaskStatus.FAILED, value=None)
    report.results["c"] = _make_result("c", status=TaskStatus.SUCCESS)
    assert report.succeeded_tasks() == ["a", "c"]


def test_run_report_skipped_tasks() -> None:
    """skipped_tasks 应仅返回 SKIPPED 状态的任务名。"""
    report = RunReport()
    report.results["a"] = _make_result("a", status=TaskStatus.SKIPPED, value=None, reason="条件不满足")
    report.results["b"] = _make_result("b", status=TaskStatus.SUCCESS)
    assert report.skipped_tasks() == ["a"]


def test_run_report_tasks_by_status() -> None:
    """tasks_by_status 应按指定状态过滤任务名。"""
    report = RunReport()
    report.results["a"] = _make_result("a", status=TaskStatus.SUCCESS)
    report.results["b"] = _make_result("b", status=TaskStatus.FAILED, value=None)
    report.results["c"] = _make_result("c", status=TaskStatus.SUCCESS)
    assert report.tasks_by_status(TaskStatus.SUCCESS) == ["a", "c"]
    assert report.tasks_by_status(TaskStatus.FAILED) == ["b"]
    assert report.tasks_by_status(TaskStatus.SKIPPED) == []


def test_run_report_describe() -> None:
    """describe 应返回含 run_id/success 与每任务行的多行字符串。"""
    report = RunReport()
    report.results["a"] = _make_result("a", status=TaskStatus.SUCCESS, duration=0.5)
    report.results["b"] = _make_result("b", status=TaskStatus.FAILED, value=None, duration=0.1, attempts=3)
    report.success = False

    text = report.describe()
    assert f"run_id={report.run_id}" in text
    assert "success=False" in text
    assert "a: success" in text
    assert "b: failed" in text
    assert "attempts=3" in text
