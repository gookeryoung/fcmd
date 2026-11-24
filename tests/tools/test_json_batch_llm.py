"""`--json` 接入批次 4：本地大模型工具（llmenv / llmmodel / llmserve / llmbench / llmdoctor）。

仅验证机读信封（schema、字段、退出码）；人类可读表格由 ``tests/apps/llm`` 覆盖。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.llm import llmbench, llmdoctor, llmenv, llmserve
from fcmd.apps.llm.llmbase import DEFAULT_API_KEY, Check, Endpoint, ServeState
from fcmd.apps.llm.llmbench import BenchResult
from fcmd.core.toolkit import run_tool


def _payload(raw: str) -> dict[str, Any]:
    return json.loads(raw[raw.index("{") :])


def _state(name: str = "alpha", pid: int = 4_000_000) -> ServeState:
    return ServeState(
        name=name,
        pid=pid,
        create_time=0.0,
        host="127.0.0.1",
        port=8080,
        model="m.gguf",
        profile="balanced",
        context=65536,
        engine="llama-server.exe",
        alias="qwen",
        api_key=DEFAULT_API_KEY,
        log="log.txt",
        started_at="2026-11-24T10:00:00+08:00",
    )


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离的 FCMD_HOME，避免读写真实用户目录。"""
    target = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(target))
    for name in ("FCMD_MODELS", "FCMD_SERVE_DIR", "FCMD_CONFIG", "FCMD_ENGINES"):
        monkeypatch.delenv(name, raising=False)
    return target


def test_llmenv_show_json(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_tool("llmenv", ["show", "--json"])
    payload = _payload(capsys.readouterr().out)
    assert code == 0
    assert payload["subcommand"] == "show"
    data = payload["data"]
    assert data["fcmd_home"]
    assert data["config_path"].endswith("config.json")
    assert data["models_dirs"]
    assert data["registered_services"] == 0


def test_llmenv_list_json_empty(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("llmenv", ["list", "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"] == {"count": 0, "services": []}


def test_llmenv_list_json_with_state(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    llmenv.save_state(_state("alpha"))
    assert run_tool("llmenv", ["list", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["count"] == 1
    assert data["services"][0]["name"] == "alpha"
    assert data["services"][0]["base_url"] == "http://127.0.0.1:8080"


def test_llmserve_status_json_list_and_named(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    llmenv.save_state(_state("alpha"))
    monkeypatch.setattr(llmserve, "process_alive", lambda *args: False)

    assert run_tool("llmserve", ["status", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["count"] == 1
    assert data["services"][0]["running"] is False
    assert data["services"][0]["profile"] == "balanced"

    assert run_tool("llmserve", ["status", "--name", "alpha", "--json"]) == 0
    detail = _payload(capsys.readouterr().out)["data"]
    assert detail["name"] == "alpha"
    assert detail["context"] == 65536
    assert detail["vision"] is False


def test_llmserve_status_missing_json(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_tool("llmserve", ["status", "--name", "nope", "--json"]) == 4
    payload = _payload(capsys.readouterr().err)
    assert payload["schema"] == "fcmd-error/1"
    assert payload["code"] == "NOT_FOUND"


def test_llmmodel_list_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "m-Q4_K_M.gguf").write_bytes(b"x" * 16)
    assert run_tool("llmmodel", ["list", "--models-root", str(tmp_path), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["count"] == 1
    assert data["models"][0]["relative"] == "m-Q4_K_M.gguf"
    assert data["models"][0]["size"] == 16
    assert data["selection"]["speed"]["quant"] == "Q4_K_M"
    assert data["selection"]["speed"]["path"].endswith("m-Q4_K_M.gguf")
    assert data["selection"]["balanced"]["path"] is None


def test_llmmodel_list_missing_dir_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run_tool("llmmodel", ["list", "--models-root", str(tmp_path / "missing"), "--json"])
    assert code == 4
    assert _payload(capsys.readouterr().err)["code"] == "DIR_NOT_FOUND"


def test_llmmodel_find_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "m-Q4_K_M.gguf").write_bytes(b"x" * 8)
    assert run_tool("llmmodel", ["find", "Q4_K_M", "--models-root", str(tmp_path), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["quant"] == "Q4_K_M"
    assert data["path"].endswith("m-Q4_K_M.gguf")


def test_llmmodel_find_not_found_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "m-Q4_K_M.gguf").write_bytes(b"x" * 8)
    assert run_tool("llmmodel", ["find", "Q8_0", "--models-root", str(tmp_path), "--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "NOT_FOUND"


def test_llmmodel_find_non_gguf_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "weights.bin"
    target.write_bytes(b"x")
    assert run_tool("llmmodel", ["find", "Q4_K_M", "--model", str(target), "--json"]) == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_llmmodel_vram_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "m.gguf"
    target.write_bytes(b"x" * 1024)
    assert run_tool("llmmodel", ["vram", str(target), "--ctx", "2048", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["size"] == 1024
    assert data["context"] == 2048
    assert data["kv"] == "q8_0"
    assert data["estimate"] > 0


def test_llmmodel_vram_invalid_kv_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "m.gguf"
    target.write_bytes(b"x")
    assert run_tool("llmmodel", ["vram", str(target), "--kv", "nope", "--json"]) == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_llmbench_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmbench, "_resolve_endpoint", lambda *args: Endpoint("http://x", "k", "m"))
    monkeypatch.setattr(llmbench, "bench", lambda *args, **kwargs: BenchResult(0.5, 2000, 64, 20.0))
    llmbench.bench_cmd(json_output=True)
    data = _payload(capsys.readouterr().out)["data"]
    assert data["base_url"] == "http://x"
    assert data["tokens_per_s"] == 20.0
    assert "hint" not in data


def test_llmbench_json_short_prompt_hint(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmbench, "_resolve_endpoint", lambda *args: Endpoint("http://x", "k", "m"))
    monkeypatch.setattr(llmbench, "bench", lambda *args, **kwargs: BenchResult(0.5, 10, 64, 20.0))
    llmbench.bench_cmd(json_output=True)
    assert "短上下文吞吐偏乐观" in _payload(capsys.readouterr().out)["data"]["hint"]


def test_llmbench_json_no_service(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from fcmd.apps.llm.llmbase import ServeError

    def _fail(*args: object) -> Endpoint:
        raise ServeError("没有已登记的服务：用 --base-url 指定地址，或先 fcmd llmserve start")

    monkeypatch.setattr(llmbench, "_resolve_endpoint", _fail)
    assert run_tool("llmbench", ["--json"]) == 4
    assert _payload(capsys.readouterr().err)["code"] == "NOT_FOUND"


def test_llmdoctor_json_ok(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(llmdoctor, "doctor", lambda **kwargs: [Check("GPU", "ok", "RTX")])
    assert run_tool("llmdoctor", ["--json"]) == 0
    payload = _payload(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["total"] == 1
    assert payload["data"]["checks"][0]["status"] == "ok"


def test_llmdoctor_json_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(
        llmdoctor,
        "doctor",
        lambda **kwargs: [Check("GPU", "ok", "RTX"), Check("引擎", "fail", "未安装")],
    )
    assert run_tool("llmdoctor", ["--json"]) == 5
    payload = _payload(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["data"]["failed"] == 1
    assert payload["data"]["checks"][1]["name"] == "引擎"
