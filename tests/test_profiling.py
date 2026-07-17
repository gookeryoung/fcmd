"""ProfileReport 性能剖面测试。

覆盖 ``fcmd.profiling`` 模块的全部公共 API 与内部计算路径：

* ``ProfileReport.from_report``：从 RunReport + Graph 构建剖面
* 关键路径算法（拓扑排序 + 动态规划）
* 并行度计算（事件时间线扫描）
* 等待时间计算
* 查询方法（task/top_bottlenecks/critical_tasks/failed_tasks/skipped_tasks）
* 输出方法（to_dict/describe/to_html）
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from fcmd.dag import Graph
from fcmd.profiling import ProfileReport, TaskProfile
from fcmd.report import RunReport
from fcmd.task import TaskResult, TaskSpec, TaskStatus


def _fn() -> int:
    """构造 TaskSpec 用的占位 fn。"""
    return 1


def _spec(name: str, depends_on: tuple[str, ...] = ()) -> TaskSpec[Any]:
    """构造 TaskSpec。"""
    return TaskSpec(name=name, fn=_fn, depends_on=depends_on)


def _result(  # noqa: PLR0913
    name: str,
    status: TaskStatus = TaskStatus.SUCCESS,
    duration: float | None = 0.5,
    attempts: int = 1,
    start: datetime | None = None,
    reason: str | None = None,
) -> TaskResult[Any]:
    """构造测试用 TaskResult。

    用 timedelta 精确表达秒数。``start`` 默认 2024-01-01 00:00:00。
    """
    spec = _spec(name)
    base = start or datetime(2024, 1, 1, 0, 0, 0)
    end = base + timedelta(seconds=duration) if duration is not None else None
    return TaskResult(
        spec=spec,
        status=status,
        value=42,
        attempts=attempts,
        started_at=base if status != TaskStatus.SKIPPED else None,
        finished_at=end if status != TaskStatus.SKIPPED else None,
        reason=reason,
    )


def _build_graph(specs: list[TaskSpec[Any]]) -> Graph:
    """从 specs 列表构建 Graph。"""
    return Graph.from_specs(specs)


# ---------------------------------------------------------------------- #
# from_report 基础
# ---------------------------------------------------------------------- #
def test_profile_report_from_report_single_task() -> None:
    """单任务图：总耗时 = 任务耗时，关键路径 = [任务]，并行度 = 1。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.5)

    profile = ProfileReport.from_report(report, graph)

    assert len(profile.tasks) == 1
    assert profile.total_duration == pytest.approx(1.5)
    assert profile.critical_path_duration == pytest.approx(1.5)
    assert profile.critical_path == ("a",)
    assert profile.avg_parallelism == pytest.approx(1.0)
    assert profile.peak_parallelism == 1
    assert profile.parallelism_efficiency == pytest.approx(1.0)
    assert profile.tasks[0].is_on_critical_path is True
    assert profile.tasks[0].wait_time == 0.0


def test_profile_report_from_report_empty() -> None:
    """空报告：所有指标为 0，关键路径为空。"""
    graph = _build_graph([])
    report = RunReport()

    profile = ProfileReport.from_report(report, graph)

    assert len(profile.tasks) == 0
    assert profile.total_duration == 0.0
    assert profile.critical_path_duration == 0.0
    assert profile.critical_path == ()
    assert profile.avg_parallelism == 0.0
    assert profile.peak_parallelism == 0
    assert profile.parallelism_efficiency == 0.0


def test_profile_report_from_report_serial_chain() -> None:
    """串行链 a -> b -> c：关键路径为 [a, b, c]，并行度 = 1。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    spec_c = _spec("c", depends_on=("b",))
    graph = _build_graph([spec_a, spec_b, spec_c])

    report = RunReport()
    base = datetime(2024, 1, 1, 0, 0, 0)
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=2.0, start=base + timedelta(seconds=1.0))
    report.results["c"] = _result("c", duration=0.5, start=base + timedelta(seconds=3.0))

    profile = ProfileReport.from_report(report, graph)

    assert profile.critical_path == ("a", "b", "c")
    assert profile.critical_path_duration == pytest.approx(3.5)
    assert profile.total_duration == pytest.approx(3.5)
    assert profile.peak_parallelism == 1
    assert profile.avg_parallelism == pytest.approx(1.0)
    assert profile.parallelism_efficiency == pytest.approx(1.0)


def test_profile_report_from_report_parallel_branches() -> None:
    """并行分支 a -> b/c -> d：关键路径取最长分支。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    spec_c = _spec("c", depends_on=("a",))
    spec_d = _spec("d", depends_on=("b", "c"))
    graph = _build_graph([spec_a, spec_b, spec_c, spec_d])

    base = datetime(2024, 1, 1, 0, 0, 0)
    report = RunReport()
    # a: 0-1s, b: 1-3s（2s）, c: 1-2s（1s）, d: 3-4s
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=2.0, start=base + timedelta(seconds=1.0))
    report.results["c"] = _result("c", duration=1.0, start=base + timedelta(seconds=1.0))
    report.results["d"] = _result("d", duration=1.0, start=base + timedelta(seconds=3.0))

    profile = ProfileReport.from_report(report, graph)

    # 关键路径 a -> b -> d（b 比 c 长）
    assert profile.critical_path == ("a", "b", "d")
    assert profile.critical_path_duration == pytest.approx(4.0)
    assert profile.total_duration == pytest.approx(4.0)
    # b 与 c 在 1-2s 重叠 → 峰值并行度 = 2
    assert profile.peak_parallelism == 2
    # 平均并行度 = (1 + 2 + 1 + 1) / 4 = 1.25
    assert profile.avg_parallelism == pytest.approx(1.25)
    # 并行度效率 = 4.0 / 4.0 = 1.0
    assert profile.parallelism_efficiency == pytest.approx(1.0)


# ---------------------------------------------------------------------- #
# 等待时间
# ---------------------------------------------------------------------- #
def test_wait_time_zero_when_no_deps() -> None:
    """无硬依赖时等待时间为 0。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0)

    profile = ProfileReport.from_report(report, graph)

    assert profile.task("a").wait_time == 0.0


def test_wait_time_zero_when_dep_finished_exactly_at_start() -> None:
    """依赖完成时间 == 任务开始时间时等待时间为 0（无缝衔接）。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])

    base = datetime(2024, 1, 1, 0, 0, 0)
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0, start=base)
    # b 在 a 完成时立即开始
    report.results["b"] = _result("b", duration=1.0, start=base + timedelta(seconds=1.0))

    profile = ProfileReport.from_report(report, graph)

    assert profile.task("b").wait_time == 0.0


def test_wait_time_positive_when_gap_exists() -> None:
    """依赖完成后到任务开始有间隔时等待时间为正。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])

    base = datetime(2024, 1, 1, 0, 0, 0)
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0, start=base)
    # b 在 a 完成后 0.5s 才开始
    report.results["b"] = _result("b", duration=1.0, start=base + timedelta(seconds=1.5))

    profile = ProfileReport.from_report(report, graph)

    assert profile.task("b").wait_time == pytest.approx(0.5)


def test_wait_time_zero_for_skipped_task() -> None:
    """SKIPPED 任务等待时间为 0。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])

    base = datetime(2024, 1, 1, 0, 0, 0)
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", status=TaskStatus.SKIPPED, duration=None, reason="upstream skipped")

    profile = ProfileReport.from_report(report, graph)

    assert profile.task("b").wait_time == 0.0
    assert profile.task("b").duration == 0.0


def test_wait_time_with_multiple_deps_uses_latest() -> None:
    """多依赖时等待时间基于最晚完成的依赖。"""
    spec_a = _spec("a")
    spec_b = _spec("b")
    spec_c = _spec("c", depends_on=("a", "b"))
    graph = _build_graph([spec_a, spec_b, spec_c])

    base = datetime(2024, 1, 1, 0, 0, 0)
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0, start=base)
    # b 比 a 晚完成
    report.results["b"] = _result("b", duration=2.0, start=base)
    # c 在 b 完成后 0.3s 开始
    report.results["c"] = _result("c", duration=0.5, start=base + timedelta(seconds=2.3))

    profile = ProfileReport.from_report(report, graph)

    # 最晚依赖 b 完成于 2.0s，c 开始于 2.3s → 等待 0.3s
    assert profile.task("c").wait_time == pytest.approx(0.3)


# ---------------------------------------------------------------------- #
# 关键路径
# ---------------------------------------------------------------------- #
def test_critical_path_picks_longest_branch() -> None:
    """两条独立链：关键路径取耗时更长的链。"""
    spec_a = _spec("a")
    spec_b = _spec("b")
    spec_c = _spec("c", depends_on=("a",))
    spec_d = _spec("d", depends_on=("b",))
    graph = _build_graph([spec_a, spec_b, spec_c, spec_d])

    base = datetime(2024, 1, 1, 0, 0, 0)
    report = RunReport()
    # 链 a -> c 耗时 1+2=3s，链 b -> d 耗时 1+5=6s
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["c"] = _result("c", duration=2.0, start=base + timedelta(seconds=1.0))
    report.results["b"] = _result("b", duration=1.0, start=base)
    report.results["d"] = _result("d", duration=5.0, start=base + timedelta(seconds=1.0))

    profile = ProfileReport.from_report(report, graph)

    # 关键路径为 b -> d（6s > 3s）
    assert profile.critical_path == ("b", "d")
    assert profile.critical_path_duration == pytest.approx(6.0)


def test_critical_path_empty_when_graph_layers_raise() -> None:
    """graph.layers() 抛异常时关键路径回退为空。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0)

    # 用 monkeypatch 让 layers() 抛异常
    original_layers = graph.layers
    graph.layers = lambda: (_ for _ in ()).throw(ValueError("simulated cycle"))  # type: ignore[method-assign]

    try:
        profile = ProfileReport.from_report(report, graph)
        assert profile.critical_path == ()
        assert profile.critical_path_duration == 0.0
    finally:
        graph.layers = original_layers  # type: ignore[method-assign]


# ---------------------------------------------------------------------- #
# 并行度
# ---------------------------------------------------------------------- #
def test_parallelism_zero_when_all_skipped() -> None:
    """全部 SKIPPED 时并行度为 0。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", status=TaskStatus.SKIPPED, duration=None, reason="test")

    profile = ProfileReport.from_report(report, graph)

    assert profile.avg_parallelism == 0.0
    assert profile.peak_parallelism == 0


def test_parallelism_zero_when_no_timestamps() -> None:
    """无时间戳时并行度为 0。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    # 手动构造无时间戳的结果
    result = TaskResult(spec=_spec("a"), status=TaskStatus.SUCCESS, attempts=1)
    report.results["a"] = result

    profile = ProfileReport.from_report(report, graph)

    assert profile.avg_parallelism == 0.0
    assert profile.peak_parallelism == 0


def test_parallelism_skips_zero_duration_tasks() -> None:
    """end <= start 的任务不参与并行度计算。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    # duration=0 → started_at == finished_at → end <= start
    report.results["a"] = _result("a", duration=0.0)

    profile = ProfileReport.from_report(report, graph)

    assert profile.peak_parallelism == 0
    assert profile.avg_parallelism == 0.0


# ---------------------------------------------------------------------- #
# 查询方法
# ---------------------------------------------------------------------- #
def test_task_method_returns_profile_by_name() -> None:
    """task(name) 返回对应的 TaskProfile。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])
    report = RunReport()
    base = datetime(2024, 1, 1)
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=2.0, start=base + timedelta(seconds=1.0))

    profile = ProfileReport.from_report(report, graph)

    assert profile.task("a").name == "a"
    assert profile.task("b").name == "b"


def test_task_method_raises_keyerror_for_unknown() -> None:
    """task(name) 对未知任务抛 KeyError。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0)

    profile = ProfileReport.from_report(report, graph)

    with pytest.raises(KeyError):
        profile.task("nonexistent")


def test_top_bottlenecks_sorted_by_duration_desc() -> None:
    """top_bottlenecks 按耗时降序返回 Top-N。"""
    spec_a = _spec("a")
    spec_b = _spec("b")
    spec_c = _spec("c")
    graph = _build_graph([spec_a, spec_b, spec_c])
    report = RunReport()
    base = datetime(2024, 1, 1)
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=3.0, start=base)
    report.results["c"] = _result("c", duration=2.0, start=base)

    profile = ProfileReport.from_report(report, graph)

    top2 = profile.top_bottlenecks(2)
    assert [t.name for t in top2] == ["b", "c"]
    assert top2[0].duration == pytest.approx(3.0)


def test_top_bottlenecks_zero_n_returns_empty() -> None:
    """n <= 0 时返回空元组。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0)

    profile = ProfileReport.from_report(report, graph)

    assert profile.top_bottlenecks(0) == ()
    assert profile.top_bottlenecks(-1) == ()


def test_critical_tasks_returns_path_in_order() -> None:
    """critical_tasks 按关键路径顺序返回。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    spec_c = _spec("c", depends_on=("b",))
    graph = _build_graph([spec_a, spec_b, spec_c])
    report = RunReport()
    base = datetime(2024, 1, 1)
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=2.0, start=base + timedelta(seconds=1.0))
    report.results["c"] = _result("c", duration=0.5, start=base + timedelta(seconds=3.0))

    profile = ProfileReport.from_report(report, graph)

    crit = profile.critical_tasks()
    assert [t.name for t in crit] == ["a", "b", "c"]
    assert all(t.is_on_critical_path for t in crit)


def test_failed_tasks_returns_only_failed() -> None:
    """failed_tasks 仅返回 FAILED 任务。"""
    spec_a = _spec("a")
    spec_b = _spec("b")
    graph = _build_graph([spec_a, spec_b])
    report = RunReport()
    base = datetime(2024, 1, 1)
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", status=TaskStatus.FAILED, duration=0.5, start=base)

    profile = ProfileReport.from_report(report, graph)

    failed = profile.failed_tasks()
    assert len(failed) == 1
    assert failed[0].name == "b"


def test_skipped_tasks_returns_only_skipped() -> None:
    """skipped_tasks 仅返回 SKIPPED 任务。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])
    report = RunReport()
    report.results["a"] = _result("a", status=TaskStatus.SKIPPED, duration=None, reason="condition false")
    report.results["b"] = _result("b", status=TaskStatus.SKIPPED, duration=None, reason="upstream skipped")

    profile = ProfileReport.from_report(report, graph)

    skipped = profile.skipped_tasks()
    assert len(skipped) == 2
    assert {t.name for t in skipped} == {"a", "b"}


# ---------------------------------------------------------------------- #
# 输出
# ---------------------------------------------------------------------- #
def test_to_dict_structure() -> None:
    """to_dict 返回包含全部字段的字典。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0)

    profile = ProfileReport.from_report(report, graph)

    d = profile.to_dict()
    assert "tasks" in d
    assert "total_duration_seconds" in d
    assert "critical_path_duration_seconds" in d
    assert "critical_path" in d
    assert "avg_parallelism" in d
    assert "peak_parallelism" in d
    assert "parallelism_efficiency" in d
    assert "bottlenecks" in d
    assert d["tasks"][0]["name"] == "a"
    assert d["tasks"][0]["status"] == "success"
    assert d["critical_path"] == ["a"]


def test_task_profile_to_dict() -> None:
    """TaskProfile.to_dict 字段完整。"""
    tp = TaskProfile(
        name="x",
        status=TaskStatus.SUCCESS,
        duration=1.5,
        attempts=2,
        wait_time=0.3,
        is_on_critical_path=True,
        deps=("y",),
    )
    d = tp.to_dict()
    assert d == {
        "name": "x",
        "status": "success",
        "duration_seconds": 1.5,
        "attempts": 2,
        "wait_time_seconds": 0.3,
        "is_on_critical_path": True,
        "deps": ["y"],
    }


def test_describe_includes_all_sections() -> None:
    """describe 文本报告包含所有章节。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])
    base = datetime(2024, 1, 1)
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=2.0, start=base + timedelta(seconds=1.0))

    profile = ProfileReport.from_report(report, graph)

    text = profile.describe()
    assert "fcmd 性能剖面报告" in text
    assert "【图级指标】" in text
    assert "【关键路径】" in text
    assert "【Top" in text
    assert "【全部任务】" in text
    assert "a" in text
    assert "b" in text
    # ASCII 安全：不应含 Unicode 符号（如 ✓）
    assert "✓" not in text


def test_describe_empty_profile() -> None:
    """空剖面 describe 不报错且含基本章节。"""
    graph = _build_graph([])
    report = RunReport()

    profile = ProfileReport.from_report(report, graph)
    text = profile.describe()

    assert "fcmd 性能剖面报告" in text
    assert "(无)" in text


def test_to_html_contains_required_sections() -> None:
    """to_html 生成完整 HTML 文档。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])
    base = datetime(2024, 1, 1)
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", duration=2.0, start=base + timedelta(seconds=1.0))

    profile = ProfileReport.from_report(report, graph)

    html = profile.to_html()
    assert "<!DOCTYPE html>" in html
    assert "fcmd 性能剖面报告" in html
    assert "图级指标" in html
    assert "关键路径" in html
    assert "任务时间线" in html
    assert "Top 瓶颈任务" in html
    assert "全部任务" in html
    assert "a" in html
    assert "b" in html


def test_to_html_empty_profile() -> None:
    """空剖面 to_html 不报错。"""
    graph = _build_graph([])
    report = RunReport()

    profile = ProfileReport.from_report(report, graph)
    html = profile.to_html()

    assert "<!DOCTYPE html>" in html
    assert "(无)" in html


def test_repr_format() -> None:
    """__repr__ 包含关键字段。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.5)

    profile = ProfileReport.from_report(report, graph)

    r = repr(profile)
    assert "ProfileReport" in r
    assert "tasks=1" in r
    assert "total=1.500s" in r


# ---------------------------------------------------------------------- #
# Graph.specs 不含某任务名（防御路径）
# ---------------------------------------------------------------------- #
def test_task_profile_deps_empty_when_spec_not_in_graph() -> None:
    """report 含任务但 graph.specs 不含时，deps 为空元组。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    report.results["a"] = _result("a", duration=1.0)
    # 手动塞入 graph 没有的任务
    report.results["ghost"] = _result("ghost", duration=0.5)

    profile = ProfileReport.from_report(report, graph)

    ghost = profile.task("ghost")
    assert ghost.deps == ()
    assert ghost.duration == pytest.approx(0.5)


# ---------------------------------------------------------------------- #
# 覆盖率补充：边界场景
# ---------------------------------------------------------------------- #
def test_wait_time_zero_when_all_deps_missing_finished_at() -> None:
    """所有依赖的 finished_at 为 None 时，wait_time 为 0.0（dep_end_times 为空）。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])
    report = RunReport()
    # a 是 SKIPPED（finished_at=None），b 是 SUCCESS
    report.results["a"] = _result("a", status=TaskStatus.SKIPPED, duration=None, reason="test")
    base = datetime(2024, 1, 1, 0, 0, 0)
    report.results["b"] = _result("b", duration=1.0, start=base)

    profile = ProfileReport.from_report(report, graph)
    # b 的依赖 a 没有 finished_at，dep_end_times 为空，返回 0.0
    assert profile.task("b").wait_time == 0.0


def test_parallelism_skips_skipped_tasks_with_timestamps() -> None:
    """SKIPPED 任务（有时间戳）不参与并行度计算。"""
    spec_a = _spec("a")
    spec_b = _spec("b")
    graph = _build_graph([spec_a, spec_b])
    report = RunReport()
    base = datetime(2024, 1, 1, 0, 0, 0)
    # a 是 SUCCESS
    report.results["a"] = _result("a", duration=1.0, start=base)
    # b 是 SKIPPED 但手动设置时间戳（覆盖 _calc_parallelism 的 SKIPPED 分支）
    report.results["b"] = TaskResult(
        spec=_spec("b"),
        status=TaskStatus.SKIPPED,
        attempts=0,
        started_at=base,
        finished_at=base + timedelta(seconds=0.5),
        reason="test",
    )

    profile = ProfileReport.from_report(report, graph)
    # 只有 a 参与并行度计算
    assert profile.peak_parallelism == 1


def test_to_html_with_skipped_task_in_timeline() -> None:
    """to_html 包含 SKIPPED 任务时时间线正常渲染。"""
    spec_a = _spec("a")
    spec_b = _spec("b", depends_on=("a",))
    graph = _build_graph([spec_a, spec_b])
    report = RunReport()
    base = datetime(2024, 1, 1, 0, 0, 0)
    report.results["a"] = _result("a", duration=1.0, start=base)
    report.results["b"] = _result("b", status=TaskStatus.SKIPPED, duration=None, reason="test")

    profile = ProfileReport.from_report(report, graph)
    html = profile.to_html()
    assert "<!DOCTYPE html>" in html
    assert "skipped" in html


def test_to_html_zero_duration_single_task() -> None:
    """to_html 单个零耗时任务时 span=1.0 兜底不除零。"""
    spec_a = _spec("a")
    graph = _build_graph([spec_a])
    report = RunReport()
    # duration=0 → started_at == finished_at → span=0 → 走 span=1.0 兜底
    report.results["a"] = _result("a", duration=0.0)

    profile = ProfileReport.from_report(report, graph)
    html = profile.to_html()
    assert "<!DOCTYPE html>" in html
