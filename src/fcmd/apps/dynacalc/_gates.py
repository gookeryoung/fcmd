"""dynacalc 门控检查：爆速 / 到达时程 / 管壁速度积分 / 熄爆分类（纯逻辑）。

阈值全部来自参数文件（缺省为工程建议值），检查结果分「通过 / 不通过 / 跳过」。
**这些阈值是工程建议值，不是物理常数**，输出必须带 :data:`DISCLAIMER`。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fcmd.apps.dynacalc._params import group_text
from fcmd.apps.dynacalc._results import CaseMetrics, ResultSet

__all__ = [
    "DISCLAIMER",
    "FAIL",
    "PASS",
    "SKIP",
    "GateCheck",
    "GateReport",
    "evaluate",
    "format_gate_report",
]

PASS = "通过"
FAIL = "不通过"
SKIP = "跳过"

DISCLAIMER = (
    "以上阈值（爆速相对差 2%、到达时程 L2 相对差 3%、管壁速度积分相对差 5%、"
    "末端反应分数判据）是本项目工程建议值，不是物理常数，不构成安全裕量或放行依据。"
)

_GATE_DEFAULTS = {
    "velocity_rel_diff": 0.02,
    "arrival_l2_rel": 0.03,
    "wall_impulse_rel": 0.05,
}
_EPS = 1e-12


@dataclass(frozen=True)
class GateCheck:
    """一条门控检查结论。"""

    gate: str
    label: str
    pair: tuple[str, str]
    verdict: str
    detail: str
    measured: float | None = None
    threshold: float | None = None


@dataclass(frozen=True)
class GateReport:
    """一次门控检查的全部结论。"""

    checks: tuple[GateCheck, ...]
    reference: str
    groups: Mapping[str, str] = field(default_factory=dict)
    """工况编码 → 分组（``main`` / ``typical`` / ``supplementary:...``）。"""

    def count(self, verdict: str) -> int:
        """统计某结论的条数。"""
        return sum(1 for item in self.checks if item.verdict == verdict)

    @property
    def passed(self) -> bool:
        """是否存在不通过项。"""
        return self.count(FAIL) == 0


def _gate_settings(settings: Mapping[str, Any]) -> Mapping[str, Any]:
    """取门控配置段；缺失或非映射时返回空映射（阈值随后回落默认值）。"""
    raw = settings.get("gates")
    return raw if isinstance(raw, Mapping) else {}


def _threshold(settings: Mapping[str, Any], key: str) -> float:
    """取阈值；非法值回落工程建议默认值。"""
    gates = _gate_settings(settings)
    value = gates.get(key, _GATE_DEFAULTS[key])
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return _GATE_DEFAULTS[key]


def _quench_fraction(settings: Mapping[str, Any]) -> float:
    """取熄爆判据的末端反应分数阈值。"""
    value = settings.get("quench_fraction", 0.99)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    return 0.99


def _pairs(settings: Mapping[str, Any], result_set: ResultSet) -> tuple[str, tuple[tuple[str, str], ...]]:
    """确定基准工况与配对：显式 pairs 优先，否则基准 vs 其余全部。"""
    gates = _gate_settings(settings)
    codes = [item.code for item in result_set.results]
    if not codes:
        raise ValueError("汇总为空，无法执行门控检查")
    reference = str(gates.get("reference") or codes[0])
    if reference not in codes:
        raise ValueError(f"基准工况 {reference} 不在汇总中")
    configured = gates.get("pairs") or []
    pairs: list[tuple[str, str]] = []
    if isinstance(configured, Sequence) and not isinstance(configured, (str, bytes)) and configured:
        for item in configured:
            if (
                isinstance(item, Sequence)
                and not isinstance(item, (str, bytes))
                and len(item) == 2
                and all(isinstance(name, str) for name in item)
            ):
                left, right = str(item[0]), str(item[1])
                for code in (left, right):
                    if code not in codes:
                        raise ValueError(f"门控配对引用了不存在的工况 {code}")
                pairs.append((reference, right) if left == reference else (left, right))
            else:
                raise ValueError(f"门控配对格式非法: {item!r}")
    else:
        pairs = [(reference, code) for code in codes if code != reference]
    return reference, tuple(pairs)


def _metrics(result_set: ResultSet, code: str) -> CaseMetrics | None:
    """取成功工况的指标；失败返回 None。"""
    item = result_set.by_code(code)
    return item.metrics if item.ok else None


def _verdict(measured: float, threshold: float) -> str:
    """按阈值给出结论。"""
    return PASS if measured <= threshold else FAIL


def _velocity_check(
    reference: str, target: str, ref: CaseMetrics, other: CaseMetrics | None, threshold: float
) -> GateCheck:
    """稳态爆速相对差。"""
    label = "稳态爆速相对差"
    if other is None:
        return GateCheck("velocity", label, (reference, target), SKIP, "目标工况解析失败", None, threshold)
    left = ref.value("velocity_m_s")
    right = other.value("velocity_m_s")
    if left is None or right is None:
        return GateCheck("velocity", label, (reference, target), SKIP, "爆速指标缺失", None, threshold)
    if abs(left) <= _EPS:
        return GateCheck("velocity", label, (reference, target), SKIP, "基准爆速接近 0，相对差无意义", None, threshold)
    measured = abs(right - left) / abs(left)
    detail = f"{left:.6g} → {right:.6g} m/s，相对差 {measured:.2%}（阈值 {threshold:.2%}）"
    return GateCheck("velocity", label, (reference, target), _verdict(measured, threshold), detail, measured, threshold)


def _arrival_check(
    reference: str, target: str, ref: CaseMetrics, other: CaseMetrics | None, threshold: float
) -> GateCheck:
    """波面到达时程 L2 相对差（共享探针）。"""
    label = "波面到达时程 L2 相对差"
    if other is None:
        return GateCheck("arrival", label, (reference, target), SKIP, "目标工况解析失败", None, threshold)
    shared = sorted(set(ref.arrivals_us) & set(other.arrivals_us))
    if not shared:
        return GateCheck("arrival", label, (reference, target), SKIP, "没有共同到达的探针", None, threshold)
    relative: list[float] = []
    parts: list[str] = []
    for probe in shared:
        left = ref.arrivals_us[probe]
        right = other.arrivals_us[probe]
        if abs(left) <= _EPS:
            continue
        ratio = (right - left) / left
        relative.append(ratio)
        parts.append(f"{probe:g}mm {left:.3f}→{right:.3f}µs({ratio:+.2%})")
    if not relative:
        return GateCheck("arrival", label, (reference, target), SKIP, "探针时刻接近 0，相对差无意义", None, threshold)
    measured = math.sqrt(sum(value * value for value in relative) / len(relative))
    detail = "；".join(parts) + f"；L2={measured:.2%}（阈值 {threshold:.2%}）"
    return GateCheck("arrival", label, (reference, target), _verdict(measured, threshold), detail, measured, threshold)


def _impulse_check(
    reference: str, target: str, ref: CaseMetrics, other: CaseMetrics | None, threshold: float
) -> GateCheck:
    """管壁速度积分相对差。"""
    label = "管壁速度积分相对差"
    if other is None:
        return GateCheck("impulse", label, (reference, target), SKIP, "目标工况解析失败", None, threshold)
    left = ref.value("wall_impulse_m")
    right = other.value("wall_impulse_m")
    if left is None or right is None:
        return GateCheck("impulse", label, (reference, target), SKIP, "管壁速度积分缺失", None, threshold)
    if abs(left) <= _EPS:
        return GateCheck("impulse", label, (reference, target), SKIP, "基准积分接近 0，相对差无意义", None, threshold)
    measured = abs(right - left) / abs(left)
    detail = f"{left:.6g} → {right:.6g} m，相对差 {measured:.2%}（阈值 {threshold:.2%}）"
    return GateCheck("impulse", label, (reference, target), _verdict(measured, threshold), detail, measured, threshold)


def _classify(fraction: float | None, quench_fraction: float) -> str | None:
    """按末端反应分数分类：低于阈值判熄爆。"""
    if fraction is None:
        return None
    return "熄爆" if fraction < quench_fraction else "未熄爆"


def _quench_check(
    reference: str, target: str, ref: CaseMetrics, other: CaseMetrics | None, quench_fraction: float
) -> GateCheck:
    """熄爆分类是否一致。"""
    label = "熄爆分类一致"
    if other is None:
        return GateCheck("quench", label, (reference, target), SKIP, "目标工况解析失败", None, quench_fraction)
    left_fraction = ref.value("reaction_fraction_end")
    right_fraction = other.value("reaction_fraction_end")
    left = _classify(left_fraction, quench_fraction)
    right = _classify(right_fraction, quench_fraction)
    if left is None or right is None:
        return GateCheck("quench", label, (reference, target), SKIP, "末端反应分数缺失", None, quench_fraction)
    detail = f"基准={left}({left_fraction:.6g})，目标={right}({right_fraction:.6g})，熄爆阈值 {quench_fraction:.6g}"
    verdict = PASS if left == right else FAIL
    return GateCheck("quench", label, (reference, target), verdict, detail, None, quench_fraction)


def evaluate(result_set: ResultSet, settings: Mapping[str, Any]) -> GateReport:
    """执行四项门控检查。

    Raises
    ------
    ValueError:
        汇总为空、基准工况不存在或基准工况解析失败。
    """
    reference, pairs = _pairs(settings, result_set)
    reference_item = result_set.by_code(reference)
    if not reference_item.ok or reference_item.metrics is None:
        raise ValueError(f"基准工况 {reference} 解析失败，无法执行门控: {reference_item.error}")
    thresholds = {key: _threshold(settings, key) for key in _GATE_DEFAULTS}
    quench_fraction = _quench_fraction(settings)
    checks: list[GateCheck] = []
    for left, right in pairs:
        left_metrics = _metrics(result_set, left)
        if left_metrics is None:
            raise ValueError(f"配对基准工况 {left} 解析失败，无法执行门控")
        other = _metrics(result_set, right)
        checks.append(_velocity_check(left, right, left_metrics, other, thresholds["velocity_rel_diff"]))
        checks.append(_arrival_check(left, right, left_metrics, other, thresholds["arrival_l2_rel"]))
        checks.append(_impulse_check(left, right, left_metrics, other, thresholds["wall_impulse_rel"]))
        checks.append(_quench_check(left, right, left_metrics, other, quench_fraction))
    groups = {item.code: item.group for item in result_set.manifest.cases}
    return GateReport(checks=tuple(checks), reference=reference, groups=groups)


def _tag(report: GateReport, code: str) -> str:
    """工况编码附中文分组名；主矩阵不加后缀。"""
    group = report.groups.get(code, "main")
    return code if group == "main" else f"{code}（{group_text(group)}）"


def format_gate_report(report: GateReport) -> str:
    """文本门控清单，末尾固定附带工程建议值声明。"""
    lines = [
        f"门控检查：基准工况 {_tag(report, report.reference)}，"
        f"通过 {report.count(PASS)}，不通过 {report.count(FAIL)}，跳过 {report.count(SKIP)}"
    ]
    codes = {code for item in report.checks for code in item.pair}
    if codes:
        grouped: dict[str, int] = {}
        for code in codes:
            name = group_text(report.groups.get(code, "main"))
            grouped[name] = grouped.get(name, 0) + 1
        lines.append("配对工况分组：" + "，".join(f"{name} {count} 个" for name, count in grouped.items()))
    for item in report.checks:
        pair = f"{_tag(report, item.pair[0])} vs {_tag(report, item.pair[1])}"
        lines.append(f"[{item.verdict}] {pair} {item.label}：{item.detail}")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines) + "\n"
