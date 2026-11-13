"""读取 ``dynacalc-model/1``，只为网格生成提取几何与分布场信息。

本模块是**消费方**实现：按契约做结构校验与指纹规范化，不修改模型文件，
也不把密度值、拉拔工艺等下游才用到的字段带进网格。TBD 分为「必须给出 /
可留 TBD / 不允许 TBD」三类，可留 TBD 的字段一律降级并给出警告，不直接报错。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

import yaml

from fcmd.apps.paramesh._errors import ParameshError

__all__ = [
    "CONFIDENCE_DOMAINS",
    "DEFECT_KINDS",
    "MODEL_SCHEMA",
    "TBD_TEXT",
    "UNITS",
    "LocalDefectSpec",
    "MeshModel",
    "canonical_payload_json",
    "digest_of_payload",
    "format_model_summary",
    "load_models",
    "parse_models",
    "select_model",
]

MODEL_SCHEMA = "dynacalc-model/1"
TBD_TEXT = "TBD"
UNITS: dict[str, str] = {"length": "mm", "density": "g/cm^3", "velocity": "m/s"}
CONFIDENCE_DOMAINS = ("geometry", "drawing", "density_field", "interface")
CONFIDENCE_LEVELS = ("public", "controlled", "uncalibrated")
DENSITY_AXES = ("axial", "radial", "circumferential")
POLYLINE_KINDS = ("uniform", "polyline", "tbd")
DEFECT_KINDS = ("void", "thin_layer", "crack", "debond")
DEBOND_STATES = ("bonded", "debonded", "tbd")

_KNOWN_MODEL_KEYS = frozenset(
    {
        "id",
        "case_scope",
        "units",
        "confidence",
        "geometry",
        "drawing",
        "density_field",
        "interface",
        "tbd",
        "meta",
    }
)


def _fail(message: str) -> NoReturn:
    raise ParameshError(message)


def _mapping(value: Any, path: str, *, required: bool) -> dict[str, Any] | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if not isinstance(value, dict):
        _fail(f"{path} 必须是映射，实际为 {type(value).__name__}")
    return cast("dict[str, Any]", value)


def _sequence(value: Any, path: str, *, required: bool) -> list[Any] | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(f"{path} 必须是列表，实际为 {type(value).__name__}")
    return list(value)


def _text(value: Any, path: str, *, required: bool) -> str | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if not isinstance(value, str):
        _fail(f"{path} 必须是文本，实际为 {type(value).__name__}")
    if value == TBD_TEXT:
        _fail(f"{path} 是文本字段，不允许 TBD")
    if not value.strip():
        _fail(f"{path} 不能为空")
    return value


def _enum(value: Any, path: str, allowed: Sequence[str], *, required: bool) -> str | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if not isinstance(value, str):
        _fail(f"{path} 必须是文本枚举，实际为 {type(value).__name__}")
    if value == TBD_TEXT:
        _fail(f"{path} 是枚举字段，不允许 TBD（未定请用 tbd）")
    if value not in allowed:
        _fail(f"{path} 取值未知：{value!r}，允许 {', '.join(allowed)}")
    return value


def _num(value: Any, path: str, *, required: bool, allow_tbd: bool = True) -> float | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if isinstance(value, str):
        if value == TBD_TEXT:
            if allow_tbd:
                return None
            _fail(f"{path} 不允许 TBD（未定请用 kind: tbd 或省略该键）")
        _fail(f"{path} 必须是数值或 TBD，实际为 {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{path} 必须是数值，实际为 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{path} 必须是有限数值")
    return number


def _required_num(value: Any, path: str) -> float:
    number = _num(value, path, required=True, allow_tbd=False)
    if number is None:  # pragma: no cover - allow_tbd=False 已保证非 None
        _fail(f"{path} 缺失")
    return number


def _integer(value: Any, path: str, *, required: bool) -> int | None:
    if value is None:
        if required:
            _fail(f"{path} 缺失")
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{path} 必须是整数，实际为 {type(value).__name__}")
    return value


def _units(value: Any) -> dict[str, str]:
    raw = _mapping(value, "units", required=True)
    assert raw is not None
    result: dict[str, str] = {}
    for key, expected in UNITS.items():
        actual = raw.get(key)
        if actual != expected:
            _fail(f"units.{key} 必须是 {expected}，实际为 {actual!r}")
        result[key] = expected
    return result


def _confidence(value: Any, warnings: list[str]) -> dict[str, str]:
    raw = _mapping(value, "confidence", required=False)
    result: dict[str, str] = {}
    for domain in CONFIDENCE_DOMAINS:
        if raw is None or domain not in raw:
            result[domain] = "uncalibrated"
            continue
        level = _enum(raw.get(domain), f"confidence.{domain}", CONFIDENCE_LEVELS, required=True)
        assert level is not None
        result[domain] = level
    if raw is not None:
        unknown = sorted(set(raw) - set(CONFIDENCE_DOMAINS))
        if unknown:
            warnings.append(f"confidence 含未知域，已忽略：{', '.join(unknown)}")
    return result


def _circumferential_payload(value: Any, path: str) -> dict[str, Any] | None:
    block = _mapping(value, path, required=False)
    if block is None:
        return None
    kind = _enum(block.get("kind"), f"{path}.kind", POLYLINE_KINDS, required=True)
    if kind == "tbd":
        return None
    if kind == "uniform":
        uniform = _required_num(block.get("value"), f"{path}.value")
        return {"kind": "uniform", "value": uniform}
    samples_raw = _sequence(block.get("samples"), f"{path}.samples", required=True)
    assert samples_raw is not None
    samples: list[dict[str, Any]] = []
    previous = -math.inf
    for index, item in enumerate(samples_raw):
        sample = _mapping(item, f"{path}.samples[{index}]", required=True)
        assert sample is not None
        angle = _required_num(sample.get("angle_deg"), f"{path}.samples[{index}].angle_deg")
        if not 0.0 <= angle < 360.0:
            _fail(f"{path}.samples[{index}].angle_deg 必须在 [0, 360) 内")
        if angle <= previous:
            _fail(f"{path}.samples 的角度必须严格递增")
        previous = angle
        thickness = _num(sample.get("thickness"), f"{path}.samples[{index}].thickness", required=True)
        samples.append({"angle_deg": angle, "thickness": thickness if thickness is not None else TBD_TEXT})
    if len(samples) < 2:
        _fail(f"{path}.samples 至少需要 2 个点")
    return {"kind": "polyline", "samples": samples}


def _geometry_payload(value: Any, path: str) -> dict[str, Any]:
    geometry = _mapping(value, path, required=True)
    assert geometry is not None
    outer = _required_num(geometry.get("outer_diameter"), f"{path}.outer_diameter")
    inner = _required_num(geometry.get("inner_diameter"), f"{path}.inner_diameter")
    length = _required_num(geometry.get("length"), f"{path}.length")
    if inner >= outer:
        _fail(f"{path}.inner_diameter 必须小于 outer_diameter")
    wall_raw = _mapping(geometry.get("wall_thickness"), f"{path}.wall_thickness", required=True)
    assert wall_raw is not None
    nominal = _required_num(wall_raw.get("nominal"), f"{path}.wall_thickness.nominal")
    wall: dict[str, Any] = {"nominal": nominal}
    low = _num(wall_raw.get("min"), f"{path}.wall_thickness.min", required=False)
    high = _num(wall_raw.get("max"), f"{path}.wall_thickness.max", required=False)
    if low is not None:
        wall["min"] = low
    if high is not None:
        wall["max"] = high
    if low is not None and high is not None and not low <= nominal <= high:
        _fail(f"{path}.wall_thickness 需满足 min <= nominal <= max")
    circumferential = _circumferential_payload(
        wall_raw.get("circumferential"), f"{path}.wall_thickness.circumferential"
    )
    if circumferential is not None:
        wall["circumferential"] = circumferential

    payload: dict[str, Any] = {
        "outer_diameter": outer,
        "inner_diameter": inner,
        "length": length,
        "wall_thickness": wall,
    }
    eccentricity_raw = _mapping(geometry.get("eccentricity"), f"{path}.eccentricity", required=False)
    if eccentricity_raw is not None:
        eccentricity: dict[str, Any] = {}
        for key in ("radial_mm", "circumferential_mm", "angle_deg"):
            component = _num(eccentricity_raw.get(key), f"{path}.eccentricity.{key}", required=False)
            if component is not None:
                eccentricity[key] = component
        if eccentricity:
            payload["eccentricity"] = eccentricity
    return payload


def _axis_payload(value: Any, path: str) -> dict[str, Any] | None:
    block = _mapping(value, path, required=False)
    if block is None:
        return None
    kind = _enum(block.get("kind"), f"{path}.kind", POLYLINE_KINDS, required=True)
    if kind == "tbd":
        return None
    if kind == "uniform":
        uniform = _required_num(block.get("value"), f"{path}.value")
        return {"kind": "uniform", "value": uniform}
    samples_raw = _sequence(block.get("samples"), f"{path}.samples", required=True)
    assert samples_raw is not None
    samples: list[dict[str, Any]] = []
    previous = -math.inf
    for index, item in enumerate(samples_raw):
        sample = _mapping(item, f"{path}.samples[{index}]", required=True)
        assert sample is not None
        position = _required_num(sample.get("position"), f"{path}.samples[{index}].position")
        if not 0.0 <= position <= 1.0:
            _fail(f"{path}.samples[{index}].position 必须在 0–1 内")
        if position <= previous:
            _fail(f"{path}.samples 的 position 必须严格递增")
        previous = position
        density = _num(sample.get("density"), f"{path}.samples[{index}].density", required=True)
        samples.append({"position": position, "density": density if density is not None else TBD_TEXT})
    if len(samples) < 2:
        _fail(f"{path}.samples 至少需要 2 个点")
    return {"kind": "polyline", "samples": samples}


def _drawing_payload(value: Any, path: str) -> dict[str, Any] | None:  # noqa: PLR0912 - 逐道次校验
    block = _mapping(value, path, required=False)
    if block is None:
        return None
    passes_raw = _sequence(block.get("passes"), f"{path}.passes", required=False) or []
    passes: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(passes_raw):
        entry = _mapping(item, f"{path}.passes[{index}]", required=True)
        assert entry is not None
        pass_index = _integer(entry.get("index"), f"{path}.passes[{index}].index", required=True)
        assert pass_index is not None
        if pass_index < 1:
            _fail(f"{path}.passes[{index}].index 必须 ≥ 1")
        if pass_index in seen:
            _fail(f"{path}.passes 的 index 必须唯一")
        seen.add(pass_index)
        normalized: dict[str, Any] = {"index": pass_index}
        reduction = _num(entry.get("diameter_reduction"), f"{path}.passes[{index}].diameter_reduction", required=False)
        if reduction is not None:
            if not 0.0 <= reduction < 1.0:
                _fail(f"{path}.passes[{index}].diameter_reduction 必须在 [0, 1) 内")
            normalized["diameter_reduction"] = reduction
        wall_change = _num(
            entry.get("wall_thickness_change"), f"{path}.passes[{index}].wall_thickness_change", required=False
        )
        if wall_change is not None:
            if wall_change <= -1.0:
                _fail(f"{path}.passes[{index}].wall_thickness_change 必须 > -1")
            normalized["wall_thickness_change"] = wall_change
        die_angle = _num(entry.get("die_angle_deg"), f"{path}.passes[{index}].die_angle_deg", required=False)
        if die_angle is not None:
            if not 0.0 < die_angle < 90.0:
                _fail(f"{path}.passes[{index}].die_angle_deg 必须在 (0, 90) 内")
            normalized["die_angle_deg"] = die_angle
        passes.append(normalized)
    passes.sort(key=lambda item: cast(int, item["index"]))

    cumulative = _num(block.get("cumulative_reduction"), f"{path}.cumulative_reduction", required=False)
    if cumulative is not None:
        if not 0.0 <= cumulative < 1.0:
            _fail(f"{path}.cumulative_reduction 必须在 [0, 1) 内")
    elif passes and all("diameter_reduction" in item for item in passes):
        remaining = 1.0
        for item in passes:
            remaining *= 1.0 - cast(float, item["diameter_reduction"])
        cumulative = 1.0 - remaining

    payload: dict[str, Any] = {}
    if passes:
        payload["passes"] = passes
    if cumulative is not None:
        payload["cumulative_reduction"] = cumulative
    return payload or None


def _defects_payload(value: Any, path: str) -> list[dict[str, Any]] | None:
    items = _sequence(value, path, required=False)
    if not items:
        return None
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        block = _mapping(item, f"{path}[{index}]", required=True)
        assert block is not None
        defect_id = _text(block.get("id"), f"{path}[{index}].id", required=True)
        assert defect_id is not None
        if defect_id in seen:
            _fail(f"{path} 的 id 必须唯一")
        seen.add(defect_id)
        kind = _enum(block.get("kind"), f"{path}[{index}].kind", DEFECT_KINDS, required=True)
        assert kind is not None
        normalized: dict[str, Any] = {"id": defect_id, "kind": kind}
        location_raw = _mapping(block.get("location"), f"{path}[{index}].location", required=False)
        if location_raw is not None:
            location: dict[str, Any] = {}
            for key in ("axial", "radial", "angle_deg"):
                component = _num(location_raw.get(key), f"{path}[{index}].location.{key}", required=False)
                if component is None:
                    continue
                if key in ("axial", "radial") and not 0.0 <= component <= 1.0:
                    _fail(f"{path}[{index}].location.{key} 必须在 0–1 内")
                location[key] = component
            if location:
                normalized["location"] = location
        size = _num(block.get("size_mm"), f"{path}[{index}].size_mm", required=False)
        if size is not None:
            normalized["size_mm"] = size
        severity = _num(block.get("severity"), f"{path}[{index}].severity", required=False)
        if severity is not None:
            normalized["severity"] = severity
        result.append(normalized)
    return result


def _density_payload(value: Any, path: str) -> dict[str, Any] | None:
    block = _mapping(value, path, required=False)
    if block is None:
        return None
    payload: dict[str, Any] = {}
    for axis in DENSITY_AXES:
        axis_payload = _axis_payload(block.get(axis), f"{path}.{axis}")
        if axis_payload is not None:
            payload[axis] = axis_payload
    end_loose = _mapping(block.get("end_loose"), f"{path}.end_loose", required=False)
    if end_loose is not None:
        entry: dict[str, Any] = {}
        fraction = _num(end_loose.get("length_fraction"), f"{path}.end_loose.length_fraction", required=False)
        if fraction is not None:
            if not 0.0 < fraction <= 1.0:
                _fail(f"{path}.end_loose.length_fraction 必须在 (0, 1] 内")
            entry["length_fraction"] = fraction
        density = _num(end_loose.get("density"), f"{path}.end_loose.density", required=False)
        if density is not None:
            entry["density"] = density
        if entry:
            payload["end_loose"] = entry
    defects = _defects_payload(block.get("local_defects"), f"{path}.local_defects")
    if defects is not None:
        payload["local_defects"] = defects
    return payload or None


def _interface_payload(value: Any, path: str) -> dict[str, Any] | None:
    block = _mapping(value, path, required=False)
    if block is None:
        return None
    payload: dict[str, Any] = {}
    gap = _num(block.get("gap_mm"), f"{path}.gap_mm", required=False)
    if gap is not None:
        payload["gap_mm"] = gap
    debond_raw = _mapping(block.get("debond"), f"{path}.debond", required=False)
    if debond_raw is not None:
        debond: dict[str, Any] = {}
        state = _enum(debond_raw.get("state"), f"{path}.debond.state", DEBOND_STATES, required=False)
        if state is not None and state != "tbd":
            debond["state"] = state
        extent = _num(debond_raw.get("extent_deg"), f"{path}.debond.extent_deg", required=False)
        if extent is not None:
            debond["extent_deg"] = extent
        if debond:
            payload["debond"] = debond
    return payload or None


def _normalize_model(raw: dict[str, Any], path: str) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    unknown = sorted(set(raw) - _KNOWN_MODEL_KEYS)
    if unknown:
        warnings.append(f"{path} 含未知字段，已忽略：{', '.join(unknown)}")
    model_id = _text(raw.get("id"), f"{path}.id", required=True)
    assert model_id is not None
    payload: dict[str, Any] = {
        "id": model_id,
        "units": _units(raw.get("units")),
        "confidence": _confidence(raw.get("confidence"), warnings),
        "geometry": _geometry_payload(raw.get("geometry"), f"{path}.geometry"),
    }
    drawing = _drawing_payload(raw.get("drawing"), f"{path}.drawing")
    if drawing is not None:
        payload["drawing"] = drawing
    density = _density_payload(raw.get("density_field"), f"{path}.density_field")
    if density is not None:
        payload["density_field"] = density
    interface = _interface_payload(raw.get("interface"), f"{path}.interface")
    if interface is not None:
        payload["interface"] = interface
    return payload, warnings


def canonical_payload_json(payload: Mapping[str, Any]) -> str:
    """规范化 JSON：键排序、无空格、非 ASCII 不转义。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of_payload(payload: Mapping[str, Any]) -> str:
    """模型指纹：规范化载荷 UTF-8 字节的 SHA-256。"""
    return hashlib.sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LocalDefectSpec:
    """局部缺陷中与网格尺度相关的字段。"""

    defect_id: str
    kind: str
    axial: float | None = None
    radial: float | None = None
    angle_deg: float | None = None
    size_mm: float | None = None


@dataclass(frozen=True, slots=True)
class MeshModel:
    """网格生成视角下的模型：几何已冻结，场信息只保留影响尺度的部分。"""

    model_id: str
    digest: str
    source: str
    outer_diameter: float
    inner_diameter: float
    length: float
    wall_nominal: float
    wall_min: float | None
    wall_max: float | None
    eccentricity_radial: float | None
    eccentricity_circumferential: float | None
    eccentricity_angle_deg: float | None
    circumferential_kind: str | None
    circumferential_value: float | None
    circumferential_samples: tuple[tuple[float, float | None], ...]
    circumferential_declared: bool
    eccentricity_declared: bool
    gap_mm: float | None
    end_loose_declared: bool
    end_loose_length_fraction: float | None
    end_loose_density: float | None
    defects: tuple[LocalDefectSpec, ...]
    warnings: tuple[str, ...] = ()

    @property
    def outer_radius(self) -> float:
        return self.outer_diameter / 2.0

    @property
    def inner_radius(self) -> float:
        return self.inner_diameter / 2.0


def _mesh_model(payload: dict[str, Any], raw: dict[str, Any], source: str, warnings: list[str]) -> MeshModel:
    geometry = cast("dict[str, Any]", payload["geometry"])
    wall = cast("dict[str, Any]", geometry["wall_thickness"])
    eccentricity = cast("dict[str, Any]", geometry.get("eccentricity", {}))
    interface = cast("dict[str, Any]", payload.get("interface", {}))
    density = cast("dict[str, Any]", payload.get("density_field", {}))
    end_loose = cast("dict[str, Any]", density.get("end_loose", {}))
    defects = cast("list[dict[str, Any]]", density.get("local_defects", []))

    raw_geometry = cast("dict[str, Any]", raw.get("geometry", {}))
    raw_wall = cast("dict[str, Any]", raw_geometry.get("wall_thickness", {}))
    eccentricity_declared = isinstance(raw_geometry.get("eccentricity"), dict)
    raw_circ = raw_wall.get("circumferential")
    circumferential_declared = isinstance(raw_circ, dict)
    circumferential_kind: str | None = None
    circumferential_value: float | None = None
    samples: list[tuple[float, float | None]] = []
    if circumferential_declared:
        circumferential_kind = cast(str, cast("dict[str, Any]", raw_circ).get("kind"))
        normalized = cast("dict[str, Any] | None", wall.get("circumferential"))
        if normalized is not None and circumferential_kind == "uniform":
            circumferential_value = cast(float, normalized["value"])
        elif normalized is not None and circumferential_kind == "polyline":
            for sample in cast("list[dict[str, Any]]", normalized["samples"]):
                thickness = sample["thickness"]
                samples.append(
                    (
                        cast(float, sample["angle_deg"]),
                        None if thickness == TBD_TEXT else cast(float, thickness),
                    )
                )
        warnings.append(f"模型含周向壁厚分布（kind={circumferential_kind}）；二维轴对称按代表截面处理，不表达周向变化")
        if circumferential_kind == "tbd":
            warnings.append("周向壁厚分布为 tbd，按最坏情形（最薄壁 / 最薄药层）加密，报告标注未标定")
        elif any(thickness is None for _, thickness in samples):
            warnings.append("周向壁厚折线含 TBD，按已标定最小值作最坏情形，报告标注未标定")

    raw_density = cast("dict[str, Any]", raw.get("density_field") or {})
    end_loose_declared = isinstance(raw_density.get("end_loose"), dict)
    if end_loose_declared and "length_fraction" not in end_loose:
        warnings.append("端部疏松段已声明但 length_fraction 未标定，未做端部轴向加密")

    if interface.get("gap_mm") is None:
        warnings.append("interface.gap_mm 未标定，按零间隙生成并在报告中标注未标定")

    defect_specs: list[LocalDefectSpec] = []
    for item in defects:
        location = cast("dict[str, Any]", item.get("location", {}))
        defect_specs.append(
            LocalDefectSpec(
                defect_id=cast(str, item["id"]),
                kind=cast(str, item["kind"]),
                axial=cast("float | None", location.get("axial")),
                radial=cast("float | None", location.get("radial")),
                angle_deg=cast("float | None", location.get("angle_deg")),
                size_mm=cast("float | None", item.get("size_mm")),
            )
        )
    if defect_specs and all(spec.size_mm is None for spec in defect_specs):
        warnings.append("局部缺陷均未给出 size_mm，未参与网格特征尺度判定")

    return MeshModel(
        model_id=cast(str, payload["id"]),
        digest=digest_of_payload(payload),
        source=source,
        outer_diameter=cast(float, geometry["outer_diameter"]),
        inner_diameter=cast(float, geometry["inner_diameter"]),
        length=cast(float, geometry["length"]),
        wall_nominal=cast(float, wall["nominal"]),
        wall_min=cast("float | None", wall.get("min")),
        wall_max=cast("float | None", wall.get("max")),
        eccentricity_radial=cast("float | None", eccentricity.get("radial_mm")),
        eccentricity_circumferential=cast("float | None", eccentricity.get("circumferential_mm")),
        eccentricity_angle_deg=cast("float | None", eccentricity.get("angle_deg")),
        circumferential_kind=circumferential_kind,
        circumferential_value=circumferential_value,
        circumferential_samples=tuple(samples),
        circumferential_declared=circumferential_declared,
        eccentricity_declared=eccentricity_declared,
        gap_mm=cast("float | None", interface.get("gap_mm")),
        end_loose_declared=end_loose_declared,
        end_loose_length_fraction=cast("float | None", end_loose.get("length_fraction")),
        end_loose_density=cast("float | None", end_loose.get("density")),
        defects=tuple(defect_specs),
        warnings=tuple(warnings),
    )


def parse_models(text: str, source: str = "<memory>") -> tuple[MeshModel, ...]:
    """解析并规范化模型文件；含 TBD 的合法文件不会被拒绝。"""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ParameshError(f"{source}: YAML 解析失败：{exc}") from exc
    if not isinstance(document, dict):
        raise ParameshError(f"{source}: 顶层必须是映射")
    root = cast("dict[str, Any]", document)
    schema = root.get("schema")
    if schema != MODEL_SCHEMA:
        raise ParameshError(f"{source}: schema 必须是 {MODEL_SCHEMA}，实际为 {schema!r}")
    models_raw = _sequence(root.get("models"), f"{source}: models", required=True)
    assert models_raw is not None
    if not models_raw:
        raise ParameshError(f"{source}: models 不能为空")
    models: list[MeshModel] = []
    seen: set[str] = set()
    for index, item in enumerate(models_raw):
        path = f"{source}: models[{index}]"
        raw = _mapping(item, path, required=True)
        assert raw is not None
        payload, warnings = _normalize_model(raw, path)
        model_id = cast(str, payload["id"])
        if model_id in seen:
            raise ParameshError(f"{source}: model.id 重复：{model_id}")
        seen.add(model_id)
        models.append(_mesh_model(payload, raw, source, warnings))
    return tuple(models)


def load_models(path: Path) -> tuple[MeshModel, ...]:
    """读取模型文件。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameshError(f"无法读取模型文件 {path}：{exc}") from exc
    return parse_models(text, str(path))


def select_model(models: Sequence[MeshModel], model_id: str | None = None) -> MeshModel:
    """按 id 选择模型；省略时取第一个。"""
    if not models:
        raise ParameshError("模型文件中没有可用模型")
    if model_id is None:
        return models[0]
    for model in models:
        if model.model_id == model_id:
            return model
    available = ", ".join(model.model_id for model in models)
    raise ParameshError(f"找不到模型 {model_id!r}；可用：{available}")


def format_model_summary(model: MeshModel) -> str:
    """给人看的模型摘要（GUI / check 共用）。"""
    lines = [
        f"模型：{model.model_id}",
        f"指纹：{model.digest}",
        f"外径 / 内径 / 长度：{model.outer_diameter:g} / {model.inner_diameter:g} / {model.length:g} mm",
        f"名义壁厚：{model.wall_nominal:g} mm"
        + (f"（min {model.wall_min:g} / max {model.wall_max:g}）" if model.wall_min and model.wall_max else ""),
    ]
    if model.circumferential_declared:
        detail = f"周向壁厚：{model.circumferential_kind}"
        if model.circumferential_kind == "uniform":
            detail += f"（{model.circumferential_value:g} mm）"
        elif model.circumferential_kind == "polyline":
            detail += f"（{len(model.circumferential_samples)} 个样本）"
        lines.append(detail)
    lines.append(f"间隙：{model.gap_mm:g} mm" if model.gap_mm is not None else "间隙：未标定（按零间隙）")
    if model.end_loose_declared:
        fraction = model.end_loose_length_fraction
        lines.append(f"端部疏松：{fraction:g} × 全长" if fraction is not None else "端部疏松：长度未标定，未加密")
    if model.defects:
        lines.append("局部缺陷：" + "、".join(spec.defect_id for spec in model.defects))
    for warning in model.warnings:
        lines.append(f"警告：{warning}")
    return "\n".join(lines)
