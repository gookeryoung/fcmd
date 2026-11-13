"""dynacalc 结果回收：glstat / matsum / history.csv 解析与汇总（纯逻辑）。

失败语义：文件缺失、表头缺列、非数值、时间非单调、无数据块一律抛
:class:`ResultParseError`（带文件与行号）。**不把解析失败写成 0 或空值冒充成功。**
探针未到达只记警告并省略该探针，不伪造到达时刻。
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.dynacalc._template import Manifest, ManifestCase

__all__ = [
    "HISTORY_COLUMNS",
    "CaseMetrics",
    "CaseResult",
    "CompareRow",
    "GlstatBlock",
    "History",
    "MatsumSummary",
    "ResultParseError",
    "ResultSet",
    "collect_case",
    "collect_results",
    "compare_cases",
    "extract_metrics",
    "format_text_report",
    "parse_glstat",
    "parse_history",
    "parse_matsum",
    "result_to_csv",
    "result_to_json",
    "write_report",
]

HISTORY_COLUMNS = ("time", "front_z", "reaction_fraction", "wall_velocity", "wall_strain")
"""项目标准历史量列名（单位：s / m / 无量纲 / m·s⁻¹ / 无量纲）。"""

_TIME_LINE = re.compile(r"^\s*time\s*=\s*(?P<value>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", re.IGNORECASE)
_PART_LINE = re.compile(r"^\s*part\s+id\s*=\s*(?P<value>\d+)", re.IGNORECASE)
_KV_LINE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z _\-]*?)\s*=\s*(?P<value>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
)


class ResultParseError(ValueError):
    """结果文件不可用：消息可直接展示（含文件与行号）。"""


# ---------------------------------------------------------------------- #
# 原始文件解析
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class History:
    """``history.csv`` 的列式数据。"""

    time: tuple[float, ...]
    front_z: tuple[float, ...]
    reaction_fraction: tuple[float, ...]
    wall_velocity: tuple[float, ...]
    wall_strain: tuple[float, ...]

    def __len__(self) -> int:
        return len(self.time)


@dataclass(frozen=True)
class GlstatBlock:
    """``glstat`` 的一个时间块。"""

    time: float
    values: Mapping[str, float]


@dataclass(frozen=True)
class MatsumSummary:
    """``matsum`` 的汇总量。"""

    part_count: int
    max_part_internal_energy: float


def _read_text(path: Path) -> str:
    """读取结果文件；缺失或解码失败转成 :class:`ResultParseError`。"""
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ResultParseError(f"缺少结果文件: {path}") from exc
    except OSError as exc:
        raise ResultParseError(f"无法读取结果文件 {path}: {exc}") from exc


def _number(text: str, where: str) -> float:
    """严格解析数值：空串、非数值、``nan``/``inf`` 一律报错。"""
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise ResultParseError(f"{where} 不是数值: {text!r}") from exc
    if not math.isfinite(value):
        raise ResultParseError(f"{where} 不是有限数值: {text!r}")
    return value


def parse_history(path: Path) -> History:
    """解析 ``history.csv``：列名固定，时间必须严格递增。"""
    text = _read_text(path)
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        raise ResultParseError(f"结果文件为空: {path}")
    header = [cell.strip().lower() for cell in rows[0]]
    missing = [name for name in HISTORY_COLUMNS if name not in header]
    if missing:
        raise ResultParseError(f"{path} 缺少列: {', '.join(missing)}")
    index = {name: header.index(name) for name in HISTORY_COLUMNS}
    columns: dict[str, list[float]] = {name: [] for name in HISTORY_COLUMNS}
    for line_number, row in enumerate(rows[1:], 2):
        if len(row) < len(header):
            raise ResultParseError(f"{path} 第 {line_number} 行列数不足（需要 {len(header)} 列）")
        for name, position in index.items():
            columns[name].append(_number(row[position].strip(), f"{path} 第 {line_number} 行 {name}"))
    if len(columns["time"]) < 2:
        raise ResultParseError(f"{path} 有效数据少于 2 行，无法提取时程")
    times = columns["time"]
    for position in range(1, len(times)):
        if times[position] <= times[position - 1]:
            raise ResultParseError(f"{path} 第 {position + 2} 行 time 未严格递增")
    return History(
        time=tuple(times),
        front_z=tuple(columns["front_z"]),
        reaction_fraction=tuple(columns["reaction_fraction"]),
        wall_velocity=tuple(columns["wall_velocity"]),
        wall_strain=tuple(columns["wall_strain"]),
    )


def parse_glstat(path: Path) -> tuple[GlstatBlock, ...]:
    """解析 LS-DYNA ``glstat``：按 ``time =`` 切块，键名小写去重。"""
    text = _read_text(path)
    blocks: list[GlstatBlock] = []
    current_time: float | None = None
    current: dict[str, float] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        time_match = _TIME_LINE.match(line)
        if time_match:
            if current_time is not None:
                blocks.append(GlstatBlock(time=current_time, values=dict(current)))
            current_time = _number(time_match.group("value"), f"{path} 第 {line_number} 行 time")
            current = {}
            continue
        kv_match = _KV_LINE.match(line)
        if kv_match is None or current_time is None:
            continue
        key = " ".join(kv_match.group("key").lower().split())
        current[key] = _number(kv_match.group("value"), f"{path} 第 {line_number} 行 {key}")
    if current_time is not None:
        blocks.append(GlstatBlock(time=current_time, values=dict(current)))
    if not blocks:
        raise ResultParseError(f"{path} 没有解析到任何 time 数据块")
    if not any("internal energy" in block.values for block in blocks):
        raise ResultParseError(f"{path} 缺少 internal energy 数据")
    return tuple(blocks)


def parse_matsum(path: Path) -> MatsumSummary:
    """解析 ``matsum``：取 part 数与最大 part 内能。"""
    text = _read_text(path)
    parts: dict[int, float] = {}
    next_part = 1
    current_part: int | None = None
    for line_number, line in enumerate(text.splitlines(), 1):
        part_match = _PART_LINE.match(line)
        if part_match:
            current_part = int(part_match.group("value"))
            next_part = max(next_part, current_part + 1)
            continue
        kv_match = _KV_LINE.match(line)
        if kv_match is None:
            continue
        key = " ".join(kv_match.group("key").lower().split())
        if key != "internal energy":
            continue
        value = _number(kv_match.group("value"), f"{path} 第 {line_number} 行 {key}")
        part = current_part if current_part is not None else next_part
        if current_part is None:
            next_part += 1
        parts[part] = value
    if not parts:
        raise ResultParseError(f"{path} 缺少 part internal energy 数据")
    return MatsumSummary(part_count=len(parts), max_part_internal_energy=max(parts.values()))


# ---------------------------------------------------------------------- #
# 指标提取
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class CaseMetrics:
    """一个工况的关键指标。"""

    code: str
    values: Mapping[str, float | None]
    arrivals_us: Mapping[float, float]
    warnings: tuple[str, ...]

    def value(self, key: str) -> float | None:
        """按指标键取值（缺失返回 None）。"""
        return self.values.get(key)


@dataclass(frozen=True)
class CaseResult:
    """一个工况的回收结果：成功带指标，失败带中文原因。"""

    code: str
    status: str
    metrics: CaseMetrics | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        """是否解析成功。"""
        return self.status == "ok"


@dataclass(frozen=True)
class ResultSet:
    """一次汇总的全部工况结果。"""

    manifest: Manifest
    results: tuple[CaseResult, ...]

    def failed(self) -> tuple[CaseResult, ...]:
        """解析失败的工况。"""
        return tuple(item for item in self.results if not item.ok)

    def by_code(self, code: str) -> CaseResult:
        """按编码取结果；不存在时抛 :class:`ResultParseError`。"""
        for item in self.results:
            if item.code == code:
                return item
        raise ResultParseError(f"汇总中没有工况 {code}")


def _setting_float(settings: Mapping[str, Any], key: str, default: float) -> float:
    """从清单 settings 取浮点配置；非法值回落默认。"""
    value = settings.get(key, default)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _probes(settings: Mapping[str, Any]) -> tuple[float, ...]:
    """从清单 settings 取探针位置（mm）。"""
    raw = settings.get("probes_mm")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        probes = [float(item) for item in raw if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if probes:
            return tuple(probes)
    return ()


def _fit_velocity(history: History, skip: float, tail: float) -> tuple[float, float | None]:
    """稳态窗口内对 ``front_z(t)`` 做最小二乘拟合，返回 ``(斜率, R²)``。"""
    start = history.time[0] + skip * (history.time[-1] - history.time[0])
    end = history.time[-1] - tail * (history.time[-1] - history.time[0])
    points = [(time, z) for time, z in zip(history.time, history.front_z, strict=True) if start <= time <= end]
    if len(points) < 3:
        raise ResultParseError(f"稳态窗口内有效点不足（{len(points)} < 3），无法拟合爆速")
    mean_t = sum(item[0] for item in points) / len(points)
    mean_z = sum(item[1] for item in points) / len(points)
    denominator = sum((item[0] - mean_t) ** 2 for item in points)
    if denominator <= 0:
        raise ResultParseError("稳态窗口内时间无变化，无法拟合爆速")
    slope = sum((item[0] - mean_t) * (item[1] - mean_z) for item in points) / denominator
    ss_tot = sum((item[1] - mean_z) ** 2 for item in points)
    if ss_tot <= 0:
        return slope, None
    ss_res = sum((item[1] - (mean_z + slope * (item[0] - mean_t))) ** 2 for item in points)
    return slope, 1.0 - ss_res / ss_tot


def _arrival_us(history: History, probe_mm: float) -> float | None:
    """波面首次到达 ``probe_mm``（mm）的时刻（µs），线性插值。"""
    target = probe_mm / 1000.0
    for index, z in enumerate(history.front_z):
        if z < target:
            continue
        if index == 0:
            return history.time[0] * 1e6
        previous_z = history.front_z[index - 1]
        previous_t = history.time[index - 1]
        current_t = history.time[index]
        if z == previous_z:
            return current_t * 1e6
        ratio = (target - previous_z) / (z - previous_z)
        return (previous_t + ratio * (current_t - previous_t)) * 1e6
    return None


def _trapezoid(time: Sequence[float], values: Sequence[float]) -> float:
    """梯形法时间积分。"""
    total = 0.0
    for index in range(1, len(time)):
        total += 0.5 * (values[index] + values[index - 1]) * (time[index] - time[index - 1])
    return total


def extract_metrics(
    code: str,
    history: History,
    glstat: Sequence[GlstatBlock],
    matsum: MatsumSummary,
    settings: Mapping[str, Any],
) -> CaseMetrics:
    """从三类结果提取关键指标；稳态拟合失败视为解析错误。"""
    skip = _setting_float(settings, "skip_fraction", 0.2)
    tail = _setting_float(settings, "tail_fraction", 0.1)
    velocity, r2 = _fit_velocity(history, skip, tail)
    warnings: list[str] = []
    if velocity <= 0:
        warnings.append(f"稳态段爆速非正（{velocity:.3f} m/s），请检查时程与单位")
    arrivals: dict[float, float] = {}
    for probe in _probes(settings):
        arrival = _arrival_us(history, probe)
        if arrival is None:
            warnings.append(f"波面未到达探针 {probe:g} mm")
        else:
            arrivals[float(probe)] = arrival
    values: dict[str, float | None] = {
        "velocity_m_s": velocity,
        "velocity_r2": r2,
        "reaction_fraction_end": history.reaction_fraction[-1],
        "wall_velocity_max_m_s": max(history.wall_velocity),
        "wall_impulse_m": _trapezoid(history.time, history.wall_velocity),
        "wall_strain_max": max(history.wall_strain),
    }
    internal = [block.values["internal energy"] for block in glstat if "internal energy" in block.values]
    if internal:
        values["glstat_peak_internal_energy"] = max(internal)
    else:
        values["glstat_peak_internal_energy"] = None
        warnings.append("glstat 缺少 internal energy，峰值内能记为缺失")
    totals = [block.values["total energy"] for block in glstat if "total energy" in block.values]
    if totals:
        values["glstat_final_total_energy"] = totals[-1]
    else:
        values["glstat_final_total_energy"] = None
        warnings.append("glstat 缺少 total energy，末态总能量记为缺失")
    values["matsum_part_count"] = float(matsum.part_count)
    values["matsum_max_part_internal_energy"] = matsum.max_part_internal_energy
    return CaseMetrics(code=code, values=values, arrivals_us=arrivals, warnings=tuple(warnings))


def collect_case(root: Path, item: ManifestCase, settings: Mapping[str, Any]) -> CaseResult:
    """回收单个工况：目录、三个结果文件都要存在且可解析。"""
    directory = root / item.slug
    if not directory.is_dir():
        return CaseResult(code=item.code, status="error", error=f"工况目录不存在: {directory}")
    try:
        history = parse_history(directory / "history.csv")
        glstat = parse_glstat(directory / "glstat")
        matsum = parse_matsum(directory / "matsum")
        metrics = extract_metrics(item.code, history, glstat, matsum, settings)
    except ResultParseError as exc:
        return CaseResult(code=item.code, status="error", error=str(exc))
    return CaseResult(code=item.code, status="ok", metrics=metrics)


def collect_results(root: Path, manifest: Manifest) -> ResultSet:
    """按生成清单逐工况回收结果；单个工况失败不影响其余工况。"""
    results = tuple(collect_case(root, item, manifest.settings) for item in manifest.cases)
    return ResultSet(manifest=manifest, results=results)


# ---------------------------------------------------------------------- #
# 输出
# ---------------------------------------------------------------------- #
_CSV_METRICS = (
    ("velocity_m_s", "爆速(m/s)"),
    ("velocity_r2", "拟合R²"),
    ("reaction_fraction_end", "末端反应分数"),
    ("wall_velocity_max_m_s", "管壁速度峰值(m/s)"),
    ("wall_impulse_m", "管壁速度积分(m)"),
    ("wall_strain_max", "管壁应变峰值"),
    ("glstat_peak_internal_energy", "glstat峰值内能"),
    ("glstat_final_total_energy", "glstat末态总能量"),
    ("matsum_part_count", "matsum part数"),
    ("matsum_max_part_internal_energy", "matsum最大part内能"),
)


def _probe_columns(result_set: ResultSet) -> tuple[float, ...]:
    """汇总中出现过的探针（mm），稳定排序。"""
    probes: set[float] = set()
    for item in result_set.results:
        if item.metrics is not None:
            probes.update(item.metrics.arrivals_us)
    return tuple(sorted(probes))


def _metric_text(value: float | None) -> str:
    """CSV/文本里的数值格式：缺失写空串，其余保留 6 位有效。"""
    if value is None:
        return ""
    return f"{value:.6g}"


def result_to_csv(result_set: ResultSet) -> str:
    """汇总 CSV 文本（不含 BOM；写文件时由 :func:`write_report` 加 BOM）。"""
    probes = _probe_columns(result_set)
    header = [
        "code",
        "status",
        *(label for _, label in _CSV_METRICS),
        *(f"到达{probe:g}mm(us)" for probe in probes),
        "error",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for item in result_set.results:
        metrics = item.metrics
        row = [item.code, item.status]
        row.extend(_metric_text(metrics.value(key)) if metrics else "" for key, _ in _CSV_METRICS)
        row.extend(_metric_text(metrics.arrivals_us.get(probe)) if metrics else "" for probe in probes)
        row.append(item.error)
        writer.writerow(row)
    return buffer.getvalue()


def result_to_json(result_set: ResultSet) -> str:
    """汇总 JSON：逐工况状态、指标、到达时程、警告与错误。"""
    payload = {
        "schema": "dynacalc-report/1",
        "total": len(result_set.results),
        "ok": sum(1 for item in result_set.results if item.ok),
        "errors": len(result_set.failed()),
        "cases": [
            {
                "code": item.code,
                "status": item.status,
                "values": dict(item.metrics.values) if item.metrics else {},
                "arrivals_us": {f"{probe:g}": value for probe, value in item.metrics.arrivals_us.items()}
                if item.metrics
                else {},
                "warnings": list(item.metrics.warnings) if item.metrics else [],
                "error": item.error,
            }
            for item in result_set.results
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def format_text_report(result_set: ResultSet) -> str:
    """可读文本汇总：逐工况指标 + 解析失败清单。"""
    lines = [
        f"汇总：{len(result_set.results)} 个工况，成功 {len(result_set.results) - len(result_set.failed())}，失败 {len(result_set.failed())}"
    ]
    probes = _probe_columns(result_set)
    for item in result_set.results:
        if not item.ok or item.metrics is None:
            lines.append(f"[{item.code}] 失败：{item.error}")
            continue
        metrics = item.metrics
        parts = [
            f"爆速={_metric_text(metrics.value('velocity_m_s'))} m/s",
            f"R²={_metric_text(metrics.value('velocity_r2'))}",
            f"末端反应分数={_metric_text(metrics.value('reaction_fraction_end'))}",
            f"管壁速度峰值={_metric_text(metrics.value('wall_velocity_max_m_s'))} m/s",
            f"管壁速度积分={_metric_text(metrics.value('wall_impulse_m'))} m",
        ]
        if probes:
            arrivals = "，".join(f"{probe:g}mm={_metric_text(metrics.arrivals_us.get(probe))}µs" for probe in probes)
            parts.append(f"到达时程({arrivals})")
        lines.append(f"[{item.code}] " + "；".join(parts))
        for warning in metrics.warnings:
            lines.append(f"  - 警告：{warning}")
    if result_set.failed():
        lines.append("")
        lines.append("解析失败清单：")
        lines.extend(f"  - {item.code}: {item.error}" for item in result_set.failed())
    return "\n".join(lines) + "\n"


def write_report(result_set: ResultSet, path: Path, fmt: str) -> None:
    """把汇总写到文件：CSV 带 BOM（Excel 中文），JSON / 文本为 UTF-8。"""
    if fmt == "csv":
        text = result_to_csv(result_set)
        path.write_text(text, encoding="utf-8-sig", newline="")
        return
    if fmt == "json":
        path.write_text(result_to_json(result_set), encoding="utf-8", newline="\n")
        return
    if fmt == "text":
        path.write_text(format_text_report(result_set), encoding="utf-8", newline="\n")
        return
    raise ValueError(f"未知报告格式: {fmt}")


@dataclass(frozen=True)
class CompareRow:
    """两工况对比的一行。"""

    label: str
    left: float | None
    right: float | None
    delta: float | None
    relative: float | None


_COMPARE_METRICS = (*_CSV_METRICS,)


def compare_cases(result_set: ResultSet, left: str, right: str) -> list[CompareRow]:
    """对比两个工况的数值指标；缺失值保留为空并标注不可比。"""
    first = result_set.by_code(left)
    second = result_set.by_code(right)
    if not first.ok or first.metrics is None:
        raise ResultParseError(f"工况 {left} 解析失败，无法对比: {first.error}")
    if not second.ok or second.metrics is None:
        raise ResultParseError(f"工况 {right} 解析失败，无法对比: {second.error}")
    rows: list[CompareRow] = []
    for key, label in _COMPARE_METRICS:
        a = first.metrics.value(key)
        b = second.metrics.value(key)
        delta = a - b if a is not None and b is not None else None
        relative = delta / abs(b) if delta is not None and b not in (None, 0) else None
        rows.append(CompareRow(label=label, left=a, right=b, delta=delta, relative=relative))
    probes = sorted(set(first.metrics.arrivals_us) | set(second.metrics.arrivals_us))
    for probe in probes:
        a = first.metrics.arrivals_us.get(probe)
        b = second.metrics.arrivals_us.get(probe)
        delta = a - b if a is not None and b is not None else None
        relative = delta / abs(b) if delta is not None and b not in (None, 0) else None
        rows.append(CompareRow(label=f"到达{probe:g}mm(µs)", left=a, right=b, delta=delta, relative=relative))
    return rows
