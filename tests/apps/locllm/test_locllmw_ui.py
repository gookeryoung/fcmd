"""`_locllmw_ui` 的扫描分类与 Qt 概览页的已加载服务 / 推荐动作。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.locllm import _locllmw_ui, llmmodel
from fcmd.apps.locllm._llmgguf import GgufPrecision
from fcmd.apps.locllm._llmgguf_cache import PrecisionCache
from fcmd.apps.locllm._locllmw_recommend import Recommendation
from fcmd.apps.locllm.llmbase import DEFAULT_PROFILE, ModelEntry, ServeState


def test_scan_dir_reports_missing_directory(tmp_path: Path) -> None:
    entries, issue = _locllmw_ui._scan_dir(tmp_path / "missing")
    assert entries == []
    assert issue == f"模型目录不存在：{tmp_path / 'missing'}"


def test_scan_dir_reports_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> list[Any]:
        raise PermissionError("denied")

    monkeypatch.setattr(llmmodel, "scan_models", boom)
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == []
    assert issue == f"没有权限读取模型目录：{tmp_path}"


def test_scan_dir_reports_other_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_root: Path) -> list[Any]:
        raise OSError("disk error")

    monkeypatch.setattr(llmmodel, "scan_models", boom)
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == []
    assert issue == "扫描模型目录失败：disk error"


def test_scan_dir_leaves_empty_directory_to_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llmmodel, "scan_models", lambda _root: [])
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries == []
    assert issue is None


def test_scan_dir_returns_entries_without_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = ["entry"]
    monkeypatch.setattr(llmmodel, "scan_models", lambda _root: sentinel)
    entries, issue = _locllmw_ui._scan_dir(tmp_path)
    assert entries is sentinel
    assert issue is None


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture()
def overview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """离屏概览页：不探测硬件 / 不扫描磁盘，只验证已加载服务与推荐动作。"""
    from fcmd.apps.locllm._locllmw_qt_overview import OverviewPage
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState
    from fcmd.gui.qt.app import create_application

    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    create_application("locllmw-overview-test")
    state = LocllmState(tmp_path / "ui" / "locllmw.json")
    page = OverviewPage(state)
    yield state, page
    page.deleteLater()


def _state(name: str) -> Any:
    return ServeState(
        name=name,
        pid=1,
        create_time=0.0,
        host="127.0.0.1",
        port=8080,
        model="m.gguf",
        profile=DEFAULT_PROFILE,
        context=8192,
        engine="cpu",
        alias="model",
        api_key=f"sk-{name}",
        log=f"{name}.log",
        started_at="",
    )


def test_loaded_actions_require_selection(overview: Any) -> None:
    _state_obj, page = overview
    page.set_services([_state("a")])
    page._open_web_ui()
    page._show_logs()
    page._stop_selected()
    page._copy("address")
    assert page._loaded_hint.text() == "先在列表中选择一个运行中的服务"


def test_loaded_actions_use_selected_state(overview: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _state_obj, page = overview
    opened: list[str] = []
    stopped: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    from fcmd.apps.locllm import _locllmw_qt_overview as qt_overview

    monkeypatch.setattr(qt_overview, "submit", lambda fn, *args, **kwargs: fn(*args))
    from fcmd.apps.locllm import llmserve

    monkeypatch.setattr(llmserve, "stop_server", stopped.append)
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    page.set_services([_state("a"), _state("b")])
    page._loaded_table.selectRow(1)
    page._open_web_ui()
    page._stop_selected()
    assert opened == ["http://127.0.0.1:8080"]
    assert stopped == ["b"]


def test_loaded_copy_writes_clipboard_and_status(overview: Any) -> None:
    from PySide6.QtWidgets import QApplication

    state, page = overview
    statuses: list[str] = []
    state.statusText.connect(statuses.append)
    page.set_services([_state("a")])
    page._loaded_table.selectRow(0)
    page._copy("address")
    assert QApplication.clipboard().text() == "http://127.0.0.1:8080"
    assert statuses[-1] == "已复制地址: http://127.0.0.1:8080"
    page._copy("api_key")
    assert QApplication.clipboard().text() == "sk-a"
    assert "已复制 API Key" in statuses[-1]


def _recommendation(tmp_path: Path) -> Recommendation:
    path = tmp_path / "coder.gguf"
    path.write_bytes(b"")
    return Recommendation(
        model=ModelEntry(root=tmp_path, path=path, size=1024, tags=("代码",)),
        profile="coder",
        context=8192,
        kv_type="q8_0",
        batch=4096,
        ubatch=2048,
        parallel=1,
        tuning="自动 → 默认（现状）",
        decode_tps=10.0,
        prompt_tps=20.0,
        measured=False,
        basis="代码 · 按参考机器外推",
    )


def test_apply_recommendation_emits_and_navigates(overview: Any, tmp_path: Path) -> None:
    state, page = overview
    row = _recommendation(tmp_path)
    page._recommendations = (row,)
    page._recommend_table.setRowCount(1)
    from fcmd.gui.qt.widgets import make_table_item

    for column, text in enumerate(("coder", "8K", "q8_0", "预测")):
        page._recommend_table.setItem(0, column, make_table_item(text))
    applied: list[Any] = []
    navigated: list[str] = []
    state.recommendationApplied.connect(applied.append)
    state.navigateRequested.connect(navigated.append)
    page._recommend_table.selectRow(0)
    page.apply_recommendation()
    assert applied == [row]
    assert navigated == ["run"]


def test_apply_recommendation_requires_selection(overview: Any) -> None:
    _state_obj, page = overview
    page._recommendations = ()
    page.apply_recommendation()
    assert "先在推荐表中选择一行" in page._recommend_status.text()


def _entries(tmp_path: Path, count: int) -> list[ModelEntry]:
    entries = []
    for index in range(count):
        path = tmp_path / f"model-{index}.gguf"
        path.write_bytes(b"x" * (index + 1))
        entries.append(ModelEntry(root=tmp_path, path=path, size=path.stat().st_size))
    return entries


def test_detect_precisions_preserves_order_and_reuses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _entries(tmp_path, 6)
    calls: list[str] = []

    def fake(path: Path, *, tensors: bool = True) -> GgufPrecision:
        assert tensors is False
        calls.append(path.name)
        return GgufPrecision(label=path.stem, quantized=True, source="filename")

    monkeypatch.setattr(_locllmw_ui, "detect_precision", fake)
    cache = PrecisionCache(tmp_path / "cache.json")
    first = _locllmw_ui._detect_precisions(entries, cache=cache)
    assert list(first) == [_locllmw_ui.llmenv.path_key(entry.path) for entry in entries]
    key = _locllmw_ui.llmenv.path_key(entries[0].path)
    assert first[key] == GgufPrecision(label="model-0", quantized=True, source="filename")
    assert len(calls) == 6

    calls.clear()
    second = _locllmw_ui._detect_precisions(entries, cache=PrecisionCache(tmp_path / "cache.json"))
    assert second == first
    assert calls == []


def _model(root: Path, relative: str) -> ModelEntry:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return ModelEntry(root=root, path=path, size=1)


def test_group_name_uses_first_directory(tmp_path: Path) -> None:
    root = tmp_path / "models"
    assert _locllmw_ui._group_name(_model(root, "Qwen/a.gguf"), root) == "Qwen"
    assert _locllmw_ui._group_name(_model(root, "top.gguf"), root) == _locllmw_ui._GROUP_ROOT_LABEL
    outside = _model(tmp_path / "other", "b.gguf")
    assert _locllmw_ui._group_name(outside, root) == "other"


def test_group_models_keeps_input_order_and_indexes(tmp_path: Path) -> None:
    root = tmp_path / "models"
    entries = [
        _model(root, "b/x.gguf"),
        _model(root, "a/y.gguf"),
        _model(root, "b/z.gguf"),
    ]
    groups = dict(_locllmw_ui._group_models(entries, root))
    assert list(groups) == ["b", "a"]
    assert [index for index, _entry in groups["b"]] == [0, 2]
    assert [index for index, _entry in groups["a"]] == [1]


def test_default_open_groups_only_running_and_selected(tmp_path: Path) -> None:
    root = tmp_path / "models"
    running = _model(root, "run/a.gguf")
    selected = _model(root, "pick/b.gguf")
    idle = _model(root, "idle/c.gguf")
    groups = _locllmw_ui._group_models([running, selected, idle], root)
    running_keys = {_locllmw_ui.llmenv.path_key(running.path)}

    assert _locllmw_ui._default_open_groups(groups, running_keys, None) == {"run"}
    assert _locllmw_ui._default_open_groups(groups, set(), selected.path) == {"pick"}
    assert _locllmw_ui._default_open_groups(groups, running_keys, selected.path) == {"run", "pick"}
