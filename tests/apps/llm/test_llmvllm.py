"""vLLM 引擎接入的纯逻辑测试：平台解析、版本列表、安装镜像、启动参数与 HF 扫描。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.llm import _llmvllm
from fcmd.apps.llm.llmbase import CommandResult, ServeError, ServeState


def _platform(*interpreters: _llmvllm.VllmInterpreter) -> _llmvllm.VllmPlatform:
    return _llmvllm.VllmPlatform("wsl", True, "WSL2 · test", tuple(interpreters))


def _interpreter(distro: str = "Ubuntu-24.04", home: str = "/home/tester") -> _llmvllm.VllmInterpreter:
    return _llmvllm.VllmInterpreter(
        python="/usr/bin/python3.12",
        version="3.12.3",
        distro=distro,
        home=home,
        gpu="NVIDIA GeForce RTX 5090 D",
    )


def _hf_model(root: Path) -> Path:
    model = root / "Qwen3-Coder-30B"
    model.mkdir(parents=True)
    (model / "config.json").write_text(json.dumps({"max_position_embeddings": 65536}), encoding="utf-8")
    (model / "model-00001-of-00002.safetensors").write_bytes(b"x" * 16)
    return model


def test_parse_wsl_distros_handles_utf16_garbage() -> None:
    text = "".join(ch + "\x00" for ch in "  * Ubuntu-24.04    Running         2")
    other = "".join(ch + "\x00" for ch in "    kali-linux      Stopped         2")
    assert _llmvllm.parse_wsl_distros(f"\x00{text}\n{other}\n") == ["Ubuntu-24.04", "kali-linux"]


def test_parse_wsl_distros_ignores_header_and_duplicates() -> None:
    text = "NAME STATE VERSION\n* Ubuntu Running 2\nUbuntu Running 2\nDebian Stopped 2\n"
    assert _llmvllm.parse_wsl_distros(text) == ["Ubuntu", "Debian"]


def test_python_supported_range() -> None:
    assert not _llmvllm.python_supported("3.9.18")
    assert _llmvllm.python_supported("3.10.12")
    assert _llmvllm.python_supported("3.12.3")
    assert not _llmvllm.python_supported("3.13.0")


def test_parse_versions_filters_yanked_and_sorts() -> None:
    payload = {
        "releases": {
            "0.10.0": [{"yanked": False}],
            "0.11.0": [{"yanked": False}],
            "0.12.0rc1": [{"yanked": False}],
            "0.9.0": [{"yanked": True}],
            "0.8.5.post1": [{"yanked": False}],
        }
    }
    assert _llmvllm.parse_versions(payload) == ("0.11.0", "0.10.0", "0.8.5.post1")


def test_wsl_path_translates_drive(tmp_path: Path) -> None:
    assert _llmvllm.wsl_path(Path("D:/models/qwen")) == "/mnt/d/models/qwen"
    assert _llmvllm.wsl_path(Path(r"C:\Users\x\model")) == "/mnt/c/Users/x/model"


def test_install_registers_engine_and_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform = _platform(_interpreter())
    monkeypatch.setattr(_llmvllm, "detect_platform", lambda *args, **kwargs: platform)
    seen: list[list[str]] = []

    def execute(cmd: list[str], timeout: float, on_output: object) -> CommandResult:
        seen.append(cmd)
        return CommandResult(0, "FCMD_VLLM_READY 0.11.0", "")

    engine = _llmvllm.install_engine("0.11.0", interpreter=platform.primary, home=tmp_path, execute=execute)
    assert engine.python == "/home/tester/.fcmd/engines/vllm/0.11.0/venv/bin/python"
    assert seen and seen[0][:2] == ["wsl.exe", "-d"]
    assert (tmp_path / "engines" / "vllm" / "0.11.0" / "engine.json").is_file()
    assert _llmvllm.current_tag(tmp_path) == "0.11.0"
    listed = _llmvllm.list_engines(tmp_path)
    assert [item.tag for item in listed] == ["0.11.0"]
    assert listed[0].source == "current"
    assert listed[0].in_wsl is True
    assert _llmvllm.resolve_engine(home=tmp_path).tag == "0.11.0"


def test_install_rejects_invalid_tag(tmp_path: Path) -> None:
    with pytest.raises(ServeError):
        _llmvllm.install_engine("../evil", home=tmp_path)


def test_install_failure_surfaces_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform = _platform(_interpreter())
    monkeypatch.setattr(_llmvllm, "detect_platform", lambda *args, **kwargs: platform)

    def execute(cmd: list[str], timeout: float, on_output: object) -> CommandResult:
        return CommandResult(1, "No matching distribution found for vllm==9.9.9", "")

    with pytest.raises(ServeError, match="No matching distribution"):
        _llmvllm.install_engine("9.9.9", interpreter=platform.primary, home=tmp_path, execute=execute)


def test_install_script_quotes_paths() -> None:
    script = _llmvllm.install_script("0.11.0", "/home/a b/vllm", "/usr/bin/python3.12")
    assert "python3.12 -m venv '/home/a b/vllm/venv'" in script
    assert "pip install 'vllm==0.11.0'" in script


def test_make_plan_builds_wsl_argv(tmp_path: Path) -> None:
    model = _hf_model(tmp_path)
    engine = _llmvllm.VllmEngine("0.11.0", "/home/t/.fcmd/x/venv/bin/python", distro="Ubuntu", platform="wsl")
    plan = _llmvllm.make_plan(
        "qwen",
        model,
        engine,
        host="127.0.0.1",
        port=8100,
        context=32768,
        alias="qwen",
        api_key="secret",
        gpu_memory_utilization=0.85,
        max_num_seqs=8,
        extra_args=("--enable-prefix-caching",),
        home=tmp_path,
    )
    argv = plan.argv()
    assert argv[:4] == ["wsl.exe", "-d", "Ubuntu", "--"]
    assert "vllm.entrypoints.openai.api_server" in argv
    assert _llmvllm.wsl_path(model) in argv
    assert argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--port") + 1] == "8100"
    assert argv[argv.index("--gpu-memory-utilization") + 1] == "0.85"
    assert argv[argv.index("--max-num-seqs") + 1] == "8"
    assert argv[-1] == "--enable-prefix-caching"
    assert plan.engine_kind == "vllm"
    assert plan.launcher == "wsl:Ubuntu"
    assert plan.api_key == "secret"


def test_make_plan_uses_vllm_serve_entrypoint(tmp_path: Path) -> None:
    """有 venv 时走官方 ``vllm serve`` 入口，旧式模块入口只在缺 venv 时兜底。"""
    model = _hf_model(tmp_path)
    engine = _llmvllm.VllmEngine(
        "0.34.0", "/root/e/venv/bin/python", distro="Ubuntu", platform="wsl", venv="/root/e/venv"
    )
    plan = _llmvllm.make_plan(
        "qwen", model, engine, host="127.0.0.1", port=8100, context=4096, alias="qwen", api_key="", home=tmp_path
    )
    argv = plan.argv()
    assert argv[:5] == ["wsl.exe", "-d", "Ubuntu", "--", "/root/e/venv/bin/python"]
    assert argv[5] == "-u"
    assert argv[6:8] == ["/root/e/venv/bin/vllm", "serve"]


def test_launch_once_swaps_detached_for_no_window_on_wsl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """wsl.exe 在 DETACHED_PROCESS 下不转发日志，启动时改用 CREATE_NO_WINDOW。"""
    import os
    from types import SimpleNamespace
    from typing import cast

    from fcmd.apps.llm import llmserve

    model = _hf_model(tmp_path)
    engine = _llmvllm.VllmEngine("0.11.0", "/home/t/.fcmd/x/venv/bin/python", distro="Ubuntu", platform="wsl")
    plan = _llmvllm.make_plan(
        "qwen", model, engine, host="127.0.0.1", port=8100, context=4096, alias="qwen", api_key="", home=tmp_path
    )
    captured: dict[str, object] = {}

    class _Proc:
        pid = os.getpid()

        @staticmethod
        def poll() -> None:
            return None

    def fake_popen(_argv: list[str], **kwargs: object) -> _Proc:
        captured.update(kwargs)
        return _Proc()

    monkeypatch.setattr(llmserve, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(llmserve, "_creation_flags", lambda: llmserve._DETACHED_PROCESS)
    state = llmserve._launch_once(
        plan,
        home=tmp_path,
        popen=fake_popen,
        health=lambda *_args: True,
        sleep=lambda _seconds: None,
    )

    flags = cast(int, captured["creationflags"])
    assert state.launcher == "wsl:Ubuntu"
    assert flags & llmserve._CREATE_NO_WINDOW
    assert not flags & llmserve._DETACHED_PROCESS


def test_make_plan_rejects_gguf_model(tmp_path: Path) -> None:
    model = tmp_path / "gguf-model"
    model.mkdir()
    (model / "model.gguf").write_bytes(b"x")
    engine = _llmvllm.VllmEngine("0.11.0", "/usr/bin/python3")
    with pytest.raises(ServeError, match="safetensors"):
        _llmvllm.make_plan("x", model, engine, host="127.0.0.1", port=1, context=1, alias="x", api_key="")


def test_scan_hf_models_finds_directories(tmp_path: Path) -> None:
    model = _hf_model(tmp_path)
    nested = tmp_path / "hub" / "models--org--name" / "snapshots" / "abc"
    nested.mkdir(parents=True)
    (nested / "config.json").write_text("{}", encoding="utf-8")
    (nested / "model.safetensors").write_bytes(b"y" * 32)
    (tmp_path / "plain").mkdir()
    found = _llmvllm.scan_hf_models([tmp_path])
    assert {item.path for item in found} == {model, nested}
    assert found[0].path == nested  # 32 字节比 16 字节大


def test_stop_engine_runs_pkill_for_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: float = 15.0) -> CommandResult:
        calls.append(cmd)
        return CommandResult(0, "", "")

    monkeypatch.setattr(_llmvllm, "run_command", fake_run)
    state = ServeState(
        name="qwen",
        model="/mnt/d/models/qwen",
        profile="balanced",
        context=32768,
        host="127.0.0.1",
        port=8100,
        pid=1,
        create_time=1.0,
        engine="vLLM 0.11.0",
        alias="qwen",
        api_key="",
        log="x",
        started_at="now",
        engine_kind="vllm",
        launcher="wsl:Ubuntu",
    )
    _llmvllm.stop_engine(state)
    assert calls and calls[0][:4] == ["wsl.exe", "-d", "Ubuntu", "--"]
    assert calls[0][4] == "pkill"
    assert calls[0][5] == "-f"
    assert "--port 8100" in calls[0][6]


def test_detect_platform_reports_unsupported_without_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_llmvllm.os, "name", "nt")
    _llmvllm.reset_platform_cache()

    def fake_run(cmd: list[str], timeout: float = 15.0) -> CommandResult:
        return CommandResult(1, "", "wsl: 无法找到发行版")

    platform = _llmvllm.detect_platform(fake_run, use_cache=False)
    assert platform.supported is False
    assert "WSL2" in platform.reason


def test_engine_options_reflect_platform() -> None:
    from fcmd.apps.llm._llmw_qt_run import _infer_engine_options

    supported = _infer_engine_options(_platform(_interpreter()))
    unsupported = _infer_engine_options(_llmvllm.VllmPlatform("none", False, "不支持"))
    assert supported[1][0] == "vllm"
    assert supported[1][2] == "HF safetensors · WSL2 / Linux"
    assert unsupported[1][2] == "当前系统不可用"
