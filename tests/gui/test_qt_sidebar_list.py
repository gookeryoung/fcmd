"""公共侧边栏列表：分组、key 选择、两行高度与选中态绘制。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from fcmd.gui import theme as _guitheme
from fcmd.gui.widgets import SidebarEntry, SidebarList


@pytest.fixture(scope="module", autouse=True)
def _app() -> object:
    app = QApplication.instance() or QApplication([])
    from fcmd.gui import style as _qt_theme

    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return app


def test_entries_group_headers_and_keys() -> None:
    app = QApplication.instance()
    assert app is not None
    widget = SidebarList()
    try:
        widget.set_entries(
            [
                SidebarEntry("第一个对话", key=1, section="今天"),
                SidebarEntry("第二个对话", key=2, section="今天"),
                SidebarEntry("更早的对话", key=3, section="更早"),
            ]
        )
        app.processEvents()
        # 2 个分组标题 + 3 条对话
        assert widget.count() == 5
        headers = [widget.item(row) for row in (0, 3)]
        assert all(item is not None and not (item.flags() & Qt.ItemFlag.ItemIsSelectable) for item in headers)
        widget.set_current_key(2)
        assert widget.current_key() == 2
    finally:
        widget.deleteLater()


def test_subtitle_row_is_taller() -> None:
    app = QApplication.instance()
    assert app is not None
    widget = SidebarList()
    try:
        widget.set_entries(
            [
                SidebarEntry("单行", key=1),
                SidebarEntry("两行", key=2, subtitle="副标题", meta="12:30"),
            ]
        )
        app.processEvents()
        assert widget.item(0) is not None and widget.item(1) is not None
        assert widget.sizeHintForRow(0) < widget.sizeHintForRow(1)
    finally:
        widget.deleteLater()


def test_selected_row_paints_rounded_background() -> None:
    app = QApplication.instance()
    assert app is not None
    widget = SidebarList()
    try:
        widget.resize(260, 120)
        widget.set_entries([SidebarEntry("对话 A", key=1), SidebarEntry("对话 B", key=2)])
        widget.show()
        app.processEvents()
        plain = widget.viewport().grab().toImage()
        widget.set_current_key(2)
        app.processEvents()
        selected = widget.viewport().grab().toImage()
        assert selected != plain
    finally:
        widget.deleteLater()
