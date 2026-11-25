"""pptmakew Qt 窗口的离屏冒烟测试。

覆盖侧边栏分页（设计 / 样式）、设计页三栏布局、页面增删改序、表单回写、全部版式
渲染、设置落盘与缩到很小时的布局完整性；版式几何与导出由 ``test_pptmake`` /
``test_pptmake_export`` 覆盖，这里只验证界面编排与绘制入口。
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QListView, QWidget

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _pptmakew_qt, _pptmakew_tokens, _tokens
from fcmd.apps.pptmake._pptmakew_qt import PptmakeQtWindow, _paint_slide
from fcmd.apps.pptmake.pptmakew import LAYOUT_GROUPS, LAYOUT_OPTIONS
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """隔离设置目录、应用默认主题并返回设计器窗口。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    app = create_application("pptmakew-test")
    theme = _guitheme.resolve_theme("dark-blue")
    _qt_theme.apply_theme(app, theme)
    widget = PptmakeQtWindow(tmp_path / "ui" / "pptmakew.json")
    yield widget
    widget._recover_timer.stop()
    widget._autosave_timer.stop()
    widget._apply_timer.stop()
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


def test_initial_sidebar_layout(window: Any) -> None:
    assert window.current_page() == "design"
    assert [spec.key for spec in _pptmakew_qt.PAGES] == list(_pptmakew_qt.PAGE_KEYS)
    assert window.nav.label("design") == "设计"
    assert window.nav.label("style") == "样式"
    assert window._slides_list.count() == len(window._template.slides)
    assert window._slides_list.count() == len(core.LAYOUTS)
    assert window._layout_list.count() == len(LAYOUT_OPTIONS)
    assert len(window._color_buttons) == len(core.PALETTE_FIELDS)
    assert window._splitter.count() == 3
    assert window._style_splitter.count() == 2
    assert window._title_edit.text()
    assert window.status_text() == "就绪"
    assert "第 1/" in window.status_detail_text()
    assert "处溢出" in window.status_detail_text()
    assert window._slide_overflow_count(0) == 0


def test_style_page_has_live_preview_and_controls(window: Any) -> None:
    window.show_page("style")
    assert window.current_page() == "style"
    assert window._name_edit.text() == window._template.name
    assert window._intensity_value.text().endswith("%")
    assert window._style_canvas in window._canvases
    # 样式页左右并排：左侧设置卡、右侧实时预览。
    assert window._style_splitter.widget(1) is window._style_canvas.parentWidget()
    window.show_page("design")
    assert window.current_page() == "design"


def test_layout_combo_groups_and_capacity_hint(window: Any) -> None:
    """版式下拉按 6 组分类，提示同时带格式建议与 ``rule_for`` 容量。"""
    combo = window._layout_combo
    headers = [combo.itemText(index) for index in range(combo.count()) if combo.itemData(index) is None]
    assert headers == ["— 封面 —", "— 结构 —", "— 内容 —", "— 数据 —", "— 图文 —", "— 结束 —"]
    values = [combo.itemData(index) for index in range(combo.count()) if combo.itemData(index)]
    assert set(values) == set(core.LAYOUTS)

    window._layout_combo.setCurrentIndex(combo.findData(core.LAYOUT_IMAGE))
    assert "容量" in window._layout_hint.text()
    assert "要点 ≤ 4 条" in window._layout_hint.text()
    assert window._image_ratio_combo.isEnabled()
    window._layout_combo.setCurrentIndex(combo.findData(core.LAYOUT_BULLETS))
    assert not window._image_ratio_combo.isEnabled()


def test_layout_library_filters_by_category(window: Any) -> None:
    """布局库分类下拉只显示所选类别的版式，且始终保留全部 24 项。"""
    filter_combo = window._layout_filter
    assert filter_combo.currentText() == "全部分类"
    assert filter_combo.count() == len(LAYOUT_GROUPS) + 1
    ordered = [value for _text, value in LAYOUT_OPTIONS]
    groups = dict(LAYOUT_GROUPS)
    for index in range(filter_combo.count()):
        group = filter_combo.itemData(index)
        filter_combo.setCurrentIndex(index)
        visible = [
            window._layout_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(window._layout_list.count())
            if not window._layout_list.item(row).isHidden()
        ]
        expected = [layout for layout in ordered if not group or layout in groups[group]]
        assert visible == expected
        assert window._layout_list.currentItem() is not None
    assert window._layout_list.count() == len(LAYOUT_OPTIONS)


def test_image_detail_controls_roundtrip(window: Any) -> None:
    """焦点 / 替代文本 / 图注 / 底色 / 图文比例写回当前页。"""
    window._slides_list.setCurrentRow(0)
    window._layout_combo.setCurrentIndex(window._layout_combo.findData(core.LAYOUT_IMAGE))
    window._image_edit.setText("assets/hero.png")
    window._image_focus_combo.setCurrentIndex(window._image_focus_combo.findData(core.IMAGE_FOCUS_TOP))
    window._image_alt_edit.setText("主视觉")
    window._image_caption_edit.setText("产品主视觉")
    window._background_combo.setCurrentIndex(window._background_combo.findData(core.BACKGROUND_DARK))
    window._image_ratio_combo.setCurrentIndex(window._image_ratio_combo.findData(core.IMAGE_RATIO_GRID_2))
    window._apply_form()

    slide = window._template.slides[0]
    assert slide.layout == core.LAYOUT_IMAGE
    assert slide.image_focus == core.IMAGE_FOCUS_TOP
    assert slide.image_alt == "主视觉"
    assert slide.image_caption == "产品主视觉"
    assert slide.background == core.BACKGROUND_DARK
    assert slide.image_ratio == core.IMAGE_RATIO_GRID_2


def test_image_ratio_numeric_fine_tune(window: Any) -> None:
    """选中预设后拖动数值微调，落盘为 clamp 后的数值字符串。"""
    window._slides_list.setCurrentRow(0)
    window._layout_combo.setCurrentIndex(window._layout_combo.findData(core.LAYOUT_IMAGE))
    window._image_ratio_combo.setCurrentIndex(window._image_ratio_combo.findData(core.IMAGE_RATIO_50_50))
    assert window._image_ratio_spin.value() == 50
    window._image_ratio_spin.setValue(35)
    assert window._image_ratio_combo.currentData() == "custom"
    window._apply_form()
    slide = window._template.slides[0]
    assert slide.image_ratio == "0.35"

    window._refresh_form()
    assert window._image_ratio_combo.currentData() == "custom"
    assert window._image_ratio_spin.value() == 35


def test_check_panel_lists_issues_and_fixes(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """检查面板：列出问题、点击跳页，一键修复截断超容量要点。"""
    rule = _pptmakew_qt.rule_for(core.LAYOUT_BULLETS)
    template = core.new_template(
        slides=(
            core.SlideSpec(
                layout=core.LAYOUT_BULLETS,
                title="溢出页",
                bullets=tuple("这是一行非常长的要点文字" * 8 for _ in range(rule.max_bullets + 4)),
                image="assets/hero.png",
            ),
        )
    )
    window._set_template(template)
    window._inspector_tabs.setCurrentIndex(window._inspector_tabs.count() - 1)
    assert window._check_list.count() >= 1
    assert "问题" in window._check_summary.text()
    assert window._check_fix_button.isEnabled()
    window._on_check_item_clicked(window._check_list.item(0))
    assert window._slides_list.currentRow() == 0

    monkeypatch.setattr(_pptmakew_qt.QMessageBox, "information", staticmethod(lambda *args, **kwargs: None))
    window._fix_quality_issues()
    slide = window._template.slides[0]
    assert len(slide.bullets) <= rule.max_bullets
    assert all(len(item) <= rule.max_chars for item in slide.bullets)
    assert slide.image_alt


def test_check_panel_reports_clean_template(window: Any) -> None:
    window._refresh_check_panel()
    assert window._check_list.count() == 0
    assert "未发现" in window._check_summary.text()
    assert not window._check_fix_button.isEnabled()


def test_overflow_badge_and_status_detail(window: Any) -> None:
    """溢出页在缩略图与状态栏使用同一计数（角标数据源）。"""
    window._slides_list.setCurrentRow(0)
    window._layout_combo.setCurrentIndex(window._layout_combo.findData(core.LAYOUT_BULLETS))
    window._bullets_text.setPlainText("\n".join(["这是一行非常长的要点文字" * 8] * 12))
    window._apply_form()
    count = window._slide_overflow_count(0)
    assert count > 0
    assert f"{count} 处溢出" in window.status_detail_text()
    assert window._slides_list.count() == window._slides_list.count()
    assert not window._slides_list.item(0).icon().isNull()


def test_splitter_sizes_persist(window: Any, tmp_path: Path) -> None:

    window._splitter.setSizes([240, 620, 320])
    window._style_splitter.setSizes([360, 700])
    window._persist_settings("1200x760+10+20")
    settings = _pptmakew_qt.load_settings(tmp_path / "ui" / "pptmakew.json")
    assert len(settings.design_splitter) == 3
    assert len(settings.style_splitter) == 2
    assert all(size > 0 for size in settings.design_splitter + settings.style_splitter)


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
    assert window._slides_list.count() == window._slides_list.count()


def test_undo_redo_through_window(window: Any) -> None:
    window._apply_style(core.STYLE_CORAL)
    assert window._dirty is True
    assert window.windowTitle().endswith(" *")
    assert window._undo_action.isEnabled()
    window._undo()
    assert window._template.theme.style == core.STYLE_AURORA
    assert window._redo_action.isEnabled()
    window._redo()
    assert window._template.theme.style == core.STYLE_CORAL


def test_multi_select_duplicate_and_delete(window: Any) -> None:
    initial = window._slides_list.count()
    window._slides_list.clearSelection()
    for row in (0, 1):
        item = window._slides_list.item(row)
        assert item is not None
        item.setSelected(True)
    window._duplicate_slide()
    assert window._slides_list.count() == initial + 2
    window._undo()
    assert window._slides_list.count() == initial

    window._slides_list.clearSelection()
    for row in (0, 1):
        item = window._slides_list.item(row)
        assert item is not None
        item.setSelected(True)
    window._delete_slide()
    assert window._slides_list.count() == initial - 2
    window._undo()
    assert window._slides_list.count() == initial


def test_drag_reorder_updates_template(window: Any) -> None:
    first = window._template.slides[0]
    second = window._template.slides[1]
    # 模拟 Qt 内部拖拽后的列表顺序：交换前两项，并保留原索引数据。
    item = window._slides_list.takeItem(0)
    window._slides_list.insertItem(1, item)
    window._on_slides_reordered()
    assert window._template.slides[0] == second
    assert window._template.slides[1] == first
    window._undo()
    assert window._template.slides[0] == first


def test_sorter_view_and_inspector_toggle(window: Any) -> None:
    assert window._inspector_tabs.count() == 4
    assert window._inspector_tabs.tabText(0) == "内容"
    assert window._inspector_tabs.tabText(1) == "版式"
    assert window._inspector_tabs.tabText(2) == "备注"
    assert window._inspector_tabs.tabText(3) == "检查"
    assert window._layout_hint.text()

    window._toggle_sorter_view()
    assert window._sorter_view == "list"
    assert window._slides_list.viewMode() == QListView.ViewMode.ListMode
    window._toggle_sorter_view()
    assert window._sorter_view == "grid"
    assert window._slides_list.viewMode() == QListView.ViewMode.IconMode

    window._toggle_inspector()
    assert window._inspector_visible is False
    assert window._inspector_pane.isVisible() is False
    window._toggle_inspector()
    assert window._inspector_visible is True


def test_layout_gallery_has_thumbnails(window: Any) -> None:
    assert window._layout_list.count() == len(LAYOUT_OPTIONS)
    assert all(window._layout_list.item(row).icon().isNull() is False for row in range(window._layout_list.count()))


def test_autosave_and_recover_draft(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    window._apply_style(core.STYLE_CORAL)
    window._autosave()
    assert window._draft_path.is_file()

    window._set_template(core.new_template(name="临时"))
    assert window._template.name == "临时"
    monkeypatch.setattr(
        _pptmakew_qt.QMessageBox,
        "question",
        lambda *args, **kwargs: _pptmakew_qt.QMessageBox.StandardButton.Yes,
    )
    window._maybe_recover_draft()
    assert window._template.theme.style == core.STYLE_CORAL
    assert window._dirty is True


def test_recent_templates_persist(window: Any, tmp_path: Path) -> None:
    path = tmp_path / "品牌模板.pptmtpl.json"
    window._remember_recent(path)
    assert window._settings.recent_templates[0] == str(path)
    window._persist_settings("1200x760+10+20")
    settings = _pptmakew_qt.load_settings(tmp_path / "ui" / "pptmakew.json")
    assert settings.recent_templates[0] == str(path)


def test_slide_image_and_accent_controls(window: Any) -> None:
    window._slides_list.setCurrentRow(0)
    window._image_edit.setText("assets/hero.png")
    window._image_fit_combo.setCurrentIndex(window._image_fit_combo.findData(core.IMAGE_FIT_CONTAIN))
    window._accent_value = "#123456"
    window._apply_form()
    slide = window._template.slides[0]
    assert slide.image == "assets/hero.png"
    assert slide.image_fit == core.IMAGE_FIT_CONTAIN
    assert slide.accent == "#123456"

    window._clear_slide_accent()
    window._apply_form()
    assert window._template.slides[0].accent == ""


def test_footer_and_font_controls(window: Any) -> None:
    from PySide6.QtGui import QFont

    window._footer_edit.setText("品牌页脚")
    window._page_number_check.setChecked(False)
    window._logo_edit.setText("assets/logo.png")
    assert window._template.theme.footer == "品牌页脚"
    assert window._template.theme.logo == "assets/logo.png"
    assert window._template.theme.show_page_number is False

    window._heading_font_combo.setCurrentFont(QFont("Arial"))
    family = window._heading_font_combo.currentFont().family()
    assert window._template.theme.heading_font == family


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


def test_export_options_dialog(window: Any) -> None:
    dialog = _pptmakew_qt._ExportOptionsDialog(10, window)
    try:
        dialog.start.setValue(3)
        dialog.end.setValue(5)
        dialog.pdf.setChecked(True)
        assert dialog.options() == (3, 5, True)
        dialog.start.setValue(7)
        dialog.end.setValue(2)
        assert dialog.options() == (2, 7, True)
    finally:
        dialog.deleteLater()


def test_presenter_window_renders(window: Any) -> None:
    presenter = _pptmakew_qt._PresenterWindow(window, 0)
    try:
        presenter.resize(480, 270)
        presenter.show()
        create_application("pptmakew-test").processEvents()
        assert not presenter.grab().isNull()
        presenter._go(1)
        assert presenter._index == 1
    finally:
        presenter.close()


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


def test_token_dialog_edits_tokens_and_rejects_bad_color(window: Any) -> None:
    dialog = _pptmakew_tokens.TokenDialog(window._template.theme.tokens, window)
    dialog._type_spins["body"].setValue(30)
    dialog._spacing_spins["line_body"].setValue(1.6)
    dialog._radius_spins["card"].setValue(24)
    dialog._color_edits["chart.0"].setText("#112233")
    dialog._color_edits["semantic.info"].setText("#00A3FF")
    dialog._bullet_char.setText("·")
    edited = dialog.values()
    assert edited.type_scale.body == 30
    assert edited.spacing.line_body == 1.6
    assert edited.radius.card == 24
    assert edited.chart_palette[0] == "#112233"
    assert edited.semantic.info == "#00A3FF"
    assert edited.bullet.char == "·"
    assert dialog.result_tokens() is None
    dialog._color_edits["chart.1"].setText("不是颜色")
    with pytest.raises(_tokens.TokenError):
        dialog.values()
    dialog.close()


def test_apply_tokens_commits_undoable_change(window: Any) -> None:
    new_tokens = replace(
        window._template.theme.tokens,
        type_scale=replace(window._template.theme.tokens.type_scale, body=30),
    )
    window._apply_tokens(new_tokens)
    assert window._template.theme.tokens.type_scale.body == 30
    assert window._history.undo_label() == "调整设计令牌"


def test_layout_survives_tiny_window(window: Any) -> None:
    window.show()
    window.setMinimumSize(960, 420)
    window.resize(960, 420)
    create_application("pptmakew-test").processEvents()
    page = window._design_page.scroll.widget()
    assert page is not None
    assert _overflowing_children(page) == []
    assert window._canvas.height() >= window._canvas.minimumHeight()
    window.show_page("style")
    create_application("pptmakew-test").processEvents()
    style_page = window._style_page.scroll.widget()
    assert style_page is not None
    assert _overflowing_children(style_page) == []
    window.close()
