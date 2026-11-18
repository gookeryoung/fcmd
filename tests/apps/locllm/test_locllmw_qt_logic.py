"""locllmw Qt 前端的纯逻辑测试：参数解析、资源检查、行渲染与采样封装。

页面类本身是 ``pragma: no cover`` 的 GUI 胶水，这里只测与 Qt 无关的模块级
函数。Qt 不可用时整体跳过（CI 只装 ``--extra test``，不含 PySide6）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.locllm import llmbase, llmserve
from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot
from fcmd.apps.locllm._locllmw_monitor import LoadSample
from fcmd.apps.locllm._locllmw_qt_center import _ModelDetailView, _ModelItemRow, quantize_menu_items
from fcmd.apps.locllm._locllmw_qt_chat import endpoint_from_form, service_labels
from fcmd.apps.locllm._locllmw_qt_overview import (
    recommendation_params_html,
    recommendation_perf_html,
    sample_once,
)
from fcmd.apps.locllm._locllmw_qt_run import (
    build_plan_from,
    memory_guard,
    model_combo_text,
    parse_custom_args,
    parse_int,
    parse_optional_int,
    pick_model,
    plan_lines,
    sanitize_custom_args,
    startup_args_tail,
)
from fcmd.apps.locllm._locllmw_qt_settings import engine_rows_values
from fcmd.apps.locllm._locllmw_recommend import Recommendation
from fcmd.apps.locllm.llmbase import EngineInfo, LaunchPlan, ModelEntry, Profile, ServeError, ServeState, Tuning
from fcmd.apps.locllm.llmdown import ModelInfo, ModelSummary


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


def test_model_combo_text_truncates_long_names() -> None:
    entry = ModelEntry(
        root=Path("/models"),
        path=Path("/models/Qwen3-Coder-30B-A3B-Instruct-Q5_K_M.gguf"),
        size=20 * 1024**3,
    )
    text = model_combo_text(entry)
    assert "…" in text
    assert "Qwen3-Coder" in text
    assert len(text.split("  (")[0]) == 30


def test_sanitize_custom_args_drops_model_flags() -> None:
    assert sanitize_custom_args(
        ("D:/models/demo.gguf", "-ngl", "99", "-m", "x.gguf", "--model", "y.gguf", "--mmproj", "z.gguf")
    ) == ("-ngl", "99")


def test_startup_args_tail_drops_executable_and_model() -> None:
    argv = [
        "llama-server",
        "-m",
        "D:/models/demo.gguf",
        "-ngl",
        "99",
        "--mmproj",
        "D:/models/vision.gguf",
        "-c",
        "65536",
    ]
    assert startup_args_tail(argv) == ("-ngl", "99", "-c", "65536")


def test_parse_custom_args_splits_shell_like_text() -> None:
    assert parse_custom_args("") == ()
    assert parse_custom_args("  --no-mmap   --flash-attn on  ") == ("--no-mmap", "--flash-attn", "on")
    with pytest.raises(ValueError):
        parse_custom_args("'unclosed")


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


def test_quantize_menu_items_marks_existing_precisions() -> None:
    """已量化的精度在菜单里打勾并禁用，其余精度保持可选。"""
    items = quantize_menu_items(("q4_k_m", "Q5_K_M"))
    by_quant = {quant: done for quant, _desc, done in items}
    assert by_quant["Q4_K_M"] is True
    assert by_quant["Q5_K_M"] is True
    assert by_quant["Q8_0"] is False
    assert all(desc for _quant, desc, _done in items)


def test_model_item_row_expands_inline_detail() -> None:
    """浏览结果行展开后，详情区直接挂在行下方（不再需要右侧面板）。"""
    from PySide6.QtWidgets import QApplication, QLabel

    QApplication.instance() or QApplication([])
    row = _ModelItemRow(ModelSummary(repo="Qwen/demo"))
    installed_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), installed=True)
    assert installed_row.download_button.text() == "✓ 已安装"
    assert installed_row.download_button.objectName() == "InstalledButton"
    assert not installed_row.download_button.isEnabled()
    convertible_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), convertible=True)
    assert convertible_row.convert_button is not None
    assert convertible_row.convert_button.text() == "转换"
    assert convertible_row.convert_button.isEnabled()
    converted_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), convertible=True, converted=True)
    assert converted_row.convert_button is not None
    assert converted_row.convert_button.text() == "已转换"
    assert converted_row.convert_button.objectName() == "ConvertedButton"
    assert not converted_row.convert_button.isEnabled()
    blocked_row = _ModelItemRow(ModelSummary(repo="Qwen/image"), convert_blocked="diffusers 多组件模型")
    assert blocked_row.convert_button is not None
    assert blocked_row.convert_button.text() == "不可转换"
    assert not blocked_row.convert_button.isEnabled()
    quantized_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), quant_tags=("Q4_K_M", "Q5_K_M"))
    assert quantized_row.findChild(QLabel, "QuantTag") is not None
    start_row = _ModelItemRow(
        ModelSummary(repo="Qwen/demo"),
        quant_tags=("Q4_K_M", "Q5_K_M"),
        startable=True,
        default_quant="Q4_K_M",
        serve_path=Path("/models/Qwen/demo/x-Q4_K_M.gguf"),
    )
    assert start_row.start_button is not None
    assert start_row.start_button.text() == "启动服务"
    assert start_row.start_button.isEnabled()
    tag_texts = {label.text() for label in start_row.findChildren(QLabel, "QuantTag")}
    assert "Q4_K_M · 上次启动" in tag_texts
    assert "Q5_K_M" in tag_texts
    requested: list[str] = []
    start_row.startRequested.connect(requested.append)
    start_row.start_button.click()
    assert requested == ["Qwen/demo"]
    start_row.set_serve_state(busy=False, running=True)
    assert start_row.start_button.text() == "已启动"
    assert not start_row.start_button.isEnabled()
    quantizable_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), quantizable=True)
    assert quantizable_row.quant_button is not None
    assert quantizable_row.quant_button.text() == "量化"
    assert quantizable_row.quant_button.isEnabled()
    blocked_quant_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), quant_blocked=True)
    assert blocked_quant_row.quant_button is not None
    assert blocked_quant_row.quant_button.text() == "不可量化"
    assert not blocked_quant_row.quant_button.isEnabled()
    partial_row = _ModelItemRow(ModelSummary(repo="Qwen/demo"), incomplete=True, progress_text="已下载 42%")
    assert partial_row.download_button.text() == "继续下载"
    assert partial_row.download_button.isEnabled()
    progress = partial_row.findChild(QLabel, "DownloadProgress")
    assert progress is not None and progress.text() == "已下载 42%"
    assert row.download_button.text() == "下载"
    assert row.repo == "Qwen/demo"
    assert not row.is_expanded()
    assert row.expand_button.text() == "展开"
    assert not hasattr(row, "files_button")
    assert row.detail_host.isHidden()
    row.set_expanded(True)
    assert row.is_expanded()
    assert row.expand_button.text() == "收起"
    assert not row.detail_host.isHidden()
    row.set_expanded(False)
    assert not row.is_expanded()
    assert row.expand_button.text() == "展开"
    assert not hasattr(row, "files_button")
    assert row.detail_host.isHidden()


def test_model_detail_view_reset_and_metadata() -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    view = _ModelDetailView()
    view.reset("Qwen/demo")
    assert view.repo == "Qwen/demo"
    assert not hasattr(view, "download_button")
    assert not hasattr(view, "readme")
    view.set_error("网络错误")
    assert view.error.text() == "网络错误"
    assert not view.error.isHidden()
    view.render(ModelInfo(summary=ModelSummary(repo="Qwen/demo", chinese_name="示例")))
    assert view.meta_values["中文名"].text() == "示例"


# ---------------------------------------------------------------------- #
# 概览 / 对话 / 设置行渲染
# ---------------------------------------------------------------------- #
def test_service_labels_marks_dead_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_chat.process_alive", lambda pid, created: pid == 1)
    labels = service_labels([_state(pid=1), _state(name="dead", pid=2)])
    assert labels[0][0] == "qwen（运行中）"
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
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_overview._sample_load", lambda pid, process: LoadSample())
    load, metrics, error = sample_once(None)
    assert isinstance(load, LoadSample)
    assert metrics is None
    assert error is None


def test_sample_once_reports_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_overview._attach_process", lambda pid: None)
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_overview._sample_load", lambda pid, process: LoadSample())
    metrics = llmserve.ServeMetrics(prompt_tokens=5, predicted_tokens=4, processing=1, deferred=2, kv_usage=0.5)
    monkeypatch.setattr(llmserve, "fetch_metrics", lambda host, port, api_key: metrics)
    _load, metrics, error = sample_once(_state())
    assert metrics is not None
    assert metrics.predicted_tokens == 4
    assert error is None


def test_sample_once_reports_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_overview._attach_process", lambda pid: None)
    monkeypatch.setattr("fcmd.apps.locllm._locllmw_qt_overview._sample_load", lambda pid, process: LoadSample())

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ServeError("connection refused")

    monkeypatch.setattr(llmserve, "fetch_metrics", boom)
    _load, metrics, error = sample_once(_state())
    assert metrics is None
    assert error == "connection refused"


def test_hardware_snapshot_import_is_usable() -> None:
    """概览页只读快照；这里确认快照类型仍可独立构造（防止上游改名）。"""
    assert HardwareSnapshot().gpu_mib is None


def test_pick_model_prefers_explicit_selection(tmp_path: Path) -> None:
    entries = [_model(tmp_path, "a-Q4_K_XL.gguf", 10), _model(tmp_path, "b-Q5_K_M.gguf", 20)]
    selected = tmp_path / "other.gguf"
    assert pick_model(selected, entries, "Q5_K_M") == selected


def test_pick_model_matches_profile_quant_first(tmp_path: Path) -> None:
    entries = [
        _model(tmp_path, "a-Q4_K_XL.gguf", 100),
        _model(tmp_path, "b-Q5_K_M.gguf", 20),
    ]
    assert pick_model(None, entries, "Q5_K_M") == tmp_path / "b-Q5_K_M.gguf"


def test_pick_model_falls_back_to_largest_when_quant_missing(tmp_path: Path) -> None:
    entries = [_model(tmp_path, "a-Q4_K_XL.gguf", 10), _model(tmp_path, "b-IQ4_XS.gguf", 30)]
    assert pick_model(None, entries, "Q5_K_M") == tmp_path / "b-IQ4_XS.gguf"


def test_pick_model_returns_none_for_empty_list() -> None:
    assert pick_model(None, [], "Q5_K_M") is None


def test_recommendation_html_highlights_name_params_and_perf() -> None:
    """推荐行参数 / 性能 HTML：关键数值加粗，性能标注来源。"""
    recommendation = Recommendation(
        model=ModelEntry(root=Path("/models"), path=Path("/models/qwen3-8b-Q5_K_M.gguf"), size=5 * 1024**3),
        profile="coder",
        context=32768,
        kv_type="q8_0",
        batch=512,
        ubatch=256,
        parallel=2,
        tuning="并发吞吐",
        decode_tps=42.4,
        prompt_tps=16200.0,
        measured=True,
        basis="代码 · 实测",
        score=90,
        score_parts=(40, 25, 15, 10),
    )

    params = recommendation_params_html(recommendation)
    assert "档位 <b>coder</b>" in params
    assert "上下文 <b>32K</b>" in params
    assert "KV <b>q8_0</b>" in params
    assert "并行 <b>2</b>" in params
    assert "调优 <b>并发吞吐</b>" in params

    perf = recommendation_perf_html(recommendation)
    assert "解码 <b>42</b> tok/s" in perf
    assert "预填充 <b>16.2k</b> tok/s" in perf
    assert "<b>实测</b>" in perf
