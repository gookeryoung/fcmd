"""dynacalc 参数表与模型描述：矩阵展开、指纹、容错与校验语义。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from fcmd.apps.dynacalc._model import (
    MODEL_SCHEMA,
    ModelError,
    ModelSpec,
    apply_model,
    derived_values,
    load_models,
    model_digest,
    model_tbd_paths,
    parse_models,
)
from fcmd.apps.dynacalc._params import (
    SCHEMA,
    TBD,
    ParamError,
    build_cases,
    case_fingerprint,
    example_params,
    expand_main_cases,
    expand_supplementary_cases,
    is_tbd,
    load_params,
    params_to_mapping,
    parse_params,
    save_params,
    summary_rows,
)
from tests.apps.dynacalc.conftest import MODEL_YAML

GOLDEN_DIGEST = "ce29bedba34f6bc6fcc3916ba9912c46da3f008b7cd4e8c23b6ae8098f5cf5b1"


def _raw(text: str) -> dict[str, Any]:
    return yaml.safe_load(text)


class TestExampleMatrix:
    """示例参数表的矩阵规模与编码。"""

    def test_main_matrix_is_48(self, params_file: Path) -> None:
        table = load_params(params_file)
        cases = expand_main_cases(table)
        assert len(cases) == 48
        assert [case.code for case in cases][:2] == ["M001", "M002"]

    def test_supplementary_groups_are_independent(self, params_file: Path) -> None:
        table = load_params(params_file)
        cases = expand_supplementary_cases(table)
        assert len(cases) == 12
        assert [case.group for case in cases] == [
            "supplementary:gap_mm",
            "supplementary:gap_mm",
            "supplementary:gap_mm",
            "supplementary:initiation_strength",
            "supplementary:initiation_strength",
            "supplementary:initiation_strength",
            "supplementary:residual_stress",
            "supplementary:residual_stress",
            "supplementary:die_skew",
            "supplementary:die_skew",
            "supplementary:wall_thickness_direction",
            "supplementary:wall_thickness_direction",
        ]
        assert cases[0].code == "S1-001"

    def test_all_main_levels_complete(self, params_file: Path) -> None:
        table = load_params(params_file)
        codes = {tuple(code for _, code in case.level_codes) for case in expand_main_cases(table)}
        assert len(codes) == 48
        assert all(len(item) == 5 for item in codes)

    def test_summary_rows_and_counts(self, params_file: Path) -> None:
        table = load_params(params_file)
        rows = summary_rows(table)
        assert len(rows) == len(table.factors) == 10
        assert table.confidence_counts()["controlled"] >= 1
        assert table.tbd_counts()

    def test_example_is_all_tbd(self) -> None:
        table = parse_params(_raw(example_params()))
        assert table.tbd_counts()


class TestParamsValidation:
    """参数文件错误语义：拒绝静默降级。"""

    def test_top_level_must_be_mapping(self) -> None:
        with pytest.raises(ParamError, match="映射"):
            parse_params([1, 2])

    def test_schema_mismatch(self) -> None:
        raw = _raw(example_params())
        raw["schema"] = "dynacalc/9"
        with pytest.raises(ParamError, match="schema"):
            parse_params(raw)

    def test_duplicate_factor(self) -> None:
        raw = _raw(example_params())
        raw["factors"].append(copy.deepcopy(raw["factors"][0]))
        with pytest.raises(ParamError, match="重复"):
            parse_params(raw)

    def test_unknown_kind(self) -> None:
        raw = _raw(example_params())
        raw["factors"][0]["kind"] = "weird"
        with pytest.raises(ParamError, match="kind"):
            parse_params(raw)

    def test_null_level_value(self) -> None:
        raw = _raw(example_params())
        raw["factors"][0]["levels"][0]["value"] = None
        table = parse_params(raw)
        assert table.factors[0].levels[0].tbd
        assert any(TBD in message for message in table.warnings)

    def test_matrix_references_unknown_factor(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["main"].append("nope")
        with pytest.raises(ParamError, match="nope"):
            parse_params(raw)

    def test_supplementary_requires_baseline(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["baseline"]["density"] = "NOPE"
        with pytest.raises(ParamError, match="NOPE"):
            parse_params(raw)

    def test_unknown_baseline_factor_only_warns(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["baseline"]["nope"] = "L1"
        table = parse_params(raw)
        assert any("nope" in message for message in table.warnings)

    def test_file_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ParamError, match="读取"):
            load_params(tmp_path / "missing.yaml")
        broken = tmp_path / "broken.yaml"
        broken.write_text("schema: [", encoding="utf-8")
        with pytest.raises(ParamError, match="YAML"):
            load_params(broken)

    def test_save_roundtrip(self, small_params: Path, tmp_path: Path) -> None:
        table = load_params(small_params)
        target = tmp_path / "out.yaml"
        save_params(table, target)
        again = load_params(target)
        assert params_to_mapping(again)["schema"] == SCHEMA
        assert [case.code for case in build_cases(again)] == [case.code for case in build_cases(table)]


class TestFingerprint:
    """工况指纹稳定且对模板 / 模型敏感。"""

    def test_stable_and_sensitive(self, small_params: Path) -> None:
        table = load_params(small_params)
        case = build_cases(table)[0]
        assert case_fingerprint(case) == case_fingerprint(case)
        assert case_fingerprint(case, "tpl-a") != case_fingerprint(case, "tpl-b")
        assert case_fingerprint(case, "tpl", "model-a") != case_fingerprint(case, "tpl", "model-b")

    def test_slug_contains_code_and_hash_prefix(self, small_params: Path) -> None:
        case = build_cases(load_params(small_params))[0]
        assert case.slug() == f"{case.code}-{case.fingerprint[:8]}"
        assert len(case.fingerprint) == 64

    def test_is_tbd(self) -> None:
        assert is_tbd(TBD)
        assert is_tbd(" tbd ")
        assert not is_tbd(0.0)


class TestModel:
    """模型描述：schema、单位、派生键与指纹。"""

    def test_golden_digest(self, model_file: Path) -> None:
        model_set = load_models([model_file])
        assert model_set.models[0].digest == GOLDEN_DIGEST

    def test_digest_ignores_meta_scope_and_tbd(self, model_file: Path) -> None:
        spec = load_models([model_file]).models[0]
        raw = _raw(MODEL_YAML)
        raw["meta"]["revision"] = "r99"
        raw["models"][0]["case_scope"] = {"include": ["M048"]}
        raw["models"][0]["tbd"] = ["geometry.outer_diameter"]
        parsed, _ = parse_models(raw)
        assert parsed[0].digest == spec.digest

    def test_digest_changes_with_value(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["outer_diameter"] = 61.0
        parsed, _ = parse_models(raw)
        assert parsed[0].digest != GOLDEN_DIGEST

    def test_units_are_enforced(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["units"]["length"] = "m"
        with pytest.raises(ModelError, match=r"units\.length"):
            parse_models(raw)

    def test_missing_units_and_geometry(self) -> None:
        raw = {"schema": MODEL_SCHEMA, "models": [{"id": "x"}]}
        with pytest.raises(ModelError, match="units"):
            parse_models(raw)
        with pytest.raises(ModelError, match="geometry"):
            parse_models(
                {
                    "schema": MODEL_SCHEMA,
                    "models": [{"id": "x", "units": {"length": "mm", "density": "g/cm^3", "velocity": "m/s"}}],
                }
            )

    def test_null_section_rejected(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["interface"] = None
        with pytest.raises(ModelError, match="null"):
            parse_models(raw)

    def test_schema_and_models_shape(self) -> None:
        with pytest.raises(ModelError, match="schema"):
            parse_models({"schema": "dynacalc-model/2", "models": []})
        with pytest.raises(ModelError, match="models"):
            parse_models({"schema": MODEL_SCHEMA, "models": []})
        with pytest.raises(ModelError, match="映射"):
            parse_models({"schema": MODEL_SCHEMA, "models": [1]})
        with pytest.raises(ModelError, match="id"):
            parse_models(
                {
                    "schema": MODEL_SCHEMA,
                    "models": [{"units": {"length": "mm", "density": "g/cm^3", "velocity": "m/s"}}],
                }
            )

    def test_duplicate_ids_rejected(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"].append(copy.deepcopy(raw["models"][0]))
        with pytest.raises(ModelError, match="重复"):
            parse_models(raw)
        with pytest.raises(ModelError, match="重复"):
            load_models([model_file, model_file])

    def test_unknown_fields_warn(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["top_unknown"] = 1
        raw["models"][0]["unknown_field"] = 1
        raw["models"][0]["confidence"]["thermal"] = "controlled"
        _, warnings = parse_models(raw)
        assert any("top_unknown" in item for item in warnings)
        assert any("unknown_field" in item for item in warnings)
        assert any("thermal" in item for item in warnings)

    def test_confidence_level_validated(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["confidence"]["geometry"] = "guess"
        with pytest.raises(ModelError, match="confidence"):
            parse_models(raw)

    def test_geometry_consistency(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["inner_diameter"] = 70.0
        with pytest.raises(ModelError, match="内径"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"] = {"nominal": 15.0, "min": 16.0}
        with pytest.raises(ModelError, match="最小壁厚"):
            parse_models(raw)

    def test_required_geometry_key_allows_tbd_value(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["length"] = TBD
        parsed, _ = parse_models(raw)
        assert parsed[0].geometry["length"] == TBD
        raw = _raw(MODEL_YAML)
        del raw["models"][0]["geometry"]["length"]
        with pytest.raises(ModelError, match="length"):
            parse_models(raw)

    def test_eccentricity_angle_range(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["eccentricity"]["angle_deg"] = 360
        with pytest.raises(ModelError, match="angle_deg"):
            parse_models(raw)

    def test_drawing_validation(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"][0]["diameter_reduction"] = 1.5
        with pytest.raises(ModelError, match="diameter_reduction"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"].append(copy.deepcopy(raw["models"][0]["drawing"]["passes"][0]))
        with pytest.raises(ModelError, match="序号"):
            parse_models(raw)

    def test_empty_drawing_is_omitted(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"] = []
        parsed, _ = parse_models(raw)
        assert parsed[0].drawing is None

    def test_minimal_model_golden_digest(self) -> None:
        raw = {
            "schema": MODEL_SCHEMA,
            "models": [
                {
                    "id": "baseline",
                    "units": {"length": "mm", "density": "g/cm^3", "velocity": "m/s"},
                    "geometry": {
                        "outer_diameter": 60.0,
                        "inner_diameter": 30.0,
                        "length": 300.0,
                        "wall_thickness": {"nominal": 15.0},
                    },
                }
            ],
        }
        parsed, _ = parse_models(raw)
        assert parsed[0].digest == "d583539119054ec21c73c78fc0d7a911b015dc9b59b1d8e2c0870b02abe08d5d"

    def test_cumulative_only_drawing(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"] = {"cumulative_reduction": 0.2}
        parsed, _ = parse_models(raw)
        assert parsed[0].drawing == {"passes": [], "cumulative_reduction": 0.2}

    def test_reduction_interval_is_half_open(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"][0]["diameter_reduction"] = 1.0
        with pytest.raises(ModelError, match=r"0, 1"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["cumulative_reduction"] = 1.0
        with pytest.raises(ModelError, match=r"0, 1"):
            parse_models(raw)

    def test_circumferential_wall_thickness(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {
            "kind": "polyline",
            "samples": [
                {"angle_deg": 0, "thickness": 14.8},
                {"angle_deg": 180, "thickness": 15.2},
            ],
        }
        parsed, _ = parse_models(raw)
        values = derived_values(parsed[0])
        assert values["wall_thickness_circumferential_kind"] == "polyline"
        assert values["wall_thickness_circumferential_min"] == 14.8
        assert values["wall_thickness_circumferential_max"] == 15.2
        assert values["wall_thickness_circumferential_mean"] == pytest.approx(15.0)
        assert values["wall_thickness_circumferential_curve"] == "0.0:14.8;180.0:15.2"

    def test_circumferential_validation(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {
            "kind": "polyline",
            "samples": [{"angle_deg": 0, "thickness": 15.0}, {"angle_deg": 0, "thickness": 15.0}],
        }
        with pytest.raises(ModelError, match="递增"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {"kind": "uniform", "value": TBD}
        with pytest.raises(ModelError, match="TBD"):
            parse_models(raw)

    def test_end_loose_mapping(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["end_loose"] = {"length_fraction": 0.1, "density": 1.45}
        parsed, _ = parse_models(raw)
        values = derived_values(parsed[0])
        assert values["density_end_loose_length_fraction"] == 0.1
        assert values["density_end_loose_density"] == 1.45

    def test_absent_tbd_fields_are_omitted_from_digest(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"] = {"axial": {"kind": "tbd"}}
        raw["models"][0]["interface"] = {"gap_mm": TBD, "debond": {"state": "tbd"}}
        parsed, _ = parse_models(raw)
        assert parsed[0].density_field == {}
        assert parsed[0].interface == {}

    def test_density_profile_validation(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"]["samples"][1]["position"] = -0.1
        with pytest.raises(ModelError, match="position"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"]["samples"][2]["position"] = 0.4
        with pytest.raises(ModelError, match="递增"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"] = {"kind": "uniform", "value": "TBD"}
        with pytest.raises(ModelError, match=r"uniform|TBD"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"]["kind"] = "scatter"
        with pytest.raises(ModelError, match="kind"):
            parse_models(raw)

    def test_defect_validation(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"][0]["kind"] = "hole"
        with pytest.raises(ModelError, match="kind"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"].append(
            copy.deepcopy(raw["models"][0]["density_field"]["local_defects"][0])
        )
        with pytest.raises(ModelError, match="重复"):
            parse_models(raw)

    def test_interface_state(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["interface"]["debond"]["state"] = "glued"
        with pytest.raises(ModelError, match=r"debond\.state"):
            parse_models(raw)

    def test_scope_and_resolution(self, model_file: Path) -> None:
        model_set = load_models([model_file])
        assert model_set.resolve("M001") is not None
        assert model_set.resolve("M002") is None
        model_set.validate_against(["M001", "M002"])
        with pytest.raises(ModelError, match="不存在"):
            model_set.validate_against(["M002"])
        raw = _raw(MODEL_YAML)
        raw["models"][0]["case_scope"] = {}
        raw["models"].append(copy.deepcopy(raw["models"][0]))
        raw["models"][1]["id"] = "other"
        parsed, _ = parse_models(raw)
        from fcmd.apps.dynacalc._model import ModelSet

        with pytest.raises(ModelError, match="多个模型"):
            ModelSet(models=parsed).resolve("M001")

    def test_derived_values_and_apply(self, model_file: Path, small_params: Path) -> None:
        spec = load_models([model_file]).models[0]
        values = derived_values(spec)
        assert values["outer_diameter"] == 60.0
        assert values["drawing_passes"] == 1
        assert values["density_axial_min"] == 1.6
        assert values["defect_count"] == 1
        assert values["defect_ids"] == "D1"
        case = build_cases(load_params(small_params))[0]
        merged = apply_model(case, spec, "tpl")
        assert merged.value_mapping()["outer_diameter"] == 60.0
        assert merged.value_mapping()["density"] == 1.6
        assert merged.fingerprint != case.fingerprint
        assert model_tbd_paths(spec)[0] == "density_field.local_defects[0].severity"

    def test_missing_model_returns_none(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["case_scope"] = {"exclude": ["M001"]}
        parsed, _ = parse_models(raw)
        from fcmd.apps.dynacalc._model import ModelSet

        assert ModelSet(models=parsed).resolve("M001") is None

    def test_model_digest_function(self, model_file: Path) -> None:
        spec = load_models([model_file]).models[0]
        assert model_digest(spec) == spec.digest
        assert isinstance(spec, ModelSpec)
