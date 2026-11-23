"""性能剖面数据模型与分析（不含 HTML 渲染）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..dag import Graph
from ..report import RunReport
from ..task import TaskResult, TaskStatus

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class TaskProfile:
    """单个任务的性能剖面。

    属性
    ----
    name:
        任务名。
    status:
        终态（SUCCESS/FAILED/SKIPPED）。
    duration:
        wall-clock 执行耗时（秒）。SKIPPED 任务为 0.0。
    attempts:
        尝试次数（含首次）。
    wait_time:
        从最早硬依赖完成到本任务开始的等待时间（秒）。
        无硬依赖或 SKIPPED 时为 0.0。
    is_on_critical_path:
        是否位于关键路径上。
    deps:
        硬依赖任务名列表。
    """

    name: str
    status: TaskStatus
    duration: float
    attempts: int
    wait_time: float
    is_on_critical_path: bool
    deps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """转为 JSON 友好的字典。"""
        return {
            "name": self.name,
            "status": self.status.value,
            "duration_seconds": round(self.duration, 6),
            "attempts": self.attempts,
            "wait_time_seconds": round(self.wait_time, 6),
            "is_on_critical_path": self.is_on_critical_path,
            "deps": list(self.deps),
        }


@dataclass(frozen=True)
class ProfileReport:
    """工作流执行的性能剖面报告。

    通过 :meth:`from_report` 从 :class:`RunReport` + :class:`Graph` 构建。
    所有字段在构造时一次性计算完毕，后续访问为 O(1)。
    """

    tasks: tuple[TaskProfile, ...]
    """所有任务的性能剖面（按拓扑序）。"""

    total_duration: float
    """整次运行的 wall-clock 耗时（秒）。"""

    critical_path_duration: float
    """关键路径耗时（秒）：从最早任务开始到最晚任务结束的最长依赖路径。"""

    critical_path: tuple[str, ...]
    """关键路径上的任务名序列（按执行顺序）。"""

    avg_parallelism: float
    """平均并行度 = 任务总耗时 / wall-clock 总耗时。"""

    peak_parallelism: int
    """峰值并行度：任一时刻同时运行的任务数最大值。"""

    parallelism_efficiency: float
    """并行度效率 = 关键路径耗时 / wall-clock 总耗时。``1.0`` 表示完全串行，
    越大表示并行化收益越低（瓶颈在关键路径上）。"""

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    @classmethod
    def from_report(cls, report: RunReport, graph: Graph) -> ProfileReport:
        """从运行报告与图构建性能剖面。

        参数
        ----
        report:
            已完成的 :class:`RunReport`，需包含 ``started_at``/``finished_at``。
        graph:
            对应的 :class:`Graph`，用于依赖关系与关键路径分析。

        Note
        -----
        本方法不修改 ``report`` 或 ``graph``，纯函数式计算。
        """
        task_profiles = cls._build_task_profiles(report, graph)
        total_duration = cls._calc_total_duration(report)
        critical_path, critical_duration = cls._calc_critical_path(graph, report)
        avg_par, peak_par = cls._calc_parallelism(report)
        efficiency = critical_duration / total_duration if total_duration > 0 else 0.0

        # 标记关键路径上的任务
        critical_set = set(critical_path)
        marked = tuple(
            TaskProfile(
                name=t.name,
                status=t.status,
                duration=t.duration,
                attempts=t.attempts,
                wait_time=t.wait_time,
                is_on_critical_path=t.name in critical_set,
                deps=t.deps,
            )
            for t in task_profiles
        )

        return cls(
            tasks=marked,
            total_duration=total_duration,
            critical_path_duration=critical_duration,
            critical_path=critical_path,
            avg_parallelism=avg_par,
            peak_parallelism=peak_par,
            parallelism_efficiency=efficiency,
        )

    @staticmethod
    def _build_task_profiles(report: RunReport, graph: Graph) -> tuple[TaskProfile, ...]:
        """构建每个任务的性能剖面。"""
        profiles: list[TaskProfile] = []
        for name, result in report.results.items():
            spec = graph.specs.get(name)
            deps = tuple(spec.depends_on) if spec is not None else ()
            duration = result.duration or 0.0
            wait_time = ProfileReport._calc_wait_time(result, deps, report)
            profiles.append(
                TaskProfile(
                    name=name,
                    status=result.status,
                    duration=duration,
                    attempts=result.attempts,
                    wait_time=wait_time,
                    is_on_critical_path=False,  # 后续标记
                    deps=deps,
                )
            )
        return tuple(profiles)

    @staticmethod
    def _calc_wait_time(
        result: TaskResult[Any],
        deps: tuple[str, ...],
        report: RunReport,
    ) -> float:
        """计算等待时间：从最早依赖完成到本任务开始。

        无硬依赖、SKIPPED 任务或时间戳缺失时返回 0.0。
        """
        if not deps or result.started_at is None or result.status == TaskStatus.SKIPPED:
            return 0.0
        # 找出所有已完成依赖的最晚完成时间
        dep_end_times: list[datetime] = []
        for dep in deps:
            dep_result = report.results.get(dep)
            if dep_result is not None and dep_result.finished_at is not None:
                dep_end_times.append(dep_result.finished_at)
        if not dep_end_times:
            return 0.0
        latest_dep_end = max(dep_end_times)
        delta = (result.started_at - latest_dep_end).total_seconds()
        return max(0.0, delta)

    @staticmethod
    def _calc_total_duration(report: RunReport) -> float:
        """计算 wall-clock 总耗时：最早开始到最晚结束。"""
        starts: list[datetime] = []
        ends: list[datetime] = []
        for r in report.results.values():
            if r.started_at is not None:
                starts.append(r.started_at)
            if r.finished_at is not None:
                ends.append(r.finished_at)
        if not starts or not ends:
            return 0.0
        return (max(ends) - min(starts)).total_seconds()

    @staticmethod
    def _calc_critical_path(graph: Graph, report: RunReport) -> tuple[tuple[str, ...], float]:
        """计算关键路径：DAG 最长路径（按实际执行耗时）。

        使用拓扑排序 + 动态规划，O(V+E)。SKIPPED 任务耗时按 0 计。
        """
        # 构建耗时映射
        durations: dict[str, float] = {}
        for name, result in report.results.items():
            durations[name] = result.duration or 0.0

        # 拓扑序（使用 graph.layers 保证与分层一致）
        try:
            layers = graph.layers()
        except Exception:
            # 图校验失败时回退为空
            return (), 0.0

        # earliest_finish[name] = duration[name] + max(earliest_finish[dep] for dep in deps)
        earliest_finish: dict[str, float] = {}
        predecessor: dict[str, str | None] = {}

        for layer in layers:
            for name in layer:
                spec = graph.specs.get(name)
                deps = spec.depends_on if spec is not None else ()
                if not deps:
                    earliest_finish[name] = durations.get(name, 0.0)
                    predecessor[name] = None
                else:
                    best_dep: str | None = None
                    best_ef = 0.0
                    for dep in deps:
                        ef = earliest_finish.get(dep, 0.0)
                        if ef >= best_ef:
                            best_ef = ef
                            best_dep = dep
                    earliest_finish[name] = best_ef + durations.get(name, 0.0)
                    predecessor[name] = best_dep

        if not earliest_finish:
            return (), 0.0

        # 找到 earliest_finish 最大的节点作为终点
        end_node = max(earliest_finish, key=lambda n: earliest_finish[n])
        total = earliest_finish[end_node]

        # 回溯关键路径
        path: list[str] = []
        node: str | None = end_node
        while node is not None:
            path.append(node)
            node = predecessor.get(node)
        path.reverse()

        return tuple(path), total

    @staticmethod
    def _calc_parallelism(report: RunReport) -> tuple[float, int]:
        """计算平均并行度与峰值并行度。

        基于时间线扫描：将每个任务的 [started_at, finished_at] 区间
        转为事件点（+1/-1），排序后扫描得到瞬时并行度序列。

        返回 (avg_parallelism, peak_parallelism)。
        无有效时间戳时返回 (0.0, 0)。
        """
        events: list[tuple[float, int]] = []  # (timestamp, delta)
        for r in report.results.values():
            if r.started_at is None or r.finished_at is None:
                continue
            if r.status == TaskStatus.SKIPPED:
                continue
            start_ts = r.started_at.timestamp()
            end_ts = r.finished_at.timestamp()
            if end_ts <= start_ts:
                continue
            events.append((start_ts, 1))
            events.append((end_ts, -1))

        if not events:
            return 0.0, 0

        # 排序：同一时间点先处理结束（-1）再处理开始（+1），避免虚假峰值
        events.sort(key=lambda e: (e[0], e[1]))

        current = 0
        peak = 0
        # 加权面积用于计算平均并行度
        area = 0.0
        prev_ts = events[0][0]
        for ts, delta in events:
            if ts > prev_ts:
                area += current * (ts - prev_ts)
            current += delta
            peak = max(peak, current)
            prev_ts = ts

        total_span = events[-1][0] - events[0][0]
        avg = area / total_span if total_span > 0 else 0.0
        return avg, peak

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def task(self, name: str) -> TaskProfile:
        """返回指定任务的剖面。不存在则 ``KeyError``。"""
        for t in self.tasks:
            if t.name == name:
                return t
        raise KeyError(name)

    def top_bottlenecks(self, n: int = 5) -> tuple[TaskProfile, ...]:
        """返回耗时最长的 Top-N 任务（按 duration 降序）。

        参数
        ----
        n:
            返回数量。``n <= 0`` 返回空元组。
        """
        if n <= 0:
            return ()
        return tuple(sorted(self.tasks, key=lambda t: t.duration, reverse=True)[:n])

    def critical_tasks(self) -> tuple[TaskProfile, ...]:
        """返回关键路径上的所有任务（按路径顺序）。"""
        critical_set = set(self.critical_path)
        # 保持关键路径顺序
        order = {name: i for i, name in enumerate(self.critical_path)}
        return tuple(sorted((t for t in self.tasks if t.name in critical_set), key=lambda t: order[t.name]))

    def failed_tasks(self) -> tuple[TaskProfile, ...]:
        """返回 FAILED 状态的任务。"""
        return tuple(t for t in self.tasks if t.status == TaskStatus.FAILED)

    def skipped_tasks(self) -> tuple[TaskProfile, ...]:
        """返回 SKIPPED 状态的任务。"""
        return tuple(t for t in self.tasks if t.status == TaskStatus.SKIPPED)

    # ------------------------------------------------------------------ #
    # 输出
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        """转为 JSON 友好的字典。"""
        return {
            "tasks": [t.to_dict() for t in self.tasks],
            "total_duration_seconds": round(self.total_duration, 6),
            "critical_path_duration_seconds": round(self.critical_path_duration, 6),
            "critical_path": list(self.critical_path),
            "avg_parallelism": round(self.avg_parallelism, 4),
            "peak_parallelism": self.peak_parallelism,
            "parallelism_efficiency": round(self.parallelism_efficiency, 4),
            "bottlenecks": [t.to_dict() for t in self.top_bottlenecks(5)],
        }

    def to_html(self) -> str:
        """生成自包含的 HTML 报告（含 CSS，无外部依赖）。

        报告含：图级指标卡片、关键路径、时间线甘特图、Top 瓶颈表格、
        全部任务表格。适合直接用浏览器打开查看。
        """
        from .render import _render_html

        return _render_html(self)

    def describe(self) -> str:
        """用于终端的可读多行报告（ASCII 安全，无 Unicode 符号）。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("fcmd 性能剖面报告")
        lines.append("=" * 70)
        lines.append("")
        lines.append("【图级指标】")
        lines.append(f"  总耗时 (wall-clock):     {self.total_duration:.3f}s")
        lines.append(f"  关键路径耗时:            {self.critical_path_duration:.3f}s")
        lines.append(f"  平均并行度:              {self.avg_parallelism:.2f}")
        lines.append(f"  峰值并行度:              {self.peak_parallelism}")
        lines.append(f"  并行度效率:              {self.parallelism_efficiency:.2%}")
        lines.append(f"  任务总数:                {len(self.tasks)}")
        lines.append("")

        # 关键路径
        lines.append("【关键路径】")
        if self.critical_path:
            lines.append(f"  {' -> '.join(self.critical_path)}")
        else:
            lines.append("  (无)")
        lines.append("")

        # Top 瓶颈
        bottlenecks = self.top_bottlenecks(5)
        lines.append(f"【Top {len(bottlenecks)} 瓶颈任务】")
        if bottlenecks:
            lines.append(f"  {'任务':<30} {'耗时':>10} {'等待':>10} {'尝试':>6} {'关键路径':>8} {'状态':>8}")
            lines.append(f"  {'-' * 30} {'-' * 10} {'-' * 10} {'-' * 6} {'-' * 8} {'-' * 8}")
            for t in bottlenecks:
                critical_flag = "*" if t.is_on_critical_path else ""
                lines.append(
                    f"  {t.name:<30} {t.duration:>9.3f}s {t.wait_time:>9.3f}s {t.attempts:>6} "
                    f"{critical_flag:>8} {t.status.value:>8}",
                )
        else:
            lines.append("  (无)")
        lines.append("")

        # 全部任务详情
        lines.append("【全部任务】")
        if self.tasks:
            lines.append(f"  {'任务':<30} {'耗时':>10} {'等待':>10} {'尝试':>6} {'关键路径':>8} {'状态':>8}")
            lines.append(f"  {'-' * 30} {'-' * 10} {'-' * 10} {'-' * 6} {'-' * 8} {'-' * 8}")
            for t in self.tasks:
                critical_flag = "*" if t.is_on_critical_path else ""
                lines.append(
                    f"  {t.name:<30} {t.duration:>9.3f}s {t.wait_time:>9.3f}s {t.attempts:>6} "
                    f"{critical_flag:>8} {t.status.value:>8}",
                )
        else:
            lines.append("  (无)")
        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"ProfileReport(tasks={len(self.tasks)}, "
            f"total={self.total_duration:.3f}s, "
            f"critical={self.critical_path_duration:.3f}s, "
            f"avg_par={self.avg_parallelism:.2f}, "
            f"peak_par={self.peak_parallelism})"
        )
