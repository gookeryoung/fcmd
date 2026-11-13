"""v1.1 契约：黄金向量逐字节对拍与归一化细则。"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from fcmd.apps.paramcad._defects import DefectParams
from fcmd.apps.paramcad._export import (
    canonical_payload,
    dumps_models,
    model_digest,
    model_document,
    parse_document,
)
from fcmd.apps.paramcad._fields import Field, MeasuredProfile
from fcmd.apps.paramcad._model import CaseScope, CircumferentialWall, GeometryState, TubeModel, WallSample
from fcmd.apps.paramcad._preview import axial_section, distribution_curves, section_at
from fcmd.apps.paramcad._quantity import DENSITY_UNIT, LENGTH_UNIT, ParamcadError, quantity

StateFactory = Callable[..., GeometryState]

# 契约第 11 / 12 节原文；逐字节对拍，不做任何重排。
GOLDEN_MINIMAL = """\
schema: dynacalc-model/1
models:
  - id: baseline
    units: {length: mm, density: g/cm^3, velocity: m/s}
    geometry:
      outer_diameter: 60.0
      inner_diameter: 30.0
      length: 300.0
      wall_thickness: {nominal: 15.0}
"""

GOLDEN_EXAMPLE = """\
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

GOLDEN_ADDITIVE = """\
schema: dynacalc-model/1
models:
  - id: additive
    units: {length: mm, density: g/cm^3, velocity: m/s}
    geometry:
      outer_diameter: 60.0
      inner_diameter: 30.0
      length: 300.0
      wall_thickness:
        nominal: 15.0
        min: 14.0
        max: 16.0
        circumferential:
          kind: polyline
          samples:
            - {angle_deg: 0, thickness: 14.8}
            - {angle_deg: 180, thickness: 15.2}
    density_field:
      end_loose:
        length_fraction: 0.1
        density: 1.45
    interface:
      gap_mm: 0.05
      debond: {state: tbd}
"""

CANONICAL_MINIMAL = (
    '{"confidence":{"density_field":"uncalibrated","drawing":"uncalibrated","geometry":"uncalibrated",'
    '"interface":"uncalibrated"},"geometry":{"inner_diameter":30.0,"length":300.0,"outer_diameter":60.0,'
    '"wall_thickness":{"nominal":15.0}},"id":"baseline","units":{"density":"g/cm^3","length":"mm",'
    '"velocity":"m/s"}}'
)

CANONICAL_EXAMPLE = (
    '{"confidence":{"density_field":"uncalibrated","drawing":"uncalibrated","geometry":"controlled",'
    '"interface":"uncalibrated"},"density_field":{"axial":{"kind":"polyline","samples":['
    '{"density":1.62,"position":0.0},{"density":1.65,"position":0.5},{"density":1.6,"position":1.0}]},'
    '"local_defects":[{"id":"D1","kind":"void","location":{"angle_deg":30.0,"axial":0.4,"radial":0.9},'
    '"size_mm":2.0}]},"drawing":{"cumulative_reduction":0.07999999999999996,"passes":['
    '{"diameter_reduction":0.08,"die_angle_deg":12.0,"index":1,"wall_thickness_change":-0.03}]},'
    '"geometry":{"eccentricity":{"angle_deg":0.0,"circumferential_mm":0.0,"radial_mm":0.15},'
    '"inner_diameter":30.0,"length":300.0,"outer_diameter":60.0,"wall_thickness":{"max":15.8,"min":14.2,'
    '"nominal":15.0}},"id":"M001-geometry","interface":{"debond":{"extent_deg":0.0,"state":"bonded"},'
    '"gap_mm":0.05},"units":{"density":"g/cm^3","length":"mm","velocity":"m/s"}}'
)

CANONICAL_ADDITIVE = (
    '{"confidence":{"density_field":"uncalibrated","drawing":"uncalibrated","geometry":"uncalibrated",'
    '"interface":"uncalibrated"},"density_field":{"end_loose":{"density":1.45,"length_fraction":0.1}},'
    '"geometry":{"inner_diameter":30.0,"length":300.0,"outer_diameter":60.0,"wall_thickness":'
    '{"circumferential":{"kind":"polyline","samples":[{"angle_deg":0.0,"thickness":14.8},'
    '{"angle_deg":180.0,"thickness":15.2}]},"max":16.0,"min":14.0,"nominal":15.0}},"id":"additive",'
    '"interface":{"gap_mm":0.05},"units":{"density":"g/cm^3","length":"mm","velocity":"m/s"}}'
)

DIGEST_MINIMAL = "d583539119054ec21c73c78fc0d7a911b015dc9b59b1d8e2c0870b02abe08d5d"
DIGEST_EXAMPLE = "ce29bedba34f6bc6fcc3916ba9912c46da3f008b7cd4e8c23b6ae8098f5cf5b1"
DIGEST_ADDITIVE = "6ced6d37a78466e473bc451af32a1e42a4d74aa313d043e586a938169908fc72"

_BASE = (
    "schema: dynacalc-model/1\nmodels:\n  - id: m\n    units: {length: mm, density: g/cm^3, velocity: m/s}\n"
    "    geometry: {outer_diameter: 30, inner_diameter: 24, length: 200, wall_thickness: {nominal: 3}}\n"
)


def _canonical_json(model: TubeModel) -> str:
    return json.dumps(canonical_payload(model), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize(
    ("name", "text", "canonical", "digest"),
    [
        ("12.2 最小模型", GOLDEN_MINIMAL, CANONICAL_MINIMAL, DIGEST_MINIMAL),
        ("12.1 示例模型", GOLDEN_EXAMPLE, CANONICAL_EXAMPLE, DIGEST_EXAMPLE),
        ("12.3 v1.1 新增字段", GOLDEN_ADDITIVE, CANONICAL_ADDITIVE, DIGEST_ADDITIVE),
    ],
)
def test_golden_vectors_are_byte_equal(name: str, text: str, canonical: str, digest: str) -> None:
    model = parse_document(text, source=name)[0]
    assert _canonical_json(model) == canonical
    assert model_digest(model) == digest
    reparsed = parse_document(dumps_models((model,)), source=f"{name}-round-trip")[0]
    assert _canonical_json(reparsed) == canonical
    assert model_digest(reparsed) == digest


# ---------------------------------------------------------------------- #
# 新增字段往返
# ---------------------------------------------------------------------- #
def test_end_loose_and_circumferential_round_trip() -> None:
    model = parse_document(GOLDEN_ADDITIVE)[0]
    assert model.defects.end_loose_length_fraction.value == pytest.approx(0.1)
    assert model.defects.end_loose_density.value == pytest.approx(1.45)
    circumferential = model.geometry.wall_circumferential
    assert circumferential is not None and circumferential.kind == "polyline"
    document = model_document(model)
    assert document["density_field"]["end_loose"] == {"length_fraction": 0.1, "density": 1.45}
    samples = document["geometry"]["wall_thickness"]["circumferential"]["samples"]
    assert [sample["angle_deg"] for sample in samples] == [0.0, 180.0]
    assert dumps_models((model,)) == dumps_models((parse_document(dumps_models((model,)))[0],))


def test_end_loose_partial_fields_are_exported_individually(make_state: StateFactory) -> None:
    only_fraction = DefectParams(end_loose_length_fraction=quantity(0.2, "1"))
    block = model_document(TubeModel(model_id="m", geometry=make_state(), defects=only_fraction))
    assert block["density_field"]["end_loose"] == {"length_fraction": 0.2}
    only_density = DefectParams(end_loose_density=quantity(1.4, DENSITY_UNIT))
    block = model_document(TubeModel(model_id="m", geometry=make_state(), defects=only_density))
    assert block["density_field"]["end_loose"] == {"density": 1.4}


def test_end_loose_tbd_density_is_omitted() -> None:
    text = _BASE + "    density_field: {end_loose: {length_fraction: 0.1, density: TBD}}\n"
    model = parse_document(text)[0]
    assert model.defects.end_loose_density.value is None
    assert model_document(model)["density_field"]["end_loose"] == {"length_fraction": 0.1}


def test_circumferential_kind_tbd_is_omitted() -> None:
    text = _BASE.replace(
        "wall_thickness: {nominal: 3}",
        "wall_thickness: {nominal: 3, circumferential: {kind: tbd}}",
    )
    model = parse_document(text)[0]
    assert "circumferential" not in model_document(model)["geometry"]["wall_thickness"]


def test_circumferential_uniform_value_rejects_tbd() -> None:
    text = _BASE.replace(
        "wall_thickness: {nominal: 3}",
        "wall_thickness: {nominal: 3, circumferential: {kind: uniform, value: TBD}}",
    )
    with pytest.raises(ParamcadError, match="kind: tbd"):
        parse_document(text)


def test_circumferential_polyline_tbd_sample_is_valid_file() -> None:
    text = _BASE.replace(
        "wall_thickness: {nominal: 3}",
        "wall_thickness: {nominal: 3, circumferential: {kind: polyline, samples: ["
        "{angle_deg: 0, thickness: 3.1}, {angle_deg: 180, thickness: TBD}]}}",
    )
    model = parse_document(text)[0]
    circumferential = model.geometry.wall_circumferential
    assert circumferential is not None and not circumferential.is_calibrated
    document = model_document(model)
    samples = document["geometry"]["wall_thickness"]["circumferential"]["samples"]
    assert samples[1]["thickness"] == "TBD"
    assert document["tbd"] == ["geometry.wall_thickness.circumferential.samples[1].thickness"]


def test_circumferential_polyline_angle_order_is_strict() -> None:
    text = _BASE.replace(
        "wall_thickness: {nominal: 3}",
        "wall_thickness: {nominal: 3, circumferential: {kind: polyline, samples: ["
        "{angle_deg: 180, thickness: 3.1}, {angle_deg: 0, thickness: 3.0}]}}",
    )
    with pytest.raises(ParamcadError, match="严格递增"):
        parse_document(text)


# ---------------------------------------------------------------------- #
# TBD 三分法与归一化
# ---------------------------------------------------------------------- #
def test_polyline_density_tbd_is_valid_and_preview_marks_uncalibrated() -> None:
    text = _BASE + (
        "    density_field: {axial: {kind: polyline, samples: "
        "[{position: 0, density: 1.6}, {position: 1, density: TBD}]}}\n"
    )
    model = parse_document(text)[0]
    assert model.density_fields[0].is_calibrated is False
    document = model_document(model)
    assert document["density_field"]["axial"]["samples"][1]["density"] == "TBD"
    assert document["tbd"] == ["density_field.axial.samples[1].density"]
    curve = distribution_curves(model)[0]
    assert curve.points == ()
    assert "未标定" in curve.note
    assert section_at(model).available is True


def test_polyline_position_tbd_is_still_invalid() -> None:
    text = _BASE + (
        "    density_field: {axial: {kind: polyline, samples: "
        "[{position: TBD, density: 1.6}, {position: 1, density: 1.6}]}}\n"
    )
    with pytest.raises(ParamcadError, match="position"):
        parse_document(text)


def test_length_key_is_required_but_value_may_be_tbd() -> None:
    """§5 定稿口径：length 的键必须在，值可显式 TBD；null / 缺键都报错。"""
    missing_key = _BASE.replace("length: 200, ", "")
    with pytest.raises(ParamcadError, match=r"geometry\.length 缺失或为 null"):
        parse_document(missing_key)
    explicit_null = _BASE.replace("length: 200", "length: null")
    with pytest.raises(ParamcadError, match=r"geometry\.length 缺失或为 null"):
        parse_document(explicit_null)
    explicit_tbd = _BASE.replace("length: 200", "length: TBD")
    model = parse_document(explicit_tbd)[0]
    assert model.geometry.length.value is None
    assert model_document(model)["geometry"]["length"] == "TBD"
    assert section_at(model).available is True
    axial = axial_section(model)
    assert axial.available is False
    assert "长度未标定" in axial.note


def test_tbd_kind_equals_omission() -> None:
    text = _BASE + "    density_field: {axial: {kind: tbd}}\n    interface: {debond: {state: tbd}}\n"
    document = model_document(parse_document(text)[0])
    assert "density_field" not in document
    assert "interface" not in document


def test_cumulative_reduction_is_omitted_when_underivable() -> None:
    text = _BASE + "    drawing: {passes: [{index: 1, diameter_reduction: TBD}]}\n"
    document = model_document(parse_document(text)[0])
    assert "cumulative_reduction" not in document["drawing"]


def test_reduction_range_is_half_open() -> None:
    accepted = _BASE + "    drawing: {passes: [{index: 1, diameter_reduction: 0.999}]}\n"
    assert parse_document(accepted)[0].process is not None
    rejected = _BASE + "    drawing: {passes: [{index: 1, diameter_reduction: 1.0}]}\n"
    with pytest.raises(ParamcadError, match=r"\[0, 1\)"):
        parse_document(rejected)


def test_case_scope_is_structural_only() -> None:
    text = _BASE + "    case_scope: {include: [M999], exclude: [M888]}\n"
    model = parse_document(text)[0]
    assert model.case_scope is not None
    assert model.case_scope.include == ("M999",)
    assert model_document(model)["case_scope"] == {"include": ["M999"], "exclude": ["M888"]}


def test_case_scope_rejects_blank_codes() -> None:
    with pytest.raises(ParamcadError, match="空编码"):
        CaseScope(include=(" ",))


def test_density_uniform_value_rejects_tbd() -> None:
    text = _BASE + "    density_field: {axial: {kind: uniform, value: TBD}}\n"
    with pytest.raises(ParamcadError, match="kind: tbd"):
        parse_document(text)


# ---------------------------------------------------------------------- #
# 模型层类型自检
# ---------------------------------------------------------------------- #
def test_wall_types_validate_ranges() -> None:
    with pytest.raises(ParamcadError, match=r"\(0, 1\]"):
        DefectParams(end_loose_length_fraction=quantity(1.2, "1"))
    with pytest.raises(ParamcadError, match="严格递增"):
        CircumferentialWall(
            kind="polyline",
            samples=(WallSample(180.0, quantity(3.0, LENGTH_UNIT)), WallSample(10.0, quantity(3.1, LENGTH_UNIT))),
        )
    with pytest.raises(ParamcadError, match="uniform"):
        CircumferentialWall(kind="uniform")


def test_measured_profile_with_tbd_returns_none_on_span() -> None:
    field = Field(axis="axial", profile=MeasuredProfile(((0.0, 1.6), (0.5, None), (1.0, 1.5))), source="实测")
    assert field.sample(0.25) is None
    assert field.sample(0.0) == pytest.approx(1.6)
    assert field.is_calibrated is False
