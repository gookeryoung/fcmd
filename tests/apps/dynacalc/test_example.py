"""dynacalc 内置示例：结构不变量、典型工况 01/02/03、跨应用互通与一键演示。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from fcmd.apps.dynacalc import dynacalc
from fcmd.apps.dynacalc._model import load_models
from fcmd.apps.dynacalc._params import (
    TBD,
    ParamTable,
    build_cases,
    example_params,
    expand_main_cases,
    expand_supplementary_cases,
    group_text,
    parse_params,
)
from fcmd.apps.dynacalc._template import DeckError, generate_decks, load_manifest

MAIN_LEVELS = {
    "density": 2,
    "inner_diameter": 3,
    "wall_thickness": 2,
    "axial_density": 2,
    "eccentricity": 2,
}
SUPPLEMENTARY_LEVELS = {
    "gap_mm": 3,
    "initiation_strength": 3,
    "residual_stress": 2,
    "die_skew": 2,
    "wall_thickness_direction": 2,
}
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def table() -> ParamTable:
    """解析内置示例参数表。"""
    return parse_params(yaml.safe_load(example_params()))


class TestExampleStructure:
    """示例参数表锁定的结构性事实（不含任何真实数值）。"""

    def test_main_matrix_is_cartesian_product(self, table: ParamTable) -> None:
        assert table.main_keys == tuple(MAIN_LEVELS)
        cases = expand_main_cases(table)
        assert len(cases) == math.prod(MAIN_LEVELS.values())
        assert [case.code for case in cases] == [f"M{index:03d}" for index in range(1, len(cases) + 1)]
        for case in cases:
            assert len(case.level_codes) == len(MAIN_LEVELS)

    def test_main_continuous_levels_stay_tbd(self, table: ParamTable) -> None:
        assert table.tbd_counts() == {
            "density": 2,
            "inner_diameter": 3,
            "wall_thickness": 2,
            "gap_mm": 2,
        }
        assert all(case.tbd for case in expand_main_cases(table))

    def test_supplementary_matches_report_items(self, table: ParamTable) -> None:
        assert [list(group.factors) for group in table.supplementary] == [[key] for key in SUPPLEMENTARY_LEVELS]
        assert all(group.question for group in table.supplementary)
        cases = expand_supplementary_cases(table)
        assert len(cases) == sum(SUPPLEMENTARY_LEVELS.values())
        for group, expected in zip(table.supplementary, SUPPLEMENTARY_LEVELS.values(), strict=True):
            key = group.factors[0]
            assert sum(1 for case in cases if case.group == f"supplementary:{key}") == expected

    def test_supplementary_only_moves_its_own_factor(self, table: ParamTable) -> None:
        for case in expand_supplementary_cases(table):
            key = case.group.split(":", 1)[1]
            for name in table.main_keys:
                if name != key:
                    assert case.level_of(name) == table.baseline[name]

    def test_typical_cases_are_explicit(self, table: ParamTable) -> None:
        cases = {case.code: case for case in build_cases(table) if case.group == "typical"}
        assert list(cases) == ["T01", "T02", "T03"]
        assert all(len(case.level_codes) == len(table.factors) for case in cases.values())
        assert cases["T01"].value_of("eccentricity") == "symmetric"
        assert cases["T02"].value_of("eccentricity") == "eccentric"
        assert cases["T02"].value_of("wall_thickness_direction") == "reverse"
        assert cases["T03"].value_of("density") == TBD
        assert all(case.tbd for case in cases.values())

    def test_typical_matches_gates_reference(self, table: ParamTable) -> None:
        assert table.gates["reference"] == "T01"
        assert [tuple(pair) for pair in table.gates["pairs"]] == [("T01", "T02"), ("T01", "T03")]

    def test_group_labels(self) -> None:
        assert group_text("typical") == "典型工况"
        assert group_text("supplementary:gap_mm") == "补充组"
        assert group_text("main") == "主矩阵"

    def test_strict_generation_refuses_tbd(self, table: ParamTable, tmp_path: Path) -> None:
        with pytest.raises(DeckError, match="TBD"):
            generate_decks(table, tmp_path / "out", strict=True)


class TestCrossApp:
    """与 paramcad 示例模型（digest ed6f...）的绑定口径。"""

    def test_paramcad_example_model_binds_typical_cases(self, table: ParamTable, tmp_path: Path) -> None:
        from fcmd.apps.paramcad import _export

        model = _export.example_model()
        assert _export.model_digest(model) == "ed6f597a09a71381bfc86998daaade08e3276fa721a2e54d66abf51463f28dc4"
        model_file = tmp_path / "model.yaml"
        _export.export_model(model, model_file, overwrite=True)
        result = generate_decks(
            table,
            tmp_path / "out",
            only=["T01", "T02", "T03"],
            models=load_models([model_file]),
        )
        assert result.manifest.total == 3
        assert result.manifest.model_fallback == 0
        assert result.manifest.model_bound == 3


class TestDiscoverability:
    """一键演示入口必须出现在 README 与 docs/cli.md。"""

    def test_docs_show_demo_command(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        cli_doc = (REPO_ROOT / "docs" / "cli.md").read_text(encoding="utf-8")
        assert "dynacalc demo" in readme
        assert "dynacalc demo" in cli_doc


@pytest.mark.slow
class TestDemo:
    """一键演示：示例 → 生成 → 假求解 → 汇总 → 对比 → 门控。"""

    def test_demo_runs_typical_cases(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        out = tmp_path / "demo"
        dynacalc.demo_command(out)
        output = capsys.readouterr().out
        assert "步骤 2/6 生成：3 个工况" in output
        assert "步骤 5/6 对比" in output
        assert "（典型工况）" in output
        manifest = load_manifest(out / "decks")
        assert manifest.total == 3
        assert {case.code for case in manifest.cases} == {"T01", "T02", "T03"}
        assert (out / "report.txt").is_file()
