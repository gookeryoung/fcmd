"""网格尺度策略：特征尺度、三档全局尺寸、壁厚层数与代表截面。

二维轴对称无法表达周向变化，因此代表截面按「最薄壁 / 最薄药层」自动选择；
显式给出 ``section_angle_deg`` 时尊重用户选择，但报告仍给出实际壁厚。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fcmd.apps.paramesh._config import LEVELS, MeshConfig
from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._model import MeshModel

__all__ = [
    "ScalePlan",
    "WallAtAngle",
    "choose_section_angle",
    "format_plan",
    "plan_all",
    "plan_scale",
    "wall_thickness_at",
]

_EPS = 1e-12


@dataclass(frozen=True, slots=True)
class WallAtAngle:
    """某一周向角下的壁厚及其来源。"""

    angle_deg: float
    thickness_mm: float
    source: str
    uncalibrated: bool


@dataclass(frozen=True, slots=True)
class ScalePlan:
    """一档网格的尺度决策与派生几何。"""

    level: str
    divisor: int
    characteristic_mm: float
    driver: str
    global_h_mm: float
    reaction_zone_mm: float
    min_wall_mm: float
    min_defect_mm: float | None
    section_angle_deg: float
    section_wall_mm: float
    section_source: str
    outer_radius_mm: float
    inner_radius_mm: float
    propellant_radius_mm: float
    gap_mm: float
    wall_layers: int
    raw_wall_layers: int
    wall_layers_target_min: int
    wall_layers_target_max: int
    end_loose_band_mm: float
    tbd: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def characteristic_source(self) -> str:
        return {
            "reaction_zone": "反应区尺度",
            "min_wall": "最小壁厚",
            "min_defect": "最小几何缺陷",
        }.get(self.driver, self.driver)


def _eccentric_offset(model: MeshModel) -> tuple[float, float, bool]:
    """返回（偏心量绝对值, 偏心方向角, 是否未标定）。

    径向与周向分量合成偏心矢量；方向角缺省时由分量反推，给出时以方向角为准。
    未声明偏心时不做推断：``wall_thickness.min`` / ``max`` 表示轴向 / 名义范围，
    不是周向偏心量（契约 4.2）。
    """
    radial = model.eccentricity_radial
    circumferential = model.eccentricity_circumferential
    if radial is not None or circumferential is not None:
        x = radial if radial is not None else 0.0
        y = circumferential if circumferential is not None else 0.0
        magnitude = math.hypot(x, y)
        direction = model.eccentricity_angle_deg
        if direction is None:
            direction = math.degrees(math.atan2(y, x)) if magnitude > _EPS else 0.0
        return magnitude, direction, False
    return 0.0, 0.0, model.eccentricity_declared


def _eccentric_wall(model: MeshModel, angle_deg: float) -> WallAtAngle:
    offset, direction, uncalibrated = _eccentric_offset(model)
    inner_nominal = model.inner_radius
    if offset <= _EPS:
        return WallAtAngle(angle_deg, model.wall_nominal, "nominal", uncalibrated)
    if offset >= inner_nominal:
        raise ParameshError(f"偏心量 {offset:g} mm 不小于内腔半径 {inner_nominal:g} mm，无法构造内孔")
    radians = math.radians(angle_deg - direction)
    discriminant = inner_nominal * inner_nominal - offset * offset * math.sin(radians) ** 2
    if discriminant < 0.0:  # pragma: no cover - offset < inner_nominal 已保证非负
        raise ParameshError("偏心几何无实数解")
    inner = offset * math.cos(radians) + math.sqrt(discriminant)
    wall = model.outer_radius - inner
    if wall <= _EPS:
        raise ParameshError(f"角度 {angle_deg:g}° 处壁厚非正，几何不成立")
    return WallAtAngle(angle_deg, wall, "eccentricity", uncalibrated)


def _interpolate(points: list[tuple[float, float]], angle_deg: float) -> float:
    """在周向折线上按角度线性插值，支持跨 360° 回绕。"""
    if len(points) == 1:
        return points[0][1]
    angle = angle_deg % 360.0
    first_angle, first_thickness = points[0]
    last_angle, last_thickness = points[-1]
    if angle <= first_angle + _EPS:
        left_angle, left_thickness = last_angle - 360.0, last_thickness
        right_angle, right_thickness = first_angle, first_thickness
    elif angle >= last_angle - _EPS:
        left_angle, left_thickness = last_angle, last_thickness
        right_angle, right_thickness = first_angle + 360.0, first_thickness
    else:
        left_angle, left_thickness = points[0]
        right_angle, right_thickness = points[-1]
        for index in range(1, len(points)):
            if angle <= points[index][0]:
                left_angle, left_thickness = points[index - 1]
                right_angle, right_thickness = points[index]
                break
    span = right_angle - left_angle
    if abs(span) <= _EPS:  # pragma: no cover - 角度严格递增已保证
        return right_thickness
    ratio = (angle - left_angle) / span
    return left_thickness + (right_thickness - left_thickness) * ratio


def _worst_case_wall(model: MeshModel, angle_deg: float) -> WallAtAngle:
    fallback = _eccentric_wall(model, angle_deg)
    if model.wall_min is not None and model.wall_min < fallback.thickness_mm:
        return WallAtAngle(angle_deg, model.wall_min, "worst-case.min", True)
    return WallAtAngle(angle_deg, fallback.thickness_mm, fallback.source, True)


def wall_thickness_at(model: MeshModel, angle_deg: float) -> WallAtAngle:
    """取角度处的壁厚；折线按角度插值，tbd 取最坏情形。"""
    kind = model.circumferential_kind
    if kind == "uniform" and model.circumferential_value is not None:
        return WallAtAngle(angle_deg, model.circumferential_value, "circumferential.uniform", False)
    if kind == "polyline":
        samples = model.circumferential_samples
        concrete = [(angle, thickness) for angle, thickness in samples if thickness is not None]
        if samples and len(concrete) == len(samples):
            return WallAtAngle(
                angle_deg,
                _interpolate(concrete, angle_deg),
                "circumferential.polyline",
                False,
            )
        if concrete:
            worst_angle, worst_thickness = min(concrete, key=lambda item: (item[1], item[0]))
            return WallAtAngle(worst_angle, worst_thickness, "circumferential.polyline", True)
        return _worst_case_wall(model, angle_deg)
    if kind == "tbd":
        return _worst_case_wall(model, angle_deg)
    return _eccentric_wall(model, angle_deg)


def _score(model: MeshModel, angle_deg: float, gap: float) -> float:
    wall = wall_thickness_at(model, angle_deg).thickness_mm
    propellant = model.outer_radius - wall - gap
    if propellant <= _EPS:
        return math.inf
    return min(wall, propellant)


def choose_section_angle(model: MeshModel, config: MeshConfig) -> float:
    """选代表截面：默认取 min(壁厚, 药层厚度) 最小的角度（最坏情形）。"""
    if config.section_angle_deg is not None:
        return config.section_angle_deg
    kind = model.circumferential_kind
    if kind == "polyline":
        samples = model.circumferential_samples
        concrete = [(angle, thickness) for angle, thickness in samples if thickness is not None]
        if concrete and len(concrete) < len(samples):
            return min(concrete, key=lambda item: (item[1], item[0]))[0]
        if not concrete:
            offset, direction, _uncalibrated = _eccentric_offset(model)
            return (direction + 180.0) % 360.0 if offset > _EPS else 0.0
    if kind in ("uniform", "tbd"):
        return 0.0
    gap = model.gap_mm if model.gap_mm is not None else 0.0
    best_angle = 0.0
    best_score = math.inf
    for degree in range(360):
        score = _score(model, float(degree), gap)
        if score < best_score - _EPS:
            best_score = score
            best_angle = float(degree)
    return best_angle


def plan_scale(model: MeshModel, config: MeshConfig, level: str) -> ScalePlan:
    """按特征尺度与档位生成尺度计划；三维未实现时由配置层拒绝。"""
    if level not in LEVELS:
        raise ParameshError(f"未知网格档位：{level!r}，可选 {', '.join(LEVELS)}")
    reaction = config.reaction_zone_mm
    if reaction is None:
        raise ParameshError(
            "网格配置缺少 reaction_zone_mm（反应区尺度），无法按「三者取最小」确定基础尺寸；"
            "请在配置中给出，或使用 example 生成的占位配置"
        )

    angle = choose_section_angle(model, config)
    wall = wall_thickness_at(model, angle)
    gap_known = model.gap_mm is not None
    gap = model.gap_mm if model.gap_mm is not None else 0.0
    propellant_radius = model.outer_radius - wall.thickness_mm - gap
    if propellant_radius <= _EPS:
        raise ParameshError(
            f"代表截面（θ={angle:g}°）壁厚 {wall.thickness_mm:g} mm 与间隙 {gap:g} mm 使药层半径非正，几何不成立"
        )

    min_wall = wall.thickness_mm if model.wall_min is None else min(wall.thickness_mm, model.wall_min)
    defect_sizes = [spec.size_mm for spec in model.defects if spec.size_mm is not None]
    min_defect = min(defect_sizes) if defect_sizes else None

    order = ("reaction_zone", "min_wall", "min_defect")
    candidates: dict[str, float] = {"reaction_zone": reaction, "min_wall": min_wall}
    if min_defect is not None:
        candidates["min_defect"] = min_defect
    driver = min(candidates, key=lambda key: (candidates[key], order.index(key)))
    characteristic = candidates[driver]

    divisor = config.divisor(level)
    global_h = characteristic / divisor
    raw_layers = max(1, math.ceil(wall.thickness_mm / global_h - 1e-9))
    wall_layers = min(max(raw_layers, config.wall_layers_min), config.wall_layers_max)

    fraction = model.end_loose_length_fraction
    end_loose_band = model.length * fraction if fraction is not None else 0.0

    tbd: list[str] = []
    warnings: list[str] = []
    if wall.uncalibrated:
        path = (
            "geometry.wall_thickness.circumferential"
            if model.circumferential_declared
            else "geometry.eccentricity.radial_mm"
        )
        tbd.append(path)
        warnings.append("周向壁厚未标定，按最坏情形（最薄壁 / 最薄药层）加密，报告标注未标定")
    if not gap_known:
        tbd.append("interface.gap_mm")
        warnings.append("间隙未标定，按零间隙生成并标注未标定")
    if model.end_loose_declared and fraction is None:
        tbd.append("density_field.end_loose.length_fraction")
    if model.defects and not defect_sizes:
        tbd.append("density_field.local_defects[].size_mm")
    if raw_layers < config.wall_layers_min:
        warnings.append(f"壁厚方向按最小层数 {config.wall_layers_min} 加密（全局尺寸只能给 {raw_layers} 层）")
    if raw_layers > config.wall_layers_max:
        warnings.append(
            f"壁厚方向按最大层数 {config.wall_layers_max} 限制（全局尺寸本可给 {raw_layers} 层）；"
            "如需更细可调高 wall_layers.max"
        )
    if model.wall_min is not None and wall.thickness_mm > model.wall_min + _EPS:
        warnings.append(
            f"代表截面壁厚 {wall.thickness_mm:g} mm 大于模型最小壁厚 {model.wall_min:g} mm；"
            "特征尺度按较小值计，轴向变化未在二维轴对称中表达"
        )

    return ScalePlan(
        level=level,
        divisor=divisor,
        characteristic_mm=characteristic,
        driver=driver,
        global_h_mm=global_h,
        reaction_zone_mm=reaction,
        min_wall_mm=min_wall,
        min_defect_mm=min_defect,
        section_angle_deg=angle,
        section_wall_mm=wall.thickness_mm,
        section_source=wall.source,
        outer_radius_mm=model.outer_radius,
        inner_radius_mm=model.outer_radius - wall.thickness_mm,
        propellant_radius_mm=propellant_radius,
        gap_mm=gap,
        wall_layers=wall_layers,
        raw_wall_layers=raw_layers,
        wall_layers_target_min=config.wall_layers_min,
        wall_layers_target_max=config.wall_layers_max,
        end_loose_band_mm=end_loose_band,
        tbd=tuple(tbd),
        warnings=tuple(warnings),
    )


def plan_all(model: MeshModel, config: MeshConfig) -> tuple[ScalePlan, ...]:
    """生成三档尺度计划（共享几何与区域划分，仅尺寸等级不同）。"""
    return tuple(plan_scale(model, config, level) for level in LEVELS)


def format_plan(plan: ScalePlan) -> str:
    """给人看的尺度摘要（check / GUI 共用）。"""
    lines = [
        f"档位：{plan.level}（分母 1/{plan.divisor}）",
        f"特征尺度：{plan.characteristic_mm:g} mm（驱动：{plan.characteristic_source}）",
        f"全局尺寸：{plan.global_h_mm:g} mm",
        f"代表截面：θ={plan.section_angle_deg:g}°，壁厚 {plan.section_wall_mm:g} mm（来源：{plan.section_source}）",
        f"壁厚层数：实际 {plan.wall_layers}（全局尺寸原始 {plan.raw_wall_layers} 层）",
        f"药层半径 / 间隙：{plan.propellant_radius_mm:g} / {plan.gap_mm:g} mm",
    ]
    if plan.end_loose_band_mm > 0.0:
        lines.append(f"端部疏松加密带：{plan.end_loose_band_mm:g} mm")
    if plan.tbd:
        lines.append(f"未标定：{', '.join(plan.tbd)}")
    for warning in plan.warnings:
        lines.append(f"警告：{warning}")
    return "\n".join(lines)
