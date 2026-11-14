"""llmenv / llmmodel / llmserve / llmbench / llmconvert / llmdoctor 独立命令测试。

覆盖各工具的子命令注册与终端输出分支（均用替身，不接触真实 GPU / 网络 / 进程）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from fcmd.apps.locllm import llmbase, llmbench, llmconvert, llmdoctor, llmenv, llmmodel, llmserve
from fcmd.apps.locllm.llmbase import (
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
from fcmd.apps.locllm.llmbench import BenchResult
from fcmd.console import Console
from fcmd.core.toolkit import _TOOL_REGISTRY, get_tool


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
    for name in ("FCMD_MODELS", "FCMD_SERVE_DIR", "FCMD_CONFIG", "FCMD_ENGINES"):
        monkeypatch.delenv(name, raising=False)
    return target


# ---------------------------------------------------------------------- #
# 注册
# ---------------------------------------------------------------------- #
def test_tools_registered() -> None:
    assert set(_TOOL_REGISTRY["llmenv"]) == {"show", "add", "remove", "reset", "list", "env"}
    assert set(_TOOL_REGISTRY["llmmodel"]) == {"list", "find", "vram"}
    assert set(_TOOL_REGISTRY["llmserve"]) == {"plan", "start", "stop", "status", "logs"}
    assert set(_TOOL_REGISTRY["locllmw"]) == {None}
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


def test_env_path_overrides(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    serve = tmp_path / "serve-override"
    config = tmp_path / "custom-config.json"
    engines = tmp_path / "engines-override"
    monkeypatch.setenv("FCMD_SERVE_DIR", str(serve))
    monkeypatch.setenv("FCMD_CONFIG", str(config))
    monkeypatch.setenv("FCMD_ENGINES", str(engines))

    assert llmenv.serve_dir() == serve
    assert llmenv.config_path() == config
    assert llmenv.engines_root() == engines
    assert llmenv.state_path("qwen") == serve / "qwen.json"
    assert llmenv.log_path("qwen") == serve / "logs" / "qwen.log"


def test_env_path_overrides_ignore_explicit_home(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_SERVE_DIR", str(tmp_path / "ignored"))
    monkeypatch.setenv("FCMD_CONFIG", str(tmp_path / "ignored.json"))
    monkeypatch.setenv("FCMD_ENGINES", str(tmp_path / "ignored-engines"))

    assert llmenv.serve_dir(home) == home / "serve"
    assert llmenv.config_path(home) == home / "serve" / "config.json"
    assert llmenv.engines_root(home) == home / "engines" / "llama.cpp"


def test_env_path_overrides_ignore_blank(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("FCMD_SERVE_DIR", "FCMD_CONFIG", "FCMD_ENGINES"):
        monkeypatch.setenv(name, "   ")
    assert llmenv.serve_dir() == home / "serve"
    assert llmenv.config_path() == home / "serve" / "config.json"
    assert llmenv.engines_root() == home / "engines" / "llama.cpp"


def test_env_add_remove_and_reset(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = home / "models"
    target.mkdir(parents=True)
    llmenv.add_cmd(target)
    assert "已设置模型目录" in capsys.readouterr().out
    assert llmenv.path_key(llmenv.configured_models_dir() or Path()) == llmenv.path_key(target)

    llmenv.remove_cmd(target)
    assert "已移除模型目录" in capsys.readouterr().out

    llmenv.reset_cmd()
    assert "已清空模型目录配置" in capsys.readouterr().out


def test_env_add_missing_dir(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llmenv.add_cmd(home / "missing")
    assert "目录当前不存在" in capsys.readouterr().out


def test_env_show_legacy_directories(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(llmenv, "get_console", lambda: Console(width=300))
    monkeypatch.setattr(llmenv, "models_dir_extra", lambda: ["D:/old-models"])
    monkeypatch.setattr(llmenv, "ignored_work_dir", lambda: "E:/old-work")

    llmenv.show_cmd()

    out = capsys.readouterr().out
    assert "旧模型目录（未使用）" in out
    assert "D:/old-models" in out
    assert "旧工作目录（已忽略）" in out
    assert "E:/old-work" in out


def test_env_add_same_dir_skips_replacement_notice(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(llmenv, "get_console", lambda: Console(width=300))
    target = home / "models"
    target.mkdir(parents=True)
    llmenv.add_cmd(target)
    capsys.readouterr()

    llmenv.add_cmd(target)

    assert "已被替换" not in capsys.readouterr().out


def test_env_remove_rejects_unconfigured_dir(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "get_console", lambda: Console(width=300))
    with pytest.raises(ServeError, match="无需移除"):
        llmenv.remove_cmd(home / "models")


def test_list_states_skips_unreadable_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = llmenv.state_path("alpha", home)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(llmenv, "load_state", lambda name, home=None: None)

    assert llmenv.list_states(home) == []


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
    monkeypatch.setattr(llmenv, "models_dir", lambda *args, **kwargs: tmp_path / "missing")
    with pytest.raises(ServeError, match="模型目录不存在"):
        llmmodel.list_cmd()


def test_model_list_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmenv, "models_dir", lambda *args, **kwargs: tmp_path)
    with pytest.raises(ServeError, match=r"没有 \.gguf"):
        llmmodel.list_cmd()


def test_model_list_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "m-Q4_K_M.gguf").write_bytes(b"x" * 16)
    monkeypatch.setattr(llmenv, "models_dir", lambda *args, **kwargs: tmp_path)
    llmmodel.list_cmd()
    out = capsys.readouterr().out
    assert "m-Q4_K_M.gguf" in out
    assert "speed" in out


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf", ("代码", "对话")),
        ("DeepSeek-R1-Distill-Qwen-7B-Q4_K_M.gguf", ("推理",)),
        ("Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf", ("对话", "视觉")),
        ("Qwen2.5-Math-7B-Instruct-Q4_K_M.gguf", ("对话", "数学")),
        ("bge-reranker-v2-m3-Q4_K_M.gguf", ("重排",)),
        ("nomic-embed-text-Q8_0.gguf", ("嵌入",)),
        ("Qwen3.8-27B-Q4_K_M.gguf", ("通用",)),
        ("Meta-Llama-3.1-8B-Q4_K_M.gguf", ("通用",)),
        ("gemma-3-27b-it-Q4_K_M.gguf", ("对话",)),
        ("Mistral-7B-v0.3-Q4_K_M.gguf", ("通用",)),
        ("gpt-oss-20b-Q4_K_M.gguf", ("通用",)),
        ("custom-model.gguf", ()),
    ],
)
def test_detect_model_tags(name: str, expected: tuple[str, ...]) -> None:
    assert llmmodel.detect_model_tags(name) == expected


def test_model_advice_known_tags() -> None:
    advice = llmmodel.model_advice(("代码",))
    assert "推荐档位 balanced" in advice
    assert "temperature 0.2" in advice


def test_model_advice_unknown_tags() -> None:
    advice = llmmodel.model_advice(("不存在的标签",))
    assert "未识别" in advice
    assert "balanced" in advice


def test_model_advice_general_tag() -> None:
    advice = llmmodel.model_advice((llmmodel.GENERAL_TAG,))
    assert "推荐档位 balanced" in advice
    assert "/v1/chat/completions" in advice


def test_scan_models_fills_tags(tmp_path: Path) -> None:
    (tmp_path / "qwen3-coder-instruct-Q4_K_M.gguf").write_bytes(b"x" * 16)
    entries = llmmodel.scan_models(tmp_path)
    assert entries[0].tags == ("代码", "对话")


def test_scan_models_deduplicates_resolved_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = tmp_path / "m.gguf"
    model.write_bytes(b"x")
    original = Path.rglob
    monkeypatch.setattr(
        Path, "rglob", lambda self, pattern: iter([model, model]) if self == tmp_path else original(self, pattern)
    )

    assert len(llmmodel.scan_models(tmp_path)) == 1


def test_pair_score_without_name_features(tmp_path: Path) -> None:
    model = tmp_path / "model-f16.gguf"
    mmproj = tmp_path / "sub" / "mmproj-mmproj-f16.gguf"

    score, reasons = llmmodel.pair_score(model, mmproj)

    assert score == 5
    assert len(reasons) == 1


def test_find_mmproj_skips_model_itself_and_collects_subdir(tmp_path: Path) -> None:
    model = tmp_path / "alpha-mmproj.gguf"
    model.write_bytes(b"x" * 16)
    candidate = tmp_path / "beta-projector.gguf"
    candidate.write_bytes(b"x" * 16)

    found = llmmodel.find_mmproj(model)

    assert found is not None
    assert found.path.name == "beta-projector.gguf"


def test_find_mmproj_deduplicates_and_accepts_subdir_gguf(tmp_path: Path) -> None:
    model = tmp_path / "qwen2.5-8b.gguf"
    model.write_bytes(b"x" * 16)
    (tmp_path / "qwen2.5-8b-mmproj-projector-f16.gguf").write_bytes(b"x" * 16)
    vision = tmp_path / "mmproj" / "vision.gguf"
    vision.parent.mkdir()
    vision.write_bytes(b"x" * 16)

    found = llmmodel.find_mmproj(model)

    assert found is not None
    assert found.path.name == "qwen2.5-8b-mmproj-projector-f16.gguf"


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


def test_max_context_fits() -> None:
    # 32 GiB 显存、10 GiB 权重、2 GiB 固定开销，剩 20 GiB 给 q8_0 KV（96 KiB/token × 0.5）
    assert llmmodel.max_context(10 * llmbase.GIB, 32 * llmbase.GIB, "q8_0") == 426 * 1024
    assert llmmodel.max_context(10 * llmbase.GIB, 32 * llmbase.GIB, "f16") == 213 * 1024


def test_max_context_weight_too_large() -> None:
    assert llmmodel.max_context(40 * llmbase.GIB, 32 * llmbase.GIB, "q8_0") == 0


def test_fit_vram() -> None:
    fit = llmmodel.fit_vram(10 * llmbase.GIB, 32 * llmbase.GIB, 65536, "q8_0")
    assert fit.ok is True
    assert fit.needed == 15 * llmbase.GIB
    assert fit.available == 32 * llmbase.GIB
    assert fit.max_context == 426 * 1024

    unfit = llmmodel.fit_vram(30 * llmbase.GIB, 32 * llmbase.GIB, 65536, "f16")
    assert unfit.ok is False
    assert unfit.max_context < 65536


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


def test_convert_help_describes_parameters() -> None:
    """convert 的 --help 展示参数含义而非参数名。"""
    from fcmd.core.toolkit.args import _build_parser_for_tool

    text = _build_parser_for_tool(get_tool("llmconvert")).format_help()
    assert "含 config.json 与 *.safetensors 的模型目录" in text
    assert "量化类型" in text
    assert "转换脚本 convert_hf_to_gguf.py" in text


# ---------------------------------------------------------------------- #
# 实时输出（stream_command / convert_model on_output）
# ---------------------------------------------------------------------- #
class _FakeStreamStdout:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0
        self.closed = False

    def read1(self, size: int) -> bytes:
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeStreamProcess:
    def __init__(self, data: bytes, returncode: int = 0, *, timeout_first: bool = False) -> None:
        self.stdout = _FakeStreamStdout(data)
        self.returncode = returncode
        self.killed = False
        self._timeout_first = timeout_first
        self._wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self._wait_calls += 1
        if self._timeout_first and self._wait_calls == 1:
            raise subprocess.TimeoutExpired("cmd", timeout or 0)
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def test_stream_command_splits_carriage_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeStreamProcess(b"loading\r 20%|##   |\r 80%|#####|\ndone\n")
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    result = llmbase.stream_command(["python", "convert.py"], on_output=seen.append)

    assert result.returncode == 0
    assert seen == ["loading", " 20%|##   |", " 80%|#####|", "done"]
    assert process.stdout is not None
    assert process.stdout.closed is True


class _ChunkedStreamProcess:
    def __init__(self, chunks: list[bytes], returncode: int = 0) -> None:
        self.stdout: _ChunkedStdout | None = _ChunkedStdout(chunks)
        self.returncode = returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class _ChunkedStdout:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    def read1(self, size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def close(self) -> None:
        pass


def test_stream_command_skips_empty_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeStreamProcess(b"a\n\nb\n")
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    llmbase.stream_command(["python", "convert.py"], on_output=seen.append)

    assert seen == ["a", "b"]


def test_stream_command_without_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _ChunkedStreamProcess([])
    process.stdout = None
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)

    result = llmbase.stream_command(["python", "convert.py"], on_output=lambda line: None)

    assert result == llmbase.CommandResult(0, "", "")


def test_stream_command_flushes_partial_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmbase.locale, "getpreferredencoding", lambda do_setlocale=False: "utf-8")
    process = _ChunkedStreamProcess([b"\xe4", b"\xb8\xad"])
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    result = llmbase.stream_command(["python", "convert.py"], on_output=seen.append)

    assert result.stdout == "中"
    assert seen == ["中"]


def test_stream_command_flushes_incomplete_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmbase.locale, "getpreferredencoding", lambda do_setlocale=False: "utf-8")
    process = _ChunkedStreamProcess([b"abc\xe4"])
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    result = llmbase.stream_command(["python", "convert.py"], on_output=seen.append)

    assert result.stdout == "abc\ufffd"
    assert seen == ["abc\ufffd"]


def test_backend_protocol_default_impl_returns_none() -> None:
    assert llmbase.Backend.build_argv(cast(Any, None), cast(Any, object())) is None


def test_stream_command_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeStreamProcess(b"", timeout_first=True)
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(subprocess.TimeoutExpired):
        llmbase.stream_command(["python", "convert.py"], timeout=1)

    assert process.killed is True


def test_convert_model_streams_output(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeStreamProcess(b"a\n 45%|#### |\nb\n")
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    paths = llmconvert.convert_model(_convert_plan(), on_output=seen.append)

    assert paths == [Path("out.gguf")]
    assert seen == ["a", " 45%|#### |", "b"]


def test_convert_model_stream_filters_caret_noise(monkeypatch: pytest.MonkeyPatch) -> None:
    """``~~~~^^`` 一类栈标记行不进入实时日志，避免错误信息被片段淹没。"""
    process = _FakeStreamProcess(b"a\n    ~~~~^^\nb\n")
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    llmconvert.convert_model(_convert_plan(), on_output=seen.append)

    assert seen == ["a", "b"]


def test_convert_model_folds_chat_template_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """转换脚本整段打印的 chat template 折叠为长度摘要，避免日志刷屏。"""
    template = "{%- if messages[0]['role'] == 'system' %}{{ messages[0]['content'] }}{%- endif %}"
    process = _FakeStreamProcess(f"INFO:gguf.vocab:Setting chat_template to {template}\n".encode())
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)
    seen: list[str] = []

    llmconvert.convert_model(_convert_plan(), on_output=seen.append)

    assert len(seen) == 1
    assert seen[0].startswith("INFO:gguf.vocab:Setting chat_template to <已省略")
    assert template not in seen[0]
    assert "messages[0]" not in seen[0]
    assert llmconvert.summarize_convert_line("loading") == "loading"


def test_convert_model_streaming_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeStreamProcess(b"boom\n", returncode=1)
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(ServeError, match="boom"):
        llmconvert.convert_model(_convert_plan(), on_output=lambda line: None)


def test_convert_model_streaming_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    process = _FakeStreamProcess(b"", timeout_first=True)
    monkeypatch.setattr(llmbase.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(ServeError, match="超时"):
        llmconvert.convert_model(_convert_plan(), on_output=lambda line: None)


def test_progress_percent_parsing() -> None:
    from fcmd.apps.locllm import _locllmw_convert

    assert _locllmw_convert._progress_percent(" 45%|████▌     | 9/20") == 45
    assert _locllmw_convert._progress_percent("100%|████████| 20/20") == 100
    assert _locllmw_convert._progress_percent("Loading model file") is None


def _hf_dir(path: Path) -> Path:
    """构造一个可转换的 HF 目录（config.json + 权重 + 令牌器）。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text('{"architectures": ["LlamaForCausalLM"]}', encoding="utf-8")
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (path / "model.safetensors").write_bytes(b"x")
    return path


def test_convert_page_hf_size_and_stats(tmp_path: Path) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    model = tmp_path / "model"
    model.mkdir()
    (model / "a.safetensors").write_bytes(b"x" * 10)
    (model / "b.safetensors").write_bytes(b"y" * 5)
    (model / "config.json").write_text("{}", encoding="utf-8")
    assert _locllmw_convert._hf_size(model) == 15
    assert _locllmw_convert._dir_stats(model).startswith("2 个 safetensors")


def test_convert_page_hf_size_skips_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    model = tmp_path / "model"
    model.mkdir()
    target = model / "broken.safetensors"
    target.write_bytes(b"x")
    real_stat = Path.stat

    def fake_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == target:
            raise OSError("denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    assert _locllmw_convert._hf_size(model) == 0


def test_convert_page_find_convertible_layouts(tmp_path: Path) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    root = tmp_path / "root"
    child = _hf_dir(root / "model")
    nested = _hf_dir(root / "org" / "model")
    (root / "README.md").write_text("x", encoding="utf-8")
    (root / "org" / "notes.txt").write_text("x", encoding="utf-8")
    plain = root / "plain"
    plain.mkdir()

    found = {path for path, _ in _locllmw_convert._find_convertible(root)}
    assert found == {child, nested}


def test_convert_page_find_convertible_root_itself(tmp_path: Path) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    direct = _hf_dir(tmp_path / "direct")
    assert [path for path, _ in _locllmw_convert._find_convertible(direct)] == [direct]


def test_convert_page_find_convertible_missing(tmp_path: Path) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    assert _locllmw_convert._find_convertible(tmp_path / "missing") == []


def test_convert_page_default_output_path(tmp_path: Path) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    source = tmp_path / "Qwen3-8B"
    source.mkdir()
    root = tmp_path / "models"
    assert _locllmw_convert.default_output_path(source, root) == root / "converted" / "Qwen3-8B" / "Qwen3-8B.gguf"


def test_convert_page_output_path_from_form(tmp_path: Path) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    assert _locllmw_convert.output_path_from_form("", "", tmp_path) is None
    assert _locllmw_convert.output_path_from_form("   ", "custom.gguf", tmp_path) is None

    source = tmp_path / "model"
    source.mkdir()
    assert _locllmw_convert.output_path_from_form(str(source), "", tmp_path) == (
        _locllmw_convert.default_output_path(source, tmp_path)
    )
    explicit = tmp_path / "custom.gguf"
    assert _locllmw_convert.output_path_from_form(str(source), str(explicit), tmp_path) == explicit


def test_convert_page_find_convertible_skips_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fcmd.apps.locllm import _locllmw_convert

    broken = tmp_path / "broken"
    broken.mkdir()
    monkeypatch.setattr(_locllmw_convert.llmconvert, "is_hf_safetensors_dir", lambda path: False)
    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path) -> Any:
        if self == broken:
            raise OSError("denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    assert _locllmw_convert._find_convertible(broken) == []


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


def test_msvc_runtime_engine_local_new(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    (engine_dir / "msvcp140.dll").write_bytes(b"x")
    monkeypatch.setattr(llmdoctor, "_file_version", lambda dll: (14, 51, 36231, 0))
    check = llmdoctor._check_msvc_runtime(EngineInfo("b9", engine_dir / "llama-server.exe"))
    assert check.status == "ok"
    assert "14.51" in check.detail


def test_msvc_runtime_warns_on_old_system_dll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    system32 = tmp_path / "System32"
    system32.mkdir()
    (system32 / "msvcp140.dll").write_bytes(b"x")
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    monkeypatch.setattr(llmdoctor, "_file_version", lambda dll: (14, 0, 24215, 1))
    check = llmdoctor._check_msvc_runtime(EngineInfo("b9", engine_dir / "llama-server.exe"))
    assert check.status == "warn"
    assert "过旧" in check.detail


def test_msvc_runtime_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "nowhere"))
    check = llmdoctor._check_msvc_runtime(EngineInfo("b9", engine_dir / "llama-server.exe"))
    assert check.status == "warn"
    assert "MSVCP140" in check.detail
