"""dynacalc 入口：CLI 子命令、GUI 纯函数与 48 组最小闭环。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.dynacalc import dynacalc, dynacalcw
from fcmd.apps.dynacalc._params import load_params
from fcmd.apps.dynacalc._template import generate_decks, load_manifest
from fcmd.apps.dynacalc.dynacalcw import (
    DynacalcSettings,
    case_rows,
    config_path,
    generation_summary,
    job_rows,
    load_settings,
    parse_codes,
    save_settings,
)
from fcmd.core.toolkit import list_subcommands


class TestSettings:
    """窗口设置读写与纯格式化函数。"""

    def test_config_path_honours_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
        assert config_path() == tmp_path / "home" / "ui" / "dynacalcw.json"

    def test_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "ui" / "dynacalcw.json"
        settings = DynacalcSettings(params="a.yaml", out="out", ncpu=8, fake=False)
        save_settings(settings, target)
        loaded = load_settings(target)
        assert loaded == settings
        assert json.loads(target.read_text(encoding="utf-8"))["ncpu"] == 8

    def test_tolerant_loading(self, tmp_path: Path) -> None:
        target = tmp_path / "ui.json"
        assert load_settings(target) == DynacalcSettings()
        target.write_text("not json", encoding="utf-8")
        assert load_settings(target) == DynacalcSettings()
        target.write_text("[1, 2]", encoding="utf-8")
        assert load_settings(target) == DynacalcSettings()
        target.write_text('{"ncpu": "eight", "unknown": 1}', encoding="utf-8")
        assert load_settings(target) == DynacalcSettings()

    def test_parse_codes(self) -> None:
        assert parse_codes("M001, M002  M001") == ("M001", "M002")
        assert parse_codes("  ") == ()

    def test_rows_and_summary(self, small_params: Path, tmp_path: Path) -> None:
        result = generate_decks(load_params(small_params), tmp_path / "out")
        rows = case_rows(result.manifest)
        assert rows[0][0] == "M001"
        assert "4 个工况" in generation_summary(result)
        assert job_rows(tmp_path / "out") == []
        (tmp_path / "out" / "jobs.json").write_text("not json", encoding="utf-8")
        assert job_rows(tmp_path / "out")[0][1] == "读取失败"

    def test_tool_registration(self) -> None:
        assert dynacalcw.__version__ == "0.1.0"
        assert list_subcommands("dynacalc") == [
            "compare",
            "demo",
            "example",
            "gates",
            "generate",
            "matrix",
            "model",
            "params",
            "report",
            "run",
            "template",
        ]
        assert list_subcommands("dynacalcw") == []


class TestCli:
    """CLI 子命令的正常与失败路径。"""

    def test_params_and_matrix(self, small_params: Path, capsys: pytest.CaptureFixture[str]) -> None:
        dynacalc.params_command(small_params)
        output = capsys.readouterr().out
        assert "主矩阵 density × inner_diameter = 2 组" in output
        dynacalc.params_command(small_params, json_output=True)
        assert json.loads(capsys.readouterr().out)["matrix"]["main_cases"] == 2
        dynacalc.matrix_command(small_params)
        assert "M002" in capsys.readouterr().out
        dynacalc.matrix_command(small_params, out=small_params.parent / "cases.json")
        assert json.loads((small_params.parent / "cases.json").read_text(encoding="utf-8"))["cases"]

    def test_model_command(self, model_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        dynacalc.model_command([model_file])
        output = capsys.readouterr().out
        assert "M001-geometry" in output
        dynacalc.model_command([model_file], json_output=True)
        assert "models" in capsys.readouterr().out

    def test_generate_and_plan_only(
        self, small_params: Path, model_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = tmp_path / "out"
        dynacalc.generate_command(small_params, out, model=[model_file])
        output = capsys.readouterr().out
        assert "已生成 4 个工况" in output
        assert "模型绑定：绑定 1 个工况" in output
        dynacalc.run_command(out, plan_only=True)
        assert "未启动任何进程" in capsys.readouterr().out
        assert not (out / "jobs.json").exists()

    def test_errors_exit_2(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.params_command(tmp_path / "missing.yaml")
        assert excinfo.value.code == 2
        with pytest.raises(SystemExit):
            dynacalc.matrix_command(tmp_path / "missing.yaml")

    def test_report_and_compare_errors(self, small_params: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        dynacalc.generate_command(small_params, out)
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.report_command(out)
        assert excinfo.value.code == 2
        with pytest.raises(SystemExit):
            dynacalc.compare_command(out, "M001", "NOPE")

    def test_example_and_template(self, tmp_path: Path) -> None:
        target = tmp_path / "params.yaml"
        dynacalc.example_command(target)
        assert "schema: dynacalc/1" in target.read_text(encoding="utf-8")
        template = tmp_path / "deck.k"
        dynacalc.template_command(template)
        assert "*KEYWORD" in template.read_text(encoding="utf-8")


class TestCliFailurePaths:
    """子命令的过滤、打印与校验失败路径（统一退出码 2）。"""

    def test_template_without_out_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        dynacalc.template_command()
        assert "*KEYWORD" in capsys.readouterr().out

    def test_params_prints_typical_cases(self, params_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
        dynacalc.params_command(params_file)
        output = capsys.readouterr().out
        assert "典型工况 T01" in output
        assert "未冻结水平" in output

    def test_matrix_only_filter(self, small_params: Path, capsys: pytest.CaptureFixture[str]) -> None:
        dynacalc.matrix_command(small_params, only=["M001", " ", "M002"])
        assert "共 2 个工况" in capsys.readouterr().out

    def test_matrix_unknown_code(self, small_params: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.matrix_command(small_params, only=["NOPE"])
        assert excinfo.value.code == 2
        assert "未知工况编码" in capsys.readouterr().out

    def test_model_invalid_schema(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("schema: nope\nmodels: []\n", encoding="utf-8")
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.model_command([bad])
        assert excinfo.value.code == 2
        assert "schema" in capsys.readouterr().out

    def test_generate_missing_params(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.generate_command(tmp_path / "missing.yaml", tmp_path / "out")
        assert excinfo.value.code == 2

    def test_run_missing_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.run_command(tmp_path / "missing")
        assert excinfo.value.code == 2

    def test_report_missing_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.report_command(tmp_path / "missing")
        assert excinfo.value.code == 2

    def test_gates_missing_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.gates_command(tmp_path / "missing")
        assert excinfo.value.code == 2

    def test_demo_requires_existing_mesh_root(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            dynacalc.demo_command(tmp_path / "demo", mesh_root=tmp_path / "mesh")
        assert excinfo.value.code == 2
        assert "网格根目录" in capsys.readouterr().out


@pytest.mark.slow
class TestClosedLoop:
    """48 组主矩阵 + 五项补充组 + 典型工况 01/02/03 的完整闭环：生成 → dry-run → 假求解 → 汇总 → 门控。"""

    def test_full_loop(self, params_file: Path, tmp_path: Path) -> None:
        out = tmp_path / "out"
        table = load_params(params_file)
        result = generate_decks(table, out)
        assert result.manifest.total == 63
        assert sum(1 for item in result.manifest.cases if item.group == "main") == 48
        assert sum(1 for item in result.manifest.cases if item.group.startswith("supplementary")) == 12
        assert sum(1 for item in result.manifest.cases if item.group == "typical") == 3
        manifest = load_manifest(out)
        assert manifest.tbd_cases == manifest.total

        dynacalc.run_command(out, plan_only=True)
        assert not (out / "jobs.json").exists()

        dynacalc.run_command(out, fake=True, concurrency=8)
        jobs = json.loads((out / "jobs.json").read_text(encoding="utf-8"))
        assert len(jobs["jobs"]) == 63
        assert all(item["status"] == "done" for item in jobs["jobs"].values())

        dynacalc.report_command(out, report_out=tmp_path / "report.csv", output_format="csv")
        assert (tmp_path / "report.csv").is_file()

        with pytest.raises(SystemExit) as excinfo:
            dynacalc.gates_command(out)
        assert excinfo.value.code == 1
