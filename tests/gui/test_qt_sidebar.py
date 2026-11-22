"""``fcmd.gui.sidebar`` 的离屏测试：宽度铺满、折叠 / 展开、快捷键与提示。

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

from fcmd.gui.sidebar import BADGE_SIZE, COLLAPSED_WIDTH, EXPANDED_WIDTH, SidebarNav


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
    """页按钮横向铺满侧栏（左右不留边距），底部按钮也一样。"""
    application = QApplication.instance() or QApplication([])
    button = nav.button("run")
    assert button is not None
    assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert button.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    assert button.minimumHeight() == 0
    nav.show()
    application.processEvents()
    assert nav.width() == EXPANDED_WIDTH
    assert button.width() == nav.width()
    assert nav.toggle_button.width() == nav.width()


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


def test_nav_badge_shows_count_and_repositions_on_collapse() -> None:
    """侧边栏页按钮的数量徽标：半透明圆点、折叠 / 展开重新摆放、清零收起。"""
    app = QApplication.instance() or QApplication([])
    nav = SidebarNav(title="llmw")
    try:
        nav.add("run", "运行", icon="run")
        nav.show()
        app.processEvents()
        nav.set_badge("run", 12)
        badge = nav.badge_label("run")
        assert badge is not None and badge.isVisible()
        assert badge.objectName() == "NavBadge"
        assert badge.text() == "12"
        assert nav.badge("run") == 12
        # 点击穿透：徽标不拦截鼠标
        assert badge.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        expanded_x = badge.x()

        nav.set_collapsed(True)
        app.processEvents()
        assert badge.x() != expanded_x  # 折叠态贴右上角
        nav.set_collapsed(False)
        app.processEvents()
        assert badge.x() == expanded_x

        nav.set_badge("run", 0)
        assert nav.badge("run") == 0
        assert badge.isHidden() is True
    finally:
        nav.deleteLater()


def test_nav_badge_repositions_after_button_layout() -> None:
    """显示窗口前设置的徽标也要贴右端：按钮被布局拉宽后跟随重摆。"""
    app = QApplication.instance() or QApplication([])
    nav = SidebarNav(title="llmw")
    try:
        nav.add("run", "运行", icon="run")
        nav.set_badge("run", 1)  # 布局前设置：按钮还是默认宽度
        nav.resize(EXPANDED_WIDTH, 320)
        nav.show()
        app.processEvents()
        button = nav.button("run")
        badge = nav.badge_label("run")
        assert button is not None and badge is not None
        assert button.width() == EXPANDED_WIDTH
        assert badge.x() == EXPANDED_WIDTH - BADGE_SIZE - 12
    finally:
        nav.deleteLater()


def test_collapsed_keeps_icon_and_shows_expand_button() -> None:
    """折叠后保留应用图标（居中），并在图标下方给出始终可见的展开按钮。"""
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
        assert not nav.toggle_button.isHidden()
        assert nav.toggle_button.geometry().top() >= nav.icon_label.geometry().bottom()
        assert nav.toggle_button.icon().isNull() is False
        assert "展开侧边栏" in nav.icon_label.toolTip()
        assert "展开侧边栏" in nav.toggle_button.toolTip()
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
        # 收起后按钮回到侧栏底部，与页按钮同款整行按钮且不带文字。
        assert nav.toggle_button.text() == ""
        last_button = nav.button("overview")
        assert last_button is not None
        assert nav.toggle_button.geometry().top() > last_button.geometry().bottom()
        # 与页按钮同款整行按钮：横向铺满、同一个 objectName 尺寸契约。
        assert nav.toggle_button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    finally:
        nav.deleteLater()


def test_toggle_sits_at_sidebar_bottom(nav: Any) -> None:
    """折叠 / 展开入口固定在侧栏底部，不占标题 / LOGO 所在行。"""
    application = QApplication.instance() or QApplication([])
    nav.show()
    application.processEvents()
    assert nav.toggle_button.text() == ""
    last_button = nav.button("settings")
    assert last_button is not None
    assert nav.toggle_button.geometry().top() > last_button.geometry().bottom()
    assert nav.toggle_button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert nav.toggle_button.width() == nav.width()
    nav.set_collapsed(True)
    application.processEvents()
    assert nav.toggle_button.text() == ""
    assert nav.toggle_button.geometry().top() > last_button.geometry().bottom()
    assert nav.toggle_button.width() == nav.width()
    assert last_button.width() == nav.width()


def test_page_icon_is_shown_and_collapses_to_icon_only() -> None:
    """登记图标后：展开态图标 + 文字，折叠态只留图标（不再显示首字）。"""
    application = QApplication.instance() or QApplication([])
    nav = SidebarNav(title="LLM")
    try:
        nav.add("chat", "对话", icon="chat")
        button = nav.button("chat")
        assert button is not None
        assert button.icon().isNull() is False
        assert button.text() == "对话"
        nav.set_collapsed(True)
        application.processEvents()
        assert button.text() == ""
        assert button.icon().isNull() is False
        nav.set_collapsed(False)
        assert button.text() == "对话"
    finally:
        nav.deleteLater()


def _icon_color(button: Any) -> str:
    image = button.icon().pixmap(16, 16).toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                return color.name()
    return ""


def test_page_icon_tone_follows_selection() -> None:
    """页图标与页名同色：选中页 accent，其余 muted。"""
    from fcmd.gui.style import active_theme

    QApplication.instance() or QApplication([])
    nav = SidebarNav(title="LLM")
    try:
        nav.add("overview", "概览", icon="overview")
        nav.add("run", "运行", icon="run")
        palette = active_theme().palette
        overview = nav.button("overview")
        run = nav.button("run")
        assert overview is not None and run is not None
        assert _icon_color(overview) == palette.accent
        assert _icon_color(run) == palette.muted
        nav.select("run")
        assert _icon_color(run) == palette.accent
        assert _icon_color(overview) == palette.muted
    finally:
        nav.deleteLater()
