"""``fcmd.gui.page`` 的页面骨架测试：容器布局、页面 key 与状态通道。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel

from fcmd.gui.page import AppPage, PageBody, PageSpec


class _State:
    """最小状态桩：页面只依赖 page_status / status 两个通道。"""

    def __init__(self) -> None:
        self.page_statuses: dict[str, str] = {}
        self.statuses: list[str] = []

    def page_status(self, key: str, text: str) -> None:
        self.page_statuses[key] = text

    def status(self, text: str) -> None:
        self.statuses.append(text)


class _Page(AppPage):
    PAGE_KEY = "demo"


@pytest.fixture()
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_page_body_owns_root_layout(app: QApplication) -> None:
    body = PageBody(margins=(1, 2, 3, 4), spacing=7)
    try:
        assert body.objectName() == "PageBody"
        margins = body.body.contentsMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (1, 2, 3, 4)
        assert body.body.spacing() == 7
        assert body.header_widgets() == ()
        assert body.page_key == ""
    finally:
        body.deleteLater()


def test_page_spec_is_unpackable_and_configurable() -> None:
    spec = PageSpec("models", "模型", "GGUF", "models")
    key, label, hint, icon = spec
    assert (key, label, hint, icon) == ("models", "模型", "GGUF", "models")
    assert spec.label == "模型"


def test_app_page_routes_status_to_its_own_page(app: QApplication) -> None:
    state = _State()
    page = _Page(state)
    try:
        page.status("已加载")
        assert state.page_statuses == {"demo": "已加载"}
        assert state.statuses == []  # 不占用「当前页」通道
        assert page.current_status() == "已加载"
        assert page.page_key == "demo"
    finally:
        page.deleteLater()


def test_app_page_card_helper_adds_to_body(app: QApplication) -> None:
    page = _Page(_State())
    try:
        card = page.card("存储")
        card.body.addWidget(QLabel("内容"))
        assert page.body.count() == 1
        assert page.body.itemAt(0).widget() is card
    finally:
        page.deleteLater()
