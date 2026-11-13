"""dynacalc deck 生成与求解调度：可复现、TBD 语义、假求解器、dry-run 与续跑。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fcmd.apps.dynacalc._model import load_models
from fcmd.apps.dynacalc._params import TBD, build_cases, load_params
from fcmd.apps.dynacalc._solver import (
    SolverConfig,
    SolverError,
    build_command,
    config_for_manifest,
    load_jobs,
    run_jobs,
)
from fcmd.apps.dynacalc._template import (
    DeckError,
    Manifest,
    default_template,
    generate_decks,
    load_manifest,
    render_template,
    template_placeholders,
)


class TestRender:
    """模板渲染与 TBD 语义。"""

    def test_required_placeholder_reports_line(self, small_params: Path) -> None:
        case = build_cases(load_params(small_params))[0]
        with pytest.raises(DeckError, match="第 2 行"):
            render_template("*KEYWORD\n${nope}\n*END\n", case)

    def test_optional_placeholder_becomes_tbd(self, small_params: Path) -> None:
        case = build_cases(load_params(small_params))[0]
        text, tbd = render_template("x=${nope?}\ny=${density}\n", case)
        assert f"x={TBD}" in text
        assert "y=1.6" in text
        assert tbd == ("nope",)

    def test_placeholders_are_listed_once(self) -> None:
        found = template_placeholders("${b} ${a?} ${b}")
        assert found == [("b", False), ("a", True)]

    def test_builtin_template_has_core_keywords(self) -> None:
        text = default_template()
        for keyword in ("*KEYWORD", "*MAT_HIGH_EXPLOSIVE_BURN", "*EOS_JWL", "*ALE_MULTI_MATERIAL_GROUP", "*END"):
            assert keyword in text


class TestGenerate:
    """生成目录、清单、模型映射与可复现性。"""

    def test_directory_name_and_manifest(self, small_params: Path, tmp_path: Path) -> None:
        result = generate_decks(load_params(small_params), tmp_path / "out")
        assert result.manifest.total == 4
        assert len(result.plans) == 4
        directory = result.plans[0].directory
        assert directory.name == result.plans[0].case.slug()
        assert (directory / "case.k").is_file()
        assert (directory / "case.json").is_file()
        assert (tmp_path / "out" / "manifest.json").is_file()
        assert load_manifest(tmp_path / "out").total == 4

    def test_generation_is_reproducible(self, small_params: Path, tmp_path: Path) -> None:
        table = load_params(small_params)
        first = generate_decks(table, tmp_path / "a")
        second = generate_decks(table, tmp_path / "b")
        assert [plan.text for plan in first.plans] == [plan.text for plan in second.plans]
        for plan in first.plans:
            left = (plan.directory / "case.k").read_bytes()
            right = (tmp_path / "b" / plan.directory.name / "case.k").read_bytes()
            assert left == right

    def test_strict_rejects_tbd(self, params_file: Path, tmp_path: Path) -> None:
        with pytest.raises(DeckError, match="TBD"):
            generate_decks(load_params(params_file), tmp_path / "out", strict=True)

    def test_only_filter(self, small_params: Path, tmp_path: Path) -> None:
        result = generate_decks(load_params(small_params), tmp_path / "out", only=["M002"])
        assert result.manifest.total == 1
        assert result.plans[0].case.code == "M002"

    def test_unknown_only_code(self, small_params: Path, tmp_path: Path) -> None:
        with pytest.raises(DeckError, match="NOPE"):
            generate_decks(load_params(small_params), tmp_path / "out", only=["NOPE"])

    def test_custom_template_required_field(self, small_params: Path, tmp_path: Path) -> None:
        with pytest.raises(DeckError, match="必填占位符"):
            generate_decks(load_params(small_params), tmp_path / "out", template="*KEYWORD\n${unknown}\n")

    def test_model_overrides_and_falls_back(self, small_params: Path, model_file: Path, tmp_path: Path) -> None:
        models = load_models([model_file])
        result = generate_decks(load_params(small_params), tmp_path / "out", models=models)
        manifest = result.manifest
        assert manifest.model_bound == 1
        assert manifest.model_fallback == 3
        m001 = (tmp_path / "out" / manifest.case("M001").slug / "case.k").read_text(encoding="utf-8")
        m002 = (tmp_path / "out" / manifest.case("M002").slug / "case.k").read_text(encoding="utf-8")
        assert "outer_diameter(mm) = 60.0" in m001
        assert "model = M001-geometry" in m001
        assert "outer_diameter(mm) = TBD" in m002
        assert manifest.case("M001").model_digest
        assert manifest.models[0]["id"] == "M001-geometry"

    def test_model_scope_errors(self, small_params: Path, model_file: Path, tmp_path: Path) -> None:
        models = load_models([model_file])
        with pytest.raises(Exception, match="不存在"):
            generate_decks(load_params(small_params), tmp_path / "out", models=models, only=["M002"])


class TestSolverConfig:
    """求解器命令与配置校验。"""

    def test_fake_command(self, tmp_path: Path) -> None:
        config = SolverConfig(fake=True)
        command = build_command(config, tmp_path / "deck")
        assert command[:3] == [sys.executable, "-m", "fcmd.apps.dynacalc._fakesolver"]
        assert command[-1] == str(tmp_path / "deck")

    def test_smp_command(self) -> None:
        config = SolverConfig(solver_path="lsdyna", ncpu=8, memory="1g")
        command = build_command(config, Path("case.k"))
        assert command == ["lsdyna", "i=case.k", "ncpu=8", "memory=1g"]

    def test_mpp_command(self) -> None:
        config = SolverConfig(solver_path="lsdyna_mpp", parallel="mpp", mpi="msmpi", ncpu=4)
        command = build_command(config, Path("case.k"))
        assert "-np" in command and "i=case.k" in command

    def test_config_validation(self) -> None:
        with pytest.raises(SolverError, match="ncpu"):
            SolverConfig.from_mapping({"ncpu": 0})
        with pytest.raises(SolverError, match="parallel"):
            SolverConfig.from_mapping({"parallel": "cuda"})
        with pytest.raises(SolverError, match="timeout_s"):
            SolverConfig.from_mapping({"timeout_s": -1})
        assert SolverConfig.from_mapping({"extra_args": ["a=b"]}).extra_args == ("a=b",)

    def test_config_from_manifest(self, small_params: Path, tmp_path: Path) -> None:
        manifest = generate_decks(load_params(small_params), tmp_path / "out").manifest
        config = config_for_manifest(manifest, {"fake": True, "concurrency": 3})
        assert config.fake is True
        assert config.concurrency == 3


class TestRunJobs:
    """假求解器端到端：执行、续跑、失败、超时与 dry-run。"""

    @pytest.fixture
    def generated(self, small_params: Path, tmp_path: Path) -> tuple[Path, Manifest]:
        root = tmp_path / "out"
        result = generate_decks(load_params(small_params), root)
        return root, result.manifest

    def test_fake_run_and_resume(self, generated: tuple[Path, Manifest]) -> None:
        root, manifest = generated
        config = config_for_manifest(manifest, {"fake": True, "concurrency": 2})
        report = run_jobs(manifest, config, root=root)
        assert len(report.jobs) == 4
        assert report.failed() == ()
        jobs = load_jobs(root)
        assert all(job.status == "done" for job in jobs.values())
        assert (root / manifest.case("M001").slug / "history.csv").is_file()
        again = run_jobs(manifest, config, root=root)
        assert len(again.jobs) == 4
        assert all(job.status == "skipped" for job in again.jobs)

    def test_force_reruns(self, generated: tuple[Path, Manifest]) -> None:
        root, manifest = generated
        config = config_for_manifest(manifest, {"fake": True})
        run_jobs(manifest, config, root=root)
        report = run_jobs(manifest, config, root=root, force=True, only=["M001"])
        assert [job.code for job in report.jobs] == ["M001"]

    def test_failure_with_retry(self, generated: tuple[Path, Manifest], monkeypatch: pytest.MonkeyPatch) -> None:
        root, manifest = generated
        monkeypatch.setenv("DYNACALC_FAKE_FAIL_CODES", "M002")
        config = config_for_manifest(manifest, {"fake": True, "retries": 1})
        report = run_jobs(manifest, config, root=root, only=["M002"])
        job = {item.code: item for item in report.jobs}["M002"]
        assert job.status == "failed"
        assert job.attempts == 2
        assert "失败" in job.error or "exit" in job.error.lower() or job.error

    def test_timeout(self, generated: tuple[Path, Manifest], monkeypatch: pytest.MonkeyPatch) -> None:
        root, manifest = generated
        monkeypatch.setenv("DYNACALC_FAKE_DELAY_MS", "2000")
        config = config_for_manifest(manifest, {"fake": True, "timeout_s": 1, "retries": 0})
        report = run_jobs(manifest, config, root=root, only=["M001"])
        job = report.jobs[0]
        assert job.status == "failed"
        assert "超时" in job.error

    def test_dry_run_does_not_write_jobs(self, generated: tuple[Path, Manifest]) -> None:
        root, manifest = generated
        config = config_for_manifest(manifest, {"fake": True})
        report = run_jobs(manifest, config, root=root, dry_run=True)
        assert len(report.jobs) == 4
        assert all(job.status == "pending" for job in report.jobs)
        assert not (root / "jobs.json").exists()

    def test_unknown_only_code(self, generated: tuple[Path, Manifest]) -> None:
        root, manifest = generated
        config = config_for_manifest(manifest, {"fake": True})
        with pytest.raises(SolverError, match="NOPE"):
            run_jobs(manifest, config, root=root, only=["NOPE"])
