"""尺度策略测试：驱动项、壁厚层数、代表截面、端部疏松与未标定项。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.apps.paramesh._config import MeshConfig, parse_config
from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._model import MeshModel, parse_models
from fcmd.apps.paramesh._scale import (
    choose_section_angle,
    format_plan,
    plan_all,
    plan_scale,
    wall_thickness_at,
)

ModelText = Callable[..., str]
ConfigText = Callable[..., str]


def _model(text: str) -> MeshModel:
    return parse_models(text)[0]


def test_characteristic_driver_reaction_zone(model_text: ModelText) -> None:
    plan = plan_scale(_model(model_text()), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert plan.driver == "reaction_zone"
    assert plan.characteristic_mm == 2.0
    assert plan.global_h_mm == 0.25


def test_characteristic_driver_min_wall(model_text: ModelText) -> None:
    plan = plan_scale(_model(model_text()), MeshConfig(reaction_zone_mm=20.0), "coarse")
    assert plan.driver == "min_wall"
    assert plan.characteristic_mm == pytest.approx(9.5)


def test_characteristic_driver_min_defect(model_text: ModelText) -> None:
    text = model_text(density_field={"local_defects": [{"id": "D1", "kind": "crack", "size_mm": 0.5}]})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert plan.driver == "min_defect"
    assert plan.characteristic_mm == pytest.approx(0.5)


def test_tbd_defect_size_does_not_drive(model_text: ModelText) -> None:
    text = model_text(density_field={"local_defects": [{"id": "D1", "kind": "crack", "size_mm": "TBD"}]})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert plan.driver == "reaction_zone"
    assert plan.min_defect_mm is None
    assert "density_field.local_defects[].size_mm" in plan.tbd


def test_level_scaling_and_layer_clamp(model_text: ModelText, config_text: ConfigText) -> None:
    model = _model(model_text())
    config, _warnings = parse_config(
        config_text(
            reaction_zone_mm=2.0, levels={"coarse": 1, "medium": 2, "fine": 4}, wall_layers={"min": 6, "max": 8}
        )
    )
    plans = plan_all(model, config)
    assert [plan.global_h_mm for plan in plans] == [2.0, 1.0, 0.5]
    assert plans[0].raw_wall_layers == 5
    assert plans[0].wall_layers == 6
    assert any("最小层数" in warning for warning in plans[0].warnings)
    assert plans[-1].raw_wall_layers == 20
    assert plans[-1].wall_layers == 8
    assert any("最大层数" in warning for warning in plans[-1].warnings)


def test_section_angle_prefers_thin_wall(model_text: ModelText) -> None:
    text = model_text(
        geometry={
            "inner_diameter": 34.0,
            "wall_thickness": {
                "nominal": 3.0,
                "min": 2.6,
                "max": 3.4,
                "circumferential": {
                    "kind": "polyline",
                    "samples": [
                        {"angle_deg": 0, "thickness": 2.8},
                        {"angle_deg": 180, "thickness": 3.2},
                    ],
                },
            },
        }
    )
    model = _model(text)
    angle = choose_section_angle(model, MeshConfig(reaction_zone_mm=2.0))
    assert angle == 0.0
    assert wall_thickness_at(model, angle).thickness_mm == pytest.approx(2.8)


def test_section_angle_override(model_text: ModelText, config_text: ConfigText) -> None:
    config, _ = parse_config(config_text(section_angle_deg=90.0))
    plan = plan_scale(_model(model_text()), config, "coarse")
    assert plan.section_angle_deg == 90.0


def test_polyline_interpolation_and_wrap(model_text: ModelText) -> None:
    text = model_text(
        geometry={
            "wall_thickness": {
                "circumferential": {
                    "kind": "polyline",
                    "samples": [
                        {"angle_deg": 0, "thickness": 9.8},
                        {"angle_deg": 270, "thickness": 10.4},
                    ],
                }
            }
        }
    )
    model = _model(text)
    assert wall_thickness_at(model, 45.0).thickness_mm == pytest.approx(9.9)
    assert wall_thickness_at(model, 315.0).thickness_mm == pytest.approx(10.1)


def test_polyline_partial_tbd_uses_calibrated_min(model_text: ModelText) -> None:
    text = model_text(
        geometry={
            "wall_thickness": {
                "circumferential": {
                    "kind": "polyline",
                    "samples": [
                        {"angle_deg": 0, "thickness": "TBD"},
                        {"angle_deg": 180, "thickness": 9.6},
                    ],
                }
            }
        }
    )
    model = _model(text)
    wall = wall_thickness_at(model, 90.0)
    assert wall.thickness_mm == pytest.approx(9.6)
    assert wall.uncalibrated is True
    plan = plan_scale(model, MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert "geometry.wall_thickness.circumferential" in plan.tbd


def test_circumferential_tbd_flags_uncalibrated(model_text: ModelText) -> None:
    text = model_text(geometry={"wall_thickness": {"circumferential": {"kind": "tbd"}}})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert "geometry.wall_thickness.circumferential" in plan.tbd
    assert any("未标定" in warning for warning in plan.warnings)


def test_eccentricity_shifts_thinnest_wall(model_text: ModelText) -> None:
    text = model_text(geometry={"eccentricity": {"radial_mm": 0.2, "circumferential_mm": 0.0}})
    model = _model(text)
    assert wall_thickness_at(model, 0.0).thickness_mm == pytest.approx(9.8)
    assert wall_thickness_at(model, 180.0).thickness_mm == pytest.approx(10.2)


def test_eccentricity_circumferential_direction(model_text: ModelText) -> None:
    text = model_text(geometry={"eccentricity": {"circumferential_mm": 0.2}})
    model = _model(text)
    assert wall_thickness_at(model, 90.0).thickness_mm == pytest.approx(9.8)
    assert wall_thickness_at(model, 270.0).thickness_mm == pytest.approx(10.2)


def test_eccentricity_declared_without_value_is_uncalibrated(model_text: ModelText) -> None:
    text = model_text(geometry={"eccentricity": {"radial_mm": "TBD"}})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert "geometry.eccentricity.radial_mm" in plan.tbd


def test_gap_tbd_generates_zero_gap_with_flag(model_text: ModelText) -> None:
    text = model_text(interface={"gap_mm": "TBD"})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert plan.gap_mm == 0.0
    assert "interface.gap_mm" in plan.tbd


def test_end_loose_band_from_fraction(model_text: ModelText) -> None:
    text = model_text(density_field={"end_loose": {"length_fraction": 0.1, "density": 1.45}})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert plan.end_loose_band_mm == pytest.approx(2.0)


def test_end_loose_tbd_flags_and_skips_band(model_text: ModelText) -> None:
    text = model_text(density_field={"end_loose": {"density": 1.45}})
    plan = plan_scale(_model(text), MeshConfig(reaction_zone_mm=2.0), "coarse")
    assert plan.end_loose_band_mm == 0.0
    assert "density_field.end_loose.length_fraction" in plan.tbd


def test_missing_reaction_zone_rejected(model_text: ModelText) -> None:
    with pytest.raises(ParameshError, match="reaction_zone_mm"):
        plan_scale(_model(model_text()), MeshConfig(), "coarse")


def test_unknown_level_rejected(model_text: ModelText) -> None:
    with pytest.raises(ParameshError, match="未知网格档位"):
        plan_scale(_model(model_text()), MeshConfig(reaction_zone_mm=2.0), "ultra")


def test_format_plan_mentions_driver_and_layers(model_text: ModelText) -> None:
    plan = plan_scale(_model(model_text()), MeshConfig(reaction_zone_mm=2.0), "coarse")
    text = format_plan(plan)
    assert "coarse" in text
    assert "反应区尺度" in text
    assert "壁厚层数" in text
