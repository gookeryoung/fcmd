"""`_locllmw_ui` 的扫描分类、已加载服务动作与推荐配置带入。"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, override

import pytest

from fcmd.apps.locllm import _llmtune, _locllmw_ui, llmmodel
from fcmd.apps.locllm._llmgguf import GgufPrecision
from fcmd.apps.locllm._llmgguf_cache import PrecisionCache
from fcmd.apps.locllm._locllmw_overview import _LocllmWindowOverview
from fcmd.apps.locllm._locllmw_recommend import Recommendation
from fcmd.apps.locllm.llmbase import ModelEntry


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


class _Var:
    """替代 Tk StringVar 的最小读写对象。"""

    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _Tree:
    def __init__(self, selection: tuple[str, ...] = ()) -> None:
        self._selection = selection

    def selection(self) -> tuple[str, ...]:
        return self._selection


class _Clipboard:
    def __init__(self) -> None:
        self.text = ""

    def clipboard_clear(self) -> None:
        self.text = ""

    def clipboard_append(self, text: str) -> None:
        self.text = text


class _LoadedHost(_locllmw_ui._LocllmWindowUI):
    """只装配已加载服务动作所需成员，不创建 Tk 控件。"""

    def __init__(self, states: list[Any], selection: tuple[str, ...] = ()) -> None:
        self._loaded_states = states
        self._loaded_tree = _Tree(selection)
        self._loaded_var = _Var()
        self._name_var = _Var("old")
        self._status_var = _Var()
        self.root = _Clipboard()
        self.events: list[str] = []

    @override
    def _open_browser(self) -> None:
        self.events.append("open")

    @override
    def _show_logs(self) -> None:
        self.events.append("logs")

    @override
    def _stop_selected(self) -> None:
        self.events.append("stop")

    @override
    def _log(self, message: str) -> None:
        self.events.append(message)


def _state(name: str) -> Any:
    return types.SimpleNamespace(name=name, base_url=f"http://127.0.0.1/1/{name}", api_key=f"sk-{name}")


def test_loaded_actions_require_selection() -> None:
    host = _LoadedHost([_state("a")])
    host._loaded_open()
    host._loaded_logs()
    host._loaded_stop()
    host._loaded_copy_address()
    assert host.events == []
    assert host._loaded_var.get() == "先在列表中选择一个运行中的服务"


def test_loaded_actions_use_selected_state() -> None:
    host = _LoadedHost([_state("a"), _state("b")], selection=("1",))
    host._loaded_open()
    host._loaded_logs()
    host._loaded_stop()
    assert host._name_var.get() == "b"
    assert host.events == ["open", "logs", "stop"]


def test_loaded_copy_writes_clipboard_and_status() -> None:
    host = _LoadedHost([_state("a")], selection=("0",))
    host._loaded_copy_address()
    assert host.root.text == "http://127.0.0.1/1/a"
    assert host._status_var.get() == "已复制地址: http://127.0.0.1/1/a"
    host._loaded_copy_key()
    assert host.root.text == "sk-a"
    assert "已复制 API Key" in host._status_var.get()


class _RecoHost(_LocllmWindowOverview):
    """只装配推荐带入运行页所需成员。"""

    def __init__(self, rows: tuple[Recommendation, ...], selection: tuple[str, ...] = ("0",)) -> None:
        self._recommendations = rows
        self._recommend_tree = _Tree(selection)
        self._recommend_status = _Var()
        self._profile_var = _Var()
        self._ctx_var = _Var()
        self._kv_var = _Var()
        self._tune_var = _Var()
        self._status_var = _Var()
        self._params_box = object()
        self.selected: Path | None = None
        self.events: list[Any] = []
        self._nav = types.SimpleNamespace(select=lambda key: self.events.append(("nav", key)))

    @override
    def _select_model(self, path: Path, **_kwargs: Any) -> None:
        self.selected = path
        self.events.append(("model", str(path)))

    @override
    def _on_tune_changed(self, _event: object | None = None) -> None:
        self._kv_var.set("q4_0")  # 模拟策略把 KV 同步成自己的值，验证推荐值最后覆盖
        self.events.append("tune")

    @override
    def _on_profile_changed(self, _event: object | None = None) -> None:
        self.events.append("profile")

    @override
    def _scroll_to_widget(self, key: str, _widget: Any, /) -> None:
        self.events.append(("scroll", key))


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


def test_apply_recommendation_prefills_run_page(tmp_path: Path) -> None:
    row = _recommendation(tmp_path)
    host = _RecoHost((row,))
    host._apply_recommendation()
    assert host.selected == row.model.path
    assert host._profile_var.get() == "coder"
    assert host._ctx_var.get() == "8192"
    assert host._kv_var.get() == "q8_0"
    assert host._tune_var.get() == _llmtune.TUNE_LABELS["auto"]
    assert host.events[-2:] == [("nav", "run"), ("scroll", "run")]
    assert "已带入推荐配置" in host._status_var.get()


def test_apply_recommendation_requires_selection(tmp_path: Path) -> None:
    host = _RecoHost((_recommendation(tmp_path),), selection=())
    host._apply_recommendation()
    assert host.selected is None
    assert "先在推荐表中选择一行" in host._recommend_status.get()


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
