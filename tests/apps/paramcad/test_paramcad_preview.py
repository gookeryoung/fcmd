"""预览几何测试：截面、轴向剖面与分布曲线（不依赖 Tk）。"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from fcmd.apps.paramcad._defects import DefectParams, LocalDefect
from fcmd.apps.paramcad._fields import AnalyticProfile, Field, MeasuredProfile
from fcmd.apps.paramcad._model import GeometryState, ProcessSpec, TubeModel
from fcmd.apps.paramcad._preview import (
    axial_section,
    distribution_curves,
    section_at,
)
from fcmd.apps.paramcad._quantity import LENGTH_UNIT, quantity, tbd

StateFactory = Callable[..., GeometryState]
ProcessFactory = Callable[..., ProcessSpec]
ModelFactory = Callable[..., TubeModel]


def test_section_wall_extremes_match_geometry(make_state: StateFactory, make_model: ModelFactory) -> None:
    model = make_model()
    section = section_at(model)
    assert section.available
    assert section.state_label == "after"
    thickness = dict(section.thickness)
    assert thickness[0.0] == pytest.approx(model.geometry.wall_max.value)
    assert thickness[180.0] == pytest.approx(model.geometry.wall_min.value)
    assert len(section.outer) == len(section.inner) == 180
    outer = model.geometry.outer_diameter.value
    assert outer is not None
    assert section.half_extent_mm == pytest.approx(outer / 2.0)


def test_section_draws_propellant_when_gap_known(make_state: StateFactory, make_model: ModelFactory) -> None:
    base = make_model()
    model = TubeModel(
        model_id="m",
        geometry=base.geometry,
        defects=DefectParams(gap_mm=quantity(0.05, LENGTH_UNIT)),
    )
    section = section_at(model)
    assert section.propellant is not None
    assert len(section.propellant) == 180
    assert section.note == ""


def test_section_skips_propellant_when_gap_tbd(make_state: StateFactory, make_model: ModelFactory) -> None:
    base = make_model()
    model = TubeModel(model_id="m", geometry=base.geometry)
    section = section_at(model)
    assert section.propellant is None
    assert "药管间隙未标定" in section.note


def test_section_skips_propellant_when_gap_fills_cavity(make_state: StateFactory, make_model: ModelFactory) -> None:
    base = make_model()
    model = TubeModel(
        model_id="m",
        geometry=base.geometry,
        defects=DefectParams(gap_mm=quantity(50.0, LENGTH_UNIT)),
    )
    section = section_at(model)
    assert section.propellant is None
    assert "不小于内腔半径" in section.note


def test_section_defaults_to_concentric_with_note(make_state: StateFactory, make_model: ModelFactory) -> None:
    state = make_state(
        wall_min=tbd(LENGTH_UNIT, "未给出"),
        wall_max=tbd(LENGTH_UNIT, "未给出"),
        radial=tbd(LENGTH_UNIT, "未给出"),
    )
    model = TubeModel(model_id="m", geometry=state)
    section = section_at(model)
    assert section.available
    assert "按同心预览" in section.note
    thickness = dict(section.thickness)
    assert thickness[0.0] == pytest.approx(thickness[180.0])


def test_section_unavailable_without_wall(make_state: StateFactory, make_model: ModelFactory) -> None:
    state = make_state(
        outer=tbd(LENGTH_UNIT, "未给出"), inner=tbd(LENGTH_UNIT, "未给出"), wall=tbd(LENGTH_UNIT, "未给出")
    )
    section = section_at(TubeModel(model_id="m", geometry=state))
    assert not section.available
    assert "无法绘制截面" in section.note


def test_section_annotations_follow_local_defects(make_state: StateFactory, make_model: ModelFactory) -> None:
    base = make_model()
    defects = DefectParams(
        gap_mm=quantity(0.05, LENGTH_UNIT),
        debond_state="debonded",
        debond_extent_deg=quantity(40.0, "deg"),
        local_defects=(
            LocalDefect(defect_id="D1", kind="debond", angle_deg=90.0),
            LocalDefect(defect_id="D2", kind="thin_layer", angle_deg=270.0),
        ),
    )
    model = TubeModel(model_id="m", geometry=base.geometry, defects=defects)
    annotations = section_at(model).annotations
    assert len(annotations) == 2
    assert annotations[0].start_deg == pytest.approx(70.0)
    assert annotations[0].end_deg == pytest.approx(110.0)
    assert annotations[0].label == "局部脱粘"
    assert annotations[1].start_deg == pytest.approx(270.0)
    assert annotations[1].label == "局部薄药层"


def test_axial_section_requires_length(make_state: StateFactory) -> None:
    state = make_state()
    section = axial_section(TubeModel(model_id="m", geometry=state))
    assert section.available
    assert section.length_mm == pytest.approx(state.length.value)
    outer = state.outer_diameter.value
    assert outer is not None
    assert section.outer_top[0] == (0.0, pytest.approx(outer / 2.0))


def test_axial_section_unavailable_when_length_tbd(make_state: StateFactory) -> None:
    state = make_state(length=tbd(LENGTH_UNIT, "未给出成品长度"))
    section = axial_section(TubeModel(model_id="m", geometry=state))
    assert not section.available
    assert "长度未标定" in section.note


def test_axial_section_marks_defects_and_end_loose(make_state: StateFactory) -> None:
    defects = DefectParams(
        gap_mm=quantity(0.05, LENGTH_UNIT),
        local_defects=(LocalDefect("D1", "void", axial=0.4),),
        end_loose_density=quantity(1.5, "g/cm^3"),
    )
    model = TubeModel(model_id="m", geometry=make_state(), defects=defects)
    section = axial_section(model)
    assert section.propellant_top is not None
    assert [item.kind for item in section.annotations] == ["void", "end_loose"]


def test_axial_section_unavailable_without_geometry(make_state: StateFactory) -> None:
    state = make_state(
        outer=tbd(LENGTH_UNIT, "未给出"), inner=tbd(LENGTH_UNIT, "未给出"), wall=tbd(LENGTH_UNIT, "未给出")
    )
    section = axial_section(TubeModel(model_id="m", geometry=state))
    assert not section.available
    assert "无法绘制轴向剖面" in section.note


def test_curves_include_fields_and_derived_wall(make_state: StateFactory) -> None:
    state = make_state()
    fields = (
        Field(
            axis="axial",
            profile=AnalyticProfile("end_loose", 1.6, 1.5, 0.2),
            source="解析假设",
        ),
        Field(
            axis="circumferential",
            profile=MeasuredProfile(((0.0, 1.6), (1.0, 1.7))),
            source="实测点",
        ),
    )
    model = TubeModel(
        model_id="m",
        geometry=state,
        density_fields=fields,
        defects=DefectParams(gap_mm=quantity(0.05, LENGTH_UNIT)),
    )
    curves = distribution_curves(model)
    titles = [curve.title for curve in curves]
    assert titles[:2] == ["axial 密度", "circumferential 密度"]
    assert "壁厚（周向）" in titles
    assert "药层厚度（周向）" in titles
    axial = curves[0]
    assert axial.x_label == "z（mm）"
    assert axial.points[-1][0] == pytest.approx(state.length.value)
    assert axial.points[0][1] == pytest.approx(1.6)
    assert axial.points[-1][1] == pytest.approx(1.5)


def test_axial_curve_falls_back_to_relative_axis_when_length_tbd(make_state: StateFactory) -> None:
    state = make_state(length=tbd(LENGTH_UNIT, "未给出成品长度"))
    field = Field(axis="axial", profile=AnalyticProfile("constant", 1.6), source="测试")
    model = TubeModel(model_id="m", geometry=state, density_fields=(field,))
    curve = distribution_curves(model)[0]
    assert curve.x_label == "轴向相对位置"
    assert curve.points[-1][0] == pytest.approx(1.0)
    assert "长度未标定" in curve.note


def test_radial_curve_uses_relative_axis(make_state: StateFactory) -> None:
    field = Field(axis="radial", profile=AnalyticProfile("constant", 1.5), source="测试")
    model = TubeModel(model_id="m", geometry=make_state(), density_fields=(field,))
    curve = distribution_curves(model)[0]
    assert curve.x_label.startswith("径向相对位置")
    assert curve.points[-1][0] == pytest.approx(1.0)


def test_curves_without_geometry_have_no_derived_entries(make_state: StateFactory) -> None:
    state = make_state(
        outer=tbd(LENGTH_UNIT, "未给出"), inner=tbd(LENGTH_UNIT, "未给出"), wall=tbd(LENGTH_UNIT, "未给出")
    )
    assert distribution_curves(TubeModel(model_id="m", geometry=state)) == ()


def test_invalid_sampling_arguments(make_state: StateFactory) -> None:
    model = TubeModel(model_id="m", geometry=make_state())
    with pytest.raises(ValueError, match="至少 12"):
        section_at(model, samples=4)
    with pytest.raises(ValueError, match="至少 2"):
        distribution_curves(model, samples=1)


def test_section_points_lie_on_expected_circles(make_state: StateFactory) -> None:
    model = TubeModel(model_id="m", geometry=make_state())
    section = section_at(model)
    radius = model.geometry.outer_diameter.value
    assert radius is not None
    radius /= 2.0
    for x, y in section.outer:
        assert math.hypot(x, y) == pytest.approx(radius)
