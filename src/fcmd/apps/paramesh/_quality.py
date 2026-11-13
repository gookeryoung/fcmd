"""网格质量统计：长宽比分布、尺寸范围、退化 / 负体积、区域面积误差与壁厚层数。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fcmd.apps.paramesh._mesh import (
    SECTION_SOLVER_THICKNESS_MM,
    Mesh,
    region_thickness_mm,
)

__all__ = [
    "HistogramBin",
    "MassCheck",
    "QualityReport",
    "RegionQuality",
    "format_quality",
    "quality_report",
]

_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class HistogramBin:
    """长宽比分布的一档。"""

    label: str
    count: int


@dataclass(frozen=True, slots=True)
class RegionQuality:
    """区域面积与解析值对照。"""

    name: str
    elements: int
    area_mm2: float
    analytic_area_mm2: float
    rel_error: float


@dataclass(frozen=True, slots=True)
class MassCheck:
    """按 ``*SECTION_SHELL`` 厚度折算的体积等价质量自检。

    密度取单位值，因此比值与材料密度无关，只反映「截面厚度语义 + 网格分层」
    是否会把质量按层数放大。
    """

    section_thickness_mm: float
    analytic_volume_mm3: float
    mesh_volume_mm3: float
    ratio: float
    region_thickness_volume_mm3: float
    region_thickness_ratio: float

    def to_dict(self) -> dict[str, object]:
        return {
            "basis": "unit_density",
            "section_thickness_mm": round(self.section_thickness_mm, 12),
            "analytic_volume_mm3": round(self.analytic_volume_mm3, 12),
            "mesh_volume_mm3": round(self.mesh_volume_mm3, 12),
            "ratio": round(self.ratio, 12),
            "region_thickness_volume_mm3": round(self.region_thickness_volume_mm3, 12),
            "region_thickness_ratio": round(self.region_thickness_ratio, 12),
        }


@dataclass(frozen=True, slots=True)
class QualityReport:
    """一档网格的质量报告。"""

    element_count: int
    node_count: int
    aspect_min: float
    aspect_max: float
    aspect_mean: float
    aspect_bins: tuple[HistogramBin, ...]
    size_min_mm: float
    size_max_mm: float
    size_mean_mm: float
    negative_volume: int
    degenerate: int
    wall_layers_actual: int
    wall_layers_global_raw: int
    wall_layers_target_min: int
    wall_layers_target_max: int
    regions: tuple[RegionQuality, ...]
    mass_check: MassCheck
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "element_count": self.element_count,
            "node_count": self.node_count,
            "aspect_ratio": {
                "min": round(self.aspect_min, 12),
                "max": round(self.aspect_max, 12),
                "mean": round(self.aspect_mean, 12),
                "bins": [{"label": item.label, "count": item.count} for item in self.aspect_bins],
            },
            "size_mm": {
                "min": round(self.size_min_mm, 12),
                "max": round(self.size_max_mm, 12),
                "mean": round(self.size_mean_mm, 12),
            },
            "negative_volume": self.negative_volume,
            "degenerate": self.degenerate,
            "wall_layers": {
                "actual": self.wall_layers_actual,
                "global_raw": self.wall_layers_global_raw,
                "target_min": self.wall_layers_target_min,
                "target_max": self.wall_layers_target_max,
            },
            "regions": [
                {
                    "name": item.name,
                    "elements": item.elements,
                    "area_mm2": round(item.area_mm2, 12),
                    "analytic_area_mm2": round(item.analytic_area_mm2, 12),
                    "rel_error": round(item.rel_error, 12),
                }
                for item in self.regions
            ],
            "mass_check": self.mass_check.to_dict(),
            "warnings": list(self.warnings),
        }


def _quad_metrics(
    corners: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]],
) -> tuple[float, float, float]:
    """返回（面积, 最小边长, 最大边长）。"""
    edges: list[float] = []
    for index in range(4):
        r0, z0 = corners[index]
        r1, z1 = corners[(index + 1) % 4]
        edges.append(math.hypot(r1 - r0, z1 - z0))
    total = 0.0
    for index in range(4):
        r0, z0 = corners[index]
        r1, z1 = corners[(index + 1) % 4]
        total += r0 * z1 - r1 * z0
    return total / 2.0, min(edges), max(edges)


def _triangle_r_area(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> float:
    """三角形对 ``∫ r dA`` 的精确贡献（用于旋转体体积）。"""
    r0, z0 = p0
    r1, z1 = p1
    r2, z2 = p2
    area = abs((r1 - r0) * (z2 - z0) - (r2 - r0) * (z1 - z0)) / 2.0
    return area * (r0 + r1 + r2) / 3.0


def _polygon_r_area(
    corners: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]],
) -> float:
    """四边形对 ``∫ r dA`` 的精确贡献（沿 0–2 对角线拆成两个三角形）。"""
    return _triangle_r_area(corners[0], corners[1], corners[2]) + _triangle_r_area(corners[0], corners[2], corners[3])


def _analytic_centroid_r(mesh: Mesh, region_name: str) -> float:
    plan = mesh.plan
    if region_name == "propellant":
        return plan.propellant_radius_mm / 2.0
    if region_name == "gap":
        return (plan.propellant_radius_mm + plan.inner_radius_mm) / 2.0
    return (plan.inner_radius_mm + plan.outer_radius_mm) / 2.0


def _bins(aspects: list[float]) -> tuple[HistogramBin, ...]:
    limits = ((1.5, "≤1.5"), (2.0, "1.5–2"), (5.0, "2–5"), (10.0, "5–10"))
    counts = [0] * (len(limits) + 1)
    for aspect in aspects:
        for index, (upper, _) in enumerate(limits):
            if aspect <= upper + _EPS:
                counts[index] += 1
                break
        else:
            counts[-1] += 1
    labels = [label for _, label in limits] + [">10"]
    return tuple(HistogramBin(label=label, count=count) for label, count in zip(labels, counts, strict=True))


def quality_report(mesh: Mesh) -> QualityReport:
    """统计一档网格的质量指标；所有指标来自实际生成的单元。"""
    coordinates = {node.node_id: (node.r, node.z) for node in mesh.nodes}
    aspects: list[float] = []
    sizes: list[float] = []
    negative = 0
    degenerate = 0
    r_area = 0.0
    region_thickness_r_area = 0.0
    for element in mesh.elements:
        c0, c1, c2, c3 = (coordinates[node_id] for node_id in element.nodes)
        area, min_edge, max_edge = _quad_metrics((c0, c1, c2, c3))
        if area < -_EPS:
            negative += 1
        if area <= _EPS or min_edge <= _EPS:
            degenerate += 1
        aspects.append(math.inf if min_edge <= _EPS else max_edge / min_edge)
        sizes.append(math.sqrt(abs(area)))
        element_r_area = _polygon_r_area((c0, c1, c2, c3))
        r_area += element_r_area
        region_thickness_r_area += element_r_area * region_thickness_mm(mesh.plan, element.region)

    finite_aspects = [value for value in aspects if math.isfinite(value)]
    aspect_min = min(finite_aspects) if finite_aspects else 0.0
    aspect_max = max(aspects) if aspects else 0.0
    aspect_mean = sum(finite_aspects) / len(finite_aspects) if finite_aspects else 0.0
    regions = tuple(
        RegionQuality(
            name=region.name,
            elements=region.element_count,
            area_mm2=region.area_mm2,
            analytic_area_mm2=region.analytic_area_mm2,
            rel_error=region.rel_error,
        )
        for region in mesh.regions
    )
    analytic_volume = (
        2.0
        * math.pi
        * sum(_analytic_centroid_r(mesh, region.name) * region.analytic_area_mm2 for region in mesh.regions)
    )
    mesh_volume = 2.0 * math.pi * r_area * SECTION_SOLVER_THICKNESS_MM
    region_thickness_volume = 2.0 * math.pi * region_thickness_r_area
    mass_check = MassCheck(
        section_thickness_mm=SECTION_SOLVER_THICKNESS_MM,
        analytic_volume_mm3=analytic_volume,
        mesh_volume_mm3=mesh_volume,
        ratio=mesh_volume / analytic_volume if analytic_volume > _EPS else 0.0,
        region_thickness_volume_mm3=region_thickness_volume,
        region_thickness_ratio=region_thickness_volume / analytic_volume if analytic_volume > _EPS else 0.0,
    )
    return QualityReport(
        element_count=mesh.element_count,
        node_count=mesh.node_count,
        aspect_min=aspect_min,
        aspect_max=aspect_max,
        aspect_mean=aspect_mean,
        aspect_bins=_bins(aspects),
        size_min_mm=min(sizes) if sizes else 0.0,
        size_max_mm=max(sizes) if sizes else 0.0,
        size_mean_mm=sum(sizes) / len(sizes) if sizes else 0.0,
        negative_volume=negative,
        degenerate=degenerate,
        wall_layers_actual=mesh.plan.wall_layers,
        wall_layers_global_raw=mesh.plan.raw_wall_layers,
        wall_layers_target_min=mesh.plan.wall_layers_target_min,
        wall_layers_target_max=mesh.plan.wall_layers_target_max,
        regions=regions,
        mass_check=mass_check,
        warnings=mesh.warnings,
    )


def format_quality(report: QualityReport) -> str:
    """给人看的质量摘要。"""
    bins = "，".join(f"{item.label}: {item.count}" for item in report.aspect_bins)
    lines = [
        f"单元 / 节点：{report.element_count} / {report.node_count}",
        "坐标约定：x=轴向(z)，y=径向(r)，z=0；已按 x 轴为对称轴生成（ELFORM=14）",
        f"单元尺寸：{report.size_min_mm:.6g} – {report.size_max_mm:.6g} mm（均值 {report.size_mean_mm:.6g}）",
        f"长宽比：{report.aspect_min:.6g} – {report.aspect_max:.6g}（均值 {report.aspect_mean:.6g}）",
        f"长宽比分布：{bins}",
        f"退化 / 负体积：{report.degenerate} / {report.negative_volume}",
        f"壁厚层数：实际 {report.wall_layers_actual}（全局尺寸原始 {report.wall_layers_global_raw} 层）",
        "质量自检（单位密度）："
        f"按 section 厚度 {report.mass_check.section_thickness_mm:g} mm 折算 "
        f"{report.mass_check.mesh_volume_mm3:.6g} / 解析 {report.mass_check.analytic_volume_mm3:.6g} mm³，"
        f"比值 {report.mass_check.ratio:.6f}",
        f"  区域总厚度折算比值 {report.mass_check.region_thickness_ratio:.6g}（仅诊断，不写入 *SECTION_SHELL）",
        "区域面积对照：",
    ]
    for region in report.regions:
        lines.append(
            f"  {region.name}: {region.elements} 单元，"
            f"{region.area_mm2:.6g} / {region.analytic_area_mm2:.6g} mm²，"
            f"相对误差 {region.rel_error:.3e}"
        )
    for warning in report.warnings:
        lines.append(f"警告：{warning}")
    return "\n".join(lines)
