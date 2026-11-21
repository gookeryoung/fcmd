"""``fcmd.gui.qt.sidebar`` 的离屏测试：宽度铺满、折叠 / 展开、快捷键与提示。

Qt 不可用时整体跳过（CI 只装 ``--extra test``，不含 PySide6）。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QSizePolicy

from fcmd.gui.qt.sidebar import COLLAPSED_WIDTH, EXPANDED_WIDTH, SidebarNav


@pytest.fixture()
def nav() -> Any:
    QApplication.instance() or QApplication([])
    widget = SidebarNav(title="llm")
    widget.add("overview", "概览", hint="服务状态与快捷入口")
    widget.add("run", "运行", hint="档位、上下文与启动参数")
    widget.add("settings", "设置", hint="存储、引擎与关于")
    yield widget
    widget.deleteLater()


def test_pages_are_registered_in_order(nav: Any) -> None:
    assert nav.keys() == ("overview", "run", "settings")
    assert nav.selected() == "overview"
    assert nav.button("overview") is not None and nav.button("overview").isChecked()


def test_buttons_expand_to_fill_sidebar_width(nav: Any) -> None:
    """页按钮横向铺满侧栏；纵向高度交给共享样式统一，不在按钮上单独写死。"""
    button = nav.button("run")
    assert button is not None
    assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert button.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    assert button.minimumHeight() == 0
    assert nav.width() == EXPANDED_WIDTH


def test_collapse_and_expand(nav: Any) -> None:
    events: list[bool] = []
    nav.collapsedChanged.connect(events.append)
    nav.set_collapsed(True)
    assert nav.is_collapsed()
    assert nav.width() == COLLAPSED_WIDTH
    assert nav.title_label.isHidden()
    for key, label in (("overview", "概览"), ("run", "运行"), ("settings", "设置")):
        button = nav.button(key)
        assert button is not None
        assert button.text() == label[:1]
        assert label in button.toolTip()
    nav.set_collapsed(True)  # 幂等，不重复发信号
    nav.set_collapsed(False)
    assert not nav.is_collapsed()
    assert nav.width() == EXPANDED_WIDTH
    assert not nav.title_label.isHidden()
    assert nav.button("run").text() == "运行"
    assert events == [True, False]


def test_toggle_flips_state(nav: Any) -> None:
    nav.toggle()
    assert nav.is_collapsed()
    nav.toggle()
    assert not nav.is_collapsed()


def test_add_while_collapsed_uses_short_label(nav: Any) -> None:
    nav.set_collapsed(True)
    nav.add("models", "模型", hint="GGUF 模型")
    assert nav.button("models").text() == "模"
    assert "GGUF 模型" in nav.button("models").toolTip()


def test_select_and_relative_cycle(nav: Any) -> None:
    changed: list[str] = []
    nav.changed.connect(changed.append)
    assert nav.select("run") == "run"
    assert nav.select_relative(1) == "settings"
    assert nav.select_relative(1) == "overview"
    assert nav.select_relative(-1) == "settings"
    assert changed[-1] == "settings"
    assert nav.select(0) == "overview"


def test_select_unknown_key_raises(nav: Any) -> None:
    with pytest.raises(KeyError):
        nav.select("missing")
    with pytest.raises(KeyError):
        nav.select(99)


def test_add_duplicate_key_raises(nav: Any) -> None:
    with pytest.raises(KeyError):
        nav.add("run", "重复")


def test_set_hint_updates_tooltips_only(nav: Any) -> None:
    """页面提示不再在侧边栏底部显示，只同步按钮 tooltip。"""
    nav.set_hint("overview", "新的提示")
    assert nav.hint("overview") == "新的提示"
    button = nav.button("overview")
    assert button is not None
    assert button.toolTip() == "新的提示"
    assert not hasattr(nav, "hint_label")


def test_collapsed_without_icon_keeps_toggle_to_expand(nav: Any) -> None:
    """没有应用图标时折叠按钮保留为展开入口，避免只能靠快捷键 / 菜单展开。"""
    application = QApplication.instance() or QApplication([])
    nav.add("models", "模型")
    nav.show()
    application.processEvents()
    nav.set_collapsed(True)
    application.processEvents()
    assert nav.toggle_button.isHidden() is False
    assert nav.toggle_button.icon().isNull() is False
    assert nav.toggle_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert "展开侧边栏" in nav.toggle_button.toolTip()
    nav.toggle_button.click()
    assert not nav.is_collapsed()
    assert not nav.title_label.isHidden()
    assert nav.toggle_button.icon().isNull() is False


def test_collapsed_keeps_icon_and_click_expands() -> None:
    """折叠后保留应用图标（居中）并作为展开入口，折叠按钮隐藏。"""
    application = QApplication.instance() or QApplication([])
    pixmap = QPixmap(24, 24)
    pixmap.fill(QColor("#336699"))
    nav = SidebarNav(title="LLM", icon=QIcon(pixmap))
    try:
        nav.add("overview", "概览")
        nav.show()
        application.processEvents()
        nav.set_collapsed(True)
        application.processEvents()
        assert nav.icon_label is not None
        assert not nav.icon_label.isHidden()
        assert nav.icon_label.geometry().center().x() == nav.rect().center().x()
        assert nav.toggle_button.isHidden()
        assert "展开侧边栏" in nav.icon_label.toolTip()
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        assert nav.eventFilter(nav.icon_label, event) is True
        assert not nav.is_collapsed()
        assert not nav.toggle_button.isHidden()
    finally:
        nav.deleteLater()
