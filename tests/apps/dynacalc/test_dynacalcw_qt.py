"""dynacalcw Qt 窗口的离屏冒烟测试：表单 / 预览 / 后台生成事件 / 设置回写。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.dynacalc._dynacalcw_qt import DynacalcWindow, _work_generate
from fcmd.apps.dynacalc.dynacalcw import config_path
from fcmd.gui.qt.app import create_application
from fcmd.gui.qt.workers import EventBridge


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("dynacalcw-test")
    return base


def test_window_builds_with_defaults(home: Path) -> None:
    """默认设置回填到表单与页签。"""
    window = DynacalcWindow()
    try:
        assert window._ncpu.value() == 4
        assert window._concurrency.value() == 1
        assert window._timeout.value() == 3600
        assert window._retries.value() == 0
        assert window._fake.isChecked()
        assert window._tabs.count() == 3
        assert window._case_table.columnCount() == 4
        assert window._job_table.columnCount() == 5
        assert window.status_text().startswith("就绪")
    finally:
        window.deleteLater()


def test_settings_restored(home: Path) -> None:
    """启动时回填路径、求解参数与假求解器开关。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "params": "p.yaml",
                "model": "m.yaml",
                "out": "out",
                "solver": "solver.exe",
                "mesh_root": "meshes",
                "mesh_level": "coarse",
                "ncpu": 16,
                "concurrency": 4,
                "timeout_s": 600,
                "retries": 2,
                "fake": False,
            }
        ),
        encoding="utf-8",
    )
    window = DynacalcWindow()
    try:
        assert window._params.value() == "p.yaml"
        assert window._out.value() == "out"
        assert window._solver.value() == "solver.exe"
        assert window._mesh_level.text() == "coarse"
        assert window._ncpu.value() == 16
        assert window._concurrency.value() == 4
        assert not window._fake.isChecked()
    finally:
        window.deleteLater()


def test_preview_fills_case_table(home: Path, small_params: Path, tmp_path: Path) -> None:
    """预览清单直接在 GUI 线程填表，不需要后台线程。"""
    window = DynacalcWindow()
    try:
        window._params.edit.setText(str(small_params))
        window._out.edit.setText(str(tmp_path / "out"))
        window._preview()
        assert window._case_table.rowCount() == 4
        first = window._case_table.item(0, 0)
        assert first is not None
        assert first.text() == "M001"
        assert "4 个工况" in window.status_text()
        assert window._tabs.currentIndex() == 0
    finally:
        window.deleteLater()


def test_long_path_hint_keeps_full_text(home: Path, tmp_path: Path) -> None:
    """长路径在省略标签中保留完整文本（仅在显示层省略）。"""
    long_path = tmp_path / ("very-long-directory-" * 5) / "params.yaml"
    window = DynacalcWindow()
    try:
        window._params.edit.setText(str(long_path))
        assert window._params.hint.fullText() == str(long_path)
    finally:
        window.deleteLater()


def test_close_persists_form(home: Path, tmp_path: Path) -> None:
    """关闭时写回路径、数值参数与 geometry。"""
    window = DynacalcWindow()
    try:
        window.show()
        window._params.edit.setText("p.yaml")
        window._out.edit.setText("out")
        window._ncpu.setValue(8)
        window._fake.setChecked(False)
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert raw["params"] == "p.yaml"
    assert raw["out"] == "out"
    assert raw["ncpu"] == 8
    assert raw["fake"] is False
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_worker_generate_emits_cases_and_summary(home: Path, small_params: Path, tmp_path: Path) -> None:
    """后台生成函数依次发出清单与摘要事件。"""
    events: list[tuple[Any, ...]] = []
    bridge = EventBridge()
    bridge.event.connect(events.append)
    _work_generate(small_params, tmp_path / "out", None, [], [], None, "", bridge)
    assert events[0][0] == "cases"
    assert len(events[0][1].cases) == 4
    assert events[-1][0] == "text"
    assert "已生成 4 个工况" in events[-1][1]
