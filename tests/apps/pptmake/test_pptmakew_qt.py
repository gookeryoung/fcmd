"""pptmakew Qt 窗口的离屏冒烟测试。

覆盖初始三栏布局、页面增删改序、表单回写、全部版式渲染、设置落盘与缩到很小时的
布局完整性；版式几何与导出由 ``test_pptmake`` / ``test_pptmake_export`` 覆盖，
这里只验证界面编排与绘制入口。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _pptmakew_qt
from fcmd.apps.pptmake._pptmakew_qt import PptmakeQtWindow, _paint_slide
from fcmd.apps.pptmake.pptmakew import LAYOUT_OPTIONS
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """隔离设置目录、应用默认主题并返回设计器窗口。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    app = create_application("pptmakew-test")
    theme = _guitheme.resolve_theme("dark-blue")
    _qt_theme.apply_theme(app, theme)
    widget = PptmakeQtWindow(tmp_path / "ui" / "pptmakew.json")
    yield widget
    widget.deleteLater()


def _overflowing_children(root: QWidget) -> list[str]:
    """收集越出父控件内容区的后代控件。"""
    problems: list[str] = []

    def walk(widget: QWidget) -> None:
        parent = widget.parentWidget()
        if parent is not None:
            rect = widget.geometry()
            area = parent.contentsRect()
            if rect.bottom() > area.bottom() or rect.right() > area.right():
                problems.append(f"{type(widget).__name__}{rect.getRect()} 超出 {area.getRect()}")
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child.isVisible():
                walk(child)

    for child in root.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if child.isVisible():
            walk(child)
    return problems


def test_initial_three_pane_layout(window: Any) -> None:
    assert window._slides_list.count() == len(window._template.slides)
    assert window._slides_list.count() == len(core.LAYOUTS)
    assert window._layout_list.count() == len(LAYOUT_OPTIONS)
    assert len(window._color_buttons) == len(core.PALETTE_FIELDS)
    assert window._splitter.count() == 3
    assert window._title_edit.text()
    assert window.status_text() == "就绪"


def test_slide_add_duplicate_move_delete(window: Any) -> None:
    initial = window._slides_list.count()
    window._layout_list.setCurrentRow(0)
    window._add_slide()
    assert window._slides_list.count() == initial + 1
    assert window.selected == initial

    window._duplicate_slide()
    assert window._slides_list.count() == initial + 2
    assert window._template.slides[initial + 1].title.endswith("（副本）")

    window._move_slide(-1)
    assert window.selected == initial

    window._delete_slide()
    assert window._slides_list.count() == initial + 1
    assert window.selected == initial


def test_form_apply_updates_current_slide(window: Any) -> None:
    window._slides_list.setCurrentRow(0)
    window._title_edit.setText("季度复盘")
    window._subtitle_edit.setText("2026 Q4")
    window._bullets_text.setPlainText("营收|1200 万\n增长|18%")
    window._note_text.setPlainText("备注")
    window._apply_form()
    slide = window._template.slides[0]
    assert slide.title == "季度复盘"
    assert slide.subtitle == "2026 Q4"
    assert slide.bullets == ("营收|1200 万", "增长|18%")
    assert slide.note == "备注"
    assert "季度复盘" in window._slides_list.item(0).text()


def test_style_and_decor_switch_refreshes_window(window: Any) -> None:
    window._apply_style(core.STYLE_CORAL)
    assert window._template.theme.style == core.STYLE_CORAL
    window._apply_decor(core.DECOR_MINIMAL)
    assert window._template.theme.decor == core.DECOR_MINIMAL
    window._intensity_slider.setValue(0)
    assert window._template.theme.tokens.decor_intensity == 0.0
    assert window._thumbs.count() == window._slides_list.count()


def test_all_layouts_render_without_error(window: Any) -> None:
    theme = _guitheme.resolve_theme("dark-blue")
    for layout in core.LAYOUTS:
        slide = core.new_slide(layout)
        template = core.new_template(slides=(slide,))
        pixmap = QPixmap(320, 180)
        painter = QPainter(pixmap)
        try:
            _paint_slide(
                painter,
                template,
                slide,
                320.0,
                180.0,
                zoom=None,
                ui=theme.palette,
                with_overflow_hint=True,
            )
        finally:
            painter.end()
        assert not pixmap.isNull()


def test_zoom_controls(window: Any) -> None:
    window._set_zoom(1.0)
    assert window.zoom == 1.0
    assert window._zoom_label.text() == "100%"
    window._step_zoom(1.25)
    assert window.zoom == pytest.approx(1.25)
    window._set_zoom(None)
    assert window.zoom is None
    assert window._zoom_label.text() == "适应窗口"


def test_persist_writes_style_aspect_and_geometry(window: Any, tmp_path: Path) -> None:
    window._apply_style(core.STYLE_GRAPHITE)
    window._persist_settings("1200x760+10+20")
    settings = _pptmakew_qt.load_settings(tmp_path / "ui" / "pptmakew.json")
    assert settings.style == core.STYLE_GRAPHITE
    assert settings.geometry == "1200x760+10+20"


def test_layout_survives_tiny_window(window: Any) -> None:
    window.show()
    window.setMinimumSize(960, 420)
    window.resize(960, 420)
    create_application("pptmakew-test").processEvents()
    page = window.scroll.widget()
    assert page is not None
    assert _overflowing_children(page) == []
    assert window._canvas.height() >= window._canvas.minimumHeight()
    window.close()
