"""性能剖面渲染：终端 / HTML / 甘特图（与数据模型分离）。"""

from __future__ import annotations

from ..task import TaskStatus
from .model import ProfileReport, TaskProfile

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
