"""llmenv / llmmodel / llmserve / llmbench / llmconvert / llmdoctor 独立命令测试。

覆盖各工具的子命令注册与终端输出分支（均用替身，不接触真实 GPU / 网络 / 进程）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.console import Console
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.llm import llmbench, llmconvert, llmdoctor, llmenv, llmmodel, llmserve
from fcmd.tools.llm.llmbase import (
    DEFAULT_API_KEY,
    PROFILES,
    Check,
    Endpoint,
    EngineInfo,
    LaunchPlan,
    ServeError,
    ServeState,
    StopResult,
)
from fcmd.tools.llm.llmbench import BenchResult


# ---------------------------------------------------------------------- #
# 测试替身
# ---------------------------------------------------------------------- #
def _state(name: str = "qwen", *, pid: int = 4321) -> ServeState:
    """构造状态对象。"""
    return ServeState(
        name=name,
        pid=pid,
        create_time=0.0,
        host="127.0.0.1",
        port=8080,
        model="D:/models/m.gguf",
        profile="balanced",
        context=65536,
        engine="llama-server.exe",
        alias="qwen3-coder-30b-a3b",
        api_key=DEFAULT_API_KEY,
        log="log.txt",
        started_at="2026-11-09T12:00:00+08:00",
    )


def _plan(name: str = "qwen") -> LaunchPlan:
    """构造启动计划。"""
    return LaunchPlan(
        name=name,
        model=Path("m.gguf"),
        profile=PROFILES["balanced"],
        context=65536,
        host="127.0.0.1",
        port=8080,
        alias="qwen3-coder-30b-a3b",
        api_key=DEFAULT_API_KEY,
        threads=8,
        engine=EngineInfo("b1", Path("llama-server.exe")),
        log=Path("log.txt"),
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的 FCMD_HOME，避免污染用户目录。"""
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    monkeypatch.delenv("FCMD_MODELS", raising=False)
    return target


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #
def test_tools_registered() -> None:
    assert set(_TOOL_REGISTRY["llmenv"]) == {"show", "add", "remove", "reset", "list", "env"}
    assert set(_TOOL_REGISTRY["llmmodel"]) == {"list", "find", "vram"}
    assert set(_TOOL_REGISTRY["llmserve"]) == {"plan", "start", "stop", "status", "logs"}
    assert set(_TOOL_REGISTRY["llmbench"]) == {None}
    assert set(_TOOL_REGISTRY["llmconvert"]) == {None}
    assert set(_TOOL_REGISTRY["llmdoctor"]) == {None}


# ---------------------------------------------------------------------- #
# llmenv
# ---------------------------------------------------------------------- #
def test_env_show(home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmenv, "get_console", lambda: Console(width=200))
    llmenv.show_cmd()
    out = capsys.readouterr().out
    assert "目录布局" in out
    assert str(home) in out


def test_env_add_remove_and_reset(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = home / "models"
    target.mkdir(parents=True)
    llmenv.add_cmd(target)
    assert "已登记模型目录" in capsys.readouterr().out
    assert any(llmenv.path_key(item) == llmenv.path_key(target) for item in llmenv.configured_roots())

    llmenv.remove_cmd(target)
    assert "已移除模型目录" in capsys.readouterr().out

    llmenv.reset_cmd()
    assert "已清空模型目录配置" in capsys.readouterr().out


def test_env_add_missing_dir(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llmenv.add_cmd(home / "missing")
    assert "目录当前不存在" in capsys.readouterr().out


def test_env_list_empty(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llmenv.list_cmd()
    assert "没有已登记的服务" in capsys.readouterr().out


def test_env_list_with_state(home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmenv, "get_console", lambda: Console(width=200))
    monkeypatch.setattr(llmenv, "list_states", lambda home=None: [_state("alpha")])
    llmenv.list_cmd()
    assert "alpha" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("shell", "prefix"),
    [("plain", "OPENAI_BASE_URL="), ("powershell", "$env:OPENAI_BASE_URL="), ("bash", "export OPENAI_BASE_URL=")],
)
def test_env_cmd_shells(
    shell: str, prefix: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(llmenv, "require_state", lambda name, home=None: _state())
    llmenv.env_cmd(shell=shell)  # type: ignore[arg-type]
    assert prefix in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# llmmodel
# ---------------------------------------------------------------------- #
def test_model_list_without_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "models_roots", lambda *args, **kwargs: [tmp_path / "missing"])
    with pytest.raises(ServeError, match="模型目录不存在"):
        llmmodel.list_cmd()


def test_model_list_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "models_roots", lambda *args, **kwargs: [tmp_path])
    with pytest.raises(ServeError, match=r"没有 \.gguf"):
        llmmodel.list_cmd()


def test_model_list_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "m-Q4_K_M.gguf").write_bytes(b"x" * 16)
    monkeypatch.setattr(llmenv, "models_roots", lambda *args, **kwargs: [tmp_path])
    llmmodel.list_cmd()
    out = capsys.readouterr().out
    assert "m-Q4_K_M.gguf" in out
    assert "speed" in out


def test_model_find(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmmodel, "find_model", lambda *args, **kwargs: Path("D:/models/x.gguf"))
    llmmodel.find_cmd("Q4_K_M")
    assert "x.gguf" in capsys.readouterr().out


def test_model_vram_invalid_kv(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="不支持的 KV"):
        llmmodel.vram_cmd(tmp_path / "m.gguf", kv="nope")


def test_model_vram_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ServeError, match="读取模型失败"):
        llmmodel.vram_cmd(tmp_path / "m.gguf")


def test_model_vram_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmmodel, "get_console", lambda: Console(width=200))
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x" * 1024)
    llmmodel.vram_cmd(model, ctx=1024, kv="q8_0")
    out = capsys.readouterr().out
    assert "显存估算" in out
    assert "预计占用" in out


# ---------------------------------------------------------------------- #
# llmserve
# ---------------------------------------------------------------------- #
def test_serve_plan(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmserve, "make_plan", lambda *args, **kwargs: _plan())
    llmserve.plan_cmd()
    out = capsys.readouterr().out
    assert "启动计划" in out
    assert "命令:" in out


def test_serve_start(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmserve, "make_plan", lambda *args, **kwargs: _plan())
    monkeypatch.setattr(llmserve, "start_server", lambda plan, **kwargs: _state())
    monkeypatch.setattr(llmserve, "process_alive", lambda *args: True)
    llmserve.start_cmd()
    out = capsys.readouterr().out
    assert "运行中" in out


def test_serve_stop_running(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmserve, "stop_server", lambda name, home=None: StopResult(_state(), True))
    llmserve.stop_cmd()
    assert "已停止服务" in capsys.readouterr().out


def test_serve_stop_not_running(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmserve, "stop_server", lambda name, home=None: StopResult(_state(), False))
    llmserve.stop_cmd()
    assert "未在运行" in capsys.readouterr().out


def test_serve_status_one(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmenv, "require_state", lambda name, home=None: _state("alpha"))
    monkeypatch.setattr(llmserve, "process_alive", lambda *args: False)
    llmserve.status_cmd("alpha")
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "已退出" in out


def test_serve_status_empty(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmenv, "list_states", lambda home=None: [])
    llmserve.status_cmd()
    assert "没有已登记的服务" in capsys.readouterr().out


def test_serve_status_list(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmenv, "list_states", lambda home=None: [_state("alpha")])
    monkeypatch.setattr(llmserve, "process_alive", lambda *args: True)
    llmserve.status_cmd()
    out = capsys.readouterr().out
    assert "alpha" in out
    assert "运行中" in out


def test_serve_logs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmserve, "read_log", lambda name, lines, home=None: "log-line")
    llmserve.logs_cmd()
    assert "log-line" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# llmbench
# ---------------------------------------------------------------------- #
def test_bench_resolve_base_url() -> None:
    endpoint = llmbench._resolve_endpoint(None, "http://127.0.0.1:8000/v1", "k", "m")
    assert endpoint == Endpoint("http://127.0.0.1:8000", "k", "m")


def test_bench_resolve_trailing_slash() -> None:
    endpoint = llmbench._resolve_endpoint(None, "http://127.0.0.1:8000/", "k", "m")
    assert endpoint.base_url == "http://127.0.0.1:8000"


def test_bench_resolve_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "require_state", lambda name, home=None: _state("alpha"))
    assert llmbench._resolve_endpoint("alpha", None, "k", "m").model == "qwen3-coder-30b-a3b"


def test_bench_resolve_single_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "list_states", lambda home=None: [_state("alpha")])
    assert llmbench._resolve_endpoint(None, None, "k", "m").model == "qwen3-coder-30b-a3b"


def test_bench_resolve_no_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "list_states", lambda home=None: [])
    with pytest.raises(ServeError, match="没有已登记的服务"):
        llmbench._resolve_endpoint(None, None, "k", "m")


def test_bench_resolve_multiple_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "list_states", lambda home=None: [_state("a"), _state("b")])
    with pytest.raises(ServeError, match="--name"):
        llmbench._resolve_endpoint(None, None, "k", "m")


def test_bench_cmd_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmbench, "_resolve_endpoint", lambda *args: Endpoint("http://x", "k", "m"))
    monkeypatch.setattr(llmbench, "bench", lambda *args, **kwargs: BenchResult(0.5, 2000, 64, 20.0))
    llmbench.bench_cmd()
    out = capsys.readouterr().out
    assert "tok/s" in out
    assert "提示" not in out


def test_bench_cmd_short_prompt_hint(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmbench, "_resolve_endpoint", lambda *args: Endpoint("http://x", "k", "m"))
    monkeypatch.setattr(llmbench, "bench", lambda *args, **kwargs: BenchResult(0.5, 10, 64, 20.0))
    llmbench.bench_cmd()
    assert "短上下文吞吐偏乐观" in capsys.readouterr().out


# ---------------------------------------------------------------------- #
# llmconvert
# ---------------------------------------------------------------------- #
def _convert_plan(quant: str | None = None) -> llmconvert.ConvertPlan:
    return llmconvert.ConvertPlan(
        source=Path("src"),
        output=Path("out.gguf"),
        outtype="f16",
        script=Path("convert_hf_to_gguf.py"),
        python="python",
        quant=quant,
        quantize_exe=Path("llama-quantize.exe") if quant else None,
    )


def test_convert_cmd(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmconvert, "find_convert_python", lambda: "python")
    monkeypatch.setattr(llmconvert, "plan_convert", lambda *args, **kwargs: _convert_plan())
    monkeypatch.setattr(llmconvert, "convert_model", lambda plan: [Path("out.gguf")])
    llmconvert.convert_cmd(Path("src"))
    out = capsys.readouterr().out
    assert "转换计划" in out
    assert "已生成: out.gguf" in out


def test_convert_cmd_with_quant_and_python(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []

    def _find() -> str:
        calls.append("find")
        return "auto"

    monkeypatch.setattr(llmconvert, "find_convert_python", _find)
    monkeypatch.setattr(llmconvert, "plan_convert", lambda *args, **kwargs: _convert_plan("Q4_K_M"))
    monkeypatch.setattr(llmconvert, "convert_model", lambda plan: [Path("out.gguf")])
    llmconvert.convert_cmd(Path("src"), python="custom")
    out = capsys.readouterr().out
    assert calls == []
    assert "中间文件" in out


# ---------------------------------------------------------------------- #
# llmdoctor
# ---------------------------------------------------------------------- #
def test_doctor_cmd_ok(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmdoctor, "doctor", lambda **kwargs: [Check("GPU", "ok", "RTX")])
    llmdoctor.doctor_cmd()
    assert "RTX" in capsys.readouterr().out


def test_doctor_cmd_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmdoctor, "doctor", lambda **kwargs: [Check("引擎", "fail", "未安装")])
    with pytest.raises(ServeError, match="1 项检查失败"):
        llmdoctor.doctor_cmd()
