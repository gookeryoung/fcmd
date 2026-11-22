"""parameshw Qt 窗口的离屏冒烟测试：菜单 / 分栏 / 画布 / 设置回写。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.paramesh._parameshw_qt import ParameshWindow
from fcmd.gui import config as _guiconfig
from fcmd.gui.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("parameshw-test")
    return base


def test_window_builds_with_menus_and_splitter(home: Path) -> None:
    """骨架菜单保留，文件 / 视图动作插在退出与主题之前，画布与摘要分栏。"""
    window = ParameshWindow()
    try:
        assert [action.text() for action in window.menuBar().actions()] == ["文件", "视图", "帮助"]
        file_actions = [action.text() for action in window._menu("文件").actions()]
        assert file_actions.index("打开模型…") < file_actions.index("退出")
        view_actions = [action.text() for action in window._menu("视图").actions()]
        assert view_actions.index("适应窗口") < view_actions.index("主题")
        assert window._splitter.count() == 2
        assert window._level_box.count() == 3
        assert window.status_text().startswith("载入模型或示例")
    finally:
        window.deleteLater()


def test_load_example_renders_canvas_and_summary(home: Path) -> None:
    """载入示例后生成网格、写入摘要并完成一次离屏绘制。"""
    window = ParameshWindow()
    try:
        window.show()
        window._load_example()
        assert "示例（内存）" in window.status_text()
        summary = window._summary.text()
        assert "质量报告" in summary
        assert window._frame is not None
        pixmap = window._canvas.grab()
        assert not pixmap.isNull()
    finally:
        window.deleteLater()


def test_close_persists_level_splitter_and_geometry(home: Path) -> None:
    """关闭时写回档位、分栏比例与 geometry。"""
    window = ParameshWindow()
    try:
        window.show()
        window._level_box.setCurrentText("fine")
        window._splitter.setSizes([800, 300])
        expected = window._splitter.sizes()
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("paramesh").read_text(encoding="utf-8"))
    assert raw["level"] == "fine"
    assert raw["splitter"] == expected
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_splitter_sizes_restored_on_next_start(home: Path) -> None:
    """下次启动按记忆的分栏比例恢复。"""
    path = _guiconfig.config_path("paramesh")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"splitter": [640, 360]}), encoding="utf-8")
    window = ParameshWindow()
    try:
        window.show()
        sizes = window._splitter.sizes()
        assert sizes[0] > sizes[1]
        assert abs(sizes[0] / sum(sizes) - 640 / 1000) < 0.02
    finally:
        window.deleteLater()
