"""dynacalc 模型描述：``dynacalc-model/1`` 读取、校验、工况绑定与派生键（纯逻辑）。

接口契约见 ``docs/dynacalc-model-contract.md``。要点：

- 单位固定 mm / g·cm⁻³ / m·s⁻¹，v1 不做换算；
- 必填缺失、``null``、枚举未知、单位不符 → :class:`ModelError`；
- 未知字段忽略并警告；工况无匹配模型不算错误，回落矩阵值；
- 模型非 TBD 值覆盖矩阵同名因子，模型 TBD / 缺失回落矩阵，仍无则 TBD；
- 规范化形态与 paramcad 的导出文档一致，模型指纹按规范 JSON 计算，
  两侧对同一模型必须得到同一 64 位摘要。
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from fcmd.apps.dynacalc._params import (
    TBD,
    Case,
    canonical_json,
    case_fingerprint,
    is_tbd,
)

__all__ = [
    "CONFIDENCE_LEVELS",
    "MODEL_SCHEMA",
    "UNITS",
    "ModelError",
    "ModelSet",
    "ModelSpec",
    "apply_model",
    "derived_values",
    "load_models",
    "model_digest",
    "model_tbd_paths",
    "parse_models",
]

MODEL_SCHEMA = "dynacalc-model/1"

UNITS = {"length": "mm", "density": "g/cm^3", "velocity": "m/s"}

CONFIDENCE_LEVELS = ("public", "controlled", "uncalibrated")
_CONFIDENCE_DOMAINS = ("geometry", "drawing", "density_field", "interface")
_DENSITY_AXES = ("axial", "radial", "circumferential")

_DEFECT_KINDS = ("void", "thin_layer", "crack", "debond")
_DEBOND_STATES = ("bonded", "debonded", "tbd")
_PROFILE_KINDS = ("uniform", "polyline", "tbd")
_CIRCUMFERENTIAL_KINDS = ("uniform", "polyline", "tbd")

_MODEL_KEYS = {"id", "case_scope", "units", "confidence", "geometry", "drawing", "density_field", "interface", "tbd"}
_DRAWING_KEYS = {"passes", "cumulative_reduction"}
_PASS_KEYS = {"index", "diameter_reduction", "wall_thickness_change", "die_angle_deg"}
_INTERFACE_KEYS = {"gap_mm", "debond"}
_DEBOND_KEYS = {"state", "extent_deg"}


class ModelError(ValueError):
    """模型描述不可用：消息为可直接展示给用户的中文原因。"""


@dataclass(frozen=True)
class ModelSpec:
    """一个已校验的模型描述（字段已规范化为导出文档形态）。"""

    id: str
    units: Mapping[str, str]
    confidence: Mapping[str, str]
    geometry: Mapping[str, Any]
    drawing: Mapping[str, Any] | None
    density_field: Mapping[str, Any]
    interface: Mapping[str, Any]
    include: tuple[str, ...] | None
    exclude: tuple[str, ...]
    declared_tbd: tuple[str, ...]
    source: str
    digest: str

    def applies_to(self, code: str) -> bool:
        """该模型是否绑定到指定工况编码。"""
        if self.include is not None and code not in self.include:
            return False
        return code not in self.exclude


@dataclass(frozen=True)
class ModelSet:
    """一组模型文件合并后的模型集合。"""

    models: tuple[ModelSpec, ...]
    warnings: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.models)

    def by_id(self, model_id: str) -> ModelSpec:
        """按模型 id 取模型。"""
        for item in self.models:
            if item.id == model_id:
                return item
        raise ModelError(f"没有模型 {model_id}")

    def resolve(self, code: str) -> ModelSpec | None:
        """解析工况绑定的模型；命中多个直接报错。"""
        matches = [item for item in self.models if item.applies_to(code)]
        if len(matches) > 1:
            names = ", ".join(item.id for item in matches)
            raise ModelError(f"工况 {code} 同时匹配多个模型: {names}")
        return matches[0] if matches else None

    def validate_against(self, codes: Sequence[str]) -> None:
        """生成前校验：作用域引用必须存在，且每个工况最多命中一个模型。"""
        known = set(codes)
        for item in self.models:
            for name in [*(item.include or ()), *item.exclude]:
                if name not in known:
                    raise ModelError(f"模型 {item.id} 的 case_scope 引用了不存在的工况 {name}")
        for code in codes:
            self.resolve(code)

    def summary(self) -> list[dict[str, Any]]:
        """模型摘要（给 manifest 与 CLI 展示）。"""
        return [
            {
                "id": item.id,
                "digest": item.digest,
                "source": item.source,
                "include": list(item.include) if item.include is not None else [],
                "exclude": list(item.exclude),
                "confidence": dict(item.confidence),
                "tbd": list(model_tbd_paths(item)),
            }
            for item in self.models
        ]


# ---------------------------------------------------------------------- #
# 读取与校验
# ---------------------------------------------------------------------- #
def load_models(paths: Sequence[Path]) -> ModelSet:
    """读取并合并多个模型文件；文件缺失、YAML 损坏或模型 id 冲突时报错。"""
    models: list[ModelSpec] = []
    warnings: list[str] = []
    sources: list[str] = []
    seen: set[str] = set()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ModelError(f"无法读取模型文件 {path}: {exc}") from exc
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ModelError(f"模型文件 {path} 不是合法 YAML: {exc}") from exc
        parsed, parsed_warnings = parse_models(raw, source=path.name)
        for item in parsed:
            if item.id in seen:
                raise ModelError(f"模型 id 重复: {item.id}（{path}）")
            seen.add(item.id)
            models.append(item)
        warnings.extend(f"{path.name}: {message}" for message in parsed_warnings)
        sources.append(str(path))
    return ModelSet(models=tuple(models), warnings=tuple(warnings), sources=tuple(sources))


def parse_models(raw: Any, *, source: str = "") -> tuple[tuple[ModelSpec, ...], tuple[str, ...]]:
    """校验 ``dynacalc-model/1`` 顶层对象，返回 ``(模型列表, 警告)``。"""
    if not isinstance(raw, Mapping):
        raise ModelError("模型文件顶层必须是映射（YAML 对象）")
    schema = raw.get("schema")
    if schema != MODEL_SCHEMA:
        raise ModelError(f"模型文件 schema 应为 {MODEL_SCHEMA}，实际为 {schema!r}")
    warnings: list[str] = []
    for key in raw:
        if key not in {"schema", "models", "meta"}:
            warnings.append(f"未知顶层字段 {key!r}，已忽略")
    raw_models = raw.get("models")
    if not isinstance(raw_models, Sequence) or isinstance(raw_models, (str, bytes)) or not raw_models:
        raise ModelError("models 必须是非空列表")
    models: list[ModelSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_models, 1):
        spec, item_warnings = _parse_model(item, index, source)
        if spec.id in seen:
            raise ModelError(f"模型 id 重复: {spec.id}")
        seen.add(spec.id)
        models.append(spec)
        warnings.extend(item_warnings)
    return tuple(models), tuple(warnings)


def _parse_model(raw: Any, index: int, source: str) -> tuple[ModelSpec, list[str]]:
    """校验单个模型；未知字段只警告。"""
    if not isinstance(raw, Mapping):
        raise ModelError(f"models[{index}] 必须是映射")
    warnings: list[str] = []
    model_id = raw.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ModelError(f"models[{index}] 缺少 id")
    model_id = model_id.strip()
    for key in raw:
        if key not in _MODEL_KEYS:
            warnings.append(f"模型 {model_id} 的未知字段 {key!r}，已忽略")
    units = _parse_units(raw.get("units"), model_id, warnings)
    confidence = _parse_confidence(raw.get("confidence"), model_id, warnings)
    geometry = _parse_geometry(raw.get("geometry"), model_id, warnings)
    drawing = _parse_drawing(_optional_section(raw, "drawing", model_id), model_id, warnings)
    density_field = _parse_density_field(_optional_section(raw, "density_field", model_id), model_id, warnings)
    interface = _parse_interface(_optional_section(raw, "interface", model_id), model_id, warnings)
    include, exclude = _parse_scope(_optional_section(raw, "case_scope", model_id), model_id, warnings)
    tbd_doc = _parse_tbd_doc(raw.get("tbd"), model_id)
    spec = ModelSpec(
        id=model_id,
        units=units,
        confidence=confidence,
        geometry=geometry,
        drawing=drawing,
        density_field=density_field,
        interface=interface,
        include=include,
        exclude=exclude,
        declared_tbd=tbd_doc,
        source=source,
        digest="",
    )
    return replace(spec, digest=model_digest(spec)), warnings


def _parse_units(raw: Any, model_id: str, warnings: list[str]) -> dict[str, str]:
    """单位必须显式写出且三项完全匹配固定工程单位。"""
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 缺少 units（必须是 {UNITS}）")
    for key in raw:
        if key not in UNITS:
            warnings.append(f"模型 {model_id} 的未知单位 units.{key}，已忽略")
    units: dict[str, str] = {}
    for key, expected in UNITS.items():
        value = raw.get(key)
        if value != expected:
            raise ModelError(f"模型 {model_id} 的单位 units.{key} 应为 {expected}，实际为 {value!r}")
        units[key] = expected
    return units


def _parse_confidence(raw: Any, model_id: str, warnings: list[str]) -> dict[str, str]:
    """置信等级：四个域缺省 uncalibrated，未知域警告忽略。"""
    confidence = dict.fromkeys(_CONFIDENCE_DOMAINS, "uncalibrated")
    if raw is None:
        return confidence
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 的 confidence 必须是映射")
    for key, value in raw.items():
        if key not in _CONFIDENCE_DOMAINS:
            warnings.append(f"模型 {model_id} 的 confidence 未知域 {key!r}，已忽略")
            continue
        if value not in CONFIDENCE_LEVELS:
            raise ModelError(
                f"模型 {model_id} 的 confidence.{key} 必须是 {'/'.join(CONFIDENCE_LEVELS)}，实际为 {value!r}"
            )
        confidence[key] = str(value)
    return confidence


def _parse_geometry(raw: Any, model_id: str, warnings: list[str]) -> dict[str, Any]:
    """几何：外径 / 内径 / 长度 / 名义壁厚必填，其余可 TBD。"""
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 缺少 geometry（映射）")
    where = f"模型 {model_id} geometry"
    _warn_unknown(
        raw,
        {"outer_diameter", "inner_diameter", "length", "wall_thickness", "eccentricity"},
        model_id,
        "geometry",
        warnings,
    )
    outer = _required_num(raw, "outer_diameter", where)
    inner = _required_num(raw, "inner_diameter", where)
    length = _required_num(raw, "length", where)
    thickness_raw = raw.get("wall_thickness")
    if not isinstance(thickness_raw, Mapping):
        raise ModelError(f"{where} 缺少 wall_thickness（映射）")
    _warn_unknown(
        thickness_raw, {"nominal", "min", "max", "circumferential"}, model_id, "geometry.wall_thickness", warnings
    )
    thickness: dict[str, Any] = {"nominal": _required_num(thickness_raw, "nominal", f"{where}.wall_thickness")}
    for key in ("min", "max"):
        value = _optional_num(thickness_raw, key, f"{where}.wall_thickness")
        if not is_tbd(value):
            thickness[key] = value
    circumferential = _parse_circumferential(
        thickness_raw.get("circumferential"), model_id, f"{where}.wall_thickness.circumferential", warnings
    )
    if circumferential:
        thickness["circumferential"] = circumferential
    eccentricity_raw = raw.get("eccentricity")
    eccentricity: dict[str, Any] = {}
    if eccentricity_raw is not None:
        if not isinstance(eccentricity_raw, Mapping):
            raise ModelError(f"{where}.eccentricity 必须是映射")
        _warn_unknown(
            eccentricity_raw,
            {"radial_mm", "circumferential_mm", "angle_deg"},
            model_id,
            "geometry.eccentricity",
            warnings,
        )
        for key in ("radial_mm", "circumferential_mm", "angle_deg"):
            value = _optional_num(eccentricity_raw, key, f"{where}.eccentricity")
            if not is_tbd(value):
                eccentricity[key] = value
    _check_geometry(outer, inner, thickness)
    angle = eccentricity.get("angle_deg", TBD)
    if not is_tbd(angle) and not 0.0 <= float(angle) < 360.0:
        raise ModelError(f"{where}.eccentricity.angle_deg 必须在 [0, 360)：{angle!r}")
    block: dict[str, Any] = {
        "outer_diameter": outer,
        "inner_diameter": inner,
        "length": length,
        "wall_thickness": thickness,
    }
    if eccentricity:
        block["eccentricity"] = eccentricity
    return block


def _parse_circumferential(raw: Any, model_id: str, where: str, warnings: list[str]) -> dict[str, Any]:
    """壁厚周向分布：``uniform`` / ``polyline`` / ``tbd``；全 TBD 时省略该段。"""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ModelError(f"{where} 必须是映射")
    kind = raw.get("kind")
    if kind not in _CIRCUMFERENTIAL_KINDS:
        raise ModelError(f"{where}.kind 必须是 {'/'.join(_CIRCUMFERENTIAL_KINDS)}，实际为 {kind!r}")
    if kind == "tbd":
        return {}
    if kind == "uniform":
        _warn_unknown(raw, {"kind", "value"}, model_id, "geometry.wall_thickness.circumferential", warnings)
        value = _required_num(raw, "value", where)
        if is_tbd(value):
            raise ModelError(f"{where}.value 不能是 TBD；未标定请改用 kind: tbd")
        return {"kind": "uniform", "value": value}
    _warn_unknown(raw, {"kind", "samples"}, model_id, "geometry.wall_thickness.circumferential", warnings)
    samples_raw = raw.get("samples")
    if not isinstance(samples_raw, Sequence) or isinstance(samples_raw, (str, bytes)) or len(samples_raw) < 2:
        raise ModelError(f"{where}.samples 至少需要 2 个点")
    samples: list[dict[str, Any]] = []
    previous = -1.0
    for index, item in enumerate(samples_raw, 1):
        spot = f"{where}.samples[{index}]"
        if not isinstance(item, Mapping):
            raise ModelError(f"{spot} 必须是映射")
        _warn_unknown(item, {"angle_deg", "thickness"}, model_id, f"{where}.samples[{index}]", warnings)
        angle = _required_num(item, "angle_deg", spot)
        if is_tbd(angle):
            raise ModelError(f"{spot}.angle_deg 不能是 TBD")
        if not 0.0 <= float(angle) < 360.0:
            raise ModelError(f"{spot}.angle_deg 必须在 [0, 360)：{angle!r}")
        if float(angle) <= previous:
            raise ModelError(f"{where}.samples 的 angle_deg 必须严格递增")
        previous = float(angle)
        samples.append({"angle_deg": angle, "thickness": _required_num(item, "thickness", spot)})
    return {"kind": "polyline", "samples": samples}


def _check_geometry(outer: Any, inner: Any, thickness: Mapping[str, Any]) -> None:
    """几何一致性：内外径、壁厚范围。"""
    if not is_tbd(inner) and not is_tbd(outer) and float(inner) >= float(outer):
        raise ModelError(f"内径必须小于外径：{inner!r} >= {outer!r}")
    nominal = thickness["nominal"]
    low = thickness.get("min", TBD)
    high = thickness.get("max", TBD)
    if not is_tbd(nominal) and not is_tbd(low) and float(low) > float(nominal):
        raise ModelError(f"最小壁厚不能大于名义壁厚：{low!r} > {nominal!r}")
    if not is_tbd(nominal) and not is_tbd(high) and float(nominal) > float(high):
        raise ModelError(f"名义壁厚不能大于最大壁厚：{nominal!r} > {high!r}")
    if not is_tbd(low) and not is_tbd(high) and float(low) > float(high):
        raise ModelError(f"最小壁厚不能大于最大壁厚：{low!r} > {high!r}")


def _parse_drawing(  # noqa: PLR0912 - 逐道次校验与省略规则，展开写比表驱动更易审计
    raw: Any, model_id: str, warnings: list[str]
) -> dict[str, Any] | None:
    """拉拔道次：index 必填且唯一；无道次时整节省略（与 paramcad 导出一致）。"""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 的 drawing 必须是映射")
    _warn_unknown(raw, _DRAWING_KEYS, model_id, "drawing", warnings)
    passes_raw = raw.get("passes", [])
    if not isinstance(passes_raw, Sequence) or isinstance(passes_raw, (str, bytes)):
        raise ModelError(f"模型 {model_id} 的 drawing.passes 必须是列表")
    passes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(passes_raw, 1):
        if not isinstance(item, Mapping):
            raise ModelError(f"模型 {model_id} 的 drawing.passes[{index}] 必须是映射")
        _warn_unknown(item, _PASS_KEYS, model_id, f"drawing.passes[{index}]", warnings)
        sequence = item.get("index")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ModelError(f"模型 {model_id} 的 drawing.passes[{index}].index 必须是 ≥1 的整数")
        if sequence in seen:
            raise ModelError(f"模型 {model_id} 的 drawing.passes 序号重复: {sequence}")
        seen.add(sequence)
        pass_where = f"模型 {model_id} drawing.passes[{index}]"
        entry: dict[str, Any] = {"index": sequence}
        reduction = _optional_num(item, "diameter_reduction", pass_where)
        if not is_tbd(reduction) and not 0.0 <= float(reduction) < 1.0:
            raise ModelError(f"{pass_where}.diameter_reduction 必须在 [0, 1)：{reduction!r}")
        change = _optional_num(item, "wall_thickness_change", pass_where)
        if not is_tbd(change) and float(change) <= -1.0:
            raise ModelError(f"{pass_where}.wall_thickness_change 必须大于 -1：{change!r}")
        die_angle = _optional_num(item, "die_angle_deg", pass_where)
        if not is_tbd(die_angle) and not 0.0 < float(die_angle) < 90.0:
            raise ModelError(f"{pass_where}.die_angle_deg 必须在 (0, 90)：{die_angle!r}")
        for key, value in (
            ("diameter_reduction", reduction),
            ("wall_thickness_change", change),
            ("die_angle_deg", die_angle),
        ):
            if not is_tbd(value):
                entry[key] = value
        passes.append(entry)
    cumulative = _optional_num(raw, "cumulative_reduction", f"模型 {model_id} drawing")
    if not is_tbd(cumulative) and not 0.0 <= float(cumulative) < 1.0:
        raise ModelError(f"模型 {model_id} 的 drawing.cumulative_reduction 必须在 [0, 1)：{cumulative!r}")
    if not passes and is_tbd(cumulative):
        return None
    drawing: dict[str, Any] = {"passes": sorted(passes, key=lambda item: item["index"])}
    if not is_tbd(cumulative):
        drawing["cumulative_reduction"] = cumulative
    return drawing


def _parse_density_field(raw: Any, model_id: str, warnings: list[str]) -> dict[str, Any]:
    """密度场：只保留文档给出的轴（缺失轴不补键，保证指纹与 paramcad 一致）。"""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 的 density_field 必须是映射")
    _warn_unknown(raw, {*_DENSITY_AXES, "local_defects", "end_loose"}, model_id, "density_field", warnings)
    block: dict[str, Any] = {}
    for axis in _DENSITY_AXES:
        if raw.get(axis) is not None:
            profile = _parse_profile(raw[axis], model_id, axis, warnings)
            if profile != {"kind": "tbd"}:
                block[axis] = profile
    end_loose = _parse_end_loose(raw.get("end_loose"), model_id, warnings)
    if end_loose:
        block["end_loose"] = end_loose
    defects = _parse_defects(raw.get("local_defects"), model_id, warnings)
    if defects:
        block["local_defects"] = defects
    return block


def _parse_end_loose(raw: Any, model_id: str, warnings: list[str]) -> dict[str, Any]:
    """端部疏松段（轴向尾端低密度）：长度占比与端部密度，未标定项不写键。"""
    if raw is None:
        return {}
    where = f"模型 {model_id} density_field.end_loose"
    if not isinstance(raw, Mapping):
        raise ModelError(f"{where} 必须是映射")
    _warn_unknown(raw, {"length_fraction", "density"}, model_id, "density_field.end_loose", warnings)
    block: dict[str, Any] = {}
    fraction = _optional_num(raw, "length_fraction", where)
    if not is_tbd(fraction):
        if not 0.0 < float(fraction) <= 1.0:
            raise ModelError(f"{where}.length_fraction 必须在 (0, 1]：{fraction!r}")
        block["length_fraction"] = fraction
    density = _optional_num(raw, "density", where)
    if not is_tbd(density):
        block["density"] = density
    return block


def _parse_profile(raw: Any, model_id: str, axis: str, warnings: list[str]) -> dict[str, Any]:
    """单个密度轴：``tbd`` / ``uniform`` / ``polyline``。"""
    where = f"模型 {model_id} density_field.{axis}"
    if raw is None:
        return {"kind": "tbd"}
    if not isinstance(raw, Mapping):
        raise ModelError(f"{where} 必须是映射")
    kind = raw.get("kind")
    if kind not in _PROFILE_KINDS:
        raise ModelError(f"{where}.kind 必须是 {'/'.join(_PROFILE_KINDS)}，实际为 {kind!r}")
    if kind == "tbd":
        return {"kind": "tbd"}
    if kind == "uniform":
        _warn_unknown(raw, {"kind", "value"}, model_id, f"density_field.{axis}", warnings)
        value = _required_num(raw, "value", where)
        if is_tbd(value):
            raise ModelError(f"{where}.value 不能是 TBD；未标定请改用 kind: tbd")
        return {"kind": "uniform", "value": value}
    _warn_unknown(raw, {"kind", "samples"}, model_id, f"density_field.{axis}", warnings)
    samples_raw = raw.get("samples")
    if not isinstance(samples_raw, Sequence) or isinstance(samples_raw, (str, bytes)) or len(samples_raw) < 2:
        raise ModelError(f"{where}.samples 至少需要 2 个点")
    samples: list[dict[str, Any]] = []
    previous = -1.0
    for index, item in enumerate(samples_raw, 1):
        spot = f"{where}.samples[{index}]"
        if not isinstance(item, Mapping):
            raise ModelError(f"{spot} 必须是映射")
        _warn_unknown(item, {"position", "density"}, model_id, f"density_field.{axis}.samples[{index}]", warnings)
        position = _required_num(item, "position", spot)
        if is_tbd(position):
            raise ModelError(f"{spot}.position 不能是 TBD")
        if not 0.0 <= float(position) <= 1.0:
            raise ModelError(f"{spot}.position 必须在 0–1：{position!r}")
        if float(position) <= previous:
            raise ModelError(f"{where}.samples 的 position 必须严格递增")
        previous = float(position)
        samples.append({"position": position, "density": _required_num(item, "density", spot)})
    return {"kind": "polyline", "samples": samples}


def _parse_defects(raw: Any, model_id: str, warnings: list[str]) -> list[dict[str, Any]]:
    """局部缺陷：id 唯一、kind 受限；可选项为 TBD 时与缺失同形（不写键）。"""
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ModelError(f"模型 {model_id} 的 density_field.local_defects 必须是列表")
    defects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise ModelError(f"模型 {model_id} 的 local_defects[{index}] 必须是映射")
        _warn_unknown(
            item,
            {"id", "kind", "location", "size_mm", "severity"},
            model_id,
            f"density_field.local_defects[{index}]",
            warnings,
        )
        defect_id = item.get("id")
        if not isinstance(defect_id, str) or not defect_id.strip():
            raise ModelError(f"模型 {model_id} 的 local_defects[{index}] 缺少 id")
        if defect_id in seen:
            raise ModelError(f"模型 {model_id} 的缺陷 id 重复: {defect_id}")
        seen.add(defect_id)
        kind = item.get("kind")
        if kind not in _DEFECT_KINDS:
            raise ModelError(f"模型 {model_id} 的缺陷 {defect_id} 的 kind 必须是 {'/'.join(_DEFECT_KINDS)}")
        location = _parse_defect_location(item.get("location"), model_id, defect_id)
        defect: dict[str, Any] = {"id": defect_id, "kind": str(kind)}
        if location:
            defect["location"] = location
        for key in ("size_mm", "severity"):
            value = _optional_num(item, key, f"模型 {model_id} 缺陷 {defect_id}")
            if not is_tbd(value):
                defect[key] = value
        defects.append(defect)
    return defects


def _parse_defect_location(raw: Any, model_id: str, defect_id: str) -> dict[str, Any]:
    """缺陷位置：轴向/径向归一化坐标与周向角度。"""
    where = f"模型 {model_id} 缺陷 {defect_id} location"
    if raw is not None and not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 的缺陷 {defect_id} 的 location 必须是映射")
    raw = raw or {}
    location: dict[str, Any] = {}
    for key in ("axial", "radial"):
        value = _optional_num(raw, key, where)
        if is_tbd(value):
            continue
        if not 0.0 <= float(value) <= 1.0:
            raise ModelError(f"模型 {model_id} 的缺陷 {defect_id} 的 location.{key} 必须在 0–1")
        location[key] = value
    angle = _optional_num(raw, "angle_deg", where)
    if not is_tbd(angle):
        if not 0.0 <= float(angle) < 360.0:
            raise ModelError(f"模型 {model_id} 的缺陷 {defect_id} 的 location.angle_deg 必须在 [0, 360)")
        location["angle_deg"] = angle
    return location


def _parse_interface(raw: Any, model_id: str, warnings: list[str]) -> dict[str, Any]:
    """间隙与脱粘状态；TBD 字段不写键（与 paramcad 导出的省略形态一致）。"""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 的 interface 必须是映射")
    _warn_unknown(raw, _INTERFACE_KEYS, model_id, "interface", warnings)
    block: dict[str, Any] = {}
    gap = _optional_num(raw, "gap_mm", f"模型 {model_id} interface")
    if not is_tbd(gap):
        block["gap_mm"] = gap
    debond_raw = raw.get("debond")
    debond: dict[str, Any] = {}
    if debond_raw is not None:
        if not isinstance(debond_raw, Mapping):
            raise ModelError(f"模型 {model_id} 的 interface.debond 必须是映射")
        _warn_unknown(debond_raw, _DEBOND_KEYS, model_id, "interface.debond", warnings)
        state = debond_raw.get("state", "tbd")
        if state not in _DEBOND_STATES:
            raise ModelError(f"模型 {model_id} 的 interface.debond.state 必须是 {'/'.join(_DEBOND_STATES)}")
        if state != "tbd":
            debond["state"] = str(state)
        extent = _optional_num(debond_raw, "extent_deg", f"模型 {model_id} interface.debond")
        if not is_tbd(extent):
            debond["extent_deg"] = extent
    if debond:
        block["debond"] = debond
    return block


def _parse_scope(raw: Any, model_id: str, warnings: list[str]) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """工况绑定：include 缺省表示全部，exclude 后应用。"""
    if raw is None:
        return None, ()
    if not isinstance(raw, Mapping):
        raise ModelError(f"模型 {model_id} 的 case_scope 必须是映射")
    _warn_unknown(raw, {"include", "exclude"}, model_id, "case_scope", warnings)
    include: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    if raw.get("include") is not None:
        include = _scope_codes(raw["include"], model_id, "include", allow_empty=False)
    if raw.get("exclude") is not None:
        exclude = _scope_codes(raw["exclude"], model_id, "exclude", allow_empty=True)
    return include, exclude


def _scope_codes(raw: Any, model_id: str, key: str, *, allow_empty: bool) -> tuple[str, ...]:
    """校验 case_scope 的编码列表。"""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or (not raw and not allow_empty):
        raise ModelError(f"模型 {model_id} 的 case_scope.{key} 必须是{'可空' if allow_empty else '非空'}列表")
    codes: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ModelError(f"模型 {model_id} 的 case_scope.{key} 含非法工况编码")
        codes.append(item.strip())
    return tuple(dict.fromkeys(codes))


# ---------------------------------------------------------------------- #
# 数值与未知字段辅助
# ---------------------------------------------------------------------- #
def _warn_unknown(raw: Mapping[str, Any], allowed: set[str], model_id: str, path: str, warnings: list[str]) -> None:
    """未知字段只警告，保证 v1 只做加法的向前兼容。"""
    for key in raw:
        if key not in allowed:
            warnings.append(f"模型 {model_id} 的未知字段 {path}.{key}，已忽略")


def _optional_section(raw: Mapping[str, Any], key: str, model_id: str) -> Any:
    """可选段落：缺省返回 None，显式 ``null`` 视为非法（避免「静默当没有」）。"""
    if key not in raw:
        return None
    if raw[key] is None:
        raise ModelError(f"模型 {model_id} 的 {key} 不能为 null；请省略该字段或填写映射")
    return raw[key]


def _parse_tbd_doc(raw: Any, model_id: str) -> tuple[str, ...]:
    """文档性 TBD 清单：只接受非空文本列表，重复项去重。"""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ModelError(f"模型 {model_id} 的 tbd 必须是文本列表")
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ModelError(f"模型 {model_id} 的 tbd 含非法条目: {item!r}")
    return tuple(dict.fromkeys(item.strip() for item in raw))


def _num(value: Any, where: str) -> float | str:
    """数值或 ``TBD``；``null``、布尔、列表、非数值文本、非有限值都报错。"""
    if isinstance(value, str) and value.strip().upper() == TBD:
        return TBD
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelError(f"{where} 必须是数值或 TBD，实际为 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ModelError(f"{where} 必须是有限数值，实际为 {value!r}")
    return number


def _required_num(raw: Mapping[str, Any], key: str, where: str) -> float | str:
    """必填数值字段。"""
    if key not in raw or raw[key] is None:
        raise ModelError(f"{where}.{key} 是必填字段")
    return _num(raw[key], f"{where}.{key}")


def _optional_num(raw: Mapping[str, Any], key: str, where: str = "") -> float | str:
    """可选数值字段：缺失即 TBD（不填 0，不猜默认值）。"""
    if key not in raw or raw[key] is None:
        return TBD
    label = f"{where}.{key}" if where else key
    return _num(raw[key], label)


# ---------------------------------------------------------------------- #
# 指纹、派生键与工况合并
# ---------------------------------------------------------------------- #
def _plain(value: Any) -> Any:
    """把嵌套映射/元组转成纯 JSON 结构，数值统一为 ``float``。"""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _digest_payload(spec: ModelSpec) -> dict[str, Any]:
    """参与指纹的功能载荷：与 paramcad 导出的模型文档逐字段同形。

    归一化要点（契约第 8 节）：可选段缺失或全为 TBD 时整节省略；可选字段为 TBD
    时不写键；`uniform` 的 `value` 不允许 TBD；`drawing.cumulative_reduction` 由
    道次推导后写入（推不出则省略键）。
    """
    payload: dict[str, Any] = {
        "id": spec.id,
        "units": dict(spec.units),
        "confidence": {name: spec.confidence.get(name, "uncalibrated") for name in _CONFIDENCE_DOMAINS},
        "geometry": _plain(spec.geometry),
    }
    if spec.drawing is not None:
        drawing = _plain(spec.drawing)
        drawing.pop("cumulative_reduction", None)
        reduction = _drawing_totals(spec.drawing)[1]
        if not is_tbd(reduction):
            drawing["cumulative_reduction"] = reduction
        payload["drawing"] = drawing
    if spec.density_field:
        payload["density_field"] = _plain(spec.density_field)
    if spec.interface:
        payload["interface"] = _plain(spec.interface)
    return payload


def model_digest(spec: ModelSpec) -> str:
    """模型指纹：规范 JSON 的 SHA-256（64 位小写十六进制）。"""
    return hashlib.sha256(canonical_json(_digest_payload(spec)).encode("utf-8")).hexdigest()


def model_tbd_paths(spec: ModelSpec) -> tuple[str, ...]:
    """模型规范化载荷中的 TBD 点路径（用于摘要，不作为权威数据源）。"""
    paths: list[str] = []

    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{prefix}[{index}]")
        elif is_tbd(value):
            paths.append(prefix)

    walk(_digest_payload(spec), "")
    return tuple(dict.fromkeys((*spec.declared_tbd, *paths)))


def _axial_stats(axial: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    """轴向密度 min / max / mean；任一点 TBD 则三者都是 TBD。"""
    if axial.get("kind") == "uniform":
        value = axial.get("value", TBD)
        return value, value, value
    if axial.get("kind") != "polyline":
        return TBD, TBD, TBD
    values = [item.get("density", TBD) for item in axial.get("samples", [])]
    if not values or any(is_tbd(value) for value in values):
        return TBD, TBD, TBD
    numbers = [float(value) for value in values]
    return min(numbers), max(numbers), sum(numbers) / len(numbers)


def _drawing_totals(drawing: Mapping[str, Any] | None) -> tuple[Any, Any, Any]:
    """拉拔道次数、累计减径率、累计壁厚变化率；无拉拔节时三者都是 TBD。"""
    if drawing is None:
        return TBD, TBD, TBD
    passes = list(drawing.get("passes") or [])
    reduction = drawing.get("cumulative_reduction", TBD)
    if is_tbd(reduction) and passes:
        factors = [item.get("diameter_reduction", TBD) for item in passes]
        if all(not is_tbd(value) for value in factors):
            product = 1.0
            for value in factors:
                product *= 1.0 - float(value)
            reduction = 1.0 - product
    changes = [item.get("wall_thickness_change", TBD) for item in passes]
    wall_change: Any = TBD
    if passes and all(not is_tbd(value) for value in changes):
        wall_change = sum(float(value) for value in changes)
    return len(passes), reduction, wall_change


def _circumferential_stats(thickness: Mapping[str, Any]) -> tuple[str, Any, Any, Any, Any]:
    """周向壁厚分布的 kind / min / max / mean / 曲线文本（缺失或 TBD 时为 TBD）。"""
    block = thickness.get("circumferential") or {}
    kind = block.get("kind", "tbd")
    if kind == "uniform":
        value = block.get("value", TBD)
        curve = TBD if is_tbd(value) else f"0:{float(value)!r}"
        return kind, value, value, value, curve
    if kind != "polyline":
        return "tbd", TBD, TBD, TBD, TBD
    samples = list(block.get("samples") or [])
    values = [item.get("thickness", TBD) for item in samples]
    if not values or any(is_tbd(value) for value in values):
        return kind, TBD, TBD, TBD, TBD
    numbers = [float(value) for value in values]
    curve = ";".join(f"{float(item['angle_deg'])!r}:{float(item['thickness'])!r}" for item in samples)
    return kind, min(numbers), max(numbers), sum(numbers) / len(numbers), curve


def derived_values(spec: ModelSpec) -> dict[str, Any]:
    """模型 → deck 派生键（扁平、缺失即 TBD）。"""
    geometry = spec.geometry
    thickness = geometry.get("wall_thickness") or {}
    eccentricity = geometry.get("eccentricity") or {}
    interface = spec.interface
    debond = interface.get("debond") or {}
    density_field = spec.density_field
    axial = density_field.get("axial") or {"kind": "tbd"}
    axial_min, axial_max, axial_mean = _axial_stats(axial)
    end_loose = density_field.get("end_loose") or {}
    passes, reduction, wall_change = _drawing_totals(spec.drawing)
    defects = list(density_field.get("local_defects") or [])
    circ_kind, circ_min, circ_max, circ_mean, circ_curve = _circumferential_stats(thickness)
    return {
        "model_id": spec.id,
        "model_digest": spec.digest,
        "outer_diameter": geometry.get("outer_diameter", TBD),
        "inner_diameter": geometry.get("inner_diameter", TBD),
        "length": geometry.get("length", TBD),
        "wall_thickness": thickness.get("nominal", TBD),
        "wall_thickness_min": thickness.get("min", TBD),
        "wall_thickness_max": thickness.get("max", TBD),
        "wall_thickness_circumferential_kind": circ_kind,
        "wall_thickness_circumferential_min": circ_min,
        "wall_thickness_circumferential_max": circ_max,
        "wall_thickness_circumferential_mean": circ_mean,
        "wall_thickness_circumferential_curve": circ_curve,
        "eccentricity_radial": eccentricity.get("radial_mm", TBD),
        "eccentricity_circumferential": eccentricity.get("circumferential_mm", TBD),
        "eccentricity_angle_deg": eccentricity.get("angle_deg", TBD),
        "gap_mm": interface.get("gap_mm", TBD),
        "debond_state": debond.get("state", TBD),
        "debond_extent_deg": debond.get("extent_deg", TBD),
        "drawing_passes": passes,
        "drawing_reduction_total": reduction,
        "drawing_wall_change_total": wall_change,
        "density_axial_kind": axial.get("kind", "tbd"),
        "density_axial_min": axial_min,
        "density_axial_max": axial_max,
        "density_axial_mean": axial_mean,
        "density_end_loose_length_fraction": end_loose.get("length_fraction", TBD),
        "density_end_loose_density": end_loose.get("density", TBD),
        "density_radial_kind": (density_field.get("radial") or {}).get("kind", "tbd"),
        "density_circumferential_kind": (density_field.get("circumferential") or {}).get("kind", "tbd"),
        "defect_count": len(defects),
        "defect_ids": ",".join(str(item.get("id", "")) for item in defects),
    }


def apply_model(case: Case, spec: ModelSpec, template_digest: str) -> Case:
    """把模型的非 TBD 派生值叠加到工况，并重算含模型指纹的工况指纹。"""
    merged = case.value_mapping()
    for key, value in derived_values(spec).items():
        if is_tbd(value):
            continue
        merged[key] = value
    levels = case.level_codes
    fingerprint = case_fingerprint(
        Case(code=case.code, group=case.group, level_codes=levels, values=tuple(merged.items())),
        template_digest,
        spec.digest,
    )
    return Case(
        code=case.code,
        group=case.group,
        level_codes=levels,
        values=tuple(merged.items()),
        fingerprint=fingerprint,
    )
