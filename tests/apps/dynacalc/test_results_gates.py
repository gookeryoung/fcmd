"""dynacalc 结果回收与门控：解析错误必须显式，四项判据可配置。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.dynacalc._gates import DISCLAIMER, FAIL, PASS, SKIP, evaluate, format_gate_report
from fcmd.apps.dynacalc._params import load_params
from fcmd.apps.dynacalc._results import (
    CaseMetrics,
    CaseResult,
    ResultParseError,
    ResultSet,
    collect_case,
    collect_results,
    compare_cases,
    extract_metrics,
    format_text_report,
    parse_glstat,
    parse_history,
    parse_matsum,
    result_to_csv,
    result_to_json,
    write_report,
)
from fcmd.apps.dynacalc._template import generate_decks

SETTINGS: dict[str, Any] = {
    "skip_fraction": 0.2,
    "tail_fraction": 0.1,
    "probes_mm": [50.0, 100.0],
}


def _write_history(path: Path, velocity: float = 6500.0, steps: int = 11, final: float = 1.0) -> None:
    lines = ["time,front_z,reaction_fraction,wall_velocity,wall_strain"]
    for index in range(steps):
        time = index * 5e-5
        fraction = final if index == steps - 1 else min(final, index / max(steps - 1, 1) * final)
        lines.append(f"{time:.6e},{velocity * time:.6e},{fraction:.6f},{10.0 + index:.6f},{0.001 * index:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_glstat(path: Path, steps: int = 3) -> None:
    blocks = []
    for index in range(steps):
        blocks.append(
            f" time =        {index * 1e-5:.6e}\n"
            f" internal energy = {1.0 + index:.6e}\n"
            f" total energy = {2.0 + index:.6e}\n"
        )
    path.write_text("\n".join(blocks), encoding="utf-8", newline="\n")


def _write_matsum(path: Path) -> None:
    path.write_text(
        " part id = 1\n internal energy = 3.0\n part id = 2\n internal energy = 5.0\n",
        encoding="utf-8",
        newline="\n",
    )


class TestParsers:
    """三类结果文件的正常与损坏路径。"""

    def test_history_ok(self, tmp_path: Path) -> None:
        path = tmp_path / "history.csv"
        _write_history(path)
        history = parse_history(path)
        assert len(history) == 11
        assert history.front_z[-1] > 0

    def test_history_errors(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.csv"
        with pytest.raises(ResultParseError, match="缺少"):
            parse_history(missing)
        empty = tmp_path / "empty.csv"
        empty.write_text("", encoding="utf-8")
        with pytest.raises(ResultParseError, match="为空"):
            parse_history(empty)
        columns = tmp_path / "columns.csv"
        columns.write_text("time,front_z\n0,0\n1,1\n", encoding="utf-8")
        with pytest.raises(ResultParseError, match="缺少列"):
            parse_history(columns)
        short = tmp_path / "short.csv"
        short.write_text("time,front_z,reaction_fraction,wall_velocity,wall_strain\n0,0,1,1\n", encoding="utf-8")
        with pytest.raises(ResultParseError, match="列数不足"):
            parse_history(short)
        bad = tmp_path / "bad.csv"
        bad.write_text(
            "time,front_z,reaction_fraction,wall_velocity,wall_strain\n0,0,1,1,0\nx,1,1,1,0\n",
            encoding="utf-8",
        )
        with pytest.raises(ResultParseError, match="不是数值"):
            parse_history(bad)
        unsorted = tmp_path / "unsorted.csv"
        unsorted.write_text(
            "time,front_z,reaction_fraction,wall_velocity,wall_strain\n0,0,1,1,0\n1,1,1,1,0\n1,2,1,1,0\n",
            encoding="utf-8",
        )
        with pytest.raises(ResultParseError, match="递增"):
            parse_history(unsorted)

    def test_glstat_ok_and_errors(self, tmp_path: Path) -> None:
        path = tmp_path / "glstat"
        _write_glstat(path)
        blocks = parse_glstat(path)
        assert len(blocks) == 3
        assert blocks[-1].values["internal energy"] == 3.0
        empty = tmp_path / "glstat_empty"
        empty.write_text("nothing here\n", encoding="utf-8")
        with pytest.raises(ResultParseError, match="time"):
            parse_glstat(empty)
        no_energy = tmp_path / "glstat_no_energy"
        no_energy.write_text(" time = 1.0\n kinetic energy = 2.0\n", encoding="utf-8")
        with pytest.raises(ResultParseError, match="internal energy"):
            parse_glstat(no_energy)

    def test_matsum_ok_and_error(self, tmp_path: Path) -> None:
        path = tmp_path / "matsum"
        _write_matsum(path)
        summary = parse_matsum(path)
        assert summary.part_count == 2
        assert summary.max_part_internal_energy == 5.0
        empty = tmp_path / "matsum_empty"
        empty.write_text("nothing\n", encoding="utf-8")
        with pytest.raises(ResultParseError, match="internal energy"):
            parse_matsum(empty)


class TestMetrics:
    """指标提取与汇总。"""

    def test_extract_metrics(self, tmp_path: Path) -> None:
        history_path = tmp_path / "history.csv"
        glstat_path = tmp_path / "glstat"
        matsum_path = tmp_path / "matsum"
        _write_history(history_path)
        _write_glstat(glstat_path)
        _write_matsum(matsum_path)
        metrics = extract_metrics(
            "M001",
            parse_history(history_path),
            parse_glstat(glstat_path),
            parse_matsum(matsum_path),
            SETTINGS,
        )
        assert metrics.value("velocity_m_s") == pytest.approx(6500.0)
        assert metrics.value("reaction_fraction_end") == pytest.approx(1.0)
        assert metrics.arrivals_us[50.0] == pytest.approx(50e-3 / 6500 * 1e6, rel=1e-6)
        assert metrics.value("matsum_part_count") == 2.0
        assert metrics.value("glstat_peak_internal_energy") == pytest.approx(3.0)

    def test_unreached_probe_warns(self, tmp_path: Path) -> None:
        history_path = tmp_path / "history.csv"
        _write_history(history_path)
        glstat_path = tmp_path / "glstat"
        _write_glstat(glstat_path)
        matsum_path = tmp_path / "matsum"
        _write_matsum(matsum_path)
        metrics = extract_metrics(
            "M001",
            parse_history(history_path),
            parse_glstat(glstat_path),
            parse_matsum(matsum_path),
            {"probes_mm": [100000.0]},
        )
        assert any("未到达" in item for item in metrics.warnings)

    def test_collect_case_errors(self, small_params: Path, tmp_path: Path) -> None:
        root = tmp_path / "out"
        manifest = generate_decks(load_params(small_params), root).manifest
        no_files = collect_case(root, manifest.cases[0], manifest.settings)
        assert not no_files.ok
        assert "缺少结果文件" in no_files.error
        shutil.rmtree(root / manifest.cases[0].slug)
        no_dir = collect_case(root, manifest.cases[0], manifest.settings)
        assert not no_dir.ok
        assert "目录不存在" in no_dir.error

    def test_collect_results_mixed(self, small_params: Path, tmp_path: Path) -> None:
        root = tmp_path / "out"
        manifest = generate_decks(load_params(small_params), root).manifest
        good = root / manifest.cases[0].slug
        _write_history(good / "history.csv")
        _write_glstat(good / "glstat")
        _write_matsum(good / "matsum")
        result_set = collect_results(root, manifest)
        assert result_set.results[0].ok
        assert len(result_set.failed()) == 3
        assert result_set.by_code(manifest.cases[0].code).ok
        with pytest.raises(ResultParseError, match="没有工况"):
            result_set.by_code("NOPE")

    def test_reports(self, small_params: Path, tmp_path: Path) -> None:
        root = tmp_path / "out"
        manifest = generate_decks(load_params(small_params), root).manifest
        for item in manifest.cases:
            directory = root / item.slug
            _write_history(directory / "history.csv")
            _write_glstat(directory / "glstat")
            _write_matsum(directory / "matsum")
        result_set = collect_results(root, manifest)
        text = format_text_report(result_set)
        assert "M001" in text and "爆速" in text
        csv_text = result_to_csv(result_set)
        assert "code" in csv_text and "爆速" in csv_text
        assert '"cases"' in result_to_json(result_set)
        for fmt, suffix in (("text", "txt"), ("csv", "csv"), ("json", "json")):
            target = tmp_path / f"report.{suffix}"
            write_report(result_set, target, fmt)
            assert target.is_file()
        rows = compare_cases(result_set, "M001", "M002")
        assert any(row.label for row in rows)
        with pytest.raises(ResultParseError, match="没有工况"):
            compare_cases(result_set, "M001", "NOPE")


def _metrics(
    code: str, velocity: float = 6500.0, arrival: float = 7.69, impulse: float = 1.0, final: float = 1.0
) -> CaseMetrics:
    return CaseMetrics(
        code=code,
        values={
            "velocity_m_s": velocity,
            "velocity_r2": 1.0,
            "reaction_fraction_end": final,
            "wall_velocity_max_m_s": 100.0,
            "wall_impulse_m": impulse,
            "wall_strain_max": 0.01,
        },
        arrivals_us={50.0: arrival},
        warnings=(),
    )


class TestGates:
    """四项工程判据的通过 / 不通过 / 跳过。"""

    def _set(self, tmp_path: Path, small_params: Path, results: list[CaseResult]) -> ResultSet:
        manifest = generate_decks(load_params(small_params), tmp_path / "out").manifest
        return ResultSet(manifest=manifest, results=tuple(results))

    def test_all_pass(self, tmp_path: Path, small_params: Path) -> None:
        result_set = self._set(
            tmp_path,
            small_params,
            [CaseResult("M001", "ok", _metrics("M001")), CaseResult("M002", "ok", _metrics("M002"))],
        )
        report = evaluate(result_set, result_set.manifest.settings)
        assert report.passed
        assert report.count(PASS) == 4
        assert DISCLAIMER in format_gate_report(report)

    def test_failures(self, tmp_path: Path, small_params: Path) -> None:
        result_set = self._set(
            tmp_path,
            small_params,
            [
                CaseResult("M001", "ok", _metrics("M001")),
                CaseResult("M002", "ok", _metrics("M002", velocity=8000.0, arrival=9.0, impulse=2.0, final=0.5)),
            ],
        )
        report = evaluate(result_set, result_set.manifest.settings)
        assert report.count(FAIL) >= 3

    def test_error_case_is_skipped(self, tmp_path: Path, small_params: Path) -> None:
        result_set = self._set(
            tmp_path,
            small_params,
            [CaseResult("M001", "ok", _metrics("M001")), CaseResult("M002", "error", error="坏文件")],
        )
        report = evaluate(result_set, result_set.manifest.settings)
        assert report.count(SKIP) == 4

    def test_settings_override_and_pairs(self, tmp_path: Path, small_params: Path) -> None:
        result_set = self._set(
            tmp_path,
            small_params,
            [CaseResult("M001", "ok", _metrics("M001")), CaseResult("M002", "ok", _metrics("M002"))],
        )
        settings = {
            "gates": {"velocity_rel_diff": 0.001, "reference": "M001", "pairs": [["M001", "M002"]]},
            "probes_mm": [50.0],
        }
        report = evaluate(result_set, settings)
        assert {item.pair for item in report.checks} == {("M001", "M002")}
        assert report.count(FAIL) == 0

    def test_bad_settings(self, tmp_path: Path, small_params: Path) -> None:
        result_set = self._set(tmp_path, small_params, [CaseResult("M001", "ok", _metrics("M001"))])
        with pytest.raises(ValueError, match="基准工况"):
            evaluate(result_set, {"gates": {"reference": "NOPE"}})
        with pytest.raises(ValueError, match="格式非法"):
            evaluate(result_set, {"gates": {"pairs": [["M001"]]}})
        with pytest.raises(ValueError, match="不存在"):
            evaluate(result_set, {"gates": {"pairs": [["M001", "NOPE"]]}})
