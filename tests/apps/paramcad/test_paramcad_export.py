"""契约 ``dynacalc-model/1`` 导出 / 解析 / 指纹测试。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from fcmd.apps.paramcad._defects import DefectParams, LocalDefect
from fcmd.apps.paramcad._export import (
    SCHEMA,
    build_document,
    canonical_payload,
    contract_draft,
    digest_of_payload,
    dumps_models,
    example_model,
    export_model,
    format_contract,
    load_model,
    model_digest,
    model_document,
    parse_document,
    save_models,
)
from fcmd.apps.paramcad._fields import AnalyticProfile, Field
from fcmd.apps.paramcad._model import CaseScope, GeometryState, TubeModel
from fcmd.apps.paramcad._quantity import LENGTH_UNIT, ParamcadError, quantity, tbd

StateFactory = Callable[..., GeometryState]
ModelFactory = Callable[..., TubeModel]

# paramcad 侧黄金指纹（示例模型；与 dynacalc `model --file` 对拍）。
EXAMPLE_DIGEST = "ed6f597a09a71381bfc86998daaade08e3276fa721a2e54d66abf51463f28dc4"
CONTRACT_DIGEST = "ce29bedba34f6bc6fcc3916ba9912c46da3f008b7cd4e8c23b6ae8098f5cf5b1"

CONTRACT_EXAMPLE = """\
schema: dynacalc-model/1
meta: {generator: paramcad, generator_version: "0.1.0"}
models:
  - id: M001-geometry
    case_scope: {include: [M001]}
    units: {length: mm, density: g/cm^3, velocity: m/s}
    confidence: {geometry: controlled, density_field: uncalibrated}
    geometry:
      outer_diameter: 60.0
      inner_diameter: 30.0
      length: 300.0
      wall_thickness: {nominal: 15.0, min: 14.2, max: 15.8}
      eccentricity: {radial_mm: 0.15, circumferential_mm: 0.0, angle_deg: 0.0}
    drawing:
      passes:
        - {index: 1, diameter_reduction: 0.08, wall_thickness_change: -0.03, die_angle_deg: 12.0}
    density_field:
      axial:
        kind: polyline
        samples:
          - {position: 0.0, density: 1.62}
          - {position: 0.5, density: 1.65}
          - {position: 1.0, density: 1.60}
      local_defects:
        - id: D1
          kind: void
          location: {axial: 0.4, radial: 0.9, angle_deg: 30}
          size_mm: 2.0
          severity: TBD
    interface:
      gap_mm: 0.05
      debond: {state: bonded, extent_deg: 0.0}
    tbd: ["density_field.local_defects[0].severity"]
"""


def _document(model: TubeModel) -> Any:
    return yaml.safe_load(dumps_models((model,)))


# ---------------------------------------------------------------------- #
# 导出结构
# ---------------------------------------------------------------------- #
def test_model_document_covers_contract_sections(make_model: ModelFactory) -> None:
    base = make_model()
    model = TubeModel(
        model_id=base.model_id,
        geometry=base.geometry,
        process=base.process,
        case_scope=CaseScope(include=("M001",)),
    )
    block = model_document(model)
    assert block["id"] == "m1"
    assert block["case_scope"] == {"include": ["M001"], "exclude": []}
    assert block["units"] == {"length": "mm", "density": "g/cm^3", "velocity": "m/s"}
    assert block["confidence"] == {
        "geometry": "uncalibrated",
        "drawing": "uncalibrated",
        "density_field": "uncalibrated",
        "interface": "uncalibrated",
    }
    assert set(block["geometry"]) == {"outer_diameter", "inner_diameter", "length", "wall_thickness", "eccentricity"}
    assert block["drawing"]["cumulative_reduction"] == pytest.approx(1 - 0.9 * 0.92)


def test_optional_sections_are_omitted_when_empty(make_state: StateFactory) -> None:
    block = model_document(TubeModel(model_id="m", geometry=make_state()))
    assert "drawing" not in block
    assert "density_field" not in block
    assert "interface" not in block
    assert "tbd" not in block
    assert "case_scope" not in block


def test_tbd_is_string_not_null(make_state: StateFactory) -> None:
    state = make_state(
        length=tbd(LENGTH_UNIT, "未给出成品长度"),
        wall_min=tbd(LENGTH_UNIT, "未给出"),
        wall_max=tbd(LENGTH_UNIT, "未给出"),
        radial=tbd(LENGTH_UNIT, "未给出"),
        circumferential=tbd(LENGTH_UNIT, "未给出"),
        angle=tbd("deg", "未给出"),
    )
    block = model_document(TubeModel(model_id="m", geometry=state))
    assert block["geometry"]["length"] == "TBD"
    assert "min" not in block["geometry"]["wall_thickness"]
    assert "eccentricity" not in block["geometry"]
    assert block["tbd"] == ["geometry.length"]
    assert "null" not in json.dumps(block)


def test_density_and_interface_documents(make_state: StateFactory) -> None:
    fields = (
        Field(axis="axial", profile=AnalyticProfile("constant", 1.6), source="常数假设"),
        Field(axis="radial", profile=AnalyticProfile("end_loose", 1.6, 1.5, 0.2), source="端部疏松"),
    )
    defects = DefectParams(
        gap_mm=quantity(0.05, LENGTH_UNIT),
        debond_state="bonded",
        debond_extent_deg=quantity(0.0, "deg"),
        local_defects=(LocalDefect("D1", "void", axial=0.4, severity=quantity(0.5, "1")),),
    )
    model = TubeModel(model_id="m", geometry=make_state(), density_fields=fields, defects=defects)
    block = model_document(model)
    assert block["density_field"]["axial"] == {"kind": "uniform", "value": 1.6}
    samples = block["density_field"]["radial"]["samples"]
    assert [item["position"] for item in samples] == pytest.approx([0.0, 0.8, 1.0])
    assert block["density_field"]["local_defects"][0]["kind"] == "void"
    assert block["interface"] == {"gap_mm": 0.05, "debond": {"state": "bonded", "extent_deg": 0.0}}


def test_document_shape_and_meta() -> None:
    document = _document(example_model())
    assert document["schema"] == SCHEMA
    assert document["meta"]["generator"] == "paramcad"
    assert "models[0].density_field.axial" in document["meta"]["field_sources"]
    assert document["models"][0]["id"] == "example"


def test_build_document_rejects_empty_and_duplicates(make_model: ModelFactory) -> None:
    with pytest.raises(ParamcadError, match="至少需要一个模型"):
        build_document(())
    model = make_model()
    with pytest.raises(ParamcadError, match="id 重复"):
        build_document((model, model))


# ---------------------------------------------------------------------- #
# 往返与指纹
# ---------------------------------------------------------------------- #
def test_round_trip_keeps_text_and_digest(make_model: ModelFactory) -> None:
    model = example_model()
    first = dumps_models((model,))
    parsed = parse_document(first, source="round-trip")[0]
    assert dumps_models((parsed,)) == first
    assert model_digest(parsed) == model_digest(model)


def test_contract_example_parses_and_reaches_golden_digest() -> None:
    model = parse_document(CONTRACT_EXAMPLE, source="contract")[0]
    assert model_document(model)["case_scope"] == {"include": ["M001"], "exclude": []}
    assert model_document(model)["drawing"]["cumulative_reduction"] == pytest.approx(0.08)
    assert model_digest(model) == CONTRACT_DIGEST
    reparsed = parse_document(dumps_models((model,)), source="contract-round-trip")[0]
    assert model_digest(reparsed) == CONTRACT_DIGEST


def test_example_model_golden_digest() -> None:
    assert model_digest(example_model()) == EXAMPLE_DIGEST


def test_digest_is_full_sha256_of_canonical_payload(make_model: ModelFactory) -> None:
    model = make_model()
    payload = canonical_payload(model)
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert model_digest(model) == expected
    assert len(model_digest(model)) == 64
    assert model_digest(model) == model_digest(model)


def test_digest_ignores_meta_tbd_and_case_scope(make_model: ModelFactory) -> None:
    base = make_model()
    scoped = TubeModel(
        model_id=base.model_id,
        geometry=base.geometry,
        process=base.process,
        confidence=base.confidence,
        case_scope=CaseScope(include=("M001",)),
        notes="备注不参与指纹",
    )
    assert model_digest(scoped) == model_digest(base)
    assert digest_of_payload({"id": "m1"}) == digest_of_payload({"id": "m1"})


def test_digest_is_independent_of_yaml_formatting(make_state: StateFactory) -> None:
    model = TubeModel(model_id="m", geometry=make_state())
    text = dumps_models((model,))
    raw = yaml.safe_load(text)
    raw["models"][0]["geometry"]["outer_diameter"] = 30  # 整数写法，规范化后与 30.0 等价
    reformatted = "# 注释不影响指纹\n" + yaml.safe_dump(raw, allow_unicode=True, sort_keys=True)
    assert model_digest(parse_document(reformatted, source="reformatted")[0]) == model_digest(model)


# ---------------------------------------------------------------------- #
# 解析错误
# ---------------------------------------------------------------------- #
_NULL_UNITS = "schema: dynacalc-model/1\nmodels:\n  - id: m\n    geometry: {}\n"
_WRONG_UNITS = (
    "schema: dynacalc-model/1\nmodels:\n  - id: m\n"
    "    units: {length: mm, density: kg/m^3, velocity: m/s}\n"
    "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("models: []\n", "schema"),
        ("schema: dynacalc-model/2\nmodels: []\n", "schema"),
        ("schema: dynacalc-model/1\nmodels: []\n", "models"),
        ("schema: dynacalc-model/1\n", "models"),
        (_NULL_UNITS, "units"),
        (_WRONG_UNITS, "g/cm\\^3"),
        (
            "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: null, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n",
            "null",
        ),
        (
            "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
            "    density_field: {axial: {kind: fancy}}\n",
            "未知取值",
        ),
        (
            "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
            "    interface: {debond: {state: TBD}}\n",
            "bonded",
        ),
        (
            "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
            "    density_field: {axial: {kind: polyline, samples: [{position: 0.5, density: 1.6}]}}\n",
            "至少需要 2 个样本",
        ),
        (
            "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
            "    density_field:\n      axial:\n        kind: polyline\n"
            "        samples: [{position: 0.6, density: 1.6}, {position: 0.2, density: 1.7}]\n",
            "递增",
        ),
        (
            "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
            "    geometry_extra: {}\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
            "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n",
            "id 重复",
        ),
    ],
)
def test_parse_errors_are_explicit(text: str, message: str) -> None:
    with pytest.raises(ParamcadError, match=message):
        parse_document(text, source="bad")


def test_parse_ignores_unknown_fields() -> None:
    text = (
        "schema: dynacalc-model/1\nunknown: 1\nmeta: {generator: x}\nmodels:\n  - id: m\n"
        "    future_field: ok\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
        "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
    )
    model = parse_document(text)[0]
    assert model.model_id == "m"


def test_parse_records_field_sources_and_notes() -> None:
    text = dumps_models((example_model(),))
    model = parse_document(text)[0]
    assert {field.source for field in model.density_fields} == {"示例占位（解析假设）", "示例占位（实测点）"}
    assert model.notes


def test_parse_rejects_uniform_tbd_value() -> None:
    text = (
        "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
        "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
        "    density_field: {axial: {kind: uniform, value: TBD}}\n"
    )
    with pytest.raises(ParamcadError, match="kind: tbd"):
        parse_document(text)


# ---------------------------------------------------------------------- #
# 文件读写与工况绑定
# ---------------------------------------------------------------------- #
def test_save_overwrite_and_no_temp_left(tmp_path: Path, make_model: ModelFactory) -> None:
    path = tmp_path / "model.yaml"
    export_model(make_model(), path)
    assert "schema: dynacalc-model/1" in path.read_text(encoding="utf-8")
    with pytest.raises(ParamcadError, match="--overwrite"):
        save_models((make_model(),), path)
    save_models((make_model(),), path, overwrite=True)
    assert [item.name for item in tmp_path.iterdir()] == ["model.yaml"]


def test_load_model_requires_exactly_one(tmp_path: Path, make_model: ModelFactory) -> None:
    path = tmp_path / "model.yaml"
    save_models((make_model(), make_model("m2")), path)
    with pytest.raises(ParamcadError, match="恰好 1 个"):
        load_model(path)
    with pytest.raises(ParamcadError, match="不存在"):
        load_model(tmp_path / "missing.yaml")


def test_contract_draft_matches_exporter() -> None:
    draft = contract_draft()
    assert draft["schema"] == SCHEMA
    assert draft["units"] == {"length": "mm", "density": "g/cm^3", "velocity": "m/s"}
    assert "paramcad_meta_extension" in draft
    assert format_contract().startswith("schema: dynacalc-model/1")
