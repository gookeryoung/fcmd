"""pyprof 的 HTML 报告渲染（从 pyprof.py 拆出）。

``fcmd.tools.dev.pyprof`` 继续 re-export ``render_html`` / ``_format_time`` /
``_format_number`` / ``_short_filename``，既有导入路径不变。
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fcmd.tools.dev.pyprof import CallRef, CodeProfileReport, FlameNode

_ORIGIN_LABELS = {
    "user": "用户代码",
    "stdlib": "标准库",
    "third_party": "第三方",
    "builtin": "内置/动态",
}

_TIME_UNITS: tuple[tuple[float, str], ...] = (
    (1.0, "s"),
    (1e-3, "ms"),
    (1e-6, "µs"),
)


def _short_filename(filename: str) -> str:
    """取展示用文件名：内置伪文件保持原样，普通路径取 basename。"""
    if filename.startswith(("<", "{", "~")):
        return filename
    return filename.replace("\\", "/").rsplit("/", 1)[-1]


# ============================================================================
# HTML 报告
# ============================================================================


def _esc(text: object) -> str:
    """HTML 转义（兼容非字符串输入）。"""
    return html.escape(str(text), quote=True)


def _format_time(seconds: float) -> str:
    """把秒数格式化为带单位的人可读文本。"""
    if seconds <= 0:
        return "0 s"
    for scale, unit in _TIME_UNITS:
        if seconds >= scale:
            return f"{seconds / scale:.3f} {unit}" if unit != "s" else f"{seconds:.3f} s"
    return f"{seconds * 1e9:.0f} ns"


def _format_number(value: int) -> str:
    """整数千分位格式化。"""
    return f"{value:,}"


def _origin_badge(origin: str) -> str:
    """生成来源徽章 HTML。"""
    label = _ORIGIN_LABELS.get(origin, origin)
    return f'<span class="badge origin-{_esc(origin)}">{_esc(label)}</span>'


def _render_cards(report: CodeProfileReport) -> str:
    """渲染概览指标卡片。"""
    top_share = report.hotspots[0].tottime / report.total_time * 100 if report.hotspots and report.total_time else 0.0
    cards = [
        ("总耗时", _format_time(report.total_time)),
        ("总调用次数", _format_number(report.total_calls)),
        ("原始调用", _format_number(report.primitive_calls)),
        ("唯一函数", _format_number(report.unique_functions)),
        ("文件/模块数", _format_number(len(report.module_stats))),
        ("头号热点占比", f"{top_share:.1f}%"),
    ]
    return "".join(
        f'<div class="card"><div class="label">{_esc(label)}</div><div class="value">{_esc(value)}</div></div>'
        for label, value in cards
    )


def _render_distribution(report: CodeProfileReport) -> str:
    """渲染时间分布条形图。"""
    slices = report.time_distribution
    if not slices:
        return '<div class="empty">无耗时数据</div>'
    peak = max(item.percentage for item in slices) or 1.0
    rows = []
    for item in slices:
        width = item.percentage / peak * 100
        rows.append(
            '<div class="bar-row">'
            f'<div class="bar-label" title="{_esc(item.display_name)}">{_esc(item.display_name)}</div>'
            '<div class="bar-track">'
            f'<div class="bar origin-{_esc(item.origin)}" style="width:{width:.2f}%"></div>'
            "</div>"
            f'<div class="bar-value">{_format_time(item.tottime)} · {item.percentage:.1f}%</div>'
            "</div>"
        )
    return "".join(rows)


def _render_call_table(refs: list[CallRef], empty_text: str) -> str:
    """渲染调用者/被调用者小表格。"""
    if not refs:
        return f'<div class="empty">{_esc(empty_text)}</div>'
    rows = "".join(
        "<tr>"
        f'<td class="mono" title="{_esc(ref.filename)}">{_esc(ref.display_name)}</td>'
        f'<td class="num">{_esc(ref.ncalls)}</td>'
        f'<td class="num">{_format_time(ref.tottime)}</td>'
        f'<td class="num">{_format_time(ref.cumtime)}</td>'
        "</tr>"
        for ref in refs[:20]
    )
    return (
        '<table class="mini"><thead><tr><th>函数</th><th class="num">调用</th>'
        f'<th class="num">自身</th><th class="num">累计</th></tr></thead><tbody>{rows}</tbody></table>'
    )


def _render_hotspot_rows(report: CodeProfileReport) -> str:
    """渲染热点排行表行。"""
    from fcmd.tools.dev.pyprof import _parse_ncalls  # 函数级导入，规避顶层循环依赖

    rows = []
    for index, func in enumerate(report.hotspots, start=1):
        share = func.tottime / report.total_time * 100 if report.total_time else 0.0
        rows.append(
            f'<tr class="hotspot-row" data-origin="{_esc(func.origin)}" '
            f'data-name="{_esc((func.func_name + " " + func.filename).lower())}" '
            f'data-tottime="{func.tottime:.9f}" data-cumtime="{func.cumtime:.9f}" '
            f'data-calls="{_parse_ncalls(func.ncalls)}" data-rel="rel-{index}">'
            f'<td class="rank">{index}</td>'
            f'<td><button type="button" class="func-link">{_esc(func.func_name)}</button></td>'
            f'<td class="mono" title="{_esc(func.filename)}">{_esc(func.display_name)}</td>'
            f"<td>{_origin_badge(func.origin)}</td>"
            f'<td class="num">{_esc(func.ncalls)}</td>'
            f'<td class="num">{_format_time(func.tottime)}</td>'
            f'<td class="num">{_format_time(func.cumtime)}</td>'
            f'<td class="num">{_format_time(func.tottime_percall)}</td>'
            f'<td class="num">{share:.1f}%</td>'
            "</tr>"
        )
    return "".join(rows) or '<tr><td class="empty" colspan="9">无函数数据</td></tr>'


def _render_relation_panels(report: CodeProfileReport) -> str:
    """为每个热点函数预渲染调用关系面板（供「调用关系」页克隆）。"""
    panels = []
    for index, func in enumerate(report.hotspots, start=1):
        panels.append(
            f'<template id="rel-{index}">'
            f"<h3>{_esc(func.display_name)}</h3>"
            '<div class="rel-grid">'
            '<div class="rel-col"><h4>调用者</h4>'
            f"{_render_call_table(func.callers, '无调用者（根函数）')}</div>"
            '<div class="rel-col"><h4>被调用者</h4>'
            f"{_render_call_table(func.callees, '无被调用者（叶子函数）')}</div>"
            "</div></template>"
        )
    return "".join(panels)


def _render_module_rows(report: CodeProfileReport) -> str:
    """渲染模块聚合表行。"""
    rows = []
    for module in report.module_stats:
        top_funcs = "、".join(_esc(f.func_name) for f in module.functions[:5]) or "-"
        rows.append(
            f'<tr data-origin="{_esc(module.origin)}">'
            f'<td class="mono" title="{_esc(module.filename)}">{_esc(module.display_name)}</td>'
            f"<td>{_origin_badge(module.origin)}</td>"
            f'<td class="num">{module.func_count}</td>'
            f'<td class="num">{_format_number(module.call_count)}</td>'
            f'<td class="num">{_format_time(module.total_tottime)}</td>'
            f'<td class="num">{_format_time(module.total_cumtime)}</td>'
            f'<td class="dim">{top_funcs}</td>'
            "</tr>"
        )
    return "".join(rows) or '<tr><td class="empty" colspan="7">无模块数据</td></tr>'


def _flame_to_dict(node: FlameNode) -> dict[str, object]:
    """火焰图节点 → JSON 字典（短键压缩体积）。"""
    return {
        "n": node.name,
        "f": _short_filename(node.filename),
        "p": node.filename,
        "l": node.lineno,
        "v": round(node.value, 6),
        "s": round(node.self_time, 6),
        "o": node.origin,
        "c": [_flame_to_dict(child) for child in node.children],
    }


def _flame_json(roots: list[FlameNode]) -> str:
    """序列化火焰图数据并转义 ``</``，避免提前闭合 script 标签。"""
    return json.dumps([_flame_to_dict(node) for node in roots], ensure_ascii=False).replace("</", "<\\/")


def render_html(report: CodeProfileReport) -> str:
    """渲染单文件自包含 HTML 报告。"""
    return (
        _HTML_TEMPLATE.replace("__TITLE__", _esc(report.target_name))
        .replace("__GENERATED_AT__", _esc(report.generated_at))
        .replace("__CARDS__", _render_cards(report))
        .replace("__DISTRIBUTION__", _render_distribution(report))
        .replace("__HOTSPOT_ROWS__", _render_hotspot_rows(report))
        .replace("__RELATION_PANELS__", _render_relation_panels(report))
        .replace("__MODULE_ROWS__", _render_module_rows(report))
        .replace("__FLAME_JSON__", _flame_json(report.flame_roots))
    )


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pyprof · __TITLE__</title>
<style>
  :root {
    --bg: #f5f5f7; --card: #fff; --border: #d2d2d7; --text: #1d1d1f; --muted: #6e6e73;
    --accent: #0071e3; --user: #0071e3; --stdlib: #34c759; --third: #ff9f0a; --builtin: #8e8e93;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; line-height: 1.5; }
  h1 { margin: 0 0 6px; font-size: 26px; word-break: break-all; }
  h2 { margin: 26px 0 12px; font-size: 18px; }
  h3 { margin: 0 0 12px; font-size: 15px; word-break: break-all; }
  h4 { margin: 0 0 8px; font-size: 13px; color: var(--muted); }
  .subtitle { color: var(--muted); font-size: 13px; margin: 0 0 18px; }
  .tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }
  .tabs button { border: 1px solid var(--border); background: var(--card); color: var(--text);
    padding: 7px 16px; border-radius: 999px; font-size: 13px; cursor: pointer; }
  .tabs button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  section[hidden] { display: none; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
  .card .label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
  .card .value { font-size: 21px; font-weight: 600; }
  .panel { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
  .toolbar input, .toolbar select { padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px;
    font-size: 13px; background: var(--card); color: var(--text); }
  .toolbar input { flex: 1; min-width: 200px; }
  .chip { border: 1px solid var(--border); background: var(--card); border-radius: 999px;
    padding: 5px 12px; font-size: 12px; cursor: pointer; color: var(--text); }
  .chip.active { background: var(--text); border-color: var(--text); color: #fff; }
  table { width: 100%; border-collapse: collapse; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden; }
  th, td { padding: 8px 10px; font-size: 13px; text-align: left; vertical-align: top; }
  th { background: #fafafa; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }
  tbody tr { border-top: 1px solid var(--border); }
  tbody tr:hover { background: #fafafa; }
  td.num, th.num { text-align: right; font-family: ui-monospace, "SF Mono", Menlo, monospace; white-space: nowrap; }
  td.mono, .mono { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  td.dim { color: var(--muted); }
  td.rank { color: var(--muted); width: 40px; }
  .func-link { border: 0; background: none; padding: 0; color: var(--accent); font-size: 13px;
    font-weight: 600; cursor: pointer; font-family: ui-monospace, monospace; }
  .func-link:hover { text-decoration: underline; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; white-space: nowrap; }
  .badge.origin-user { background: rgba(0,113,227,.12); color: var(--user); }
  .badge.origin-stdlib { background: rgba(52,199,89,.14); color: #248a3d; }
  .badge.origin-third_party { background: rgba(255,159,10,.16); color: #b26a00; }
  .badge.origin-builtin { background: rgba(142,142,147,.16); color: var(--muted); }
  .bar-row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .bar-label { width: 320px; flex-shrink: 0; font-size: 12px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
  .bar-track { flex: 1; height: 16px; background: #f0f0f3; border-radius: 4px; overflow: hidden; }
  .bar { height: 100%; border-radius: 4px; background: var(--accent); }
  .bar.origin-stdlib { background: var(--stdlib); }
  .bar.origin-third_party { background: var(--third); }
  .bar.origin-builtin { background: var(--builtin); }
  .bar-value { width: 170px; flex-shrink: 0; text-align: right; font-size: 12px; color: var(--muted);
    font-family: ui-monospace, monospace; }
  .rel-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }
  .mini th, .mini td { padding: 6px 8px; font-size: 12px; }
  .empty { color: var(--muted); font-size: 13px; padding: 12px; text-align: center; }
  #flame-breadcrumb { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; font-size: 12px; }
  #flame-breadcrumb button { border: 1px solid var(--border); background: var(--card); color: var(--accent);
    border-radius: 6px; padding: 3px 9px; cursor: pointer; font-family: ui-monospace, monospace; }
  #flame-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 10px; }
  #flame-tip { position: fixed; display: none; z-index: 20; background: #1d1d1f; color: #fff;
    padding: 6px 9px; border-radius: 6px; font-size: 12px; pointer-events: none; max-width: 560px;
    font-family: ui-monospace, monospace; }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 12px; color: var(--muted); }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
  .footer { margin-top: 28px; text-align: center; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
  <h1>pyprof 性能分析报告</h1>
  <p class="subtitle">目标: <code>__TITLE__</code> · 生成时间: __GENERATED_AT__ · 由 <code>fcmd pyprof</code> 生成</p>

  <nav class="tabs" id="tabs">
    <button type="button" data-tab="overview" class="active">概览</button>
    <button type="button" data-tab="hotspots">热点排行</button>
    <button type="button" data-tab="relations">调用关系</button>
    <button type="button" data-tab="flame">火焰图</button>
    <button type="button" data-tab="modules">模块聚合</button>
  </nav>

  <section id="tab-overview">
    <h2>关键指标</h2>
    <div class="cards">__CARDS__</div>
    <h2>时间分布（按自身耗时）</h2>
    <div class="panel">__DISTRIBUTION__</div>
  </section>

  <section id="tab-hotspots" hidden>
    <h2>热点排行</h2>
    <div class="toolbar">
      <input id="hotspot-search" type="search" placeholder="搜索函数名或文件…">
      <button type="button" class="chip active" data-origin="all">全部</button>
      <button type="button" class="chip" data-origin="user">用户代码</button>
      <button type="button" class="chip" data-origin="stdlib">标准库</button>
      <button type="button" class="chip" data-origin="third_party">第三方</button>
      <button type="button" class="chip" data-origin="builtin">内置</button>
      <select id="hotspot-sort">
        <option value="tottime">按自身耗时</option>
        <option value="cumtime">按累计耗时</option>
        <option value="calls">按调用次数</option>
      </select>
    </div>
    <table id="hotspot-table">
      <thead><tr>
        <th>#</th><th>函数</th><th>位置</th><th>来源</th><th class="num">调用</th>
        <th class="num">自身耗时</th><th class="num">累计耗时</th><th class="num">自身/次</th><th class="num">占比</th>
      </tr></thead>
      <tbody>__HOTSPOT_ROWS__</tbody>
    </table>
    <p class="subtitle">点击函数名可在「调用关系」页查看其调用者与被调用者。</p>
  </section>

  <section id="tab-relations" hidden>
    <h2>调用关系</h2>
    <div class="panel" id="relation-body"><div class="empty">从「热点排行」点击一个函数名查看调用关系</div></div>
  </section>

  <section id="tab-flame" hidden>
    <h2>火焰图</h2>
    <div id="flame-breadcrumb"></div>
    <div id="flame-wrap"><svg id="flame-svg" width="100%" height="200"></svg></div>
    <div class="legend">
      <span><i style="background:#0071e3"></i>用户代码</span>
      <span><i style="background:#34c759"></i>标准库</span>
      <span><i style="background:#ff9f0a"></i>第三方</span>
      <span><i style="background:#8e8e93"></i>内置/动态</span>
      <span>点击色块可放大，顶部面包屑返回上层</span>
    </div>
  </section>

  <section id="tab-modules" hidden>
    <h2>模块聚合</h2>
    <table>
      <thead><tr>
        <th>模块</th><th>来源</th><th class="num">函数数</th><th class="num">调用次数</th>
        <th class="num">自身耗时</th><th class="num">累计耗时(最大)</th><th>耗时 Top 函数</th>
      </tr></thead>
      <tbody>__MODULE_ROWS__</tbody>
    </table>
  </section>

  <div class="footer">由 fcmd · pyprof 生成</div>
  <div id="flame-tip"></div>

  <div id="relation-templates" hidden>__RELATION_PANELS__</div>

<script>
(function () {
  "use strict";
  var FLAME = __FLAME_JSON__;

  /* ---- 标签页 ---- */
  var tabs = document.querySelectorAll("#tabs button");
  function showTab(name) {
    tabs.forEach(function (btn) { btn.classList.toggle("active", btn.dataset.tab === name); });
    document.querySelectorAll("section[id^=tab-]").forEach(function (sec) {
      sec.hidden = sec.id !== "tab-" + name;
    });
    if (name === "flame") render();
  }
  tabs.forEach(function (btn) { btn.addEventListener("click", function () { showTab(btn.dataset.tab); }); });

  /* ---- 格式化 ---- */
  function fmtTime(sec) {
    if (!(sec > 0)) return "0 s";
    if (sec >= 1) return sec.toFixed(3) + " s";
    if (sec >= 1e-3) return (sec * 1e3).toFixed(3) + " ms";
    if (sec >= 1e-6) return (sec * 1e6).toFixed(3) + " µs";
    return (sec * 1e9).toFixed(0) + " ns";
  }
  var ORIGIN_COLORS = { user: "#0071e3", stdlib: "#34c759", third_party: "#ff9f0a", builtin: "#8e8e93" };

  /* ---- 热点过滤 / 排序 ---- */
  var search = document.getElementById("hotspot-search");
  var sortSelect = document.getElementById("hotspot-sort");
  var chips = document.querySelectorAll(".chip[data-origin]");
  var activeOrigin = "all";
  function applyHotspotFilter() {
    var query = (search.value || "").toLowerCase();
    var rows = Array.prototype.slice.call(document.querySelectorAll("#hotspot-table tbody tr.hotspot-row"));
    rows.forEach(function (row) {
      var originOk = activeOrigin === "all" || row.dataset.origin === activeOrigin;
      var textOk = !query || (row.dataset.name || "").indexOf(query) !== -1;
      row.style.display = originOk && textOk ? "" : "none";
    });
  }
  function sortRows() {
    var tbody = document.querySelector("#hotspot-table tbody");
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr.hotspot-row"));
    var key = sortSelect.value;
    rows.sort(function (a, b) { return parseFloat(b.dataset[key]) - parseFloat(a.dataset[key]); });
    rows.forEach(function (row, index) {
      row.querySelector("td.rank").textContent = index + 1;
      tbody.appendChild(row);
    });
    applyHotspotFilter();
  }
  search.addEventListener("input", applyHotspotFilter);
  sortSelect.addEventListener("change", sortRows);
  chips.forEach(function (chip) {
    chip.addEventListener("click", function () {
      chips.forEach(function (c) { c.classList.remove("active"); });
      chip.classList.add("active");
      activeOrigin = chip.dataset.origin;
      applyHotspotFilter();
    });
  });

  /* ---- 调用关系 ---- */
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target.classList || !target.classList.contains("func-link")) return;
    var row = target.closest("tr.hotspot-row");
    var tpl = row && document.getElementById(row.dataset.rel);
    if (!tpl) return;
    var body = document.getElementById("relation-body");
    body.innerHTML = "";
    body.appendChild(tpl.content.cloneNode(true));
    showTab("relations");
  });

  /* ---- 火焰图 ---- */
  var svg = document.getElementById("flame-svg");
  var breadcrumb = document.getElementById("flame-breadcrumb");
  var tip = document.getElementById("flame-tip");
  var ROW_H = 22;
  var stack = [];
  var totalValue = FLAME.reduce(function (sum, node) { return sum + node.value; }, 0);

  function currentRoot() {
    if (stack.length) return stack[stack.length - 1];
    return { n: "全部", v: totalValue, s: 0, f: "", p: "", l: 0, o: "builtin", c: FLAME };
  }
  function drawNode(node, x, y, width) {
    var group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    var rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", x);
    rect.setAttribute("y", y);
    rect.setAttribute("width", Math.max(width - 1, 0));
    rect.setAttribute("height", ROW_H - 2);
    rect.setAttribute("rx", 2);
    rect.setAttribute("fill", ORIGIN_COLORS[node.o] || ORIGIN_COLORS.builtin);
    rect.style.cursor = (node.c && node.c.length) ? "pointer" : "default";
    group.appendChild(rect);
    if (width > 48) {
      var text = document.createElementNS("http://www.w3.org/2000/svg", "text");
      text.setAttribute("x", x + 4);
      text.setAttribute("y", y + 14);
      text.setAttribute("font-size", "11");
      text.setAttribute("fill", "#fff");
      text.setAttribute("pointer-events", "none");
      text.textContent = node.n.length > width / 6.2 ? node.n.slice(0, Math.max(3, Math.floor(width / 6.2) - 2)) + "…" : node.n;
      group.appendChild(text);
    }
    group.addEventListener("mousemove", function (event) {
      tip.style.display = "block";
      tip.style.left = event.clientX + 14 + "px";
      tip.style.top = event.clientY + 14 + "px";
      tip.textContent = node.n + " · 累计 " + fmtTime(node.v) + " · 自身 " + fmtTime(node.s) +
        (node.p ? " · " + node.p + ":" + node.l : "");
    });
    group.addEventListener("mouseleave", function () { tip.style.display = "none"; });
    if (node.c && node.c.length) {
      group.addEventListener("click", function () { stack.push(node); render(); });
    }
    svg.appendChild(group);
  }
  function render() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var root = currentRoot();
    if (!root.v) { svg.setAttribute("height", "120"); return; }
    var width = Math.max(svg.clientWidth || 1000, 600);
    var layoutNodes = [];
    var maxDepth = 0;
    (function walk(node, x, depth, span) {
      maxDepth = Math.max(maxDepth, depth);
      layoutNodes.push({ node: node, x: x, y: depth * ROW_H, width: node.v / span * width });
      var cursor = x;
      (node.c || []).forEach(function (child) {
        var childWidth = child.v / span * width;
        walk(child, cursor, depth + 1, span);
        cursor += childWidth;
      });
    })(root, 0, 0, root.v || 1);
    svg.setAttribute("height", String((maxDepth + 1) * ROW_H + 8));
    layoutNodes.forEach(function (item) { drawNode(item.node, item.x, item.y, item.width); });
    renderBreadcrumb();
  }
  function renderBreadcrumb() {
    breadcrumb.innerHTML = "";
    var all = document.createElement("button");
    all.textContent = "全部";
    all.addEventListener("click", function () { stack = []; render(); });
    breadcrumb.appendChild(all);
    stack.forEach(function (node, index) {
      var btn = document.createElement("button");
      btn.textContent = node.n;
      btn.addEventListener("click", function () { stack = stack.slice(0, index + 1); render(); });
      breadcrumb.appendChild(btn);
    });
  }
  window.addEventListener("resize", function () { if (!document.getElementById("tab-flame").hidden) render(); });
  render();
})();
</script>
</body>
</html>"""
