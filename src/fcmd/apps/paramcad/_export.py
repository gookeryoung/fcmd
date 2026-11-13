"""``dynacalc-model/1`` 导出器：文档构造、解析、校验与模型指纹。

契约见 ``docs/dynacalc-model-contract.md``。本模块是 paramcad 与 dynacalc 之间
唯一的格式边界：

- 导出：模型 → YAML 文档（固定单位、``TBD`` 字符串、显式默认值）；
- 解析：文档 → 模型，缺字段按契约回落，``null`` / 未知单位 / 未知枚举直接报错；
- 指纹：按契约第 8 节规范化功能载荷后取 SHA-256，64 位小写十六进制。

契约没有槽位的内部语义（分布场来源、模型备注、TBD 原因）写入 ``meta``，
``meta`` 不参与指纹，因此不影响 dynacalc 的 deck 目录。``case_scope`` 只做结构
自检；未知工况编码与模型互斥需要完整工况清单，交给 dynacalc 判定（契约第 6 节）。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, cast

import yaml

from fcmd.apps.paramcad._defects import DefectParams, LocalDefect, defect_severity
from fcmd.apps.paramcad._fields import AnalyticProfile, Field, MeasuredProfile, Profile
from fcmd.apps.paramcad._model import (
    CONFIDENCE_DOMAINS,
    CONFIDENCE_LEVELS,
    CaseScope,
    CircumferentialWall,
    GeometryState,
    PassSpec,
    ProcessSpec,
    TubeModel,
    WallSample,
)
from fcmd.apps.paramcad._quantity import (
    DENSITY_UNIT,
    LENGTH_UNIT,
    RATIO_UNIT,
    TBD_TEXT,
    VELOCITY_UNIT,
    ParamcadError,
    Quantity,
    quantity,
    tbd,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "GENERATOR_VERSION",
    "SCHEMA",
    "UNITS",
    "contract_draft",
    "dumps_models",
    "example_model",
    "export_model",
    "export_models",
    "format_contract",
    "load_model",
    "load_models",
    "model_digest",
    "model_document",
    "parse_document",
    "save_models",
]

SCHEMA = "dynacalc-model/1"
GENERATOR = "paramcad"
GENERATOR_VERSION = "0.1.0"
UNITS: dict[str, str] = {"length": LENGTH_UNIT, "density": DENSITY_UNIT, "velocity": VELOCITY_UNIT}
DENSITY_AXES = ("axial", "radial", "circumferential")
DENSITY_KINDS = ("uniform", "polyline", "tbd")
WALL_KINDS = ("uniform", "polyline", "tbd")
DEFECT_KINDS = ("void", "thin_layer", "crack", "debond")
DEBOND_STATES = ("bonded", "debonded", "tbd")


# ---------------------------------------------------------------------- #
# 导出：模型 → 文档
# ---------------------------------------------------------------------- #
def _number(item: Quantity) -> float | str:
    """数值或 ``TBD`` 字符串；契约禁止 ``null`` 顶替未标定值。"""
    return TBD_TEXT if item.value is None else float(item.value)


def _optional_number(value: float | None) -> float | str:
    return TBD_TEXT if value is None else float(value)


def _wall_circumferential_document(item: CircumferentialWall) -> dict[str, Any] | None:
    """``wall_thickness.circumferential``；``kind: tbd`` 归一为整节省略。"""
    if item.kind == "tbd":
        return None
    if item.kind == "uniform":
        if item.value is None:
            raise ParamcadError("uniform 周向壁厚缺少数值")
        return {"kind": "uniform", "value": float(item.value)}
    return {
        "kind": "polyline",
        "samples": [
            {
                "angle_deg": float(sample.angle_deg),
                "thickness": TBD_TEXT if sample.thickness.value is None else float(sample.thickness.value),
            }
            for sample in item.samples
        ],
    }


def geometry_document(state: GeometryState) -> dict[str, Any]:
    """``geometry`` 节：必填字段始终写出，可选项只写已标定值。"""
    wall: dict[str, Any] = {"nominal": _number(state.wall_mean)}
    if state.wall_min.value is not None:
        wall["min"] = float(state.wall_min.value)
    if state.wall_max.value is not None:
        wall["max"] = float(state.wall_max.value)
    if state.wall_circumferential is not None:
        circumferential = _wall_circumferential_document(state.wall_circumferential)
        if circumferential is not None:
            wall["circumferential"] = circumferential
    ecc: dict[str, Any] = {}
    if state.eccentricity_radial.value is not None:
        ecc["radial_mm"] = float(state.eccentricity_radial.value)
    if state.eccentricity_circumferential.value is not None:
        ecc["circumferential_mm"] = float(state.eccentricity_circumferential.value)
    if state.eccentricity_angle_deg.value is not None:
        ecc["angle_deg"] = float(state.eccentricity_angle_deg.value)
    block: dict[str, Any] = {
        "outer_diameter": _number(state.outer_diameter),
        "inner_diameter": _number(state.inner_diameter),
        "length": _number(state.length),
        "wall_thickness": wall,
    }
    if ecc:
        block["eccentricity"] = ecc
    return block


def drawing_document(process: ProcessSpec | None) -> dict[str, Any] | None:
    """``drawing`` 节；未给出工艺时整节省略（契约里是可选项）。"""
    if process is None:
        return None
    passes: list[dict[str, Any]] = []
    for item in process.passes:
        block: dict[str, Any] = {"index": item.index}
        if item.diameter_reduction is not None:
            block["diameter_reduction"] = float(item.diameter_reduction)
        if item.wall_thickness_change is not None:
            block["wall_thickness_change"] = float(item.wall_thickness_change)
        if item.die_angle_deg is not None:
            block["die_angle_deg"] = float(item.die_angle_deg)
        passes.append(block)
    block = {"passes": passes}
    cumulative = process.derived_reduction
    if cumulative is not None:
        block["cumulative_reduction"] = float(cumulative)
    return block


def _profile_document(profile: Profile) -> dict[str, Any]:
    if isinstance(profile, AnalyticProfile) and profile.kind == "constant":
        return {"kind": "uniform", "value": float(profile.value)}
    return {
        "kind": "polyline",
        "samples": [
            {"position": float(position), "density": TBD_TEXT if value is None else float(value)}
            for position, value in profile.vertices()
        ],
    }


def _defect_document(defect: LocalDefect) -> dict[str, Any]:
    location: dict[str, Any] = {}
    if defect.axial is not None:
        location["axial"] = float(defect.axial)
    if defect.radial is not None:
        location["radial"] = float(defect.radial)
    if defect.angle_deg is not None:
        location["angle_deg"] = float(defect.angle_deg)
    block: dict[str, Any] = {"id": defect.defect_id, "kind": defect.kind}
    if location:
        block["location"] = location
    if defect.size_mm is not None and defect.size_mm.value is not None:
        block["size_mm"] = float(defect.size_mm.value)
    if defect.severity is not None and defect.severity.value is not None:
        block["severity"] = float(defect.severity.value)
    return block


def density_document(fields: tuple[Field, ...], defects: DefectParams) -> dict[str, Any] | None:
    """``density_field`` 节：只写实际给出的轴 / 端部疏松 / 缺陷；全空时整节省略。"""
    block: dict[str, Any] = {}
    for field in fields:
        block[field.axis] = _profile_document(field.profile)
    end_loose: dict[str, Any] = {}
    if defects.end_loose_length_fraction.value is not None:
        end_loose["length_fraction"] = float(defects.end_loose_length_fraction.value)
    if defects.end_loose_density.value is not None:
        end_loose["density"] = float(defects.end_loose_density.value)
    if end_loose:
        block["end_loose"] = end_loose
    if defects.local_defects:
        block["local_defects"] = [_defect_document(item) for item in defects.local_defects]
    return block or None


def interface_document(defects: DefectParams) -> dict[str, Any] | None:
    """``interface`` 节：只写已标定的间隙 / 脱粘信息；全空时整节省略。"""
    block: dict[str, Any] = {}
    if defects.gap_mm.value is not None:
        block["gap_mm"] = float(defects.gap_mm.value)
    debond: dict[str, Any] = {}
    if defects.debond_state != "tbd":
        debond["state"] = defects.debond_state
    if defects.debond_extent_deg.value is not None:
        debond["extent_deg"] = float(defects.debond_extent_deg.value)
    if debond:
        block["debond"] = debond
    return block or None


def model_document(model: TubeModel) -> dict[str, Any]:
    """单个模型的契约载荷（不含 ``meta``）。"""
    state = model.geometry
    block: dict[str, Any] = {"id": model.model_id}
    if model.case_scope is not None and not model.case_scope.is_empty:
        block["case_scope"] = {
            "include": list(model.case_scope.include),
            "exclude": list(model.case_scope.exclude),
        }
    block["units"] = dict(UNITS)
    block["confidence"] = {domain: model.confidence.get(domain, "uncalibrated") for domain in CONFIDENCE_DOMAINS}
    block["geometry"] = geometry_document(state)
    drawing = drawing_document(model.process)
    if drawing is not None:
        block["drawing"] = drawing
    density = density_document(model.density_fields, model.defects)
    if density is not None:
        block["density_field"] = density
    interface = interface_document(model.defects)
    if interface is not None:
        block["interface"] = interface
    tbd_paths = _tbd_paths(block)
    if tbd_paths:
        block["tbd"] = tbd_paths
    return block


def _tbd_paths(payload: Any, prefix: str = "") -> list[str]:
    """收集载荷中值为 ``TBD`` 的字段路径（文档性索引，不参与指纹）。"""
    rows: list[str] = []
    if isinstance(payload, dict):
        for key, value in cast("dict[str, Any]", payload).items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_tbd_paths(value, path))
    elif isinstance(payload, list):
        for index, value in enumerate(cast("list[Any]", payload)):
            rows.extend(_tbd_paths(value, f"{prefix}[{index}]"))
    elif payload == TBD_TEXT:
        rows.append(prefix)
    return rows


def _meta_document(models: tuple[TubeModel, ...]) -> dict[str, Any]:
    """``meta`` 扩展：分布场来源与模型备注；不参与指纹。"""
    sources = {
        f"models[{index}].density_field.{field.axis}": field.source
        for index, model in enumerate(models)
        for field in model.density_fields
    }
    notes = {model.model_id: model.notes for model in models if model.notes}
    return {
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "field_sources": sources,
        "model_notes": notes,
    }


def build_document(models: Iterable[TubeModel]) -> dict[str, Any]:
    """构造完整文档：``schema`` + ``models`` + ``meta``。"""
    items = tuple(models)
    if not items:
        raise ParamcadError("至少需要一个模型")
    seen: set[str] = set()
    blocks: list[dict[str, Any]] = []
    for model in items:
        if model.model_id in seen:
            raise ParamcadError(f"模型 id 重复：{model.model_id}")
        seen.add(model.model_id)
        blocks.append(model_document(model))
    return {
        "schema": SCHEMA,
        "meta": _meta_document(items),
        "models": blocks,
    }


def dumps_models(models: Iterable[TubeModel]) -> str:
    """渲染为契约 YAML（UTF-8 文本，``\\n`` 换行，末尾换行）。"""
    text = yaml.safe_dump(
        build_document(models),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    return text if text.endswith("\n") else text + "\n"


# ---------------------------------------------------------------------- #
# 指纹
# ---------------------------------------------------------------------- #
def canonical_payload(model: TubeModel) -> dict[str, Any]:
    """功能载荷：``id`` / ``units`` / ``confidence`` / ``geometry`` / ``drawing`` /
    ``density_field`` / ``interface``，丢弃 ``meta``、``tbd``、``case_scope``。
    """
    block = model_document(model)
    return {key: value for key, value in block.items() if key not in ("case_scope", "tbd")}


def digest_of_payload(payload: dict[str, Any]) -> str:
    """规范化 JSON 后取 SHA-256，返回 64 位小写十六进制。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def model_digest(model: TubeModel) -> str:
    """按契约第 8 节计算模型指纹。"""
    return digest_of_payload(canonical_payload(model))


# ---------------------------------------------------------------------- #
# 解析：文档 → 模型
# ---------------------------------------------------------------------- #
class _Reader:
    """带来源、模型序号与分布场来源表的解析上下文。"""

    def __init__(self, source: str, model_index: int, field_sources: dict[str, str]) -> None:
        self.source = source
        self.model_index = model_index
        self.field_sources = field_sources

    def fail(self, path: str, message: str) -> NoReturn:
        raise ParamcadError(f"{self.source}: {path} {message}")

    def mapping(self, raw: Any, path: str, *, required: bool) -> dict[str, Any] | None:
        if raw is None:
            if required:
                self.fail(path, "缺失或为 null")
            return None
        if not isinstance(raw, dict):
            self.fail(path, "必须是映射")
        return {str(key): value for key, value in cast("dict[Any, Any]", raw).items()}

    def quantity(self, raw: Any, path: str, unit: str, *, required: bool) -> Quantity:
        if raw is None:
            if required:
                self.fail(path, "缺失或为 null（未标定请写字符串 TBD）")
            return tbd(unit, "文档未提供")
        if isinstance(raw, str):
            if raw != TBD_TEXT:
                self.fail(path, f"字符串只允许 {TBD_TEXT}，收到 {raw!r}")
            return tbd(unit, "文档标记 TBD")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            self.fail(path, f"必须是数值或字符串 {TBD_TEXT}，收到 {type(raw).__name__}")
        value = float(raw)
        if not math.isfinite(value):
            self.fail(path, "必须是有限数")
        return Quantity(value, unit)

    def text(self, raw: Any, path: str, *, required: bool) -> str:
        if raw is None:
            if required:
                self.fail(path, "缺失或为 null")
            return ""
        if not isinstance(raw, str) or not raw.strip():
            self.fail(path, "必须是非空文本")
        if raw == TBD_TEXT:
            self.fail(path, f"{TBD_TEXT} 不能用于枚举 / 文本字段")
        return raw

    def enum(self, raw: Any, path: str, allowed: tuple[str, ...], *, required: bool, fallback: str) -> str:
        if raw is None:
            if required:
                self.fail(path, "缺失或为 null")
            return fallback
        if not isinstance(raw, str) or raw in (TBD_TEXT, ""):
            self.fail(path, f"必须是 {allowed} 之一")
        if raw not in allowed:
            self.fail(path, f"未知取值 {raw!r}，允许 {allowed}")
        return raw


def _parse_units(reader: _Reader, raw: Any, path: str) -> None:
    block = reader.mapping(raw, path, required=True)
    assert block is not None
    for key, expected in UNITS.items():
        value = block.get(key)
        if value is None:
            reader.fail(f"{path}.{key}", f"缺失（固定值 {expected}）")
        if value != expected:
            reader.fail(f"{path}.{key}", f"必须是 {expected}，收到 {value!r}；v1 不做单位换算")


def _parse_confidence(reader: _Reader, raw: Any, path: str) -> dict[str, str]:
    block = reader.mapping(raw, path, required=False)
    result: dict[str, str] = {}
    if block is None:
        return result
    for key, value in block.items():
        if key not in CONFIDENCE_DOMAINS:
            continue
        result[key] = reader.enum(value, f"{path}.{key}", CONFIDENCE_LEVELS, required=False, fallback="uncalibrated")
    return result


def _parse_geometry(reader: _Reader, raw: Any, path: str) -> GeometryState:
    block = reader.mapping(raw, path, required=True)
    assert block is not None
    wall = reader.mapping(block.get("wall_thickness"), f"{path}.wall_thickness", required=True)
    assert wall is not None
    ecc = reader.mapping(block.get("eccentricity"), f"{path}.eccentricity", required=False) or {}
    return GeometryState(
        outer_diameter=reader.quantity(
            block.get("outer_diameter"), f"{path}.outer_diameter", LENGTH_UNIT, required=True
        ),
        inner_diameter=reader.quantity(
            block.get("inner_diameter"), f"{path}.inner_diameter", LENGTH_UNIT, required=True
        ),
        length=reader.quantity(block.get("length"), f"{path}.length", LENGTH_UNIT, required=True),
        wall_nominal=reader.quantity(wall.get("nominal"), f"{path}.wall_thickness.nominal", LENGTH_UNIT, required=True),
        wall_min=reader.quantity(wall.get("min"), f"{path}.wall_thickness.min", LENGTH_UNIT, required=False),
        wall_max=reader.quantity(wall.get("max"), f"{path}.wall_thickness.max", LENGTH_UNIT, required=False),
        eccentricity_radial=reader.quantity(
            ecc.get("radial_mm"), f"{path}.eccentricity.radial_mm", LENGTH_UNIT, required=False
        ),
        eccentricity_circumferential=reader.quantity(
            ecc.get("circumferential_mm"), f"{path}.eccentricity.circumferential_mm", LENGTH_UNIT, required=False
        ),
        eccentricity_angle_deg=reader.quantity(
            ecc.get("angle_deg"), f"{path}.eccentricity.angle_deg", "deg", required=False
        ),
        wall_circumferential=_parse_circumferential(
            reader, wall.get("circumferential"), f"{path}.wall_thickness.circumferential"
        ),
    )


def _parse_circumferential(reader: _Reader, raw: Any, path: str) -> CircumferentialWall | None:
    """解析 ``wall_thickness.circumferential``；未给出该节返回 ``None``。"""
    block = reader.mapping(raw, path, required=False)
    if block is None:
        return None
    kind = reader.enum(block.get("kind"), f"{path}.kind", WALL_KINDS, required=True, fallback="tbd")
    if kind == "tbd":
        return CircumferentialWall(kind="tbd")
    if kind == "uniform":
        value = reader.quantity(block.get("value"), f"{path}.value", LENGTH_UNIT, required=True)
        if value.value is None:
            reader.fail(f"{path}.value", "uniform 的壁厚不能是 TBD；未标定请改用 kind: tbd")
        return CircumferentialWall(kind="uniform", value=value.value)
    samples = block.get("samples")
    if not isinstance(samples, list) or len(cast("list[Any]", samples)) < 2:
        reader.fail(f"{path}.samples", "polyline 至少需要 2 个样本")
    items: list[WallSample] = []
    previous = -1.0
    for index, item in enumerate(cast("list[Any]", samples)):
        spot = f"{path}.samples[{index}]"
        sample = reader.mapping(item, spot, required=True)
        assert sample is not None
        angle = _optional_angle(reader, sample.get("angle_deg"), f"{spot}.angle_deg")
        if angle is None:
            reader.fail(f"{spot}.angle_deg", "缺失或为 TBD；样本角度必须给出具体值")
        if angle <= previous:
            reader.fail(spot, "样本角度必须严格递增")
        previous = angle
        items.append(
            WallSample(
                angle_deg=angle,
                thickness=reader.quantity(sample.get("thickness"), f"{spot}.thickness", LENGTH_UNIT, required=True),
            )
        )
    return CircumferentialWall(kind="polyline", samples=tuple(items))


def _parse_passes(reader: _Reader, raw: Any, path: str) -> tuple[PassSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        reader.fail(path, "必须是列表")
    passes: list[PassSpec] = []
    for index, item in enumerate(cast("list[Any]", raw)):
        spot = f"{path}[{index}]"
        block = reader.mapping(item, spot, required=True)
        assert block is not None
        raw_index = block.get("index")
        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
            reader.fail(f"{spot}.index", "缺失或不是整数")
        passes.append(
            PassSpec(
                index=raw_index,
                diameter_reduction=_optional_float(
                    reader, block.get("diameter_reduction"), f"{spot}.diameter_reduction"
                ),
                wall_thickness_change=_optional_float(
                    reader, block.get("wall_thickness_change"), f"{spot}.wall_thickness_change"
                ),
                die_angle_deg=_optional_float(reader, block.get("die_angle_deg"), f"{spot}.die_angle_deg"),
            )
        )
    return tuple(passes)


def _optional_float(reader: _Reader, raw: Any, path: str) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw == TBD_TEXT:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        reader.fail(path, f"必须是数值或字符串 {TBD_TEXT}")
    value = float(raw)
    if not math.isfinite(value):
        reader.fail(path, "必须是有限数")
    return value


def _parse_drawing(reader: _Reader, raw: Any, path: str) -> ProcessSpec | None:
    block = reader.mapping(raw, path, required=False)
    if block is None:
        return None
    passes = _parse_passes(reader, block.get("passes"), f"{path}.passes")
    cumulative = _optional_float(reader, block.get("cumulative_reduction"), f"{path}.cumulative_reduction")
    if not passes and cumulative is None:
        return None
    return ProcessSpec(passes=passes, cumulative_reduction=cumulative)


def _parse_profile(reader: _Reader, raw: Any, path: str) -> Profile | None:
    """解析一个密度轴；``kind: tbd`` 或空节返回 ``None``。"""
    block = reader.mapping(raw, path, required=False)
    if block is None:
        return None
    kind = reader.enum(block.get("kind"), f"{path}.kind", DENSITY_KINDS, required=True, fallback="tbd")
    if kind == "tbd":
        return None
    if kind == "uniform":
        value = reader.quantity(block.get("value"), f"{path}.value", DENSITY_UNIT, required=True)
        if value.value is None:
            reader.fail(f"{path}.value", "uniform 的数值不能是 TBD；未标定请改用 kind: tbd")
        return AnalyticProfile(kind="constant", value=value.value)
    samples = block.get("samples")
    if not isinstance(samples, list) or len(cast("list[Any]", samples)) < 2:
        reader.fail(f"{path}.samples", "polyline 至少需要 2 个样本")
    points: list[tuple[float, float | None]] = []
    for index, item in enumerate(cast("list[Any]", samples)):
        spot = f"{path}.samples[{index}]"
        sample = reader.mapping(item, spot, required=True)
        assert sample is not None
        position = _optional_fraction(reader, sample.get("position"), f"{spot}.position")
        if position is None:
            reader.fail(f"{spot}.position", "缺失或为 TBD；折线位置必须给出具体值")
        density = reader.quantity(sample.get("density"), f"{spot}.density", DENSITY_UNIT, required=True)
        points.append((position, density.value))
    return MeasuredProfile(tuple(points))


def _parse_density_fields(
    reader: _Reader, raw: Any, path: str
) -> tuple[tuple[Field, ...], tuple[LocalDefect, ...], Quantity, Quantity]:
    """返回密度轴、局部缺陷与端部疏松的两个量。"""
    block = reader.mapping(raw, path, required=False)
    if block is None:
        reason = "文档未提供密度场"
        return (), (), tbd(RATIO_UNIT, reason), tbd(DENSITY_UNIT, reason)
    fields: list[Field] = []
    for axis in DENSITY_AXES:
        profile = _parse_profile(reader, block.get(axis), f"{path}.{axis}")
        if profile is None:
            continue
        source = reader.field_sources.get(f"models[{reader.model_index}].density_field.{axis}", "")
        fields.append(Field(axis=cast("Any", axis), profile=profile, source=source or "文档未注明来源"))
    end_loose = reader.mapping(block.get("end_loose"), f"{path}.end_loose", required=False) or {}
    fraction = reader.quantity(
        end_loose.get("length_fraction"), f"{path}.end_loose.length_fraction", RATIO_UNIT, required=False
    )
    density = reader.quantity(end_loose.get("density"), f"{path}.end_loose.density", DENSITY_UNIT, required=False)
    defects = _parse_local_defects(reader, block.get("local_defects"), f"{path}.local_defects")
    return tuple(fields), defects, fraction, density


def _parse_local_defects(reader: _Reader, raw: Any, path: str) -> tuple[LocalDefect, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        reader.fail(path, "必须是列表")
    defects: list[LocalDefect] = []
    for index, item in enumerate(cast("list[Any]", raw)):
        spot = f"{path}[{index}]"
        block = reader.mapping(item, spot, required=True)
        assert block is not None
        location = reader.mapping(block.get("location"), f"{spot}.location", required=False) or {}
        defects.append(
            LocalDefect(
                defect_id=reader.text(block.get("id"), f"{spot}.id", required=True),
                kind=cast(
                    "Any", reader.enum(block.get("kind"), f"{spot}.kind", DEFECT_KINDS, required=True, fallback="void")
                ),
                axial=_optional_fraction(reader, location.get("axial"), f"{spot}.location.axial"),
                radial=_optional_fraction(reader, location.get("radial"), f"{spot}.location.radial"),
                angle_deg=_optional_angle(reader, location.get("angle_deg"), f"{spot}.location.angle_deg"),
                size_mm=_optional_quantity(reader, block.get("size_mm"), f"{spot}.size_mm", LENGTH_UNIT),
                severity=_optional_quantity(reader, block.get("severity"), f"{spot}.severity", "1"),
            )
        )
    return tuple(defects)


def _optional_quantity(reader: _Reader, raw: Any, path: str, unit: str) -> Quantity | None:
    if raw is None:
        return None
    return reader.quantity(raw, path, unit, required=False)


def _optional_fraction(reader: _Reader, raw: Any, path: str) -> float | None:
    value = _optional_float(reader, raw, path)
    if value is not None and not 0.0 <= value <= 1.0:
        reader.fail(path, "必须在 0–1")
    return value


def _optional_angle(reader: _Reader, raw: Any, path: str) -> float | None:
    value = _optional_float(reader, raw, path)
    if value is not None and not 0.0 <= value < 360.0:
        reader.fail(path, "必须在 [0, 360)")
    return value


def _parse_interface(reader: _Reader, raw: Any, path: str) -> tuple[Quantity, str, Quantity]:
    block = reader.mapping(raw, path, required=False)
    if block is None:
        return tbd(LENGTH_UNIT, "文档未提供界面节"), "tbd", tbd("deg", "文档未提供界面节")
    debond = reader.mapping(block.get("debond"), f"{path}.debond", required=False) or {}
    state = reader.enum(debond.get("state"), f"{path}.debond.state", DEBOND_STATES, required=False, fallback="tbd")
    return (
        reader.quantity(block.get("gap_mm"), f"{path}.gap_mm", LENGTH_UNIT, required=False),
        state,
        reader.quantity(debond.get("extent_deg"), f"{path}.debond.extent_deg", "deg", required=False),
    )


def _parse_case_scope(reader: _Reader, raw: Any, path: str) -> CaseScope | None:
    block = reader.mapping(raw, path, required=False)
    if block is None:
        return None

    def values(key: str) -> tuple[str, ...]:
        items = block.get(key)
        if items is None:
            return ()
        if not isinstance(items, list):
            reader.fail(f"{path}.{key}", "必须是字符串列表")
        result: list[str] = []
        for index, item in enumerate(cast("list[Any]", items)):
            result.append(reader.text(item, f"{path}.{key}[{index}]", required=True))
        return tuple(result)

    return CaseScope(include=values("include"), exclude=values("exclude"))


def _parse_model(reader: _Reader, raw: Any, path: str) -> TubeModel:
    block = reader.mapping(raw, path, required=True)
    assert block is not None
    model_id = reader.text(block.get("id"), f"{path}.id", required=True)
    _parse_units(reader, block.get("units"), f"{path}.units")
    confidence = _parse_confidence(reader, block.get("confidence"), f"{path}.confidence")
    before = _parse_geometry(reader, block.get("geometry"), f"{path}.geometry")
    process = _parse_drawing(reader, block.get("drawing"), f"{path}.drawing")
    fields, local_defects, end_loose_fraction, end_loose_density = _parse_density_fields(
        reader, block.get("density_field"), f"{path}.density_field"
    )
    gap, debond_state, extent = _parse_interface(reader, block.get("interface"), f"{path}.interface")
    scope = _parse_case_scope(reader, block.get("case_scope"), f"{path}.case_scope")
    defects = DefectParams(
        gap_mm=gap,
        debond_state=cast("Any", debond_state),
        debond_extent_deg=extent,
        local_defects=local_defects,
        end_loose_length_fraction=end_loose_fraction,
        end_loose_density=end_loose_density,
    )
    return TubeModel(
        model_id=model_id,
        geometry=before,
        process=process,
        density_fields=fields,
        defects=defects,
        case_scope=scope,
        confidence=confidence,
    )


def parse_document(text: str, *, source: str = "<text>") -> tuple[TubeModel, ...]:
    """解析契约文档；任何契约级错误都抛出 :class:`ParamcadError`。"""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParamcadError(f"{source}: YAML 解析失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise ParamcadError(f"{source}: 顶层必须是映射")
    document = {str(key): value for key, value in cast("dict[Any, Any]", raw).items()}
    if document.get("schema") != SCHEMA:
        raise ParamcadError(f"{source}: schema 必须是 {SCHEMA}，收到 {document.get('schema')!r}")
    models_raw = document.get("models")
    if not isinstance(models_raw, list) or not cast("list[Any]", models_raw):
        raise ParamcadError(f"{source}: models 必须是非空列表")
    meta = document.get("meta")
    sources: dict[str, str] = {}
    notes: dict[str, str] = {}
    if isinstance(meta, dict):
        raw_sources = cast("dict[Any, Any]", meta).get("field_sources")
        if isinstance(raw_sources, dict):
            sources = {str(key): str(value) for key, value in cast("dict[Any, Any]", raw_sources).items()}
        raw_notes = cast("dict[Any, Any]", meta).get("model_notes")
        if isinstance(raw_notes, dict):
            notes = {str(key): str(value) for key, value in cast("dict[Any, Any]", raw_notes).items()}
    models: list[TubeModel] = []
    for index, item in enumerate(cast("list[Any]", models_raw)):
        reader = _Reader(source, index, sources)
        models.append(_parse_model(reader, item, f"models[{index}]"))
    ids = [item.model_id for item in models]
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    if duplicates:
        raise ParamcadError(f"{source}: 模型 id 重复：{duplicates}")
    return tuple(replace(item, notes=notes.get(item.model_id, item.notes)) for item in models)


# ---------------------------------------------------------------------- #
# 文件读写与示例
# ---------------------------------------------------------------------- #
def save_models(models: Iterable[TubeModel], path: Path, *, overwrite: bool = False) -> Path:
    """原子写入契约 YAML；已存在且未允许覆盖时报错。"""
    if path.exists() and not overwrite:
        raise ParamcadError(f"输出已存在：{path}（使用 --overwrite 覆盖）")
    text = dumps_models(models)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return path


def export_model(model: TubeModel, path: Path, *, overwrite: bool = False) -> Path:
    """导出单个模型。"""
    return save_models((model,), path, overwrite=overwrite)


def export_models(models: Iterable[TubeModel], path: Path, *, overwrite: bool = False) -> Path:
    """导出多个模型到同一文件。"""
    return save_models(models, path, overwrite=overwrite)


def load_models(path: Path) -> tuple[TubeModel, ...]:
    """读取契约文件；文件不存在或解析失败时报错。"""
    if not path.is_file():
        raise ParamcadError(f"模型文件不存在：{path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParamcadError(f"读取模型文件失败：{exc}") from exc
    return parse_document(text, source=str(path))


def load_model(path: Path) -> TubeModel:
    """读取仅含一个模型的契约文件。"""
    models = load_models(path)
    if len(models) != 1:
        raise ParamcadError(f"{path} 含 {len(models)} 个模型，单模型入口需要恰好 1 个")
    return models[0]


def example_model() -> TubeModel:
    """构造用于跑通原型链路的虚构占位模型。

    数值仅用于演示「参数 → 截面 → 导出」链路，不是任何真实产品尺寸、配方或
    材料参数；来源字段明确标注为示例占位。
    """
    before = GeometryState(
        outer_diameter=quantity(30.0, LENGTH_UNIT),
        inner_diameter=quantity(24.0, LENGTH_UNIT),
        length=quantity(200.0, LENGTH_UNIT),
        wall_nominal=quantity(3.0, LENGTH_UNIT),
        wall_min=quantity(2.6, LENGTH_UNIT),
        wall_max=quantity(3.4, LENGTH_UNIT),
        eccentricity_radial=quantity(0.4, LENGTH_UNIT),
        eccentricity_circumferential=quantity(0.0, LENGTH_UNIT),
        eccentricity_angle_deg=quantity(0.0, "deg"),
    )
    process = ProcessSpec(
        passes=(PassSpec(1, 0.10, -0.02, 12.0), PassSpec(2, 0.08, -0.02, 10.0)),
        eccentricity_carry="factor",
        finished_length=quantity(180.0, LENGTH_UNIT),
    )
    fields = (
        Field(
            axis="axial",
            profile=AnalyticProfile("end_loose", 1.60, 1.50, 0.15),
            source="示例占位（解析假设）",
            note="端部疏松型密度分布",
        ),
        Field(
            axis="circumferential",
            profile=MeasuredProfile(((0.0, 1.62), (0.5, 1.65), (1.0, 1.61))),
            source="示例占位（实测点）",
        ),
    )
    defects = DefectParams(
        gap_mm=quantity(0.05, LENGTH_UNIT),
        debond_state="bonded",
        debond_extent_deg=quantity(0.0, "deg"),
        local_defects=(
            LocalDefect(
                defect_id="D1",
                kind="void",
                axial=0.4,
                radial=0.9,
                angle_deg=30.0,
                size_mm=quantity(2.0, LENGTH_UNIT),
                severity=defect_severity(),
            ),
        ),
        end_loose_length_fraction=quantity(0.15, RATIO_UNIT),
        end_loose_density=quantity(1.50, DENSITY_UNIT),
    )
    model = TubeModel.from_before(
        "example",
        before,
        process,
        density_fields=fields,
        defects=defects,
        confidence={"geometry": "uncalibrated", "density_field": "uncalibrated"},
        notes="示例数值为虚构占位，不是真实产品尺寸、配方或材料参数",
    )
    circumferential = CircumferentialWall(
        kind="polyline",
        samples=(
            WallSample(0.0, quantity(2.9, LENGTH_UNIT)),
            WallSample(180.0, quantity(3.1, LENGTH_UNIT)),
        ),
    )
    return replace(model, geometry=replace(model.geometry, wall_circumferential=circumferential))


def contract_draft() -> dict[str, Any]:
    """导出契约摘要，供 dynacalc 侧核对（不是新的契约版本）。"""
    return {
        "schema": SCHEMA,
        "units": dict(UNITS),
        "models": [
            "id",
            "case_scope{include,exclude}",
            "units{length,density,velocity}",
            "confidence{geometry,drawing,density_field,interface}",
            "geometry{outer_diameter,inner_diameter,length,wall_thickness{nominal,min,max,"
            "circumferential{kind,value,samples[{angle_deg,thickness}]}},"
            "eccentricity{radial_mm,circumferential_mm,angle_deg}}",
            "drawing{passes[{index,diameter_reduction,wall_thickness_change,die_angle_deg}],cumulative_reduction}",
            "density_field{axial,radial,circumferential,end_loose{length_fraction,density},local_defects[]}",
            "interface{gap_mm,debond{state,extent_deg}}",
            "tbd[]",
        ],
        "tbd_classes": {
            "required": [
                "id",
                "units.*",
                "geometry.outer_diameter",
                "geometry.inner_diameter",
                "geometry.length",
                "geometry.wall_thickness.nominal",
                "*.kind",
                "samples[].position",
                "samples[].angle_deg",
                "local_defects[].id",
                "local_defects[].kind",
                "passes[].index",
            ],
            "optional": "除 required 外的全部 num；也可整键省略",
            "forbidden": "uniform 的 value（密度三轴与 wall_thickness.circumferential.value）",
        },
        "tbd": "未标定数值统一写字符串 TBD；null 视为缺失并报错；枚举未定用 tbd",
        "preview_minimum": ["geometry.outer_diameter", "geometry.inner_diameter", "geometry.wall_thickness.nominal"],
        "digest": "SHA-256(canonical_json(功能载荷))，64 位小写十六进制",
        "paramcad_meta_extension": ["field_sources", "model_notes"],
    }


def format_contract() -> str:
    """把契约摘要渲染为 YAML 文本。"""
    text = yaml.safe_dump(contract_draft(), allow_unicode=True, sort_keys=False, default_flow_style=False)
    return text if text.endswith("\n") else text + "\n"
