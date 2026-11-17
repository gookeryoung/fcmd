"""paramcadw Qt 窗口的离屏冒烟测试：页签 / 分栏 / 绘制 / 设置回写。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.paramcad._paramcadw_qt import ParamcadWindow
from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("paramcadw-test")
    return base


def test_window_builds_with_tabs_and_splitter(home: Path) -> None:
    """三个预览页 + 摘要分栏，文件动作插在退出之前。"""
    window = ParamcadWindow()
    try:
        assert window.windowTitle().startswith("参数化建模预览")
        assert [window._tabs.tabText(index) for index in range(window._tabs.count())] == [
            "二维截面",
            "轴向剖面",
            "分布曲线",
        ]
        assert window._splitter.count() == 2
        file_actions = [action.text() for action in window._menu("文件").actions()]
        assert file_actions.index("导出模型…") < file_actions.index("退出")
        assert window.status_text().startswith("载入模型或示例")
    finally:
        window.deleteLater()


def test_load_example_draws_all_pages(home: Path) -> None:
    """载入示例后三页都能离屏绘制，摘要写入参数。"""
    window = ParamcadWindow()
    try:
        window.show()
        window._load_example()
        assert "示例（内存）" in window.status_text()
        assert window._summary.text().strip()
        for canvas in (window._section_canvas, window._axial_canvas, window._curve_canvas):
            assert not canvas.grab().isNull()
    finally:
        window.deleteLater()


def test_close_persists_splitter_and_geometry(home: Path) -> None:
    """关闭时写回分栏比例与 geometry。"""
    window = ParamcadWindow()
    try:
        window.show()
        window._splitter.setSizes([700, 300])
        expected = window._splitter.sizes()
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("paramcad").read_text(encoding="utf-8"))
    assert raw["splitter"] == expected
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]
