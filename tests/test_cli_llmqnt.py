"""llmqnt（GGUF 量化）与 llmqntw（Tk 窗口版）工具测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import fcmd as fx
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.llm import llmbase, llmengine, llmqnt


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.closed = False

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(
        self,
        lines: list[str] | None = None,
        returncode: int = 0,
        wait_exc: BaseException | None = None,
    ) -> None:
        self.stdout = _FakeStdout(lines or [])
        self.returncode = returncode
        self._wait_exc = wait_exc
        self._wait_calls = 0
        self.killed = False
        self.command: list[str] | None = None
        self.kwargs: dict[str, object] = {}

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        if self._wait_exc is not None and self._wait_calls == 1:
            raise self._wait_exc
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _fake_popen(process: _FakeProcess):
    def factory(command: list[str], **kwargs: object) -> _FakeProcess:
        process.command = command
        process.kwargs = kwargs
        return process

    return factory


def _write(path: Path, data: bytes = b"x") -> Path:
    path.write_bytes(data)
    return path


class TestRegistration:
    def test_tools_registered(self) -> None:
        assert "llmqnt" in _TOOL_REGISTRY
        assert "llmqntw" in _TOOL_REGISTRY

    def test_flat_commands(self) -> None:
        assert fx.list_subcommands("llmqnt") == []
        assert fx.list_subcommands("llmqntw") == []


class TestNaming:
    def test_strip_f16_suffix(self) -> None:
        assert llmqnt.strip_f16_suffix("model-F16") == "model"
        assert llmqnt.strip_f16_suffix("model-f16") == "model"
        assert llmqnt.strip_f16_suffix("model") == "model"

    def test_output_path(self, tmp_path: Path) -> None:
        source = tmp_path / "model-F16.gguf"
        assert llmqnt.output_path(source, "Q4_K_M") == tmp_path / "model-Q4_K_M.gguf"
        assert llmqnt.output_path(source, "Q5_K_M", tmp_path / "out") == (tmp_path / "out" / "model-Q5_K_M.gguf")


class TestPlan:
    def test_missing_input(self, tmp_path: Path) -> None:
        with pytest.raises(llmqnt.QuantizeError, match="输入文件不存在"):
            llmqnt.plan_jobs(tmp_path / "none.gguf", ["Q4_K_M"])

    def test_non_gguf_input(self, tmp_path: Path) -> None:
        source = _write(tmp_path / "model.bin")
        with pytest.raises(llmqnt.QuantizeError, match=r"必须是 \.gguf"):
            llmqnt.plan_jobs(source, ["Q4_K_M"])

    def test_unknown_quant(self, tmp_path: Path) -> None:
        source = _write(tmp_path / "model.gguf")
        with pytest.raises(llmqnt.QuantizeError, match="不支持的量化类型"):
            llmqnt.plan_jobs(source, ["Q9_K"])

    def test_pending_and_skipped(self, tmp_path: Path) -> None:
        source = _write(tmp_path / "model-F16.gguf")
        _write(tmp_path / "model-Q4_K_M.gguf")
        jobs = llmqnt.plan_jobs(source, ["Q4_K_M", "Q5_K_M"])
        pending, skipped = llmqnt.pending_jobs(jobs)
        assert [job.quant for job in pending] == ["Q5_K_M"]
        assert [job.quant for job in skipped] == ["Q4_K_M"]

        all_jobs, none = llmqnt.pending_jobs(jobs, overwrite=True)
        assert all_jobs == jobs
        assert none == []


class TestResolveExe:
    def test_explicit(self, tmp_path: Path) -> None:
        exe = _write(tmp_path / "llama-quantize.exe")
        assert llmqnt.resolve_quantize_exe(exe) == exe

    def test_explicit_missing(self, tmp_path: Path) -> None:
        with pytest.raises(llmqnt.QuantizeError, match="不存在"):
            llmqnt.resolve_quantize_exe(tmp_path / "none.exe")

    def test_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        exe = _write(tmp_path / "q.exe")
        monkeypatch.setenv("FCMD_LLAMA_QUANTIZE", str(exe))
        assert llmqnt.resolve_quantize_exe() == exe

    def test_env_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCMD_LLAMA_QUANTIZE", str(tmp_path / "none.exe"))
        with pytest.raises(llmqnt.QuantizeError, match="FCMD_LLAMA_QUANTIZE"):
            llmqnt.resolve_quantize_exe()

    def test_engine_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()
        server = _write(engine_dir / "llama-server.exe")
        quantize = _write(engine_dir / llmengine.engine_exe_name("llama-quantize"))
        monkeypatch.setattr(
            llmqnt.llmengine,
            "resolve_engine",
            lambda *args, **kwargs: llmbase.EngineInfo("b1", server),
        )
        assert llmqnt.resolve_quantize_exe() == quantize

    def test_path_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        exe = _write(tmp_path / "llama-quantize")

        def _raise(*args: object, **kwargs: object) -> None:
            raise llmbase.ServeError("未安装引擎")

        monkeypatch.setattr(llmqnt.llmengine, "resolve_engine", _raise)
        monkeypatch.setattr(llmqnt.shutil, "which", lambda name: str(exe))
        assert llmqnt.resolve_quantize_exe() == exe

    def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)

        def _raise(*args: object, **kwargs: object) -> None:
            raise llmbase.ServeError("未安装引擎")

        monkeypatch.setattr(llmqnt.llmengine, "resolve_engine", _raise)
        monkeypatch.setattr(llmqnt.shutil, "which", lambda name: None)
        with pytest.raises(llmqnt.QuantizeError, match="未找到 llama-quantize"):
            llmqnt.resolve_quantize_exe()


class TestRunJob:
    def test_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _write(tmp_path / "model.gguf")
        job = llmqnt.QuantizeJob(source, tmp_path / "model-Q4_K_M.gguf", "Q4_K_M")
        process = _FakeProcess(lines=["a\n", "b\n"])
        monkeypatch.setattr(llmqnt.subprocess, "Popen", _fake_popen(process))

        seen: list[str] = []
        llmqnt.run_job(job, Path("llama-quantize"), on_output=seen.append)

        assert seen == ["a", "b"]
        assert process.command == [
            "llama-quantize",
            str(source),
            str(job.output),
            "Q4_K_M",
        ]

    def test_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _write(tmp_path / "model.gguf")
        job = llmqnt.QuantizeJob(source, tmp_path / "model-Q4_K_M.gguf", "Q4_K_M")
        process = _FakeProcess(returncode=1)
        monkeypatch.setattr(llmqnt.subprocess, "Popen", _fake_popen(process))

        with pytest.raises(llmqnt.QuantizeError, match="退出码 1"):
            llmqnt.run_job(job, Path("llama-quantize"))

    def test_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _write(tmp_path / "model.gguf")
        job = llmqnt.QuantizeJob(source, tmp_path / "model-Q4_K_M.gguf", "Q4_K_M")
        process = _FakeProcess(wait_exc=subprocess.TimeoutExpired("cmd", 1))
        monkeypatch.setattr(llmqnt.subprocess, "Popen", _fake_popen(process))

        with pytest.raises(llmqnt.QuantizeError, match="超时"):
            llmqnt.run_job(job, Path("llama-quantize"), timeout=1)
        assert process.killed is True

    def test_start_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _write(tmp_path / "model.gguf")
        job = llmqnt.QuantizeJob(source, tmp_path / "model-Q4_K_M.gguf", "Q4_K_M")

        def _raise(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("no such exe")

        monkeypatch.setattr(llmqnt.subprocess, "Popen", _raise)
        with pytest.raises(llmqnt.QuantizeError, match="无法启动"):
            llmqnt.run_job(job, Path("llama-quantize"))

    def test_run_jobs_reports_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _write(tmp_path / "model.gguf")
        jobs = [
            llmqnt.QuantizeJob(source, tmp_path / "model-Q4_K_M.gguf", "Q4_K_M"),
            llmqnt.QuantizeJob(source, tmp_path / "model-Q5_K_M.gguf", "Q5_K_M"),
        ]

        def _run(job: llmqnt.QuantizeJob, exe: Path, on_output: object = None) -> None:
            if job.quant == "Q5_K_M":
                raise llmqnt.QuantizeError("boom")

        monkeypatch.setattr(llmqnt, "run_job", _run)
        done: list[tuple[str, bool]] = []
        failures = llmqnt.run_jobs(
            jobs, Path("llama-quantize"), on_done=lambda job, ok, err: done.append((job.quant, ok))
        )

        assert failures == ["Q5_K_M: boom"]
        assert done == [("Q4_K_M", True), ("Q5_K_M", False)]


class TestCli:
    def test_generates_outputs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = _write(tmp_path / "model-F16.gguf")
        monkeypatch.setattr(llmqnt, "resolve_quantize_exe", lambda explicit=None: Path("llama-quantize"))

        def _run(job: llmqnt.QuantizeJob, exe: Path, on_output: object = None) -> None:
            _write(job.output, b"quantized")

        monkeypatch.setattr(llmqnt, "run_job", _run)

        code = run_tool("llmqnt", [str(source), "--quants", "Q4_K_M", "Q5_K_M"])

        assert code == 0
        assert (tmp_path / "model-Q4_K_M.gguf").is_file()
        assert (tmp_path / "model-Q5_K_M.gguf").is_file()
        assert "已生成" in capsys.readouterr().out

    def test_skips_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = _write(tmp_path / "model.gguf")
        _write(tmp_path / "model-Q4_K_M.gguf")
        monkeypatch.setattr(llmqnt, "resolve_quantize_exe", lambda explicit=None: Path("llama-quantize"))
        calls: list[object] = []
        monkeypatch.setattr(llmqnt, "run_job", lambda *args, **kwargs: calls.append(args))

        code = run_tool("llmqnt", [str(source), "--quants", "Q4_K_M"])

        assert code == 0
        assert calls == []
        output = capsys.readouterr().out
        assert "已存在" in output
        assert "无需转换" in output

    def test_out_dir_and_overwrite(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        source = _write(tmp_path / "model.gguf")
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        _write(out_dir / "model-Q4_K_M.gguf")
        monkeypatch.setattr(llmqnt, "resolve_quantize_exe", lambda explicit=None: Path("llama-quantize"))
        monkeypatch.setattr(llmqnt, "run_job", lambda *args, **kwargs: None)

        code = run_tool(
            "llmqnt",
            [str(source), "--quants", "Q4_K_M", "--out-dir", str(out_dir), "--overwrite"],
        )

        assert code == 0
