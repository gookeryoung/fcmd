"""`_locllmw_ui` 的扫描分类与 Qt 概览页的推荐动作。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.locllm import _locllmw_ui, llmmodel
from fcmd.apps.locllm._llmgguf import GgufPrecision
from fcmd.apps.locllm._llmgguf_cache import PrecisionCache
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


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture()
def overview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """离屏概览页：不探测硬件 / 不扫描磁盘，只验证推荐动作。"""
    from fcmd.apps.locllm._locllmw_qt_overview import OverviewPage
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState
    from fcmd.gui.qt.app import create_application

    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    create_application("locllmw-overview-test")
    state = LocllmState(tmp_path / "ui" / "locllmw.json")
    page = OverviewPage(state)
    yield state, page
    page.deleteLater()


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
        score=78,
        score_parts=(28, 25, 15, 10),
    )


def test_apply_recommendation_emits_and_navigates(overview: Any, tmp_path: Path) -> None:
    state, page = overview
    row = _recommendation(tmp_path)
    page._recommendations = (row,)
    page._render_recommendations()
    applied: list[Any] = []
    navigated: list[str] = []
    state.recommendationApplied.connect(applied.append)
    state.navigateRequested.connect(navigated.append)
    page._recommend_list.setCurrentRow(0)
    page.apply_recommendation()
    assert applied == [row]
    assert navigated == ["run"]


def test_apply_recommendation_requires_selection(overview: Any) -> None:
    _state_obj, page = overview
    page._recommendations = ()
    page.apply_recommendation()
    assert "先在推荐列表中选择一行" in page._recommend_status.text()


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


def test_recommendation_list_uses_custom_row(overview: Any, tmp_path: Path) -> None:
    """推荐区使用自定义行：模型名独立大字标签，参数 / 性能作为小字补充。"""
    from PySide6.QtWidgets import QLabel

    from fcmd.apps.locllm._locllmw_qt_overview import RecommendationRow

    _state_obj, page = overview
    row = _recommendation(tmp_path)
    page._recommendations = (row,)
    page._render_recommendations()

    item = page._recommend_list.item(0)
    assert item is not None
    card = page._recommend_list.itemWidget(item)
    assert isinstance(card, RecommendationRow)
    name = card.findChild(QLabel, "RecommendationName")
    assert name is not None
    text = name.fullText() if hasattr(name, "fullText") else name.text()
    assert text == row.model.path.name
    assert page._recommend_list.currentRow() == 0
    assert card.property("selected") == "true"


def test_recommendation_row_shows_score_badge_with_band(overview: Any, tmp_path: Path) -> None:
    """推荐行左侧有圆形评分框，行与评分框的配色档位跟评分一致。"""
    from fcmd.apps.locllm._locllmw_qt_overview import ScoreBadge, score_band_label

    _state_obj, page = overview
    row = _recommendation(tmp_path)
    page._recommendations = (row,)
    page._render_recommendations()

    card = page._recommend_list.itemWidget(page._recommend_list.item(0))
    assert card is not None
    badge = card.findChild(ScoreBadge, "ScoreBadge")
    assert badge is not None
    assert badge.text() == str(row.score)
    assert badge.property("band") == row.score_band == "good"
    assert card.property("band") == row.score_band
    assert score_band_label(row.score_band) == "合适"
    tooltip = card.toolTip()
    assert row.score_text in tooltip
    assert row.score_detail in tooltip


def test_score_badge_band_colors_follow_theme() -> None:
    """评分框与推荐行的配色规则写进主题 QSS，切主题时一起生效。"""
    from fcmd.gui import theme as _guitheme
    from fcmd.gui.qt import theme as _qt_theme

    qss = _qt_theme.stylesheet(_guitheme.resolve_theme(_guitheme.DEFAULT_THEME_NAME))
    for band in ("excellent", "good", "fair", "limited"):
        assert f'QFrame#RecommendationRow[band="{band}"]' in qss
        assert f'QLabel#ScoreBadge[band="{band}"]' in qss
