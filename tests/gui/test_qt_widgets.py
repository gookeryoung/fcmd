"""``fcmd.gui.qt.widgets`` 的离屏测试：按钮尺寸统一与卡片标题结构。

按钮的四种变体（默认 / 主操作 / 危险 / 次要）必须共用同一套内边距与最小高度，
否则在同一行里（例如「刷新模型列表」旁边）高矮不一。卡片标题是普通 ``QLabel``，
不再使用 ``QGroupBox::title`` 子控件，避免 QSS 背景与定位问题。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import Card, LogView, MetricGrid, button

_BUTTON_KINDS = ("", "primary", "danger", "ghost")


@pytest.fixture(scope="module", autouse=True)
def _app() -> object:
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return app


def test_button_variants_share_size_hint() -> None:
    """四种按钮变体在相同文案下尺寸一致（对齐「刷新模型列表」）。"""
    sizes = {kind: button("刷新模型列表", kind=kind).sizeHint() for kind in _BUTTON_KINDS}
    assert len({size.height() for size in sizes.values()}) == 1
    assert len({size.width() for size in sizes.values()}) == 1
    assert next(iter(sizes.values())).height() >= 30


def test_ghost_button_keeps_padding_of_default() -> None:
    """次要按钮只改配色，不改内边距（否则一行里高矮不一）。"""
    text = _qt_theme.stylesheet(_guitheme.resolve_theme("dark-blue"))
    default_block = text.split("QPushButton {", 1)[1].split("}", 1)[0]
    ghost_block = text.split("QPushButton#Ghost {", 1)[1].split("}", 1)[0]
    assert "padding: 5px 14px" in default_block
    assert "padding: 5px 14px" in ghost_block
    assert "min-height" in default_block


def test_card_title_is_plain_label_inside_frame() -> None:
    card = Card("硬件信息")
    assert card.title() == "硬件信息"
    assert not card.title_label.isHidden()
    card.setTitle("")
    assert card.title() == ""
    assert card.title_label.isHidden()


def test_card_body_accepts_widgets() -> None:
    card = Card("指标")
    grid = MetricGrid((("CPU", "cpu"),), parent=card)
    card.body.addWidget(grid)
    assert card.body.count() == 1
    grid.set_value("cpu", "10 %")
    assert grid.value("cpu") == "10 %"


def test_log_view_roundtrip() -> None:
    view = LogView(height=80)
    view.append("第一行")
    view.append("第二行")
    assert "第一行" in view.text()
    view.clear()
    assert view.text() == ""
