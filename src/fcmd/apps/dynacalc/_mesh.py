"""paramesh 网格包（``paramesh-mesh/1``）读取、校验与 deck 派生值（纯逻辑）。

网格包目录：``<mesh_root>/<mesh_id>/{manifest.json, mesh.k, quality.json, quality.txt}``。
本模块只读不写，按评审文档 §6.1 逐项校验；任一失败即 :class:`MeshError`，绝不
生成半成品 deck。关键口径：

- ``*SECTION_SHELL`` 厚度只能取 ``parts[].section.thickness_mm``（固定 ``1.0``，
  轴对称连续体周向单位厚度）；``region_thickness_mm`` 只用于核对，不写卡片。
- ``quality.mass_check.ratio``（单位密度下按求解器厚度折算体积 / 解析体积）必须
  接近 ``1.0``；明显偏离说明厚度语义被误用，直接拒绝。
- 坐标固定 ``x = 轴向、y = 径向、z = 0``，与 ELFORM=14 以 x 轴为对称轴一致。
- 相邻网格误差只比较 ``geometry_key`` 相同的档位（:func:`compare_levels`）。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "AXISYMMETRIC_DIMENSION",
    "FREE_END_NODE_SET",
    "LEVEL_ORDER",
    "MASS_RATIO_TOLERANCE",
    "MESH_SCHEMA",
    "ROLE_MATERIALS",
    "LevelReport",
    "LevelRow",
    "MeshError",
    "MeshPackage",
    "MeshSet",
    "NodeSetSpec",
    "PartSetSpec",
    "PartSpec",
    "ProbeSpec",
    "SectionSpec",
    "compare_levels",
    "format_level_report",
    "load_package",
    "scan_mesh_root",
]

MESH_SCHEMA = "paramesh-mesh/1"
AXISYMMETRIC_DIMENSION = "axisymmetric-2d"
ELEMENT_TYPE = "shell"
ELFORM = 14
UNIT_THICKNESS_MM = 1.0
THICKNESS_SEMANTICS = "unit_out_of_plane"

MASS_RATIO_TOLERANCE = 1e-2
"""``quality.mass_check.ratio`` 允许偏离 1.0 的幅度（1%）；误用区域总厚度约 9.855 倍。"""

COORDINATE_SYSTEM = {
    "symmetry_axis": "x",
    "x": "axial",
    "y": "radial",
    "z": "out_of_plane",
}

ROLE_MATERIALS: Mapping[str, tuple[int, int]] = {
    "explosive": (1, 1),
    "wall": (2, 0),
    "air": (3, 3),
}
"""部件角色 → (MID, EOSID)；与内置模板族的材料卡编号一致。"""

NODE_SET_ID_RANGE = range(1, 100)
PART_SET_ID_RANGE = range(101, 200)

FREE_END_NODE_SET = "MESH_END_END"
"""自由端节点集合名（v1 稳定接口；``MESH_NRB`` 为预留名，不据此施加无反射）。"""

REQUIRED_PROBES = (
    "axis_start",
    "axis_station_025",
    "axis_station_050",
    "axis_station_075",
    "axis_end",
)
"""稳态段爆速与到达时程所需的最小探针集合。"""

LEVEL_ORDER = ("coarse", "medium", "fine")

_CONVERGENCE_SLACK = 0.05
"""档间面积误差允许的反弹幅度（工程建议值，不是物理常数）。"""

_EPS = 1e-12


class MeshError(ValueError):
    """网格包不可用：消息为可直接展示给用户的中文原因。"""


@dataclass(frozen=True)
class SectionSpec:
    """部件截面：``secid`` / ``elform`` / 求解器厚度 / 区域径向总厚度。"""

    secid: int
    elform: int
    thickness_mm: float
    thickness_semantics: str
    region_thickness_mm: float


@dataclass(frozen=True)
class PartSpec:
    """部件：编号、角色与截面；MID / EOSID 由 deck 按角色映射。"""

    part_id: int
    name: str
    role: str
    section: SectionSpec

    @property
    def mid(self) -> int:
        """材料号：``explosive→1`` / ``wall→2`` / ``air→3``。"""
        return ROLE_MATERIALS[self.role][0]

    @property
    def eosid(self) -> int:
        """状态方程号；``wall`` 不用 EOS，记 0。"""
        return ROLE_MATERIALS[self.role][1]


@dataclass(frozen=True)
class ProbeSpec:
    """输出探针：名称、节点号与物理坐标（r–z）。"""

    name: str
    node_id: int
    r_mm: float
    z_mm: float


@dataclass(frozen=True)
class NodeSetSpec:
    """节点集合：稳定名、数值 SID 与成员数。"""

    name: str
    sid: int
    count: int


@dataclass(frozen=True)
class PartSetSpec:
    """部件集合：稳定名、数值 SID 与部件号。"""

    name: str
    sid: int
    part_ids: tuple[int, ...]


@dataclass(frozen=True)
class MeshPackage:
    """一个已校验的网格包。"""

    directory: Path
    manifest: Mapping[str, Any]
    mesh_id: str
    digest: str
    geometry_key: str
    dimension: str
    level: str
    model_id: str
    model_digest: str
    parts: tuple[PartSpec, ...]
    probes: tuple[ProbeSpec, ...]
    node_sets: Mapping[str, NodeSetSpec]
    part_sets: Mapping[str, PartSetSpec]
    regions: Mapping[str, float]
    extent: Mapping[str, float]
    detonation: Mapping[str, float]
    mass_check: Mapping[str, float]
    mesh_file: Path
    warnings: tuple[str, ...] = ()

    def deck_values(self) -> dict[str, Any]:
        """模板族占位符取值：网格引用、部件 / 截面块、起爆点与探针节点号。"""
        nodes = sorted({item.node_id for item in self.probes})
        return {
            "dimension": self.dimension,
            "mesh_id": self.mesh_id,
            "mesh_digest": self.digest,
            "mesh_level": self.level,
            "mesh_geometry_key": self.geometry_key,
            "mesh_include": f"../meshes/{self.mesh_id}/mesh.k",
            "history_nodes": ", ".join(str(node) for node in nodes),
            "parts_block": _parts_block(self.parts),
            "sections_block": _sections_block(self.parts),
            "detonation_x": self.detonation["x"],
            "detonation_y": self.detonation["y"],
            "detonation_z": self.detonation["z"],
            "detonation_t0": self.detonation["t0"],
        }


@dataclass(frozen=True)
class MeshSet:
    """网格根目录下的全部网格包。"""

    packages: tuple[MeshPackage, ...]
    warnings: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.packages)

    def levels_for(self, model_digest: str) -> tuple[str, ...]:
        """某模型在网格根下已有的档位（按 coarse→medium→fine 排序）。"""
        levels = {item.level for item in self.packages if item.model_digest == model_digest}
        return tuple(level for level in LEVEL_ORDER if level in levels)

    def for_model(self, model_digest: str, *, level: str | None = None) -> MeshPackage:
        """按模型指纹（与可选档位）选择唯一网格包；歧义即报错。"""
        matches = [item for item in self.packages if item.model_digest == model_digest]
        if not matches:
            raise MeshError(f"没有与模型指纹 {model_digest[:12]} 对应的网格包，请先运行 paramesh")
        groups = {item.geometry_key for item in matches}
        if len(groups) > 1:
            raise MeshError(
                f"模型 {model_digest[:12]} 存在多个 geometry_key（{len(groups)} 个），"
                "拒绝跨几何组装 deck；请清理 meshes 根目录"
            )
        if level is not None:
            selected = [item for item in matches if item.level == level]
            if not selected:
                available = " / ".join(self.levels_for(model_digest)) or "无"
                raise MeshError(f"模型 {model_digest[:12]} 没有档位 {level}（可选：{available}）")
            matches = selected
        if len(matches) > 1:
            available = " / ".join(sorted({item.level for item in matches}))
            raise MeshError(f"模型 {model_digest[:12]} 有多个档位（{available}），请显式指定 --mesh-level")
        return matches[0]

    def compare_levels(self, model_digest: str) -> LevelReport:
        """对某模型的全部档位做档间网格误差比较。"""
        packages = tuple(item for item in self.packages if item.model_digest == model_digest)
        if not packages:
            raise MeshError(f"没有与模型指纹 {model_digest[:12]} 对应的网格包")
        return compare_levels(packages)


@dataclass(frozen=True)
class LevelRow:
    """档位误差比较中的一行：区域名、档位与面积相对误差。"""

    region: str
    level: str
    area_rel_error: float


@dataclass(frozen=True)
class LevelReport:
    """同 ``geometry_key`` 多档网格的误差比较结论。"""

    geometry_key: str
    levels: tuple[str, ...]
    rows: tuple[LevelRow, ...]
    mass_ratios: tuple[tuple[str, float], ...]
    detail: str

    @property
    def convergent(self) -> bool:
        """各区域面积误差是否随档位加密不增（允许 5% 反弹）。"""
        return self.detail.startswith("收敛")


def _fmt(value: float) -> str:
    """deck 数值文本：最短可复现表示。"""
    return "0.0" if abs(value) < 5e-13 else repr(float(value))


def _parts_block(parts: Sequence[PartSpec]) -> str:
    """``*PART`` 块：pid / secid 取自契约，MID / EOSID 按角色映射。"""
    lines: list[str] = []
    for part in parts:
        lines.append("*PART")
        lines.append(part.name)
        lines.append("$ pid, secid, mid, eosid, hgid")
        lines.append(f"{part.part_id}, {part.section.secid}, {part.mid}, {part.eosid}, 0")
    return "\n".join(lines)


def _sections_block(parts: Sequence[PartSpec]) -> str:
    """``*SECTION_SHELL`` 块：ELFORM=14，厚度只写 ``section.thickness_mm``。"""
    lines: list[str] = []
    for part in parts:
        section = part.section
        lines.append("*SECTION_SHELL")
        lines.append("$ secid, elform, shrf, nip, propt, qr, icomp, setyp")
        lines.append(f"{section.secid}, {section.elform}, 1.0, 0.0, 1.0, 0.0, 0, 1")
        lines.append("$ t1, t2, t3, t4（轴对称连续体周向单位厚度；区域径向厚度由网格分层表达）")
        thickness = _fmt(section.thickness_mm)
        lines.append(f"{thickness}, {thickness}, {thickness}, {thickness}")
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 校验辅助
# ---------------------------------------------------------------------- #
def _mapping(raw: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise MeshError(f"{where} 必须是对象")
    return raw


def _text(raw: Mapping[str, Any], key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MeshError(f"{where}.{key} 必须是非空文本")
    if "\n" in value or "\r" in value:
        raise MeshError(f"{where}.{key} 不能含换行")
    return value.strip()


def _integer(raw: Mapping[str, Any], key: str, where: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MeshError(f"{where}.{key} 必须是整数，实际为 {value!r}")
    return value


def _number(raw: Mapping[str, Any], key: str, where: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeshError(f"{where}.{key} 必须是数值，实际为 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise MeshError(f"{where}.{key} 必须是有限数值，实际为 {value!r}")
    return number


def _text_list(raw: Any, where: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise MeshError(f"{where} 必须是文本列表")
    for item in raw:
        if not isinstance(item, str):
            raise MeshError(f"{where} 含非文本条目: {item!r}")
    return tuple(str(item) for item in raw)


def _check_coordinate_system(raw: Any, mesh_id: str) -> None:
    system = _mapping(raw, f"{mesh_id} coordinate_system")
    for key, expected in COORDINATE_SYSTEM.items():
        actual = system.get(key)
        if actual != expected:
            raise MeshError(
                f"网格包 {mesh_id} 的 coordinate_system.{key} 应为 {expected!r}，实际为 {actual!r}；"
                "axisymmetric-2d 只接受 x=轴向 / y=径向 / z=0"
            )


def _check_section(raw: Any, part_id: int, mesh_id: str) -> SectionSpec:
    section = _mapping(raw, f"{mesh_id} parts[{part_id}].section")
    secid = _integer(section, "secid", f"{mesh_id} parts[{part_id}].section")
    if secid != part_id:
        raise MeshError(f"网格包 {mesh_id} 的部件 {part_id} 的 section.secid 应为 {part_id}，实际为 {secid}")
    elform = _integer(section, "elform", f"{mesh_id} parts[{part_id}].section")
    if elform != ELFORM:
        raise MeshError(f"网格包 {mesh_id} 的部件 {part_id} 的 section.elform 应为 {ELFORM}，实际为 {elform}")
    thickness = _number(section, "thickness_mm", f"{mesh_id} parts[{part_id}].section")
    if abs(thickness - UNIT_THICKNESS_MM) > 1e-9:
        raise MeshError(
            f"网格包 {mesh_id} 的部件 {part_id} 的 section.thickness_mm 应为 {UNIT_THICKNESS_MM}，"
            f"实际为 {thickness!r}；区域总厚度不得写入 *SECTION_SHELL"
        )
    semantics = section.get("thickness_semantics")
    if semantics != THICKNESS_SEMANTICS:
        raise MeshError(
            f"网格包 {mesh_id} 的部件 {part_id} 的 section.thickness_semantics 应为 "
            f"{THICKNESS_SEMANTICS!r}，实际为 {semantics!r}"
        )
    region_thickness = _number(section, "region_thickness_mm", f"{mesh_id} parts[{part_id}].section")
    if region_thickness <= 0:
        raise MeshError(f"网格包 {mesh_id} 的部件 {part_id} 的 section.region_thickness_mm 必须为正")
    return SectionSpec(
        secid=secid,
        elform=elform,
        thickness_mm=thickness,
        thickness_semantics=str(semantics),
        region_thickness_mm=region_thickness,
    )


def _check_parts(raw: Any, mesh_id: str) -> tuple[PartSpec, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise MeshError(f"网格包 {mesh_id} 的 parts 必须是非空列表")
    parts: list[PartSpec] = []
    seen: set[int] = set()
    names: set[str] = set()
    for index, item in enumerate(raw, 1):
        entry = _mapping(item, f"{mesh_id} parts[{index}]")
        part_id = _integer(entry, "part_id", f"{mesh_id} parts[{index}]")
        if part_id <= 0 or part_id in seen:
            raise MeshError(f"网格包 {mesh_id} 的 parts[{index}].part_id 非法或重复: {part_id}")
        seen.add(part_id)
        name = _text(entry, "name", f"{mesh_id} parts[{index}]")
        if name in names:
            raise MeshError(f"网格包 {mesh_id} 的部件名重复: {name}")
        names.add(name)
        role = _text(entry, "role", f"{mesh_id} parts[{index}]")
        if role not in ROLE_MATERIALS:
            raise MeshError(
                f"网格包 {mesh_id} 的部件 {name} 角色 {role!r} 尚无材料卡映射（当前支持：{' / '.join(ROLE_MATERIALS)}）"
            )
        element_type = _text(entry, "element_type", f"{mesh_id} parts[{index}]")
        if element_type != ELEMENT_TYPE:
            raise MeshError(
                f"网格包 {mesh_id} 的部件 {name} 的 element_type 应为 {ELEMENT_TYPE!r}，实际为 {element_type!r}"
            )
        section = _check_section(entry.get("section"), part_id, mesh_id)
        parts.append(PartSpec(part_id=part_id, name=name, role=role, section=section))
    return tuple(parts)


def _check_probes(raw: Any, mesh_id: str) -> tuple[ProbeSpec, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise MeshError(f"网格包 {mesh_id} 的 probes 必须是非空列表")
    probes: list[ProbeSpec] = []
    names: set[str] = set()
    nodes: set[int] = set()
    for index, item in enumerate(raw, 1):
        entry = _mapping(item, f"{mesh_id} probes[{index}]")
        name = _text(entry, "name", f"{mesh_id} probes[{index}]")
        if name in names:
            raise MeshError(f"网格包 {mesh_id} 的探针名重复: {name}")
        names.add(name)
        node_id = _integer(entry, "node_id", f"{mesh_id} probes[{index}]")
        if node_id <= 0:
            raise MeshError(f"网格包 {mesh_id} 的探针 {name} 的 node_id 必须为正")
        if node_id in nodes:
            raise MeshError(f"网格包 {mesh_id} 的探针节点号重复: {node_id}")
        nodes.add(node_id)
        probes.append(
            ProbeSpec(
                name=name,
                node_id=node_id,
                r_mm=_number(entry, "r_mm", f"{mesh_id} probes[{index}]"),
                z_mm=_number(entry, "z_mm", f"{mesh_id} probes[{index}]"),
            )
        )
    missing = [name for name in REQUIRED_PROBES if name not in names]
    if missing:
        raise MeshError(f"网格包 {mesh_id} 缺少稳态段 / 端面探针: {', '.join(missing)}")
    return tuple(probes)


def _check_sets(
    raw: Any,
    mesh_id: str,
    part_ids: set[int],
) -> tuple[dict[str, NodeSetSpec], dict[str, PartSetSpec]]:
    """集合段：SID 段位、数量与部件引用；自由端集合必须存在。"""
    sets = _mapping(raw, f"{mesh_id} sets")
    node_sets: dict[str, NodeSetSpec] = {}
    for name, item in _mapping(sets.get("nodes", {}), f"{mesh_id} sets.nodes").items():
        where = f"{mesh_id} sets.nodes.{name}"
        entry = _mapping(item, where)
        sid = _integer(entry, "id", where)
        if sid not in NODE_SET_ID_RANGE:
            raise MeshError(f"{where}.id 必须在 1–99，实际为 {sid}")
        count = _integer(entry, "count", where)
        if count <= 0:
            raise MeshError(f"{where}.count 必须为正，实际为 {count}")
        node_sets[str(name)] = NodeSetSpec(name=str(name), sid=sid, count=count)
    part_sets: dict[str, PartSetSpec] = {}
    for name, item in _mapping(sets.get("parts", {}), f"{mesh_id} sets.parts").items():
        where = f"{mesh_id} sets.parts.{name}"
        entry = _mapping(item, where)
        sid = _integer(entry, "id", where)
        if sid not in PART_SET_ID_RANGE:
            raise MeshError(f"{where}.id 必须在 101–199，实际为 {sid}")
        raw_ids = entry.get("part_ids")
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)) or not raw_ids:
            raise MeshError(f"{where}.part_ids 必须是非空列表")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in raw_ids):
            raise MeshError(f"{where}.part_ids 必须是整数列表")
        ids = tuple(int(value) for value in raw_ids)
        unknown = sorted(set(ids) - part_ids)
        if unknown:
            raise MeshError(f"{where}.part_ids 引用了不存在的部件: {unknown}")
        part_sets[str(name)] = PartSetSpec(name=str(name), sid=sid, part_ids=ids)
    if FREE_END_NODE_SET not in node_sets:
        raise MeshError(f"网格包 {mesh_id} 缺少 deck 引用的节点集合 {FREE_END_NODE_SET}")
    return node_sets, part_sets


def _check_mass(raw: Any, mesh_id: str, tolerance: float) -> dict[str, float]:
    """质量自检：单位密度下按求解器厚度折算体积 / 解析体积必须 ≈ 1。"""
    where = f"{mesh_id} quality.mass_check"
    quality = _mapping(raw, f"{mesh_id} quality")
    mass = _mapping(quality.get("mass_check"), where)
    if mass.get("basis") != "unit_density":
        raise MeshError(f"{where}.basis 应为 'unit_density'，实际为 {mass.get('basis')!r}")
    thickness = _number(mass, "section_thickness_mm", where)
    if abs(thickness - UNIT_THICKNESS_MM) > 1e-9:
        raise MeshError(f"{where}.section_thickness_mm 应为 {UNIT_THICKNESS_MM}，实际为 {thickness!r}")
    analytic = _number(mass, "analytic_volume_mm3", where)
    mesh_volume = _number(mass, "mesh_volume_mm3", where)
    ratio = _number(mass, "ratio", where)
    if analytic <= 0 or mesh_volume <= 0:
        raise MeshError(f"{where} 的解析体积与网格体积必须为正")
    if abs(ratio - 1.0) > tolerance:
        raise MeshError(
            f"网格包 {mesh_id} 的质量自检比值 {ratio:.6g} 偏离 1.0 超过 {tolerance:g}；"
            "疑似把区域总厚度写入 *SECTION_SHELL，拒绝出卡"
        )
    return {
        "section_thickness_mm": thickness,
        "analytic_volume_mm3": analytic,
        "mesh_volume_mm3": mesh_volume,
        "ratio": ratio,
        "region_thickness_ratio": _number(mass, "region_thickness_ratio", where),
    }


def _check_regions(raw: Any, mesh_id: str) -> dict[str, float]:
    """区域表：只取区域名与面积相对误差（档间比较用）。"""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise MeshError(f"网格包 {mesh_id} 的 regions 必须是非空列表")
    regions: dict[str, float] = {}
    for index, item in enumerate(raw, 1):
        where = f"{mesh_id} regions[{index}]"
        entry = _mapping(item, where)
        name = _text(entry, "name", where)
        if name in regions:
            raise MeshError(f"网格包 {mesh_id} 的区域名重复: {name}")
        error = _number(entry, "area_rel_error", where)
        if error < 0:
            raise MeshError(f"{where}.area_rel_error 不能为负")
        regions[name] = error
    return regions


def load_package(
    directory: Path,
    *,
    model_digest: str = "",
    dimension: str = AXISYMMETRIC_DIMENSION,
    mass_ratio_tolerance: float = MASS_RATIO_TOLERANCE,
) -> MeshPackage:
    """读取并校验一个网格包目录；任一不满足即 :class:`MeshError`。

    ``model_digest`` 非空时要求与 ``manifest.model.digest`` 一致（网格与工况
    必须出自同一模型）；``dimension`` 只接受已支持的维度，不静默回落 3D。
    """
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MeshError(f"网格包缺少清单 {manifest_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MeshError(f"网格清单 {manifest_path} 无法读取: {exc}") from exc
    manifest = _mapping(raw, str(manifest_path))
    if manifest.get("schema") != MESH_SCHEMA:
        raise MeshError(f"网格清单 schema 应为 {MESH_SCHEMA}，实际为 {manifest.get('schema')!r}")
    if manifest.get("dimension") != dimension:
        raise MeshError(
            f"网格包 {directory.name} 的 dimension 应为 {dimension!r}，"
            f"实际为 {manifest.get('dimension')!r}；未支持维度不回落 3D 模板"
        )
    mesh_id = _text(manifest, "mesh_id", str(manifest_path))
    if mesh_id != directory.name:
        raise MeshError(f"网格包目录名 {directory.name!r} 与 manifest.mesh_id {mesh_id!r} 不一致")
    _check_coordinate_system(manifest.get("coordinate_system"), mesh_id)
    digest = _text(manifest, "digest", f"{mesh_id} manifest")
    geometry_key = _text(manifest, "geometry_key", f"{mesh_id} manifest")
    level = _text(manifest, "level", f"{mesh_id} manifest")
    if level not in LEVEL_ORDER:
        raise MeshError(f"网格包 {mesh_id} 的 level 必须是 {' / '.join(LEVEL_ORDER)}，实际为 {level!r}")
    model = _mapping(manifest.get("model"), f"{mesh_id} model")
    model_id = _text(model, "id", f"{mesh_id} model")
    declared_digest = _text(model, "digest", f"{mesh_id} model")
    if model_digest and declared_digest != model_digest:
        raise MeshError(
            f"网格包 {mesh_id} 的 model.digest {declared_digest[:12]} 与工况模型 {model_digest[:12]} 不一致"
        )
    parts = _check_parts(manifest.get("parts"), mesh_id)
    probes = _check_probes(manifest.get("probes"), mesh_id)
    node_sets, part_sets = _check_sets(manifest.get("sets"), mesh_id, {item.part_id for item in parts})
    regions = _check_regions(manifest.get("regions"), mesh_id)
    mass_check = _check_mass(manifest.get("quality"), mesh_id, mass_ratio_tolerance)
    extent_raw = _mapping(manifest.get("extent"), f"{mesh_id} extent")
    extent = {key: _number(extent_raw, key, f"{mesh_id} extent") for key in ("z_start_mm", "z_end_mm", "r_max_mm")}
    detonation_raw = _mapping(manifest.get("detonation"), f"{mesh_id} detonation")
    detonation = {key: _number(detonation_raw, key, f"{mesh_id} detonation") for key in ("x", "y", "z", "t0")}
    files = _mapping(manifest.get("files"), f"{mesh_id} files")
    mesh_name = _text(files, "mesh", f"{mesh_id} files")
    if mesh_name != "mesh.k":
        raise MeshError(f"网格包 {mesh_id} 的 files.mesh 应为 'mesh.k'，实际为 {mesh_name!r}")
    mesh_file = directory / mesh_name
    if not mesh_file.is_file():
        raise MeshError(f"网格包 {mesh_id} 缺少网格片段 {mesh_file}")
    counts = _mapping(manifest.get("counts"), f"{mesh_id} counts")
    for key in ("nodes", "elements"):
        if _integer(counts, key, f"{mesh_id} counts") <= 0:
            raise MeshError(f"网格包 {mesh_id} 的 counts.{key} 必须为正")
    warnings = (
        *_text_list(manifest.get("tbd"), f"{mesh_id} tbd"),
        *_text_list(manifest.get("warnings"), f"{mesh_id} warnings"),
    )
    return MeshPackage(
        directory=directory,
        manifest=manifest,
        mesh_id=mesh_id,
        digest=digest,
        geometry_key=geometry_key,
        dimension=str(manifest.get("dimension")),
        level=level,
        model_id=model_id,
        model_digest=declared_digest,
        parts=parts,
        probes=probes,
        node_sets=node_sets,
        part_sets=part_sets,
        regions=regions,
        extent=extent,
        detonation=detonation,
        mass_check=mass_check,
        mesh_file=mesh_file,
        warnings=warnings,
    )


def scan_mesh_root(root: Path, *, dimension: str = AXISYMMETRIC_DIMENSION) -> MeshSet:
    """扫描网格根目录下全部网格包（只认含 ``manifest.json`` 的子目录）。"""
    root = Path(root)
    if not root.is_dir():
        raise MeshError(f"网格根目录不存在: {root}")
    packages: list[MeshPackage] = []
    warnings: list[str] = []
    seen: dict[tuple[str, str], Path] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        package = load_package(child, dimension=dimension)
        key = (package.model_digest, package.level)
        if key in seen:
            raise MeshError(
                f"网格根目录 {root} 中模型 {package.model_id} 的档位 {package.level} 重复: "
                f"{seen[key].name} 与 {package.mesh_id}"
            )
        seen[key] = child
        packages.append(package)
        warnings.extend(f"{package.mesh_id}: {message}" for message in package.warnings)
    if not packages:
        raise MeshError(f"网格根目录 {root} 下没有网格包（缺少 manifest.json）")
    return MeshSet(packages=tuple(packages), warnings=tuple(warnings))


def compare_levels(packages: Sequence[MeshPackage]) -> LevelReport:
    """比较同 ``geometry_key`` 多档网格的面积误差；跨几何直接报错。"""
    if len(packages) < 2:
        raise MeshError("档间误差比较至少需要两个网格包")
    geometry_keys = {item.geometry_key for item in packages}
    if len(geometry_keys) > 1:
        raise MeshError(
            f"geometry_key 不同的网格包不得做档间误差比较（{len(geometry_keys)} 个）；"
            "请确认三档出自同一模型与同一区域划分"
        )
    ordered = tuple(sorted(packages, key=lambda item: LEVEL_ORDER.index(item.level)))
    levels = tuple(item.level for item in ordered)
    if len(set(levels)) != len(ordered):
        raise MeshError("同一模型存在重复档位，无法做档间误差比较")
    shared = set(ordered[0].regions)
    for item in ordered[1:]:
        shared &= set(item.regions)
    if not shared:
        raise MeshError("各档没有共同区域，无法比较网格误差")
    rows: list[LevelRow] = []
    detail_parts: list[str] = []
    convergent = True
    for region in sorted(shared):
        series = [item.regions[region] for item in ordered]
        rows.extend(LevelRow(region=region, level=item.level, area_rel_error=item.regions[region]) for item in ordered)
        for earlier, later, later_level in zip(series, series[1:], levels[1:], strict=False):
            if later > earlier * (1.0 + _CONVERGENCE_SLACK) + _EPS:
                convergent = False
                detail_parts.append(f"{region} 在 {later_level} 档面积误差反弹（{earlier:.3g} → {later:.3g}）")
    mass_ratios = tuple((item.level, item.mass_check["ratio"]) for item in ordered)
    if convergent:
        detail = (
            "收敛："
            + "，".join(sorted(shared))
            + " 的面积误差随加密不增（允许 5% 反弹）；"
            + "质量比值 "
            + " / ".join(f"{level}={ratio:.6g}" for level, ratio in mass_ratios)
        )
    else:
        detail = "不收敛：" + "；".join(detail_parts)
    return LevelReport(
        geometry_key=next(iter(geometry_keys)),
        levels=levels,
        rows=tuple(rows),
        mass_ratios=mass_ratios,
        detail=detail,
    )


def format_level_report(report: LevelReport) -> str:
    """档间误差比较的文本表。"""
    lines = [f"网格档位比较：geometry_key {report.geometry_key[:12]}；{' / '.join(report.levels)}"]
    lines.append("区域\t" + "\t".join(report.levels))
    regions = sorted({row.region for row in report.rows})
    for region in regions:
        values = {row.level: row.area_rel_error for row in report.rows if row.region == region}
        lines.append(region + "\t" + "\t".join(f"{values[level]:.6g}" for level in report.levels))
    lines.append(report.detail)
    return "\n".join(lines) + "\n"
