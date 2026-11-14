"""llmqnt（GGUF 量化）工具测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import fcmd as fx
from fcmd.apps.locllm import _locllmw_convert, llmbase, llmengine, llmqnt
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool


class _FakeStdout:
    def __init__(self, lines: list[str]) -> None:
        self._data = "".join(lines).encode("utf-8")
        self._offset = 0
        self.closed = False

    def read1(self, size: int) -> bytes:
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

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


@pytest.fixture(autouse=True)
def _isolate_models_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """量化默认输出落在模型目录的 converted 子目录：隔离到临时目录。"""
    models = tmp_path / "models"
    (models / "converted").mkdir(parents=True)
    monkeypatch.setenv("FCMD_MODELS", str(models))
    return models


class TestRegistration:
    def test_tools_registered(self) -> None:
        assert "llmqnt" in _TOOL_REGISTRY

    def test_flat_commands(self) -> None:
        assert fx.list_subcommands("llmqnt") == []


class TestNaming:
    def test_strip_f16_suffix(self) -> None:
        assert llmqnt.strip_f16_suffix("model-F16") == "model"
        assert llmqnt.strip_f16_suffix("model-f16") == "model"
        assert llmqnt.strip_f16_suffix("model") == "model"

    def test_output_path(self, tmp_path: Path) -> None:
        source = tmp_path / "model-F16.gguf"
        products = tmp_path / "models" / "converted"
        assert llmqnt.output_path(source, "Q4_K_M") == products / "model-Q4_K_M.gguf"
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

    def test_iq_and_legacy_quants_supported(self, tmp_path: Path) -> None:
        source = _write(tmp_path / "model.gguf")
        quants = [
            "Q4_1",
            "Q5_1",
            "IQ1_S",
            "IQ1_M",
            "IQ2_XXS",
            "IQ2_XS",
            "IQ2_S",
            "IQ2_M",
            "IQ3_XXS",
            "IQ3_S",
            "IQ3_M",
            "IQ4_NL",
            "IQ4_XS",
        ]
        jobs = llmqnt.plan_jobs(source, quants)
        assert [job.quant for job in jobs] == quants

    def test_pending_and_skipped(self, tmp_path: Path) -> None:
        source = _write(tmp_path / "model-F16.gguf")
        llmqnt.output_path(source, "Q4_K_M").write_bytes(b"x")
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

    def test_other_engine_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """当前引擎不带量化器时，复用其他已安装引擎的 llama-quantize，而不是落到 PATH。"""
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        current_dir = tmp_path / "lmstudio"
        current_dir.mkdir()
        server = _write(current_dir / "llama-server.exe")
        other_dir = tmp_path / "b12212"
        other_dir.mkdir()
        other_server = _write(other_dir / "llama-server.exe")
        quantize = _write(other_dir / llmengine.engine_exe_name("llama-quantize"))
        monkeypatch.setattr(
            llmqnt.llmengine,
            "resolve_engine",
            lambda *args, **kwargs: llmbase.EngineInfo("lmstudio", server),
        )
        monkeypatch.setattr(
            llmqnt.llmengine,
            "list_engines",
            lambda *args, **kwargs: [llmbase.EngineInfo("b12212", other_server)],
        )
        monkeypatch.setattr(
            llmqnt.shutil,
            "which",
            lambda name: pytest.fail("有可用引擎量化器时不应回落到 PATH"),
        )
        assert llmqnt.resolve_quantize_exe() == quantize

    def test_path_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)
        exe = _write(tmp_path / "llama-quantize")

        def _raise(*args: object, **kwargs: object) -> None:
            raise llmbase.ServeError("未安装引擎")

        monkeypatch.setattr(llmqnt.llmengine, "resolve_engine", _raise)
        monkeypatch.setattr(llmqnt.llmengine, "list_engines", lambda *args, **kwargs: [])
        monkeypatch.setattr(llmqnt.shutil, "which", lambda name: str(exe))
        assert llmqnt.resolve_quantize_exe() == exe

    def test_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_LLAMA_QUANTIZE", raising=False)

        def _raise(*args: object, **kwargs: object) -> None:
            raise llmbase.ServeError("未安装引擎")

        monkeypatch.setattr(llmqnt.llmengine, "resolve_engine", _raise)
        monkeypatch.setattr(llmqnt.llmengine, "list_engines", lambda *args, **kwargs: [])
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

    def test_access_violation_includes_exe_and_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """访问冲突崩溃时提示实际使用的量化器与 VC++ 运行库排查方向。"""
        source = _write(tmp_path / "model.gguf")
        job = llmqnt.QuantizeJob(source, tmp_path / "model-Q4_K_M.gguf", "Q4_K_M")
        process = _FakeProcess(returncode=3221225477)
        monkeypatch.setattr(llmqnt.subprocess, "Popen", _fake_popen(process))
        exe = tmp_path / "llama-quantize.exe"

        with pytest.raises(llmqnt.QuantizeError) as excinfo:
            llmqnt.run_job(job, exe)

        message = str(excinfo.value)
        assert "0xC0000005" in message
        assert str(exe) in message
        assert "MSVCP140.dll" in message

    def test_failure_reports_output_and_removes_partial_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """失败时带上 llama-quantize 的真实输出，并清掉半成品避免下次被当成已完成。"""
        source = _write(tmp_path / "model.gguf")
        output = _write(tmp_path / "model-Q4_K_M.gguf")
        job = llmqnt.QuantizeJob(source, output, "Q4_K_M")
        process = _FakeProcess(lines=["error: bad tensor\n"], returncode=1)
        monkeypatch.setattr(llmqnt.subprocess, "Popen", _fake_popen(process))

        with pytest.raises(llmqnt.QuantizeError, match="bad tensor"):
            llmqnt.run_job(job, Path("llama-quantize"))

        assert output.exists() is False

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
        products = tmp_path / "models" / "converted"
        assert (products / "model-Q4_K_M.gguf").is_file()
        assert (products / "model-Q5_K_M.gguf").is_file()
        assert "已生成" in capsys.readouterr().out

    def test_skips_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        source = _write(tmp_path / "model.gguf")
        llmqnt.output_path(source, "Q4_K_M").write_bytes(b"x")
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


# ---------------------------------------------------------------------- #
# 转换页的量化选择状态
# ---------------------------------------------------------------------- #
class _FakeQuantVar:
    def __init__(self, value: bool = False) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = value


class _FakeQuantCheck:
    def __init__(self) -> None:
        self.options: dict[str, Any] = {}

    def configure(self, **kwargs: Any) -> None:
        self.options.update(kwargs)


class _FakeStrVar:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def _bare_convert_page(tmp_path: Path) -> _locllmw_convert._ConvertPage:
    page = object.__new__(_locllmw_convert._ConvertPage)
    page._closed = False
    page._quant_input = tmp_path / "model-f16.gguf"
    page._quant_overwrite_var = cast(Any, _FakeQuantVar(False))
    page._quant_out_dir_var = cast(Any, _FakeStrVar(str(tmp_path)))
    page._quant_vars = {quant: cast(Any, _FakeQuantVar(False)) for quant in llmqnt.QUANT_TYPES}
    page._quant_checks = {quant: cast(Any, _FakeQuantCheck()) for quant in llmqnt.QUANT_TYPES}
    return page


class TestQuantSelection:
    """切换模型时量化类型勾选状态（纯状态，不创建 Tk）。"""

    def test_mark_existing_does_not_auto_check_existing_outputs(self, tmp_path: Path) -> None:
        page = _bare_convert_page(tmp_path)
        input_file = page._quant_input
        assert input_file is not None
        llmqnt.output_path(input_file, "Q4_K_M", page._quant_out_dir()).write_bytes(b"x")

        page._mark_quant_existing()

        assert page._quant_vars["Q4_K_M"].get() is False
        assert page._quant_checks["Q4_K_M"].options["state"] == "disabled"
        assert "已存在" in page._quant_checks["Q4_K_M"].options["text"]
        assert page._quant_checks["Q5_K_M"].options["state"] == "normal"

    def test_reset_quants_restores_default_selection(self, tmp_path: Path) -> None:
        page = _bare_convert_page(tmp_path)
        for var in page._quant_vars.values():
            var.set(True)

        page._reset_quants()

        checked = {quant for quant, var in page._quant_vars.items() if var.get()}
        assert checked == set(llmqnt.DEFAULT_QUANTS)

    def test_mark_existing_keeps_selection_when_overwrite_enabled(self, tmp_path: Path) -> None:
        page = _bare_convert_page(tmp_path)
        page._quant_overwrite_var.set(True)
        input_file = page._quant_input
        assert input_file is not None
        llmqnt.output_path(input_file, "Q2_K", page._quant_out_dir()).write_bytes(b"x")
        page._quant_vars["Q2_K"].set(True)

        page._mark_quant_existing()

        assert page._quant_vars["Q2_K"].get() is True
        assert page._quant_checks["Q2_K"].options["state"] == "normal"
