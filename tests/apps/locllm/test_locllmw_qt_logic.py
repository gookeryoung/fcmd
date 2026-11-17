"""locllmw Qt 前端的纯逻辑测试：参数解析、资源检查、行渲染与采样封装。

页面类本身是 ``pragma: no cover`` 的 GUI 胶水，这里只测与 Qt 无关的模块级
函数。Qt 不可用时整体跳过（CI 只装 ``--extra test``，不含 PySide6）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PySide6")

from fcmd.apps.locllm import llmbase, llmserve
from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot
from fcmd.apps.locllm._locllmw_monitor import GpuSample, LoadSample
from fcmd.apps.locllm._locllmw_qt_chat import endpoint_from_form, service_labels
from fcmd.apps.locllm._locllmw_qt_models import fit_state_for, model_row_values, ordered_groups
from fcmd.apps.locllm._locllmw_qt_monitor import sample_once
from fcmd.apps.locllm._locllmw_qt_overview import loaded_row_values, usage_texts
from fcmd.apps.locllm._locllmw_qt_run import (
    build_plan_from,
    memory_guard,
    parse_int,
    parse_optional_int,
    plan_lines,
)
from fcmd.apps.locllm._locllmw_qt_settings import engine_rows_values
from fcmd.apps.locllm.llmbase import EngineInfo, LaunchPlan, ModelEntry, Profile, ServeError, ServeState, Tuning


def _plan(*, model: Path = Path("model.gguf"), context: int = 65536, kv_type: str | None = None) -> LaunchPlan:
    return LaunchPlan(
        name="qwen",
        model=model,
        profile=Profile("balanced", "Q5_K_M", 65536, "f16", 2048, "平衡"),
        context=context,
        host="127.0.0.1",
        port=8080,
        alias="qwen",
        api_key="sk-test",
        threads=8,
        engine=EngineInfo(tag="b12211", exe=Path("llama-server")),
        log=Path("qwen.log"),
        kv_type=kv_type,
        tuning=Tuning(),
    )


def _state(**kwargs: Any) -> ServeState:
    base: dict[str, Any] = {
        "name": "qwen",
        "pid": 1,
        "create_time": 0.0,
        "host": "127.0.0.1",
        "port": 8080,
        "model": "/models/qwen.gguf",
        "profile": "balanced",
        "context": 65536,
        "engine": "b12211",
        "alias": "qwen",
        "api_key": "sk",
        "log": "/tmp/qwen.log",
        "started_at": "2026-01-01T00:00:00",
    }
    base.update(kwargs)
    return ServeState(**base)


def _model(root: Path, name: str, size: int = 100) -> ModelEntry:
    return ModelEntry(root=root, path=root / name, size=size, profiles=("balanced",), tags=("代码",))


# ---------------------------------------------------------------------- #
# 参数解析与资源检查
# ---------------------------------------------------------------------- #
def test_parse_int_and_optional_int() -> None:
    assert parse_int(" 8080 ", "端口") == 8080
    assert parse_int("-1", "端口") == -1
    assert parse_optional_int("", "上下文") is None
    assert parse_optional_int("4096", "上下文") == 4096
    with pytest.raises(ServeError, match="端口 必须是整数"):
        parse_int("abc", "端口")
    with pytest.raises(ServeError, match="上下文 必须是整数"):
        parse_optional_int("x", "上下文")


def test_memory_guard_passes_with_enough_vram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmbase, "run_command", lambda *a, **k: llmbase.CommandResult(0))
    message = memory_guard(_plan(), (0, 48 * 1024))
    assert message.startswith("资源检查通过")


def test_memory_guard_rejects_hardware_limit() -> None:
    with pytest.raises(ServeError, match="整卡显存不足"):
        memory_guard(_plan(), (0, 1))


def test_memory_guard_rejects_busy_vram() -> None:
    with pytest.raises(ServeError, match="当前可用显存不足"):
        memory_guard(_plan(), (47 * 1024, 48 * 1024))


def test_memory_guard_falls_back_to_system_memory() -> None:
    message = memory_guard(_plan(), None, available_memory=64 * 1024**3)
    assert "可用内存" in message
    with pytest.raises(ServeError, match="可用内存不足"):
        memory_guard(_plan(), None, available_memory=1)


def test_plan_lines_include_command_and_warnings() -> None:
    plan = _plan()
    lines = plan_lines(
        plan,
        estimate=llmbase.GIB,
        tags=("代码", "对话"),
        precision_detail="Q5_K_M",
        advice="适合代码补全",
        total=None,
        free=None,
    )
    assert any("命令: " in line for line in lines)
    assert any("标签: 代码 · 对话" in line for line in lines)
    assert any("精度: Q5_K_M" in line for line in lines)
    assert any("显存估算" in line for line in lines)


def test_plan_lines_warn_when_fit_fails() -> None:
    from fcmd.apps.locllm import llmmodel

    plan = _plan()
    unfit = llmmodel.VramFit(needed=10 * 1024**3, available=1024**3, max_context=2048)
    lines = plan_lines(plan, estimate=10 * 1024**3, total=unfit, free=None)
    assert any(line.strip().startswith("警告") for line in lines)


def test_build_plan_from_rejects_empty_name() -> None:
    with pytest.raises(ServeError, match="服务名不能为空"):
        build_plan_from({"name": "   "})


def test_build_plan_from_rejects_bad_port() -> None:
    with pytest.raises(ServeError, match="端口 必须是整数"):
        build_plan_from({"name": "qwen", "port": "x"})


# ---------------------------------------------------------------------- #
# 模型行 / 分组
# ---------------------------------------------------------------------- #
def test_model_row_values() -> None:
    entry = _model(Path("/models"), "org/model-Q5_K_M.gguf", 2048)
    values = model_row_values(entry, precision_note="Q5_K_M", hardware="可用")
    assert values[0].endswith("（Q5_K_M）")
    assert values[2] == "balanced"
    assert values[3] == "代码"
    assert values[4] == "可用"


def test_ordered_groups_puts_running_first(tmp_path: Path) -> None:
    entries = [
        _model(tmp_path, "a/x.gguf"),
        _model(tmp_path, "b/y.gguf"),
        _model(tmp_path, "top.gguf"),
    ]
    from fcmd.apps.locllm import llmenv

    running = {llmenv.path_key(tmp_path / "b/y.gguf")}
    groups = ordered_groups(entries, tmp_path, running)
    assert groups[0][0] == "b"
    assert [label for label, _items in groups][1:] == sorted([label for label, _items in groups][1:], key=str.casefold)


def test_fit_state_for_prefers_running() -> None:
    assert fit_state_for(running=True, total=None, free=None) == "运行中"
    assert fit_state_for(running=False, total=None, free=None) == "-"


# ---------------------------------------------------------------------- #
# 概览 / 对话 / 设置行渲染
# ---------------------------------------------------------------------- #
def test_loaded_row_values() -> None:
    values = loaded_row_values(_state())
    assert values[0] == "qwen"
    assert values[1] == "qwen.gguf"
    assert "ctx 65536" in values[2]
    assert values[3] == "http://127.0.0.1:8080"


def test_usage_texts_with_gpu_and_memory() -> None:
    sample = LoadSample(
        memory_used=8 * 1024**3,
        memory_total=32 * 1024**3,
        memory_percent=25.0,
        gpu=GpuSample(name="RTX", memory_used=4096.0, memory_total=8192.0),
    )
    gpu_text, gpu_percent, memory_text, memory_percent = usage_texts(sample)
    assert "4.0 / 8.0 GB" in gpu_text
    assert gpu_percent == pytest.approx(50.0)
    assert "25.0 %" in memory_text
    assert memory_percent == pytest.approx(25.0)


def test_usage_texts_without_gpu() -> None:
    sample = LoadSample(memory_used=0, memory_total=0, memory_percent=0.0, gpu=None)
    gpu_text, gpu_percent, memory_text, memory_percent = usage_texts(sample)
    assert gpu_text == "未检测到 NVIDIA GPU"
    assert gpu_percent == 0.0
    assert memory_text == "暂不可用"
    assert memory_percent == 0.0


def test_service_labels_marks_dead_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_chat.process_alive", lambda pid, created: pid == 1)
    labels = service_labels([_state(pid=1), _state(name="dead", pid=2)])
    assert labels[0][0] == "qwen"
    assert labels[1][0] == "dead（已退出）"


def test_endpoint_from_form_strips_v1() -> None:
    endpoint = endpoint_from_form("http://127.0.0.1:8080/v1/", " sk-test ", "qwen")
    assert endpoint.base_url == "http://127.0.0.1:8080"
    assert endpoint.api_key == "sk-test"
    assert endpoint.model == "qwen"


def test_endpoint_from_form_rejects_empty_fields() -> None:
    with pytest.raises(ServeError, match="服务地址"):
        endpoint_from_form("   ", "sk", "qwen")
    with pytest.raises(ServeError, match="模型名"):
        endpoint_from_form("http://127.0.0.1:8080", "sk", "  ")


def test_engine_rows_values() -> None:
    rows = engine_rows_values([EngineInfo(tag="b1", exe=Path("llama-server"), source="current")])
    assert rows == [("b1", "current", "llama-server")]


# ---------------------------------------------------------------------- #
# 采样封装
# ---------------------------------------------------------------------- #
def test_sample_once_without_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_monitor._sample_load", lambda pid, process: LoadSample())
    load, metrics, error = sample_once(None)
    assert isinstance(load, LoadSample)
    assert metrics is None
    assert error is None


def test_sample_once_reports_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_monitor._attach_process", lambda pid: None)
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_monitor._sample_load", lambda pid, process: LoadSample())
    metrics = llmserve.ServeMetrics(prompt_tokens=5, predicted_tokens=4, processing=1, deferred=2, kv_usage=0.5)
    monkeypatch.setattr(llmserve, "fetch_metrics", lambda host, port, api_key: metrics)
    _load, metrics, error = sample_once(_state())
    assert metrics is not None
    assert metrics.predicted_tokens == 4
    assert error is None


def test_sample_once_reports_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_monitor._attach_process", lambda pid: None)
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_monitor._sample_load", lambda pid, process: LoadSample())

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ServeError("connection refused")

    monkeypatch.setattr(llmserve, "fetch_metrics", boom)
    _load, metrics, error = sample_once(_state())
    assert metrics is None
    assert error == "connection refused"


def test_hardware_snapshot_import_is_usable() -> None:
    """概览页只读快照；这里确认快照类型仍可独立构造（防止上游改名）。"""
    assert HardwareSnapshot().gpu_mib is None
