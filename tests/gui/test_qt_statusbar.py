"""``fcmd.gui.statusbar`` 的测试：单行高度与按分页隔离文字。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMainWindow

from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.statusbar import DEFAULT_STATUS, WindowStatusBar


def _window() -> tuple[QMainWindow, WindowStatusBar]:
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    window = QMainWindow()
    bar = WindowStatusBar(window)
    window.setStatusBar(bar)
    window.resize(1000, 700)
    window.show()
    app.processEvents()
    return window, bar


def test_default_text_and_page_isolation() -> None:
    """每个分页记住自己的文字：切页恢复，不把上一页的提示留在底部。"""
    window, bar = _window()
    try:
        assert bar.text() == DEFAULT_STATUS
        bar.set_page_text("distros", "刷新完成")
        bar.set_page_text("images", "正在更新镜像清单…")
        bar.set_page("distros")
        assert bar.text() == "刷新完成"
        bar.set_page("images")
        assert bar.text() == "正在更新镜像清单…"
        # 未登记的页回到兜底文字
        bar.set_page("settings")
        assert bar.text() == DEFAULT_STATUS
        assert bar.page_text("distros") == "刷新完成"
    finally:
        window.deleteLater()


def test_detail_is_page_scoped_and_hidden_when_empty() -> None:
    """页内补充（选中项摘要）只在所属页显示，空串时整段收起。"""
    window, bar = _window()
    try:
        bar.set_page("distros")
        bar.set_page_detail("distros", "Ubuntu-24.04 · 运行中 · v2")
        assert bar.detail_text().startswith("Ubuntu-24.04")
        assert bar.detail_label.isVisible() is True

        bar.set_page("images")
        assert bar.detail_text() == ""
        assert bar.detail_label.isVisible() is False

        bar.set_page("distros")
        bar.set_page_detail("distros", "")
        assert bar.detail_label.isVisible() is False
    finally:
        window.deleteLater()


def test_height_stays_single_line() -> None:
    """状态栏高度恒为一行：长文案、页内补充、加载条都不把它撑高。"""
    window, bar = _window()
    try:
        single = bar.sizeHint().height()
        assert bar.height() == single

        bar.set_page("images")
        bar.set_page_text("images", "共 120 个镜像 · 显示 120 个 · 已安装 12 · 可直链 96 · cli+manifest · 更新于 12:00")
        bar.set_page_detail("images", "Ubuntu-24.04 · x86_64 · 官方清单 · 已下载 · 可安装")
        QApplication.processEvents()
        assert bar.height() == single

        bar.show_loading("正在获取镜像清单…")
        QApplication.processEvents()
        assert bar.loading_bar.is_running()
        assert bar.height() == single
        assert bar.text().startswith("共 120 个镜像")
    finally:
        window.deleteLater()


def test_note_and_loading_do_not_hide_status_text() -> None:
    """右侧备注 / 加载条与左侧文字共存：加载时左侧状态仍可见。"""
    window, bar = _window()
    try:
        bar.set_page("models")
        bar.set_page_text("models", "环境就绪")
        bar.set_note("更新于 12:00")
        bar.show_loading("正在加载第 1 页…")
        QApplication.processEvents()
        assert bar.note_label.isVisible() is True
        assert bar.note_label.text() == "更新于 12:00"
        assert bar.loading_bar.text_label.text() == "正在加载第 1 页…"
        assert bar.text() == "环境就绪"

        bar.show_loading("")
        bar.set_note("")
        assert bar.loading_bar.is_running() is False
        assert bar.note_label.isVisible() is False
    finally:
        window.deleteLater()


def test_detail_is_right_aligned() -> None:
    """页内补充靠右：紧挨右侧备注 / 加载条，而不是夹在状态栏中间。"""
    from PySide6.QtCore import Qt

    window, bar = _window()
    try:
        bar.set_page("distros")
        bar.set_page_text("distros", "刷新完成")
        bar.set_page_detail("distros", "Ubuntu-24.04 · 已停止 · v2 · ≈12.0 GiB")
        QApplication.processEvents()

        alignment = bar.detail_label.alignment()
        assert alignment & Qt.AlignmentFlag.AlignRight
        # 主文字在左、补充在右，且补充贴着状态栏右边缘（考虑状态栏自身内边距）
        assert bar.text_label.geometry().right() < bar.detail_label.geometry().left()
        assert abs(bar.detail_label.geometry().right() - (bar.width() - 3)) <= 4

        # 右侧出现加载条时，补充仍然靠右（只是让出加载条的位置）
        bar.show_loading("正在获取镜像清单…")
        QApplication.processEvents()
        assert bar.detail_label.geometry().right() < bar.loading_bar.geometry().left()
        assert bar.detail_label.alignment() & Qt.AlignmentFlag.AlignRight
    finally:
        window.deleteLater()
