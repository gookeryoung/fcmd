"""二维轴对称（r–z）结构化四边形网格生成。

三个区域（炸药 / 间隙 / 管壁）各自成块，界面共节点；轴向站点三区共享，
便于局部加密带（起爆端、端部疏松段）与下游按集合选面。生成过程不含时间戳，
节点按 ``(z, r)`` 排序编号，结果可复现。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from fcmd.apps.paramesh._config import MeshConfig
from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._model import MeshModel
from fcmd.apps.paramesh._scale import ScalePlan, plan_scale

__all__ = [
    "AXIS_STATION_FRACTIONS",
    "ELEMENT_TYPE",
    "MESH_DIMENSION",
    "SECTION_SOLVER_THICKNESS_MM",
    "SET_AXIS",
    "SET_END_END",
    "SET_END_START",
    "SET_GAP_WALL",
    "SET_IF_PROP_GAP",
    "SET_IF_PROP_WALL",
    "SET_NRB",
    "SET_OUTER",
    "SET_PART_ALL",
    "SET_PART_FLUID",
    "SET_PART_WALL",
    "SET_PROP_GAP",
    "SET_REGION_GAP",
    "SET_REGION_PROP",
    "SET_REGION_WALL",
    "SET_WALL_INNER",
    "Element",
    "Mesh",
    "Node",
    "PartSet",
    "Refinement",
    "RegionInfo",
    "build_mesh",
    "section_choice",
]

MESH_DIMENSION = "axisymmetric-2d"
ELEMENT_TYPE = "shell"

SET_AXIS = "MESH_AXIS"
SET_OUTER = "MESH_OUTER"
SET_END_START = "MESH_END_START"
SET_END_END = "MESH_END_END"
SET_NRB = "MESH_NRB"
SET_PROP_GAP = "MESH_IF_PROP_GAP"
SET_GAP_WALL = "MESH_IF_GAP_WALL"
SET_IF_PROP_GAP = SET_PROP_GAP
SET_IF_PROP_WALL = "MESH_IF_PROP_WALL"
SET_WALL_INNER = "MESH_WALL_INNER"
SET_REGION_PROP = "MESH_REGION_PROP"
SET_REGION_GAP = "MESH_REGION_GAP"
SET_REGION_WALL = "MESH_REGION_WALL"
SET_PART_ALL = "MESH_PART_ALL"
SET_PART_FLUID = "MESH_PART_FLUID"
SET_PART_WALL = "MESH_PART_WALL"

_REGION_PROP = "propellant"
_REGION_GAP = "gap"
_REGION_WALL = "wall"
_PART_IDS = {_REGION_PROP: 1, _REGION_GAP: 2, _REGION_WALL: 3}

_EPS = 1e-12

SECTION_SOLVER_THICKNESS_MM = 1.0
"""``*SECTION_SHELL`` 写入的求解器厚度。

v1 采用轴对称连续体解释：周向单位厚度，区域真实径向厚度由网格分层表达。
"""

AXIS_STATION_FRACTIONS: tuple[float, ...] = (0.25, 0.5, 0.75)
"""轴线探针的轴向站位（占管长比例），生成时强制成为网格站点。"""


@dataclass(frozen=True, slots=True)
class Node:
    """网格节点；二维轴对称下 ``(r, z)`` 对应 LS-DYNA 的 ``(x, y)``。"""

    node_id: int
    r: float
    z: float


@dataclass(frozen=True, slots=True)
class Element:
    """四节点四边形单元；节点逆时针排列。"""

    element_id: int
    part_id: int
    region: str
    nodes: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class RegionInfo:
    """区域统计与解析面积对照。"""

    name: str
    part_id: int
    node_ids: tuple[int, ...]
    element_count: int
    area_mm2: float
    analytic_area_mm2: float

    @property
    def rel_error(self) -> float:
        if self.analytic_area_mm2 <= _EPS:
            return 0.0
        return (self.area_mm2 - self.analytic_area_mm2) / self.analytic_area_mm2


@dataclass(frozen=True, slots=True)
class Refinement:
    """局部加密记录。"""

    zone: str
    applied: bool
    factor: float | None
    regions: tuple[str, ...]
    note: str


@dataclass(frozen=True, slots=True)
class PartSet:
    """部件集合（对应 LS-DYNA ``*SET_PART_LIST``）。"""

    name: str
    part_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Mesh:
    """一档网格及其区域、集合与加密记录。"""

    model: MeshModel
    level: str
    plan: ScalePlan
    nodes: tuple[Node, ...]
    elements: tuple[Element, ...]
    regions: tuple[RegionInfo, ...]
    node_sets: dict[str, tuple[int, ...]]
    part_sets: tuple[PartSet, ...]
    refinements: tuple[Refinement, ...]
    warnings: tuple[str, ...]

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def element_count(self) -> int:
        return len(self.elements)

    @property
    def dimension(self) -> str:
        return MESH_DIMENSION


def section_choice(mesh: Mesh) -> tuple[float, float]:
    """代表截面角度与壁厚，供报告使用。"""
    return mesh.plan.section_angle_deg, mesh.plan.section_wall_mm


def region_thickness_mm(plan: ScalePlan, region_name: str) -> float:
    """区域在代表截面处的径向总厚度（不是求解器壳厚）。"""
    if region_name == _REGION_PROP:
        return plan.propellant_radius_mm
    if region_name == _REGION_GAP:
        return plan.gap_mm
    return plan.section_wall_mm


def _round_coord(value: float) -> float:
    return round(value, 9) + 0.0


def _graded_positions(start: float, end: float, h_first: float, h_max: float, growth: float) -> list[float]:
    """从一个细端出发，按几何增长过渡到 ``h_max``，覆盖到另一端。"""
    if h_first <= _EPS or h_max <= _EPS:
        raise ParameshError("网格尺寸必须为正")
    direction = 1.0 if end >= start else -1.0
    positions = [start]
    current = start
    spacing = min(h_first, h_max)
    while abs(end - current) > _EPS:
        remaining = abs(end - current)
        step = min(spacing, remaining)
        if remaining - step < 0.25 * step:
            step = remaining
        current += direction * step
        if current > end and direction > 0:
            current = end
        if current < end and direction < 0:
            current = end
        positions.append(current)
        spacing = min(spacing * growth, h_max)
    if direction < 0:
        positions.reverse()
    return positions


def _uniform_positions(start: float, end: float, count: int) -> list[float]:
    if count < 1:
        raise ParameshError("区域径向层数必须 ≥ 1")
    return [start + (end - start) * index / count for index in range(count + 1)]


def _include_stations(positions: list[float], stations: tuple[float, ...], length: float) -> list[float]:
    """把探针站位强制并入轴向站点，三档都落在完全相同的物理坐标。"""
    forced = sorted({round(min(max(value, 0.0), length), 9) for value in stations if _EPS < value < length})
    if not forced:
        return positions
    tolerance = 1e-9 * max(1.0, length)
    kept = [value for value in positions if all(abs(value - station) > tolerance for station in forced)]
    kept.extend(forced)
    return sorted(set(kept))


def _axial_positions(  # noqa: PLR0913, PLR0917 - 分段加密参数显式罗列
    length: float,
    h: float,
    factor: float,
    start_band: float,
    end_band: float,
    growth: float,
    stations: tuple[float, ...] = (),
) -> list[float]:
    """轴向站点：起爆端与端部疏松带内保持细尺寸，离开后按几何增长回落。"""
    start_band = min(max(start_band, 0.0), length / 2.0)
    end_band = min(max(end_band, 0.0), length / 2.0)
    positions = [0.0]
    z = 0.0
    spacing = h * factor if start_band > 0.0 else h
    while z < length - _EPS:
        remaining = length - z
        if start_band > 0.0 and z < start_band - _EPS:
            spacing = h * factor
        elif end_band > 0.0 and remaining > end_band + _EPS:
            spacing = min(spacing * growth, h)
        elif end_band > 0.0:
            spacing = max(spacing / growth, h * factor)
        else:
            spacing = min(spacing * growth, h)
        step = min(spacing, remaining)
        if remaining - step < 0.25 * step:
            step = remaining
        z = min(z + step, length)
        positions.append(z)
    if len(positions) < 2:  # pragma: no cover - length > 0 已保证
        positions.append(length)
    return _include_stations(positions, stations, length)


def _unique(items: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def build_mesh(model: MeshModel, config: MeshConfig, level: str) -> Mesh:  # noqa: PLR0912
    """生成一档二维轴对称网格。"""
    plan = plan_scale(model, config, level)
    if model.length <= _EPS:
        raise ParameshError("geometry.length 必须 > 0 才能生成网格")
    if plan.section_wall_mm <= _EPS:  # pragma: no cover - plan_scale 已保证
        raise ParameshError("代表截面壁厚必须 > 0")

    h = plan.global_h_mm
    factor = config.refinement_factor
    axial = _axial_positions(
        model.length,
        h,
        factor,
        config.initiation_zone_mm or 0.0,
        plan.end_loose_band_mm,
        config.growth,
        tuple(fraction * model.length for fraction in AXIS_STATION_FRACTIONS),
    )
    propellant_radial = _graded_positions(plan.propellant_radius_mm, 0.0, h * factor, h, config.growth)
    radial_by_region: list[tuple[str, list[float]]] = [(_REGION_PROP, propellant_radial)]
    if plan.gap_mm > _EPS:
        gap_layers = max(1, math.ceil(plan.gap_mm / h - 1e-9))
        radial_by_region.append(
            (_REGION_GAP, _uniform_positions(plan.propellant_radius_mm, plan.inner_radius_mm, gap_layers))
        )
    wall_radial = _uniform_positions(plan.inner_radius_mm, model.outer_radius, plan.wall_layers)
    radial_by_region.append((_REGION_WALL, wall_radial))

    coordinates: set[tuple[float, float]] = set()
    for _, radial in radial_by_region:
        for r in radial:
            for z in axial:
                coordinates.add((_round_coord(r), _round_coord(z)))
    ordered = sorted(coordinates, key=lambda item: (item[1], item[0]))
    node_ids = {coord: index + 1 for index, coord in enumerate(ordered)}
    nodes = tuple(Node(node_id=index + 1, r=coord[0], z=coord[1]) for index, coord in enumerate(ordered))

    elements: list[Element] = []
    region_nodes: dict[str, set[int]] = {name: set() for name, _ in radial_by_region}
    region_elements: dict[str, int] = {name: 0 for name, _ in radial_by_region}
    region_area: dict[str, float] = {name: 0.0 for name, _ in radial_by_region}
    element_id = 0
    for name, radial in radial_by_region:
        part_id = _PART_IDS[name]
        for radial_index in range(len(radial) - 1):
            for axial_index in range(len(axial) - 1):
                corners = (
                    (_round_coord(radial[radial_index]), _round_coord(axial[axial_index])),
                    (_round_coord(radial[radial_index + 1]), _round_coord(axial[axial_index])),
                    (_round_coord(radial[radial_index + 1]), _round_coord(axial[axial_index + 1])),
                    (_round_coord(radial[radial_index]), _round_coord(axial[axial_index + 1])),
                )
                ids = (
                    node_ids[corners[0]],
                    node_ids[corners[1]],
                    node_ids[corners[2]],
                    node_ids[corners[3]],
                )
                element_id += 1
                elements.append(Element(element_id=element_id, part_id=part_id, region=name, nodes=ids))
                region_nodes[name].update(ids)
                region_elements[name] += 1
                region_area[name] += _quad_area(corners)

    if any(count == 0 for count in region_elements.values()):  # pragma: no cover - 构造保证
        raise ParameshError("存在空区域，无法输出网格")

    tolerance = 1e-7 * max(1.0, model.outer_radius, model.length)
    node_sets: dict[str, tuple[int, ...]] = {}
    _add_node_set(node_sets, SET_AXIS, nodes, lambda node: abs(node.r) <= tolerance)
    _add_node_set(node_sets, SET_OUTER, nodes, lambda node: abs(node.r - model.outer_radius) <= tolerance)
    _add_node_set(node_sets, SET_END_START, nodes, lambda node: abs(node.z) <= tolerance)
    _add_node_set(node_sets, SET_END_END, nodes, lambda node: abs(node.z - model.length) <= tolerance)
    _add_node_set(node_sets, SET_NRB, nodes, lambda node: abs(node.z - model.length) <= tolerance)
    _add_node_set(node_sets, SET_WALL_INNER, nodes, lambda node: abs(node.r - plan.inner_radius_mm) <= tolerance)
    if plan.gap_mm > _EPS:
        _add_node_set(
            node_sets,
            SET_PROP_GAP,
            nodes,
            lambda node: abs(node.r - plan.propellant_radius_mm) <= tolerance,
        )
        _add_node_set(node_sets, SET_GAP_WALL, nodes, lambda node: abs(node.r - plan.inner_radius_mm) <= tolerance)
    else:
        _add_node_set(
            node_sets,
            SET_IF_PROP_WALL,
            nodes,
            lambda node: abs(node.r - plan.inner_radius_mm) <= tolerance,
        )
    for name, _ in radial_by_region:
        set_name = {
            _REGION_PROP: SET_REGION_PROP,
            _REGION_GAP: SET_REGION_GAP,
            _REGION_WALL: SET_REGION_WALL,
        }[name]
        node_sets[set_name] = tuple(sorted(region_nodes[name]))

    parts: list[PartSet] = []
    fluid_parts = tuple(_PART_IDS[name] for name, _ in radial_by_region if name != _REGION_WALL)
    parts.append(PartSet(name=SET_PART_ALL, part_ids=tuple(sorted(_PART_IDS[name] for name, _ in radial_by_region))))
    parts.append(PartSet(name=SET_PART_FLUID, part_ids=fluid_parts))
    parts.append(PartSet(name=SET_PART_WALL, part_ids=(_PART_IDS[_REGION_WALL],)))

    regions: list[RegionInfo] = []
    for name, _ in radial_by_region:
        if name == _REGION_PROP:
            analytic = plan.propellant_radius_mm * model.length
        elif name == _REGION_GAP:
            analytic = plan.gap_mm * model.length
        else:
            analytic = (model.outer_radius - plan.inner_radius_mm) * model.length
        regions.append(
            RegionInfo(
                name=name,
                part_id=_PART_IDS[name],
                node_ids=tuple(sorted(region_nodes[name])),
                element_count=region_elements[name],
                area_mm2=region_area[name],
                analytic_area_mm2=analytic,
            )
        )

    regions_tuple = tuple(regions)
    refinements = _refinements(model, config, plan)
    warnings = list(model.warnings) + list(plan.warnings)
    if plan.gap_mm <= _EPS:
        warnings.append("无间隙区域：药层与管壁共节点，报告按零间隙标注")
    elif plan.gap_mm < 0.1 * h:
        warnings.append(f"间隙层 {plan.gap_mm:g} mm 远小于全局尺寸 {h:g} mm：间隙区只有一层，长宽比偏大属薄层几何固有")
    if plan.propellant_radius_mm < 2.0 * h * factor:
        warnings.append("药层径向层数很少，已按界面加密保留下限")
    if plan.section_wall_mm <= 2.0 * h:
        warnings.append("最薄壁厚与全局尺寸接近，壁厚方向依赖最小层数保证")

    return Mesh(
        model=model,
        level=level,
        plan=plan,
        nodes=nodes,
        elements=tuple(elements),
        regions=regions_tuple,
        node_sets=node_sets,
        part_sets=tuple(parts),
        refinements=refinements,
        warnings=_unique(warnings),
    )


def _add_node_set(
    target: dict[str, tuple[int, ...]],
    name: str,
    nodes: tuple[Node, ...],
    predicate: Callable[[Node], bool],
) -> None:
    ids = tuple(node.node_id for node in nodes if predicate(node))
    if ids:
        target[name] = ids


def _quad_area(
    corners: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]],
) -> float:
    """四边形（r, z）平面面积，逆时针为正。"""
    total = 0.0
    for index in range(4):
        r0, z0 = corners[index]
        r1, z1 = corners[(index + 1) % 4]
        total += r0 * z1 - r1 * z0
    return total / 2.0


def _refinements(model: MeshModel, config: MeshConfig, plan: ScalePlan) -> tuple[Refinement, ...]:
    factor = config.refinement_factor
    entries = [
        Refinement(
            zone="initiation_growth",
            applied=(config.initiation_zone_mm or 0.0) > 0.0,
            factor=factor if (config.initiation_zone_mm or 0.0) > 0.0 else None,
            regions=(_REGION_PROP, _REGION_GAP, _REGION_WALL),
            note="起爆端成长区轴向加密",
        ),
        Refinement(
            zone="end_loose",
            applied=plan.end_loose_band_mm > 0.0,
            factor=factor if plan.end_loose_band_mm > 0.0 else None,
            regions=(_REGION_PROP, _REGION_GAP, _REGION_WALL),
            note="端部疏松段轴向加密",
        ),
        Refinement(
            zone="propellant_interface",
            applied=True,
            factor=factor,
            regions=(_REGION_PROP,),
            note="药管界面（药层侧）径向加密",
        ),
        Refinement(
            zone="wall_inner_surface",
            applied=plan.wall_layers > plan.raw_wall_layers,
            factor=None,
            regions=(_REGION_WALL,),
            note=f"管壁内表面按实际 {plan.wall_layers} 层保证，注入最小层数",
        ),
        Refinement(
            zone="thin_layer",
            applied=any(spec.kind == "thin_layer" for spec in model.defects)
            or (
                config.thin_layer_threshold_mm is not None
                and plan.propellant_radius_mm < config.thin_layer_threshold_mm
            ),
            factor=factor,
            regions=(_REGION_PROP,),
            note="最薄药层位于药管界面，按界面径向加密",
        ),
        Refinement(
            zone="eccentric_near_wall",
            applied=model.eccentricity_declared or model.circumferential_declared,
            factor=None,
            regions=(_REGION_WALL,),
            note=f"偏心近壁侧取代表截面 θ={plan.section_angle_deg:g}°",
        ),
    ]
    return tuple(entries)
