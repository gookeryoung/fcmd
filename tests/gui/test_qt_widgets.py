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

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import Card, Collapsible, LoadingBar, LogView, MetricGrid, PathRow, button, icon_button

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
    # 共享层运行时基准 = 字体行高 + 补偿（min-height 的内容高，随平台字体不同），
    # 控件总高再加 QSS 上下各 1px 边框。
    expected = _qt_theme.runtime_control_height(QApplication.instance()) + 2
    assert next(iter(sizes.values())).height() == expected


def test_ghost_button_keeps_padding_of_default() -> None:
    """次要按钮只改配色，不改内边距（否则一行里高矮不一）。"""
    text = _qt_theme.stylesheet(_guitheme.resolve_theme("dark-blue"))
    default_block = text.split("QPushButton {", 1)[1].split("}", 1)[0]
    ghost_block = text.split("QPushButton#Ghost {", 1)[1].split("}", 1)[0]
    assert "padding: 0 " in default_block
    assert "padding: 0 " in ghost_block
    assert "min-height" in default_block


def test_single_line_controls_share_height() -> None:
    """按钮、输入框、下拉与数值框在同一布局里等高：统一基准在共享层收敛。"""
    container = QWidget()
    layout = QVBoxLayout(container)
    combo = QComboBox()
    combo.addItem("1")
    controls = [button("确定"), QLineEdit(), combo, QSpinBox(), QDoubleSpinBox()]
    for control in controls:
        layout.addWidget(control)
    container.show()
    QApplication.processEvents()
    assert len({control.height() for control in controls}) == 1
    expected = _qt_theme.runtime_control_height(QApplication.instance()) + 2
    assert controls[0].height() == expected


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


def test_path_row_commits_on_editing_finished() -> None:
    """设置页自动保存监听 committed：输入中不落盘，失焦 / 回车后才提交。"""
    row = PathRow(value="D:/models", show_reset=True)
    seen: list[str] = []
    row.committed.connect(seen.append)
    row.edit.setText("D:/models2")
    assert seen == []
    row.edit.editingFinished.emit()
    assert seen == ["D:/models2"]


def test_icon_button_height_matches_default_button() -> None:
    """图标按钮高度跟随刷新等普通按钮，右侧工具栏不出现高矮不一。"""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    container = QWidget()
    layout = QHBoxLayout(container)
    refresh = button("刷新")
    icon = icon_button("⚙", tooltip="生成参数")
    layout.addWidget(refresh)
    layout.addWidget(icon)
    container.show()
    QApplication.processEvents()
    try:
        assert icon.height() == refresh.height()
        assert icon.width() == icon.height()
    finally:
        container.deleteLater()


def test_icon_button_is_square_and_checkable() -> None:
    """图标按钮用字形作图标，可作展开 / 收起开关。"""
    widget = icon_button("⚙", tooltip="生成参数", checkable=True)
    try:
        assert widget.text() == "⚙"
        assert widget.toolTip() == "生成参数"
        assert widget.isCheckable()
        assert widget.objectName() == "IconButton"
    finally:
        widget.deleteLater()


def test_collapsible_can_hide_header() -> None:
    """入口移到外部图标按钮时，分区内置标题隐藏但仍可程序化展开。"""
    section = Collapsible("连接参数", header=False)
    try:
        assert not section.toggle.isVisible()
        assert not section.is_expanded()
        section.set_expanded(True)
        assert section.is_expanded()
        assert not section.content.isHidden()
    finally:
        section.deleteLater()


def test_collapsible_set_title_updates_toggle() -> None:
    """文件清单等动态数量通过 set_title 更新折叠区标题。"""
    section = Collapsible("文件清单")
    assert section.toggle.text() == "文件清单"
    assert not section.is_expanded()
    section.set_title("文件清单（2 个 · 4.00 KB）")
    assert section.toggle.text() == "文件清单（2 个 · 4.00 KB）"
    section.set_expanded(True)
    assert section.is_expanded()


def test_loading_bar_tracks_elapsed_and_stops() -> None:
    """加载条显示说明文字与已用时，stop 后收起且停止计时。"""
    bar = LoadingBar("正在加载模型列表…")
    try:
        assert not bar.isVisible()
        bar.start("正在加载第 1 页…")
        assert bar.isVisible()
        assert bar.is_running()
        assert bar.text_label.text() == "正在加载第 1 页…"
        bar.set_text("正在扫描本地模型…")
        assert bar.text_label.text() == "正在扫描本地模型…"
        bar._tick()
        assert bar.elapsed_text().endswith("s")
        bar.stop()
        assert not bar.isVisible()
        assert not bar.is_running()
    finally:
        bar.deleteLater()


def test_log_view_roundtrip() -> None:
    view = LogView(height=80)
    view.append("第一行")
    view.append("第二行")
    assert "第一行" in view.text()
    view.clear()
    assert view.text() == ""
