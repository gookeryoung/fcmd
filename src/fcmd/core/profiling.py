"""工作流执行性能评估。

基于 :class:`~fcmd.report.RunReport` 中已有的 ``started_at`` /
``finished_at`` 时间戳进行离线分析，**零运行时开销**——不修改执行流程，
不注册回调，不引入额外计时器。

核心指标
--------
* **任务级**：每个任务的 wall-clock 耗时、状态、重试次数、等待时间
  （从最早依赖完成到本任务开始）。
* **图级**：总耗时（wall-clock）、关键路径耗时（理论最短耗时）、
  并行度效率（关键路径耗时 / 总耗时）。
* **关键路径**：从源点到汇点的最长依赖路径，识别真正的串行瓶颈。
* **并行度**：基于时间线重叠计算瞬时并行度，给出平均并行度与峰值并行度。
* **瓶颈识别**：按耗时排序的 Top-N 任务。

设计原则
--------
* 数据来源于 ``RunReport`` + ``Graph``，无副作用。
* 计算复杂度 O(V+E)：拓扑排序 + 单次松弛，适合大规模图。
* 所有时间戳用 ``datetime``，与 :class:`TaskResult` 保持一致。

快速上手
--------
    import fcmd as fx

    report = fx.run(graph)
    profile = fx.ProfileReport.from_report(report, graph)
    print(profile.describe())
    bottlenecks = profile.top_bottlenecks(3)
"""

from __future__ import annotations

__all__ = [
    "ProfileReport",
    "TaskProfile",
]

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .dag import Graph
from .report import RunReport
from .task import TaskResult, TaskStatus


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


# ---------------------------------------------------------------------- #
# HTML 渲染（私有，零依赖）
# ---------------------------------------------------------------------- #
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fcmd 性能剖面报告</title>
<style>
  :root {{
    --bg: #f5f5f7;
    --card: #ffffff;
    --border: #d2d2d7;
    --text: #1d1d1f;
    --muted: #6e6e73;
    --accent: #0071e3;
    --success: #34c759;
    --warning: #ff9f0a;
    --danger: #ff3b30;
    --critical: #af52de;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 0;
    padding: 24px;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }}
  h1 {{ margin: 0 0 8px; font-size: 28px; }}
  h2 {{ margin: 32px 0 12px; font-size: 20px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
  .subtitle {{ color: var(--muted); margin: 0 0 24px; font-size: 14px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 8px; }}
  .card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
  }}
  .card .label {{ font-size: 12px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card .value {{ font-size: 22px; font-weight: 600; }}
  .card .unit {{ font-size: 13px; color: var(--muted); margin-left: 2px; }}
  .critical-path {{
    background: var(--card);
    border: 1px solid var(--border);
    border-left: 4px solid var(--critical);
    border-radius: 10px;
    padding: 16px;
    margin-bottom: 8px;
  }}
  .critical-path .label {{ font-size: 12px; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .critical-path .chain {{ font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 13px; word-break: break-all; }}
  .critical-path .arrow {{ color: var(--critical); margin: 0 6px; font-weight: 600; }}
  /* 甘特图 */
  .gantt {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    overflow-x: auto;
  }}
  .gantt-row {{ display: flex; align-items: center; margin-bottom: 6px; min-width: 600px; }}
  .gantt-label {{ width: 200px; flex-shrink: 0; font-size: 13px; font-family: ui-monospace, monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .gantt-track {{ flex: 1; height: 22px; background: #f0f0f3; border-radius: 4px; position: relative; }}
  .gantt-bar {{ position: absolute; height: 100%; border-radius: 4px; min-width: 2px; }}
  .gantt-bar.success {{ background: var(--success); }}
  .gantt-bar.failed {{ background: var(--danger); }}
  .gantt-bar.skipped {{ background: var(--muted); }}
  .gantt-bar.critical {{ box-shadow: 0 0 0 2px var(--critical) inset; }}
  .gantt-bar:hover {{ opacity: 0.85; }}
  .gantt-tooltip {{ position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: #1d1d1f; color: #fff; padding: 4px 8px; border-radius: 4px; font-size: 11px; white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity 0.15s; }}
  .gantt-bar:hover .gantt-tooltip {{ opacity: 1; }}
  /* 表格 */
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }}
  th, td {{ padding: 10px 12px; text-align: left; font-size: 13px; }}
  th {{ background: #fafafa; font-weight: 600; color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: 0.5px; }}
  tbody tr {{ border-top: 1px solid var(--border); }}
  tbody tr:hover {{ background: #fafafa; }}
  td.num {{ font-family: ui-monospace, monospace; text-align: right; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; }}
  .badge.success {{ background: rgba(52,199,89,0.15); color: var(--success); }}
  .badge.failed {{ background: rgba(255,59,48,0.15); color: var(--danger); }}
  .badge.skipped {{ background: rgba(110,110,115,0.15); color: var(--muted); }}
  .star {{ color: var(--critical); font-weight: 700; }}
  .footer {{ margin-top: 32px; color: var(--muted); font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <h1>fcmd 性能剖面报告</h1>
  <p class="subtitle">由 <code>fcmd profiler</code> 生成 · {generated_at}</p>

  <h2>图级指标</h2>
  <div class="cards">
    <div class="card"><div class="label">总耗时</div><div class="value">{total_duration:.3f}<span class="unit">s</span></div></div>
    <div class="card"><div class="label">关键路径耗时</div><div class="value">{critical_duration:.3f}<span class="unit">s</span></div></div>
    <div class="card"><div class="label">平均并行度</div><div class="value">{avg_par:.2f}</div></div>
    <div class="card"><div class="label">峰值并行度</div><div class="value">{peak_par}</div></div>
    <div class="card"><div class="label">并行度效率</div><div class="value">{efficiency:.1f}<span class="unit">%</span></div></div>
    <div class="card"><div class="label">任务总数</div><div class="value">{task_count}</div></div>
  </div>

  <h2>关键路径</h2>
  <div class="critical-path">
    <div class="label">最长依赖路径（串行瓶颈）</div>
    <div class="chain">{critical_chain}</div>
  </div>

  <h2>任务时间线</h2>
  <div class="gantt">
    {gantt_rows}
  </div>

  <h2>Top 瓶颈任务</h2>
  <table>
    <thead><tr><th>任务</th><th class="num">耗时</th><th class="num">等待</th><th class="num">尝试</th><th>关键路径</th><th>状态</th></tr></thead>
    <tbody>
{bottleneck_rows}
    </tbody>
  </table>

  <h2>全部任务</h2>
  <table>
    <thead><tr><th>任务</th><th class="num">耗时</th><th class="num">等待</th><th class="num">尝试</th><th>关键路径</th><th>状态</th><th>依赖</th></tr></thead>
    <tbody>
{all_task_rows}
    </tbody>
  </table>

  <div class="footer">由 fcmd · profiler 生成</div>
</body>
</html>"""


def _status_badge(status: TaskStatus) -> str:
    """生成状态徽章 HTML。"""
    cls = status.value
    return f'<span class="badge {cls}">{cls}</span>'


def _format_critical_chain(path: tuple[str, ...]) -> str:
    """格式化关键路径为 HTML 链。"""
    if not path:
        return '<em style="color:var(--muted)">(无)</em>'
    arrow = '<span class="arrow">→</span>'
    return arrow.join(f"<strong>{name}</strong>" for name in path)


def _render_gantt(profile: ProfileReport) -> str:
    """渲染甘特图行 HTML。

    每个任务一行：标签 + 时间条。时间条位置基于 wait_time + 依赖关系
    重建相对开始时间（相对最早任务起点），归一化到 0-100% 宽度。
    SKIPPED 任务不显示（无时间戳）。
    """
    visible = [t for t in profile.tasks if t.status != TaskStatus.SKIPPED and t.duration > 0]
    if not visible:
        return '<div style="color:var(--muted);padding:12px;">(无时间线数据)</div>'

    # 重建相对开始时间：start[name] = max(end[dep]) + wait_time
    # profile.tasks 已是拓扑序，可直接按序计算
    start: dict[str, float] = {}
    end: dict[str, float] = {}
    for t in profile.tasks:
        if t.status == TaskStatus.SKIPPED:
            continue
        dep_end = 0.0
        for dep in t.deps:
            dep_end = max(dep_end, end.get(dep, 0.0))
        s = dep_end + t.wait_time
        start[t.name] = s
        end[t.name] = s + t.duration

    # 归一化：以最早开始时间为 0，最晚结束为 100%
    min_start = min(start.get(t.name, 0.0) for t in visible)
    max_end = max(end.get(t.name, 0.0) for t in visible)
    span = max_end - min_start
    if span <= 0:
        span = 1.0

    rows: list[str] = []
    for t in visible:
        s = start.get(t.name, 0.0) - min_start
        left_pct = (s / span) * 100
        width_pct = (t.duration / span) * 100
        cls = t.status.value
        critical_cls = " critical" if t.is_on_critical_path else ""
        tooltip = f"{t.name}: {t.duration:.3f}s @ +{s:.3f}s ({t.status.value})"
        rows.append(
            f'      <div class="gantt-row">'
            f'<div class="gantt-label" title="{t.name}">{t.name}</div>'
            f'<div class="gantt-track">'
            f'<div class="gantt-bar {cls}{critical_cls}" style="left:{left_pct:.2f}%;width:{width_pct:.2f}%">'
            f'<span class="gantt-tooltip">{tooltip}</span>'
            f"</div></div></div>"
        )
    return "\n".join(rows)


def _render_task_row(t: TaskProfile, show_deps: bool = False) -> str:
    """渲染任务表格行 HTML。"""
    star = '<span class="star">★</span>' if t.is_on_critical_path else ""
    deps = ", ".join(t.deps) if show_deps and t.deps else ""
    deps_cell = f"<td>{deps}</td>" if show_deps else ""
    return (
        f"      <tr>"
        f"<td><code>{t.name}</code></td>"
        f'<td class="num">{t.duration:.3f}s</td>'
        f'<td class="num">{t.wait_time:.3f}s</td>'
        f'<td class="num">{t.attempts}</td>'
        f"<td>{star}</td>"
        f"<td>{_status_badge(t.status)}</td>"
        f"{deps_cell}"
        f"</tr>"
    )


def _render_html(profile: ProfileReport) -> str:
    """渲染完整 HTML 报告。"""
    from datetime import datetime as _dt

    bottlenecks = profile.top_bottlenecks(5)
    bottleneck_rows = (
        "\n".join(_render_task_row(t) for t in bottlenecks)
        or '      <tr><td colspan="6" style="color:var(--muted);">(无)</td></tr>'
    )
    all_task_rows = (
        "\n".join(_render_task_row(t, show_deps=True) for t in profile.tasks)
        or '      <tr><td colspan="7" style="color:var(--muted);">(无)</td></tr>'
    )

    return _HTML_TEMPLATE.format(
        generated_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_duration=profile.total_duration,
        critical_duration=profile.critical_path_duration,
        avg_par=profile.avg_parallelism,
        peak_par=profile.peak_parallelism,
        efficiency=profile.parallelism_efficiency * 100,
        task_count=len(profile.tasks),
        critical_chain=_format_critical_chain(profile.critical_path),
        gantt_rows=_render_gantt(profile),
        bottleneck_rows=bottleneck_rows,
        all_task_rows=all_task_rows,
    )
