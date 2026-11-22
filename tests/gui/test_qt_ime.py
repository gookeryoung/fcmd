"""``fcmd.gui.ime``：输入法组合期间不画占位文字（避免与组合串重影）。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.ime import PlaceholderImeGuard, install_placeholder_ime_guard


@pytest.fixture(scope="module", autouse=True)
def _app() -> object:
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return app


def _preedit(text: str) -> QInputMethodEvent:
    return QInputMethodEvent(text, [])


def _commit(text: str) -> QInputMethodEvent:
    event = QInputMethodEvent()
    event.setCommitString(text)
    return event


@pytest.mark.parametrize("widget_type", [QLineEdit, QPlainTextEdit])
def test_placeholder_hides_while_composing_and_comes_back(widget_type: type) -> None:
    """组合期间清空占位文字，提交 / 取消组合后还原（内容非空时由 Qt 自己隐藏）。"""
    app = QApplication.instance()
    guard = install_placeholder_ime_guard(app)
    widget = widget_type()
    try:
        widget.setPlaceholderText("输入问题")
        QApplication.sendEvent(widget, _preedit("nihao"))
        assert widget.placeholderText() == ""
        assert widget.property("fcmdImePlaceholder") == "输入问题"

        QApplication.sendEvent(widget, _commit("你好"))
        assert widget.placeholderText() == "输入问题"

        QApplication.sendEvent(widget, _preedit("shijie"))
        assert widget.placeholderText() == ""
        # 取消组合（不提交内容）同样还原
        QApplication.sendEvent(widget, _preedit(""))
        assert widget.placeholderText() == "输入问题"
        assert guard is install_placeholder_ime_guard(app)  # 幂等：同一个过滤器
    finally:
        widget.deleteLater()


@pytest.mark.parametrize("widget_type", [QLineEdit, QPlainTextEdit])
def test_focus_out_restores_placeholder(widget_type: type) -> None:
    """组合期间失焦（切窗口 / 点别处）也要还原，否则占位文字会一直缺失。"""
    install_placeholder_ime_guard(QApplication.instance())
    widget = widget_type()
    try:
        widget.setPlaceholderText("输入问题")
        QApplication.sendEvent(widget, _preedit("nihao"))
        assert widget.placeholderText() == ""
        QApplication.sendEvent(widget, QEvent(QEvent.Type.FocusOut))
        assert widget.placeholderText() == "输入问题"
    finally:
        widget.deleteLater()


def test_composing_does_not_paint_placeholder() -> None:
    """渲染层面回归：组合串显示时不再叠着一层占位文字（此前是重影）。"""
    install_placeholder_ime_guard(QApplication.instance())
    app = QApplication.instance()

    def render(placeholder: str):
        widget = QPlainTextEdit()
        widget.setPlaceholderText(placeholder)
        widget.setReadOnly(True)  # 只读：不画闪烁光标
        # 焦点边框 / 光标也会造成像素差：两张图都不取焦点，只比较文字区。
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        widget.resize(240, 80)
        widget.show()
        app.processEvents()
        QApplication.sendEvent(widget, _preedit("nihao"))
        app.processEvents()
        image = widget.grab().toImage()
        widget.deleteLater()
        return image

    with_placeholder = render("输入问题，Ctrl+Enter 发送")
    without_placeholder = render("")
    assert with_placeholder == without_placeholder


def test_create_application_installs_guard() -> None:
    """窗口入口统一装好过滤器，新增输入框不需要各自处理。"""
    from fcmd.gui.app import create_application

    app = create_application("ime-test")
    guard = app.property("fcmdPlaceholderImeGuard")
    assert isinstance(guard, PlaceholderImeGuard)
    assert install_placeholder_ime_guard(app) is guard


def test_plain_text_edit_ghosts_without_guard() -> None:
    """回归基线：没有 guard 时 QPlainTextEdit 在组合串下仍画占位文字（重影）。

    ``QLineEdit`` 由 Qt 自己处理组合态，只有多行 ``QPlainTextEdit`` 会叠字；这里把过滤器
    临时摘掉，确认差异真实存在，避免以后把 guard 当成多余代码删掉。
    """
    app = QApplication.instance()
    guard = app.property("fcmdPlaceholderImeGuard")
    app.removeEventFilter(guard)
    try:

        def render(placeholder: str):
            widget = QPlainTextEdit()
            widget.setPlaceholderText(placeholder)
            widget.setReadOnly(True)
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            widget.resize(240, 80)
            widget.show()
            app.processEvents()
            QApplication.sendEvent(widget, _preedit("nihao"))
            app.processEvents()
            image = widget.grab().toImage()
            widget.deleteLater()
            return image

        with_placeholder = render("输入问题，Ctrl+Enter 发送")
        without_placeholder = render("")
        assert with_placeholder != without_placeholder
    finally:
        app.installEventFilter(guard)
