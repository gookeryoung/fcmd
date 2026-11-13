"""几何 / 工艺推导与 TBD 传播测试。"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest

from fcmd.apps.paramcad._model import (
    CaseScope,
    GeometryState,
    PassSpec,
    ProcessSpec,
    TubeModel,
    derive_after,
    format_summary,
    tbd_report,
    validate_model,
)
from fcmd.apps.paramcad._quantity import LENGTH_UNIT, ParamcadError, Quantity, quantity, tbd

StateFactory = Callable[..., GeometryState]
ProcessFactory = Callable[..., ProcessSpec]


def test_tbd_quantity_requires_reason() -> None:
    with pytest.raises(ParamcadError, match="必须给出原因"):
        Quantity(None, LENGTH_UNIT)


def test_known_quantity_rejects_reason() -> None:
    with pytest.raises(ParamcadError, match="不应携带"):
        Quantity(1.0, LENGTH_UNIT, "多余原因")


def test_quantity_rejects_non_finite() -> None:
    with pytest.raises(ParamcadError, match="有限数"):
        Quantity(math.inf, LENGTH_UNIT)


def test_geometry_rejects_inverted_diameters(make_state: StateFactory) -> None:
    with pytest.raises(ParamcadError, match="内径必须小于外径"):
        make_state(outer=20.0, inner=20.0)


def test_geometry_rejects_wall_above_radius(make_state: StateFactory) -> None:
    with pytest.raises(ParamcadError, match="最大壁厚必须小于外径的一半"):
        make_state(outer=10.0, inner=0.5, wall=4.75, wall_min=4.0, wall_max=5.5)


def test_geometry_rejects_wall_order(make_state: StateFactory) -> None:
    with pytest.raises(ParamcadError, match="最小壁厚不能大于名义壁厚"):
        make_state(wall=2.0, wall_min=2.5, wall_max=3.4)


def test_wall_mean_falls_back_to_diameters(make_state: StateFactory) -> None:
    state = make_state(wall=tbd(LENGTH_UNIT, "未给出名义壁厚"))
    assert state.wall_mean.value == pytest.approx(3.0)


def test_offset_requires_extremes(make_state: StateFactory) -> None:
    state = make_state(wall_min=tbd(LENGTH_UNIT, "未给出"), wall_max=tbd(LENGTH_UNIT, "未给出"))
    assert state.offset.is_tbd
    assert "壁厚极值" in state.offset.reason


def test_derive_without_process_is_fully_tbd(make_state: StateFactory) -> None:
    after = derive_after(make_state(), None)
    assert after.outer_diameter.is_tbd
    assert after.inner_diameter.is_tbd
    assert after.length.is_tbd
    assert "未给出拉拔工艺" in after.outer_diameter.reason


def test_derive_applies_reduction_and_wall_change(make_state: StateFactory, make_process: ProcessFactory) -> None:
    after = derive_after(make_state(), make_process())
    assert after.outer_diameter.value == pytest.approx(24.84)
    assert after.wall_nominal.value == pytest.approx(2.8812)
    assert after.inner_diameter.value == pytest.approx(19.0776)
    assert after.wall_min.value == pytest.approx(2.49704)
    assert after.wall_max.value == pytest.approx(3.26536)
    assert after.eccentricity_radial.value == pytest.approx(0.38416)


def test_derive_absolute_eccentricity_keeps_offset(make_state: StateFactory, make_process: ProcessFactory) -> None:
    after = derive_after(make_state(), make_process(carry="absolute"))
    assert after.eccentricity_radial.value == pytest.approx(0.4)
    assert after.wall_min.value == pytest.approx(2.8812 - 0.4)


def test_derive_requires_eccentricity_carry(make_state: StateFactory, make_process: ProcessFactory) -> None:
    after = derive_after(make_state(), make_process(carry=None))
    assert after.wall_min.is_tbd
    assert "偏心传递方式" in after.wall_min.reason


def test_derive_missing_wall_change_yields_tbd(make_state: StateFactory, make_process: ProcessFactory) -> None:
    process = make_process(passes=(PassSpec(1, 0.1, -0.02), PassSpec(2, 0.08, None)))
    after = derive_after(make_state(), process)
    assert after.wall_nominal.is_tbd
    assert "第 2 道次" in after.wall_nominal.reason
    assert after.inner_diameter.is_tbd


def test_derive_missing_reduction_yields_tbd(make_state: StateFactory, make_process: ProcessFactory) -> None:
    after = derive_after(make_state(), make_process(passes=(PassSpec(1, None),)))
    assert after.outer_diameter.is_tbd
    assert "减径率未标定" in after.outer_diameter.reason


def test_derive_cumulative_only_process_keeps_wall_tbd(make_state: StateFactory, make_process: ProcessFactory) -> None:
    after = derive_after(make_state(), make_process(passes=(), cumulative=0.2))
    assert after.outer_diameter.value == pytest.approx(24.0)
    assert after.wall_nominal.is_tbd
    assert "道次壁厚变化率" in after.wall_nominal.reason


def test_derive_finished_length(make_state: StateFactory, make_process: ProcessFactory) -> None:
    finished = quantity(180.0, LENGTH_UNIT)
    after = derive_after(make_state(), make_process(finished=finished))
    assert after.length.value == pytest.approx(180.0)
    fallback = derive_after(make_state(), make_process())
    assert fallback.length.is_tbd
    assert "体积守恒" in fallback.length.reason


def test_derive_rejects_vanishing_wall(make_state: StateFactory, make_process: ProcessFactory) -> None:
    process = make_process(passes=(PassSpec(1, 0.9, -0.9),), carry="absolute")
    with pytest.raises(ParamcadError, match="壁厚极小值非正"):
        derive_after(make_state(), process)


def test_derive_rejects_non_positive_inner(make_state: StateFactory, make_process: ProcessFactory) -> None:
    process = make_process(passes=(PassSpec(1, 0.5, 2.0),), carry="factor")
    with pytest.raises(ParamcadError, match="内径非正"):
        derive_after(make_state(), process)


def test_process_rejects_out_of_order_passes() -> None:
    with pytest.raises(ParamcadError, match="严格递增"):
        ProcessSpec(passes=(PassSpec(2, 0.1), PassSpec(1, 0.1)))


def test_process_rejects_empty_without_cumulative() -> None:
    with pytest.raises(ParamcadError, match="累计减径率"):
        ProcessSpec(passes=())


def test_pass_rejects_invalid_values() -> None:
    with pytest.raises(ParamcadError, match="减径率"):
        PassSpec(1, 1.0)
    with pytest.raises(ParamcadError, match="壁厚变化率"):
        PassSpec(1, 0.1, -1.0)
    with pytest.raises(ParamcadError, match="模具半角"):
        PassSpec(1, 0.1, die_angle_deg=90.0)


def test_case_scope_rejects_blank_code() -> None:
    with pytest.raises(ParamcadError, match="空编码"):
        CaseScope(include=("",))


def test_model_id_must_not_be_blank(make_state: StateFactory) -> None:
    with pytest.raises(ParamcadError, match="id 不能为空"):
        TubeModel(model_id=" ", geometry=make_state())


def test_model_rejects_unknown_confidence(make_state: StateFactory) -> None:
    with pytest.raises(ParamcadError, match="未知置信域"):
        TubeModel(model_id="m", geometry=make_state(), confidence={"other": "public"})
    with pytest.raises(ParamcadError, match="置信等级"):
        TubeModel(model_id="m", geometry=make_state(), confidence={"geometry": "high"})


def test_model_rejects_duplicate_density_axes(make_model: Callable[..., TubeModel]) -> None:
    from fcmd.apps.paramcad._fields import AnalyticProfile, Field

    from_field = Field(axis="axial", profile=AnalyticProfile("constant", 1.5), source="测试")
    model = make_model()
    with pytest.raises(ParamcadError, match="同一轴"):
        TubeModel(
            model_id="m",
            geometry=model.geometry,
            density_fields=(from_field, from_field),
        )


def test_tbd_report_lists_reasons(make_state: StateFactory, make_process: ProcessFactory) -> None:
    missing_extreme = TubeModel.from_before(
        "m", make_state(wall_min=tbd(LENGTH_UNIT, "未给出最小壁厚")), make_process(carry=None)
    )
    assert any("wall_thickness.min" in row for row in tbd_report(missing_extreme))
    no_carry = TubeModel.from_before("m", make_state(), make_process(carry=None))
    assert any("偏心传递方式" in row for row in tbd_report(no_carry))


def test_validate_model_flags_gap_larger_than_cavity(
    make_state: StateFactory, make_model: Callable[..., TubeModel]
) -> None:
    from fcmd.apps.paramcad._defects import DefectParams

    model = make_model()
    bad = TubeModel(
        model_id="m",
        geometry=model.geometry,
        defects=DefectParams(gap_mm=quantity(20.0, LENGTH_UNIT)),
    )
    problems = validate_model(bad)
    assert problems and "内腔半径" in problems[0]


def test_format_summary_mentions_geometry_and_tbd(make_state: StateFactory, make_process: ProcessFactory) -> None:
    model = TubeModel.from_before("demo", make_state(), make_process())
    text = format_summary(model)
    assert "模型：demo" in text
    assert "几何（拉拔前·推导输入）" in text
    assert "几何（文档 / 导出使用）" in text
    assert "TBD：" in text


def test_effective_state_label_follows_process(make_state: StateFactory, make_process: ProcessFactory) -> None:
    with_process = TubeModel.from_before("m", make_state(), make_process())
    assert with_process.effective_state()[1] == "after"
    without = TubeModel(model_id="m", geometry=make_state())
    assert without.effective_state()[1] == "before"
