"""网格包（``paramesh-mesh/1``）：清单、指纹与目录写出。

目录结构（草案，待 dynacalc 评审）：

    <out>/<mesh_id>/
        manifest.json
        mesh.k
        quality.json
        quality.txt

清单中的 ``geometry_key`` 与 ``model.digest`` 用于下游追溯和三档共享几何校验。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._kfile import (
    NODE_SET_ORDER,
    node_set_id,
    part_set_id,
    render_keyword,
)
from fcmd.apps.paramesh._mesh import ELEMENT_TYPE, MESH_DIMENSION, Mesh, Node
from fcmd.apps.paramesh._model import canonical_payload_json
from fcmd.apps.paramesh._quality import QualityReport, format_quality

__all__ = [
    "CONTRACT_DRAFT",
    "MESH_SCHEMA",
    "build_manifest",
    "geometry_key",
    "load_manifest",
    "load_quality",
    "mesh_digest",
    "mesh_id",
    "read_package",
    "write_package",
]

MESH_SCHEMA = "paramesh-mesh/1"
_SAFE_NAME = re.compile(r"[^0-9A-Za-z_.-]+")

CONTRACT_DRAFT = """\
网格 → deck 交接契约（paramesh-mesh/1，已按 dynacalc 评审 v1 做加法扩展）

一、目录结构
  <case>/meshes/<mesh_id>/
    manifest.json     网格清单（本契约的权威描述）
    mesh.k            LS-DYNA 关键字网格片段
    quality.json      完整质量报告
    quality.txt       质量报告文本摘要
  paramesh 只写自己的输出根；由上层（CLI / GUI）放到 meshes 根下。
  mesh_id 必须等于目录名；下游不得从 model.id 反推目录名。

二、坐标、单元与截面
  - 二维轴对称 r–z：节点写 x=轴向(z)，y=径向(r)，z=0；r >= 0，已按 x 轴为对称轴生成。
  - 单元为 *ELEMENT_SHELL；截面统一 ELFORM=14（轴对称壳），element_type 保持 shell。
  - section_angle_deg 是代表截面方位角（0–359°），不是整周旋转角；旋转由 ELFORM=14 隐式完成。
  - v1 不生成 *BOUNDARY_NON_REFLECTING；MESH_NRB 为预留名，当前与 MESH_END_END 同集合，
    deck 只用 MESH_END_END 作自由端约束，不得对 MESH_NRB 施加无反射吸收。

三、manifest.json 必填字段
  schema           固定为 paramesh-mesh/1
  mesh_id          <safe_model>-<level>-<digest 前 8 位>，见五.1
  digest           网格内容指纹（见五.2）
  geometry_key     几何 / 区域划分指纹；三档必须一致（见五.3）
  dimension        axisymmetric-2d
  level            coarse / medium / fine
  element_type     shell（截面由 parts[].section 给出 ELFORM=14）
  coordinate_system {symmetry_axis: x, x: axial, y: radial, z: out_of_plane}
  units            只约定长度 mm；密度 g/cm^3、速度 m/s 由 deck 换算
  model            {id, digest, source, end_loose, circumferential_wall}
  scale            特征尺度、驱动项、全局尺寸、代表截面与壁厚
  regions[]        {name, part_id, elements, nodes, area_mm2, analytic_area_mm2, area_rel_error}
                   area_mm2 是 r–z 截面面积，不是旋转体体积
  parts[]          {part_id, name, role, element_type, section{secid, elform, thickness_mm}}
                   role ∈ explosive / air / wall / inert
                   section.secid = part_id（v1 固定，deck 可重映射但须保留对应关系）
                   section.elform = 14
                   section.thickness_mm 取代表截面处的径向厚度：
                     炸药 = scale.propellant_radius_mm；间隙 = scale.gap_mm；
                     管壁 = scale.section_wall_mm（即代表截面壁厚）
                   nip 为可选项，v1 不给出，由 deck 按 ELFORM=14 与材料选择
  sets             {nodes: {集合名: {id, count}}, parts: {集合名: {id, part_ids}}}
                   SID 由本契约固定生成，见五.1
  extent           {z_start_mm, z_end_mm, r_max_mm}，来自实际网格节点
  detonation       {x, y, z, t0}，起爆端轴线（x=z_start_mm，y=0，z=0，t0=0）
  probes[]         {name, node_id, r_mm, z_mm}，见四
  counts           {nodes, elements, shells}
  size_range_mm    {min, max}
  quality          长宽比、尺寸、负体积/退化、壁厚层数摘要
  refinements[]    局部加密区是否生效及倍数
  tbd[]            生成时仍未标定、按最坏情形处理的字段路径
  warnings[]       尺度、层数、几何相关警告
  files            {mesh, quality, quality_text}

四、mesh.k 片段与探针
  1. mesh.k 只含 *NODE / *ELEMENT_SHELL / *SET_NODE_LIST_TITLE / *SET_PART_LIST_TITLE。
     不含 *PART / *SECTION_SHELL / *MAT_* / *KEYWORD / *END（方案 A）。
  2. deck 按 manifest.parts[] 生成 *PART 与 *SECTION_SHELL：
     *PART 的 pid=part_id，secid=section.secid，MID/EOSID 由 deck 映射；
     *SECTION_SHELL 的 secid=section.secid，ELFORM=section.elform，厚度=section.thickness_mm。
  3. *SET_*_LIST 首张数据卡为 SID, DA1, DA2, DA3, DA4；成员从下一行起每行 8 个。
  4. probes 由 paramesh 选点，全部锚定在几何端面（z_start / z_end）与固定半径
     （r=0、r=inner、r=outer；有间隙时另加 r=propellant_outer）。这些位置在三档中
     物理坐标相同（node_id 因节点编号不同而不同），可直接比较到达时程与压力历史。
  5. 不含 *BOUNDARY_NON_REFLECTING；自由端 / 无反射的取舍由 deck 模板决定。

五、可复算细则
  1. mesh_id 与集合 SID
     safe_model = re.sub(r"[^0-9A-Za-z_.-]+", "_", model.id).strip("_") or "mesh"
     mesh_id = f"{safe_model}-{level}-{digest[:8]}"
     节点集合 SID：按 NODE_SET_ORDER 的固定顺序从 1 开始（1–99）；未生成的集合保留空号。
     部件集合 SID：按 PART_SET_ORDER 的固定顺序从 101 开始（101–199）。
     当前顺序：
       节点 MESH_AXIS(1), MESH_OUTER(2), MESH_END_START(3), MESH_END_END(4),
            MESH_NRB(5), MESH_IF_PROP_GAP(6), MESH_IF_GAP_WALL(7), MESH_IF_PROP_WALL(8),
            MESH_WALL_INNER(9), MESH_REGION_PROP(10), MESH_REGION_GAP(11), MESH_REGION_WALL(12)
       部件 MESH_PART_ALL(101), MESH_PART_FLUID(102), MESH_PART_WALL(103)
  2. digest 规范化
     canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
     digest = sha256(canonical.encode("utf-8")).hexdigest()
     payload = {schema, dimension, model_digest, level,
                nodes: [[r_mm, z_mm], ...] 按 node_id 升序,
                elements: [[part_id, n1, n2, n3, n4], ...] 按 element_id 升序}
     节点坐标在生成时已 round(..., 9) 并归一化 -0.0；不含时间戳与绝对路径。
  3. geometry_key 规范化
     同一 canonical / sha256 规则；payload = {dimension, model_digest,
       section_angle_deg, regions: [区域名按生成顺序], parts: [part_id 按生成顺序],
       node_sets: [实际存在的节点集合名，升序], part_sets: [部件集合名，升序]}
     不含 level、节点坐标、单元连接、global_h_mm、mesh_id，因此三档一致。

六、deck 侧约束
  1. 导入前校验 schema / dimension / mesh_id==目录名 / model.digest 与工况一致
  2. 相邻网格误差判定只允许比较 geometry_key 相同的三档
  3. 不得重编号节点 / 单元；如需重编号必须同步重写全部集合
  4. 集合名视为稳定接口；边界条件按 SID 引用，不按 title 猜测
  5. 禁止修改 mesh.k 中的单元拓扑；*PART / *SECTION_SHELL / 材料 / 接触 / 载荷由 deck 侧生成
  6. 契约定稿前 paramesh 不向 dynacalc 的 deck 目录写入任何文件

七、不变量
  - 三档共享 model.digest、geometry_key、区域顺序、集合名与 SID、探针物理坐标
  - 同一模型 + 配置 + 档位 → 逐字节一致的 manifest / mesh.k
  - 所有单元面积 > 0，退化与负体积计数为 0
  - 壁厚方向实际层数落在配置的 [min, max] 内，并在 quality 中给出实际值
  - 区域面积与解析值对照，相对误差写入 regions[].area_rel_error
"""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mesh_digest(mesh: Mesh) -> str:
    """网格内容指纹：模型 digest + 档位 + 全部节点坐标 + 单元连接。"""
    payload: dict[str, Any] = {
        "schema": MESH_SCHEMA,
        "dimension": MESH_DIMENSION,
        "model_digest": mesh.model.digest,
        "level": mesh.level,
        "nodes": [[node.r, node.z] for node in mesh.nodes],
        "elements": [[element.part_id, *element.nodes] for element in mesh.elements],
    }
    return _sha256(canonical_payload_json(payload))


def geometry_key(mesh: Mesh) -> str:
    """几何与区域划分指纹：三档必须一致，只随模型 / 截面 / 集合命名变化。"""
    payload: dict[str, Any] = {
        "dimension": MESH_DIMENSION,
        "model_digest": mesh.model.digest,
        "section_angle_deg": mesh.plan.section_angle_deg,
        "regions": [region.name for region in mesh.regions],
        "parts": [region.part_id for region in mesh.regions],
        "node_sets": sorted(mesh.node_sets),
        "part_sets": sorted(item.name for item in mesh.part_sets),
    }
    return _sha256(canonical_payload_json(payload))


def _safe_model_name(model_id: str) -> str:
    cleaned = _SAFE_NAME.sub("_", model_id).strip("_")
    return cleaned or "mesh"


def mesh_id(mesh: Mesh, digest: str | None = None) -> str:
    """``<model>-<level>-<digest 前 8 位>``，确定性且可直接做目录名。"""
    content_digest = digest if digest is not None else mesh_digest(mesh)
    return f"{_safe_model_name(mesh.model.model_id)}-{mesh.level}-{content_digest[:8]}"


def _quality_summary(report: QualityReport) -> dict[str, Any]:
    return {
        "aspect_ratio": {
            "min": round(report.aspect_min, 12),
            "max": round(report.aspect_max, 12),
            "mean": round(report.aspect_mean, 12),
        },
        "size_mm": {
            "min": round(report.size_min_mm, 12),
            "max": round(report.size_max_mm, 12),
        },
        "negative_volume": report.negative_volume,
        "degenerate": report.degenerate,
        "wall_layers": {
            "actual": report.wall_layers_actual,
            "global_raw": report.wall_layers_global_raw,
            "target_min": report.wall_layers_target_min,
            "target_max": report.wall_layers_target_max,
        },
    }


_EPS = 1e-12
_PART_ROLES = {"propellant": "explosive", "gap": "air", "wall": "wall"}


def _section_thickness(mesh: Mesh, region_name: str) -> float:
    """部件截面厚度：代表截面处的径向厚度。"""
    if region_name == "propellant":
        return mesh.plan.propellant_radius_mm
    if region_name == "gap":
        return mesh.plan.gap_mm
    return mesh.plan.section_wall_mm


def _parts(mesh: Mesh) -> list[dict[str, Any]]:
    return [
        {
            "part_id": region.part_id,
            "name": region.name,
            "role": _PART_ROLES.get(region.name, "inert"),
            "element_type": ELEMENT_TYPE,
            "section": {
                "secid": region.part_id,
                "elform": 14,
                "thickness_mm": round(_section_thickness(mesh, region.name), 12),
            },
        }
        for region in mesh.regions
    ]


def _sets(mesh: Mesh) -> dict[str, dict[str, dict[str, Any]]]:
    nodes = {
        name: {"id": node_set_id(name), "count": len(mesh.node_sets[name])}
        for name in NODE_SET_ORDER
        if mesh.node_sets.get(name)
    }
    parts = {item.name: {"id": part_set_id(item.name), "part_ids": list(item.part_ids)} for item in mesh.part_sets}
    return {"nodes": nodes, "parts": parts}


def _find_node(mesh: Mesh, r: float, z: float) -> Node | None:
    """按坐标找节点；节点已按 ``(z, r)`` 升序编号，首个命中即最小 node_id。"""
    tolerance = 1e-7 * max(1.0, mesh.model.outer_radius, mesh.model.length)
    for node in mesh.nodes:
        if abs(node.r - r) <= tolerance and abs(node.z - z) <= tolerance:
            return node
    return None


def _probes(mesh: Mesh) -> list[dict[str, Any]]:
    """探针锚定在端面与固定半径，保证三档物理位置一致。"""
    length = mesh.model.length
    plan = mesh.plan
    targets: list[tuple[str, float, float]] = [
        ("axis_start", 0.0, 0.0),
        ("axis_end", 0.0, length),
        ("wall_inner_start", plan.inner_radius_mm, 0.0),
        ("wall_inner_end", plan.inner_radius_mm, length),
        ("wall_outer_start", plan.outer_radius_mm, 0.0),
        ("wall_outer_end", plan.outer_radius_mm, length),
    ]
    if plan.gap_mm > _EPS:
        targets.extend(
            [
                ("propellant_outer_start", plan.propellant_radius_mm, 0.0),
                ("propellant_outer_end", plan.propellant_radius_mm, length),
            ]
        )
    probes: list[dict[str, Any]] = []
    for name, r, z in targets:
        node = _find_node(mesh, r, z)
        if node is None:  # pragma: no cover - 端点必为节点
            continue
        probes.append(
            {
                "name": name,
                "node_id": node.node_id,
                "r_mm": round(node.r, 12),
                "z_mm": round(node.z, 12),
            }
        )
    return probes


def _extent(mesh: Mesh) -> dict[str, float]:
    return {
        "z_start_mm": round(min(node.z for node in mesh.nodes), 12),
        "z_end_mm": round(max(node.z for node in mesh.nodes), 12),
        "r_max_mm": round(max(node.r for node in mesh.nodes), 12),
    }


def _detonation(mesh: Mesh) -> dict[str, float]:
    """起爆点：起爆端轴线，x 为轴向坐标。"""
    z_start = min(node.z for node in mesh.nodes)
    return {"x": round(z_start, 12), "y": 0.0, "z": 0.0, "t0": 0.0}


def build_manifest(mesh: Mesh, report: QualityReport) -> dict[str, Any]:
    """构造清单；不写时间戳，排序稳定。"""
    digest = mesh_digest(mesh)
    manifest: dict[str, Any] = {
        "schema": MESH_SCHEMA,
        "mesh_id": mesh_id(mesh, digest),
        "digest": digest,
        "geometry_key": geometry_key(mesh),
        "dimension": MESH_DIMENSION,
        "level": mesh.level,
        "element_type": ELEMENT_TYPE,
        "coordinate_system": {
            "symmetry_axis": "x",
            "x": "axial",
            "y": "radial",
            "z": "out_of_plane",
        },
        "units": {"length": "mm"},
        "model": {
            "id": mesh.model.model_id,
            "digest": mesh.model.digest,
            "source": Path(mesh.model.source).name,
            "end_loose": {
                "declared": mesh.model.end_loose_declared,
                "length_fraction": mesh.model.end_loose_length_fraction,
                "density": mesh.model.end_loose_density,
                "refined_band_mm": mesh.plan.end_loose_band_mm,
            },
            "circumferential_wall": {
                "kind": mesh.model.circumferential_kind,
                "declared": mesh.model.circumferential_declared,
            },
        },
        "scale": {
            "characteristic_mm": mesh.plan.characteristic_mm,
            "driver": mesh.plan.driver,
            "divisor": mesh.plan.divisor,
            "global_h_mm": mesh.plan.global_h_mm,
            "reaction_zone_mm": mesh.plan.reaction_zone_mm,
            "min_wall_mm": mesh.plan.min_wall_mm,
            "min_defect_mm": mesh.plan.min_defect_mm,
            "section_angle_deg": mesh.plan.section_angle_deg,
            "section_wall_mm": mesh.plan.section_wall_mm,
            "section_source": mesh.plan.section_source,
            "gap_mm": mesh.plan.gap_mm,
        },
        "regions": [
            {
                "name": region.name,
                "part_id": region.part_id,
                "elements": region.element_count,
                "nodes": len(region.node_ids),
                "area_mm2": round(region.area_mm2, 12),
                "analytic_area_mm2": round(region.analytic_area_mm2, 12),
                "area_rel_error": round(region.rel_error, 12),
            }
            for region in mesh.regions
        ],
        "parts": _parts(mesh),
        "sets": _sets(mesh),
        "extent": _extent(mesh),
        "detonation": _detonation(mesh),
        "probes": _probes(mesh),
        "counts": {
            "nodes": mesh.node_count,
            "elements": mesh.element_count,
            "shells": mesh.element_count,
        },
        "size_range_mm": {
            "min": round(report.size_min_mm, 12),
            "max": round(report.size_max_mm, 12),
        },
        "quality": _quality_summary(report),
        "refinements": [
            {
                "zone": item.zone,
                "applied": item.applied,
                "factor": item.factor,
                "regions": list(item.regions),
                "note": item.note,
            }
            for item in mesh.refinements
        ],
        "tbd": list(mesh.plan.tbd),
        "warnings": list(mesh.warnings),
        "files": {
            "mesh": "mesh.k",
            "quality": "quality.json",
            "quality_text": "quality.txt",
        },
    }
    return manifest


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise ParameshError(f"写入 {path} 失败：{exc}") from exc


def write_package(mesh: Mesh, report: QualityReport, out_root: Path, *, overwrite: bool = False) -> Path:
    """把一档网格写成网格包；已存在且未授权覆盖时报错，不静默删除。"""
    manifest = build_manifest(mesh, report)
    target = out_root / cast(str, manifest["mesh_id"])
    if target.exists() and not overwrite:
        raise ParameshError(f"网格包已存在：{target}；如需覆盖请显式开启 overwrite")
    if target.exists() and not target.is_dir():
        raise ParameshError(f"输出路径已存在且不是目录：{target}")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ParameshError(f"无法创建网格包目录 {target}：{exc}") from exc
    _write_text(target / "mesh.k", render_keyword(mesh, cast(str, manifest["mesh_id"])))
    _write_text(target / "quality.json", _dump_json(report.to_dict()))
    _write_text(target / "quality.txt", format_quality(report) + "\n")
    _write_text(target / "manifest.json", _dump_json(manifest))
    return target


def load_manifest(path: Path) -> dict[str, Any]:
    """读取清单；``path`` 可以是网格包目录或 manifest 文件。"""
    manifest_path = path / "manifest.json" if path.is_dir() else path
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ParameshError(f"无法读取网格清单 {manifest_path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ParameshError(f"网格清单不是合法 JSON：{manifest_path}：{exc}") from exc
    if not isinstance(document, dict):
        raise ParameshError(f"网格清单顶层必须是对象：{manifest_path}")
    manifest = cast("dict[str, Any]", document)
    if manifest.get("schema") != MESH_SCHEMA:
        raise ParameshError(f"网格清单 schema 必须是 {MESH_SCHEMA}，实际为 {manifest.get('schema')!r}")
    return manifest


def load_quality(path: Path) -> dict[str, Any]:
    """读取质量报告；``path`` 可以是网格包目录或 quality.json。"""
    quality_path = path / "quality.json" if path.is_dir() else path
    try:
        document = json.loads(quality_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ParameshError(f"无法读取质量报告 {quality_path}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ParameshError(f"质量报告不是合法 JSON：{quality_path}：{exc}") from exc
    if not isinstance(document, dict):
        raise ParameshError(f"质量报告顶层必须是对象：{quality_path}")
    return cast("dict[str, Any]", document)


def read_package(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """返回（清单, 质量报告）。"""
    return load_manifest(path), load_quality(path)
