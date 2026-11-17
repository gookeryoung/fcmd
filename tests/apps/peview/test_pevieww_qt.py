"""pevieww Qt 窗口的离屏冒烟测试：菜单 / 结构树 / 联动 / 设置回写。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.peview._pevieww_qt import PeviewWindow
from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("pevieww-test")
    return base


def test_window_builds_with_menus_and_splitter(home: Path) -> None:
    """骨架菜单保留，文件动作插在退出之前，最近打开为空时占位。"""
    window = PeviewWindow()
    try:
        assert [action.text() for action in window.menuBar().actions()] == ["文件", "视图", "帮助"]
        file_actions = [action.text() for action in window._menu("文件").actions()]
        assert file_actions.index("打开…") < file_actions.index("最近打开") < file_actions.index("退出")
        assert window._splitter.count() == 2
        assert window.status_text().startswith("就绪")
        assert window._recent_menu.actions()[0].isEnabled() is False
    finally:
        window.deleteLater()


def test_load_file_populates_tree_detail_and_hex(home: Path, pe32_file: Path) -> None:
    """加载 PE 后填充结构树、概览、详情与十六进制，并记录最近文件。"""
    window = PeviewWindow()
    try:
        window._load_file(pe32_file)
        assert pe32_file.name in window.windowTitle()
        assert window._tree.topLevelItemCount() >= 10
        current = window._tree.currentItem()
        assert current is not None
        assert "DOS" in current.text(0)
        assert "32 位" in window._summary_text.toPlainText()
        assert window._detail_text.toPlainText()
        assert "00000000" in window._hex_text.toPlainText()
        assert window._path_label.toolTip() == str(pe32_file)
        assert window._recent_menu.actions()[0].text() == pe32_file.name
    finally:
        window.deleteLater()


def test_jump_updates_hex_offset(home: Path, pe32_file: Path) -> None:
    """输入十六进制偏移后跳转，视图与偏移框同步。"""
    window = PeviewWindow()
    try:
        window._load_file(pe32_file)
        window._offset_edit.setText("0x10")
        window._jump()
        assert window._hex_offset == 0x10
        assert window._hex_text.toPlainText().startswith("00000010")
    finally:
        window.deleteLater()


def test_close_persists_file_splitter_and_geometry(home: Path, pe32_file: Path) -> None:
    """关闭时写回最近文件、分栏比例与 geometry。"""
    window = PeviewWindow()
    try:
        window.show()
        window._load_file(pe32_file)
        window._splitter.setSizes([360, 820])
        expected = window._splitter.sizes()
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("pevieww").read_text(encoding="utf-8"))
    assert raw["file_path"] == str(pe32_file)
    assert raw["recent_files"][0] == str(pe32_file)
    assert raw["splitter"] == expected
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_splitter_sizes_restored_on_next_start(home: Path) -> None:
    """下次启动按记忆的分栏比例恢复。"""
    path = _guiconfig.config_path("pevieww")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"splitter": [640, 360]}), encoding="utf-8")
    window = PeviewWindow()
    try:
        window.show()
        sizes = window._splitter.sizes()
        assert sizes[0] > sizes[1]
        assert abs(sizes[0] / sum(sizes) - 640 / 1000) < 0.02
    finally:
        window.deleteLater()
