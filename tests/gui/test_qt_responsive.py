"""自适应窗口辅助组件测试：字号缩放、流式等宽与内容高度下限。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from fcmd.gui.app import create_application
from fcmd.gui.widgets import (
    ContentHeightGuard,
    FlowLayout,
    PageScroll,
    ResponsiveLabel,
    fit_flow_widths,
)


@pytest.fixture()
def app() -> QApplication:
    application = QApplication.instance()
    return application if isinstance(application, QApplication) else create_application("responsive-test")


def test_fit_flow_widths_fills_and_caps(app: QApplication) -> None:
    host = QWidget()
    flow = FlowLayout(host, h_spacing=4, v_spacing=4)
    buttons = [QPushButton(str(index), host) for index in range(7)]
    for widget in buttons:
        flow.addWidget(widget)
    try:
        assert fit_flow_widths(host, buttons, 480, spacing=4, min_width=42, max_width=70) == 479
        assert [widget.width() for widget in buttons] == [65] * 7
        assert fit_flow_widths(host, buttons, 320, spacing=4, min_width=42, max_width=70) == 318
        assert [widget.width() for widget in buttons] == [42] * 7
        assert fit_flow_widths(host, buttons, 900, spacing=4, min_width=42, max_width=70) == 514
    finally:
        host.deleteLater()


def test_responsive_label_scales_with_owner_width(app: QApplication) -> None:
    owner = QWidget()
    owner.resize(620, 400)
    label = ResponsiveLabel("12:34:56", owner=owner, min_point=24, max_point=50)
    try:
        label.sync_font()
        wide = label.point_size
        owner.resize(460, 400)
        label.sync_font()
        assert 24 <= label.point_size < wide <= 50
        owner.resize(320, 400)
        label.sync_font()
        assert label.point_size <= 30
    finally:
        label.deleteLater()
        owner.deleteLater()


def test_content_height_guard_raises_minimum_height(app: QApplication) -> None:
    owner = QWidget()
    layout = QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    scroll = PageScroll(owner)
    body = QWidget(scroll)
    body.setMinimumHeight(300)
    scroll.setWidget(body)
    layout.addWidget(scroll)
    owner.resize(400, 200)
    owner.show()
    app.processEvents()
    guard = ContentHeightGuard(owner, scroll, floor=100)
    try:
        guard.sync()
        assert owner.minimumHeight() >= 300
    finally:
        owner.close()
        owner.deleteLater()
