"""模型消费层测试：黄金指纹、TBD 三分法与必填 / 单位校验。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._model import (
    canonical_payload_json,
    format_model_summary,
    parse_models,
    select_model,
)

EXAMPLE = """\
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

MINIMAL = """\
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

ADDITIVE = """\
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


@pytest.mark.parametrize(
    ("text", "digest"),
    [
        (EXAMPLE, "ce29bedba34f6bc6fcc3916ba9912c46da3f008b7cd4e8c23b6ae8098f5cf5b1"),
        (MINIMAL, "d583539119054ec21c73c78fc0d7a911b015dc9b59b1d8e2c0870b02abe08d5d"),
        (ADDITIVE, "6ced6d37a78466e473bc451af32a1e42a4d74aa313d043e586a938169908fc72"),
    ],
)
def test_contract_golden_digest(text: str, digest: str) -> None:
    """消费侧指纹必须与契约第 12 节的黄金向量逐字节一致。"""
    model = parse_models(text)[0]
    assert model.digest == digest
    assert len(model.digest) == 64


def test_canonical_json_is_sorted_and_compact() -> None:
    text = canonical_payload_json({"b": 1, "a": {"d": 2, "c": 3}})
    assert text == '{"a":{"c":3,"d":2},"b":1}'


def test_accepts_tbd_optional_fields(model_text: Callable[..., str]) -> None:
    """可留 TBD 的字段不得报错，只降级为未标定。"""
    text = model_text(
        geometry={
            "wall_thickness": {"nominal": 10.0, "min": "TBD", "max": "TBD"},
            "eccentricity": {"radial_mm": "TBD"},
        },
        density_field={"end_loose": {"length_fraction": "TBD", "density": "TBD"}},
        interface={"gap_mm": "TBD"},
    )
    model = parse_models(text)[0]
    assert model.wall_min is None
    assert model.wall_max is None
    assert model.eccentricity_radial is None
    assert model.eccentricity_declared is True
    assert model.end_loose_declared is True
    assert model.end_loose_length_fraction is None
    assert model.gap_mm is None
    assert any("gap_mm" in warning for warning in model.warnings)


def test_accepts_circumferential_tbd(model_text: Callable[..., str]) -> None:
    text = model_text(
        geometry={"wall_thickness": {"circumferential": {"kind": "tbd"}}},
    )
    model = parse_models(text)[0]
    assert model.circumferential_kind == "tbd"
    assert model.circumferential_declared is True


def test_polyline_sample_tbd_kept_as_none(model_text: Callable[..., str]) -> None:
    text = model_text(
        geometry={
            "wall_thickness": {
                "circumferential": {
                    "kind": "polyline",
                    "samples": [
                        {"angle_deg": 0, "thickness": 9.8},
                        {"angle_deg": 180, "thickness": "TBD"},
                    ],
                }
            }
        }
    )
    model = parse_models(text)[0]
    assert model.circumferential_samples == ((0.0, 9.8), (180.0, None))


def test_end_loose_fraction_out_of_range(model_text: Callable[..., str]) -> None:
    text = model_text(density_field={"end_loose": {"length_fraction": 1.5}})
    with pytest.raises(ParameshError, match="length_fraction"):
        parse_models(text)


def test_uniform_circumferential_value_rejects_tbd(model_text: Callable[..., str]) -> None:
    text = model_text(geometry={"wall_thickness": {"circumferential": {"kind": "uniform", "value": "TBD"}}})
    with pytest.raises(ParameshError, match="不允许 TBD"):
        parse_models(text)


def test_required_numeric_rejects_tbd(model_text: Callable[..., str]) -> None:
    text = model_text(geometry={"outer_diameter": "TBD"})
    with pytest.raises(ParameshError, match="outer_diameter"):
        parse_models(text)


def test_wrong_units_rejected() -> None:
    text = MINIMAL.replace("length: mm", "length: m")
    with pytest.raises(ParameshError, match=r"units\.length"):
        parse_models(text)


def test_inner_not_smaller_than_outer(model_text: Callable[..., str]) -> None:
    text = model_text(geometry={"inner_diameter": 45.0})
    with pytest.raises(ParameshError, match="inner_diameter"):
        parse_models(text)


def test_wall_order_violation(model_text: Callable[..., str]) -> None:
    text = model_text(geometry={"wall_thickness": {"min": 9.0, "nominal": 8.0}})
    with pytest.raises(ParameshError, match="min <= nominal <= max"):
        parse_models(text)


def test_duplicate_model_ids_rejected(model_text: Callable[..., str]) -> None:
    single = model_text()
    models_block = single.split("models:\n", 1)[1]
    with pytest.raises(ParameshError, match="重复"):
        parse_models("schema: dynacalc-model/1\nmodels:\n" + models_block + models_block)


def test_wrong_schema_rejected() -> None:
    with pytest.raises(ParameshError, match="schema"):
        parse_models("schema: dynacalc-model/2\nmodels: []\n")


def test_select_model_by_id(model_text: Callable[..., str]) -> None:
    models = parse_models(model_text())
    assert select_model(models).model_id == "m1"
    assert select_model(models, "m1").model_id == "m1"
    with pytest.raises(ParameshError, match="找不到模型"):
        select_model(models, "missing")


def test_summary_mentions_geometry_and_tbd(model_text: Callable[..., str]) -> None:
    model = parse_models(model_text(density_field={"local_defects": [{"id": "D1", "kind": "void", "size_mm": "TBD"}]}))[
        0
    ]
    summary = format_model_summary(model)
    assert "m1" in summary
    assert "外径" in summary
    assert "局部缺陷" in summary
