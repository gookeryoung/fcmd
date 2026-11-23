"""dynacalc 参数表与模型描述：矩阵展开、指纹、容错与校验语义。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

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


class TestParamsValidationBranches:
    """参数表校验的其余错误 / 警告分支（拒绝静默降级）。"""

    def test_level_display_covers_bool_and_tbd(self) -> None:
        from fcmd.apps.dynacalc._params import Level

        assert Level("B", True).display() == "B(1)"
        assert Level("F", False).display() == "F(0)"
        assert Level("T", TBD).display() == "T(TBD)"

    def test_case_lookup_errors_and_defaults(self) -> None:
        from fcmd.apps.dynacalc._params import Case

        case = Case(code="M001", group="main", level_codes=(("a", "L1"),), values=(("a", 1.0),))
        assert case.level_of("a") == "L1"
        with pytest.raises(ParamError, match="不含因子"):
            case.level_of("b")
        assert case.value_of("a") == 1.0
        assert case.value_of("b", 5) == 5

    def test_table_factor_lookup_and_keys(self, small_params: Path) -> None:
        table = load_params(small_params)
        assert "density" in table.factor_keys
        with pytest.raises(ParamError, match="没有因子"):
            table.factor("nope")

    def test_matrix_section_must_be_mapping(self) -> None:
        raw = _raw(example_params())
        raw["matrix"] = cast(Any, [])
        with pytest.raises(ParamError, match="matrix"):
            parse_params(raw)

    def test_main_missing_or_empty(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["main"] = None
        with pytest.raises(ParamError, match=r"main 不能为空"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["matrix"]["main"] = []
        with pytest.raises(ParamError, match=r"main 不能为空"):
            parse_params(raw)

    def test_main_must_be_key_list(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["main"] = 5
        with pytest.raises(ParamError, match="因子键列表"):
            parse_params(raw)

    def test_main_duplicate_key(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["main"] = ["density", "density"]
        with pytest.raises(ParamError, match="因子重复"):
            parse_params(raw)

    def test_unused_factor_only_warns(self) -> None:
        raw = _raw(example_params())
        raw["factors"].append(
            {
                "key": "unused_factor",
                "kind": "discrete",
                "confidence": "uncalibrated",
                "levels": [{"code": "A", "value": "a"}],
            }
        )
        table = parse_params(raw)
        assert any("unused_factor" in message for message in table.warnings)

    def test_factors_shape_errors(self) -> None:
        raw = _raw(example_params())
        raw["factors"] = "not-a-list"
        with pytest.raises(ParamError, match="factors 必须是列表"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["factors"] = cast(Any, [])
        with pytest.raises(ParamError, match="factors 不能为空"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["factors"] = [1]
        with pytest.raises(ParamError, match=r"factors\[1\] 必须是映射"):
            parse_params(raw)
        raw = _raw(example_params())
        del raw["factors"][0]["key"]
        with pytest.raises(ParamError, match="缺少 key"):
            parse_params(raw)

    def test_bad_confidence_rejected(self) -> None:
        raw = _raw(example_params())
        raw["factors"][0]["confidence"] = "guess"
        with pytest.raises(ParamError, match="confidence"):
            parse_params(raw)

    def test_levels_shape_errors(self) -> None:
        raw = _raw(example_params())
        raw["factors"][0]["levels"] = []
        with pytest.raises(ParamError, match="levels 必须是非空列表"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["factors"][0]["levels"][0] = 1
        with pytest.raises(ParamError, match=r"levels\[1\] 必须是映射"):
            parse_params(raw)
        raw = _raw(example_params())
        del raw["factors"][0]["levels"][0]["code"]
        with pytest.raises(ParamError, match="缺少 code"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["factors"][0]["levels"][1]["code"] = raw["factors"][0]["levels"][0]["code"]
        with pytest.raises(ParamError, match="水平编码重复"):
            parse_params(raw)

    def test_continuous_level_requires_number(self) -> None:
        raw = _raw(example_params())
        raw["factors"][0]["levels"][0]["value"] = "abc"
        with pytest.raises(ParamError, match="必须是数值或 TBD"):
            parse_params(raw)

    def test_discrete_level_rejects_container(self) -> None:
        raw = _raw(example_params())
        raw["factors"][3]["levels"][0]["value"] = [1, 2]
        with pytest.raises(ParamError, match="必须是标量"):
            parse_params(raw)

    def test_supplementary_none_and_shape(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["supplementary"] = None
        assert not parse_params(raw).supplementary
        raw = _raw(example_params())
        raw["matrix"]["supplementary"] = 5
        with pytest.raises(ParamError, match="supplementary 必须是列表"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["matrix"]["supplementary"] = [{"question": "缺少 factors"}]
        with pytest.raises(ParamError, match=r"supplementary\[1\] 不能为空"):
            parse_params(raw)

    def test_typical_shape_errors(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["typical"] = 5
        with pytest.raises(ParamError, match="典型工况映射"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["matrix"]["typical"] = [1]
        with pytest.raises(ParamError, match=r"typical\[1\] 必须是映射"):
            parse_params(raw)

    def test_typical_code_errors(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["typical"][0]["code"] = "X1"
        with pytest.raises(ParamError, match="code 必须形如 T01"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["matrix"]["typical"][1]["code"] = "T01"
        with pytest.raises(ParamError, match="工况编码重复"):
            parse_params(raw)

    def test_typical_levels_errors(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["typical"][0]["levels"] = {}
        with pytest.raises(ParamError, match="缺少 levels 映射"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["matrix"]["typical"][0]["levels"]["nope"] = "L1"
        with pytest.raises(ParamError, match="引用了未定义因子"):
            parse_params(raw)
        raw = _raw(example_params())
        raw["matrix"]["typical"][0]["levels"]["density"] = 1
        with pytest.raises(ParamError, match="水平编码必须是文本"):
            parse_params(raw)

    def test_typical_missing_factor_warns(self) -> None:
        raw = _raw(example_params())
        for case in raw["matrix"]["typical"]:
            case["levels"].pop("eccentricity", None)
        table = parse_params(raw)
        assert any("eccentricity" in message for message in table.warnings)

    def test_baseline_none_and_shape(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["baseline"] = None
        table = parse_params(raw)
        assert table.baseline["density"] == "L1"
        raw = _raw(example_params())
        raw["matrix"]["baseline"] = 5
        with pytest.raises(ParamError, match="baseline 必须"):
            parse_params(raw)

    def test_baseline_code_must_be_text(self) -> None:
        raw = _raw(example_params())
        raw["matrix"]["baseline"]["density"] = 5
        with pytest.raises(ParamError, match="水平编码文本"):
            parse_params(raw)

    def test_defaults_shape_and_invalid_scalars(self) -> None:
        raw = _raw(example_params())
        raw.pop("defaults")
        assert parse_params(raw).defaults["skip_fraction"] == 0.2
        raw = _raw(example_params())
        raw["defaults"] = 5
        table = parse_params(raw)
        assert any("defaults 不是映射" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["gates"] = 5
        table = parse_params(raw)
        assert any("gates 不是映射" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["gates"]["velocity_rel_diff"] = -1
        table = parse_params(raw)
        assert any("velocity_rel_diff" in message for message in table.warnings)

    def test_gates_reference_and_pairs_warnings(self) -> None:
        raw = _raw(example_params())
        raw["defaults"]["gates"]["reference"] = 5
        table = parse_params(raw)
        assert any("reference" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["gates"]["pairs"] = [[1, 2]]
        table = parse_params(raw)
        assert any("pairs[1]" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["gates"]["pairs"] = "not-a-list"
        table = parse_params(raw)
        assert any("pairs 非法" in message for message in table.warnings)

    def test_settings_invalid_fractions(self) -> None:
        raw = _raw(example_params())
        raw["defaults"]["skip_fraction"] = -0.1
        table = parse_params(raw)
        assert any("skip_fraction" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["skip_fraction"] = 0.6
        raw["defaults"]["tail_fraction"] = 0.6
        table = parse_params(raw)
        assert table.defaults["skip_fraction"] == 0.2
        assert any("小于 1" in message for message in table.warnings)

    def test_settings_probes_and_quench_warnings(self) -> None:
        raw = _raw(example_params())
        raw["defaults"]["probes_mm"] = [0]
        table = parse_params(raw)
        assert any("probes_mm[1]" in message for message in table.warnings)
        assert any("probes_mm 为空" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["probes_mm"] = "abc"
        table = parse_params(raw)
        assert any("probes_mm 非法" in message for message in table.warnings)
        raw = _raw(example_params())
        raw["defaults"]["quench_fraction"] = 0
        table = parse_params(raw)
        assert any("quench_fraction" in message for message in table.warnings)

    def test_run_none_and_shape(self) -> None:
        raw = _raw(example_params())
        raw.pop("run")
        assert parse_params(raw).run["ncpu"] == 4
        raw = _raw(example_params())
        raw["run"] = 5
        table = parse_params(raw)
        assert any("run 不是映射" in message for message in table.warnings)

    def test_run_invalid_fields_fall_back(self) -> None:
        raw = _raw(example_params())
        raw["run"]["solver_path"] = 5
        raw["run"]["ncpu"] = 0
        raw["run"]["memory"] = 5
        raw["run"]["parallel"] = "bogus"
        raw["run"]["mpi"] = "bogus"
        raw["run"]["extra_args"] = [1]
        raw["run"]["env"] = {"a": 1}
        raw["run"]["fake"] = "yes"
        table = parse_params(raw)

        joined = "\n".join(table.warnings)
        for fragment in (
            "solver_path",
            "ncpu",
            "memory",
            "parallel",
            "mpi",
            "extra_args",
            "env",
            "fake",
        ):
            assert fragment in joined
        assert table.run["parallel"] == "smp"
        assert table.run["fake"] is False

    def test_run_memory_empty_becomes_none(self) -> None:
        raw = _raw(example_params())
        raw["run"]["memory"] = ""
        assert parse_params(raw).run["memory"] is None

    def test_save_params_write_error(self, small_params: Path, tmp_path: Path) -> None:
        table = load_params(small_params)
        with pytest.raises(ParamError, match="无法写入"):
            save_params(table, tmp_path)


class TestModelValidationBranches:
    """模型描述校验的其余错误 / 警告与派生值分支。"""

    def test_by_id_unknown_raises(self, model_file: Path) -> None:
        model_set = load_models([model_file])
        assert model_set.by_id("M001-geometry").id == "M001-geometry"
        with pytest.raises(ModelError, match="没有模型"):
            model_set.by_id("nope")

    def test_load_models_file_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ModelError, match="无法读取"):
            load_models([tmp_path / "missing.yaml"])
        broken = tmp_path / "broken.yaml"
        broken.write_text("schema: [", encoding="utf-8")
        with pytest.raises(ModelError, match="YAML"):
            load_models([broken])

    def test_parse_models_top_level_shape(self) -> None:
        with pytest.raises(ModelError, match="映射"):
            parse_models([1])

    def test_unknown_unit_only_warns(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["units"]["mass"] = "kg"
        parsed, warnings = parse_models(raw)
        assert parsed[0].units["length"] == "mm"
        assert any("units.mass" in message for message in warnings)

    def test_confidence_must_be_mapping(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["confidence"] = 5
        with pytest.raises(ModelError, match="confidence 必须是映射"):
            parse_models(raw)

    def test_geometry_section_shape_errors(self) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"] = 5
        with pytest.raises(ModelError, match="wall_thickness"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["eccentricity"] = 5
        with pytest.raises(ModelError, match="eccentricity 必须是映射"):
            parse_models(raw)

    def test_circumferential_shape_and_kind_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = 5
        with pytest.raises(ModelError, match="必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {"kind": "weird"}
        with pytest.raises(ModelError, match="kind"):
            parse_models(raw)

    def test_circumferential_tbd_and_uniform(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {"kind": "tbd"}
        parsed, _ = parse_models(raw)
        assert "circumferential" not in parsed[0].geometry["wall_thickness"]
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {"kind": "uniform", "value": 15.0}
        parsed, _ = parse_models(raw)
        assert parsed[0].geometry["wall_thickness"]["circumferential"] == {"kind": "uniform", "value": 15.0}

    def test_circumferential_polyline_errors(self, model_file: Path) -> None:
        def _build(samples: object) -> dict[str, Any]:
            raw = _raw(MODEL_YAML)
            raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {
                "kind": "polyline",
                "samples": samples,
            }
            return raw

        with pytest.raises(ModelError, match="至少需要 2 个点"):
            parse_models(_build([{"angle_deg": 0, "thickness": 15.0}]))
        with pytest.raises(ModelError, match="必须是映射"):
            parse_models(_build([1, 2]))
        with pytest.raises(ModelError, match="angle_deg 不能是 TBD"):
            parse_models(_build([{"angle_deg": TBD, "thickness": 15.0}, {"angle_deg": 1, "thickness": 15.0}]))
        with pytest.raises(ModelError, match=r"\[0, 360\)"):
            parse_models(_build([{"angle_deg": 400, "thickness": 15.0}, {"angle_deg": 410, "thickness": 15.0}]))

    def test_wall_thickness_nominal_above_max(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["max"] = 14.0
        with pytest.raises(ModelError, match="名义壁厚不能大于最大壁厚"):
            parse_models(raw)

    def test_drawing_shape_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"] = 5
        with pytest.raises(ModelError, match="drawing 必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"] = {"passes": 5}
        with pytest.raises(ModelError, match="passes 必须是列表"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"] = {"passes": [1]}
        with pytest.raises(ModelError, match=r"passes\[1\] 必须是映射"):
            parse_models(raw)

    def test_drawing_pass_value_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"][0]["index"] = 0
        with pytest.raises(ModelError, match=r"index 必须是 ≥1 的整数"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"][0]["wall_thickness_change"] = -1.0
        with pytest.raises(ModelError, match="必须大于 -1"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["drawing"]["passes"][0]["die_angle_deg"] = 90
        with pytest.raises(ModelError, match=r"\(0, 90\)"):
            parse_models(raw)

    def test_density_field_shape_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"] = 5
        with pytest.raises(ModelError, match="density_field 必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["end_loose"] = 5
        with pytest.raises(ModelError, match="必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["end_loose"] = {"length_fraction": 0}
        with pytest.raises(ModelError, match=r"\(0, 1\]"):
            parse_models(raw)

    def test_density_profile_none_helper(self) -> None:
        from fcmd.apps.dynacalc._model import _parse_profile

        assert _parse_profile(None, "m1", "axial", []) == {"kind": "tbd"}

    def test_density_profile_shape_and_uniform(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"] = 5
        with pytest.raises(ModelError, match="必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"] = {"kind": "uniform", "value": 1.63}
        parsed, _ = parse_models(raw)
        assert parsed[0].density_field["axial"] == {"kind": "uniform", "value": 1.63}

    def test_density_profile_polyline_errors(self, model_file: Path) -> None:
        def _build(samples: object) -> dict[str, Any]:
            raw = _raw(MODEL_YAML)
            raw["models"][0]["density_field"]["axial"] = {"kind": "polyline", "samples": samples}
            return raw

        with pytest.raises(ModelError, match="至少需要 2 个点"):
            parse_models(_build([{"position": 0.0, "density": 1.6}]))
        with pytest.raises(ModelError, match="必须是映射"):
            parse_models(_build([1, 2]))
        with pytest.raises(ModelError, match="position 不能是 TBD"):
            parse_models(_build([{"position": TBD, "density": 1.6}, {"position": 1.0, "density": 1.6}]))

    def test_defect_shape_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"] = 5
        with pytest.raises(ModelError, match="必须是列表"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"] = [1]
        with pytest.raises(ModelError, match=r"local_defects\[1\] 必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        del raw["models"][0]["density_field"]["local_defects"][0]["id"]
        with pytest.raises(ModelError, match="缺少 id"):
            parse_models(raw)

    def test_defect_location_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"][0]["location"] = 5
        with pytest.raises(ModelError, match="location 必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"][0]["location"] = {"axial": 2.0}
        with pytest.raises(ModelError, match="必须在 0–1"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["local_defects"][0]["location"] = {"angle_deg": 400}
        with pytest.raises(ModelError, match=r"\[0, 360\)"):
            parse_models(raw)

    def test_interface_shape_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["interface"] = 5
        with pytest.raises(ModelError, match="interface 必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["interface"]["debond"] = 5
        with pytest.raises(ModelError, match="debond 必须是映射"):
            parse_models(raw)

    def test_scope_shape_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["case_scope"] = 5
        with pytest.raises(ModelError, match="case_scope 必须是映射"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["case_scope"] = {"include": 5}
        with pytest.raises(ModelError, match=r"case_scope\.include"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["case_scope"] = {"include": [""]}
        with pytest.raises(ModelError, match="非法工况编码"):
            parse_models(raw)

    def test_unknown_geometry_field_warns(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["weird"] = 1
        _, warnings = parse_models(raw)
        assert any("geometry.weird" in message for message in warnings)

    def test_tbd_doc_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["tbd"] = 5
        with pytest.raises(ModelError, match="tbd 必须是文本列表"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["tbd"] = [""]
        with pytest.raises(ModelError, match="含非法条目"):
            parse_models(raw)

    def test_numeric_value_errors(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["length"] = "abc"
        with pytest.raises(ModelError, match="必须是数值或 TBD"):
            parse_models(raw)
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["length"] = float("inf")
        with pytest.raises(ModelError, match="有限数值"):
            parse_models(raw)

    def test_tbd_paths_include_undeclared_payload_tbd(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"]["samples"][1]["density"] = TBD
        parsed, _ = parse_models(raw)
        paths = model_tbd_paths(parsed[0])
        assert "density_field.axial.samples[1].density" in paths

    def test_derived_values_uniform_axial(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"] = {"kind": "uniform", "value": 1.62}
        parsed, _ = parse_models(raw)
        values = derived_values(parsed[0])
        assert values["density_axial_min"] == 1.62
        assert values["density_axial_max"] == 1.62
        assert values["density_axial_mean"] == 1.62

    def test_derived_values_axial_tbd_and_polyline_tbd(self, model_file: Path) -> None:
        minimal = {
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
        parsed, _ = parse_models(minimal)
        values = derived_values(parsed[0])
        assert values["density_axial_kind"] == "tbd"
        assert values["drawing_passes"] == TBD

        raw = _raw(MODEL_YAML)
        raw["models"][0]["density_field"]["axial"]["samples"][0]["density"] = TBD
        parsed, _ = parse_models(raw)
        values = derived_values(parsed[0])
        assert values["density_axial_min"] == TBD

    def test_derived_values_circumferential_uniform(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {"kind": "uniform", "value": 15.0}
        parsed, _ = parse_models(raw)
        values = derived_values(parsed[0])
        assert values["wall_thickness_circumferential_kind"] == "uniform"
        assert values["wall_thickness_circumferential_min"] == 15.0
        assert values["wall_thickness_circumferential_curve"] == "0:15.0"

    def test_derived_values_circumferential_polyline_tbd(self, model_file: Path) -> None:
        raw = _raw(MODEL_YAML)
        raw["models"][0]["geometry"]["wall_thickness"]["circumferential"] = {
            "kind": "polyline",
            "samples": [
                {"angle_deg": 0, "thickness": TBD},
                {"angle_deg": 180, "thickness": 15.2},
            ],
        }
        parsed, _ = parse_models(raw)
        values = derived_values(parsed[0])
        assert values["wall_thickness_circumferential_kind"] == "polyline"
        assert values["wall_thickness_circumferential_min"] == TBD
