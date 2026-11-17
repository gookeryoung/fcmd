"""pptmakew 的 Qt 主窗口与入口。

窗口只做编排：模板数据结构、布局绘制列表与 PPTX 导入导出全部复用
:mod:`fcmd.apps.pptmake._core` / ``_design`` / ``_preview`` / ``_pptx``；
预览画布用 ``QPainter`` 按同一份绘制列表渲染，缩略图同样来自该列表，
因此画布、缩略图与导出的 PPTX 共用一套几何。

布局口径：顶部模板与风格工具条固定；主体三栏分栏（页面 / 布局库 | 预览 +
缩略图 | 页面设置 + 配色），分栏可拖动且中间栏占满剩余宽度；窗口缩小时整页
纵向滚动，预览画布保留最小高度，不把右侧表单压出卡片。
"""

from __future__ import annotations

import contextlib
import tempfile
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _office
from fcmd.apps.pptmake import _preview as preview
from fcmd.apps.pptmake import _tokens as tokens
from fcmd.apps.pptmake._design import Line, Oval, Rect, Text, build_draw_list
from fcmd.apps.pptmake._pptx import export_pptx, import_pptx
from fcmd.apps.pptmake.pptmakew import (
    ASPECT_OPTIONS,
    DECOR_OPTIONS,
    DENSITY_OPTIONS,
    HELP_TEXT,
    LAYOUT_OPTIONS,
    MIN_HEIGHT,
    MIN_WIDTH,
    STYLE_OPTIONS,
    PptmakeSettings,
    __version__,
    load_settings,
    safe_filename,
    save_settings,
    slide_label,
)
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, button, make_splitter

__all__ = ["PptmakeQtWindow", "run_qt"]

DEFAULT_SIZE = (1320, 840)
THUMB_HEIGHT = 54.0
THUMB_STRIP_HEIGHT = 92

_PALETTE_LABELS = dict(core.PALETTE_FIELDS)


def _brush(color: str) -> QBrush:
    """有颜色时返回实心画刷，空串表示不填充。"""
    return QBrush(QColor(color)) if color else QBrush(Qt.BrushStyle.NoBrush)


def _pen(color: str, width: float = 1.0) -> QPen:
    """有颜色时返回描边笔，空串表示不描边。"""
    if not color:
        return QPen(Qt.PenStyle.NoPen)
    pen = QPen(QColor(color))
    pen.setWidthF(max(width, 1.0))
    return pen


def _paint_primitive(painter: QPainter, primitive: Any, view: preview.Viewport) -> None:
    """把一个设计图元画到画布坐标；几何与既有换算口径一致。"""
    scale = view.scale
    if isinstance(primitive, Rect):
        x, y = view.to_canvas(primitive.x, primitive.y)
        width = primitive.w * scale
        height = primitive.h * scale
        if width <= 0 or height <= 0:
            return
        painter.setBrush(_brush(primitive.fill))
        painter.setPen(_pen(primitive.line, primitive.line_width * scale))
        if primitive.radius > 0:
            radius = min(primitive.radius * scale, width / 2, height / 2)
            painter.drawRoundedRect(QRectF(x, y, width, height), radius, radius)
        else:
            painter.drawRect(QRectF(x, y, width, height))
        return
    if isinstance(primitive, Oval):
        x, y = view.to_canvas(primitive.x, primitive.y)
        width = primitive.w * scale
        height = primitive.h * scale
        if width <= 0 or height <= 0:
            return
        painter.setBrush(_brush(primitive.fill))
        painter.setPen(_pen(primitive.line, primitive.line_width * scale))
        painter.drawEllipse(QRectF(x, y, width, height))
        return
    if isinstance(primitive, Line):
        x1, y1 = view.to_canvas(primitive.x1, primitive.y1)
        x2, y2 = view.to_canvas(primitive.x2, primitive.y2)
        painter.setPen(_pen(primitive.color, primitive.width * scale))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        return
    if isinstance(primitive, Text):
        _paint_text(painter, primitive, view)


def _paint_text(painter: QPainter, primitive: Text, view: preview.Viewport) -> None:
    """逐段绘制文本：先按设计宽度断行，再按段落高度手工堆叠。"""
    scale = view.scale
    x, y = view.to_canvas(primitive.x, primitive.y)
    width = primitive.w * scale
    height = primitive.h * scale
    pixel_size = max(round(primitive.size * scale), 1)
    font = QFont(primitive.font or QApplication.font().family())
    font.setPixelSize(pixel_size)
    font.setBold(primitive.bold)
    font.setItalic(primitive.italic)
    painter.setFont(font)
    painter.setPen(QPen(QColor(primitive.color)))
    line_height = painter.fontMetrics().height()
    extra = max(round(pixel_size * (primitive.line_spacing - 1.2)), 0)
    space_after = round(primitive.space_after * scale)
    paragraphs = primitive.paragraphs or ("",)
    wrapped = [
        preview.wrap_text(paragraph, primitive.w, primitive.size, bold=primitive.bold) for paragraph in paragraphs
    ]
    heights = [len(lines) * line_height + (len(lines) - 1) * extra + space_after for lines in wrapped]
    total = sum(heights)
    cursor = {"top": y, "middle": y + (height - total) / 2, "bottom": y + height - total}[primitive.anchor]
    align = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }[primitive.align]
    flags = int(align | Qt.AlignmentFlag.AlignTop)
    for lines in wrapped:
        for line in lines:
            painter.drawText(QRectF(x, cursor, width, line_height), flags, line)
            cursor += line_height + extra
        cursor += space_after - extra


def _paint_slide(  # noqa: PLR0913 - 画布、缩略图与错误提示共用入口
    painter: QPainter,
    template: core.TemplateSpec,
    slide: core.SlideSpec,
    width: float,
    height: float,
    *,
    zoom: float | None,
    ui: Any,
    with_overflow_hint: bool = False,
    skip_text: bool = False,
) -> None:
    """在 ``width×height`` 画布上绘制一页；裁剪严格限制在页面矩形内。"""
    design_width, design_height = core.ASPECT_DESIGN[template.aspect]
    if zoom is None:
        view = preview.fit_viewport(design_width, design_height, width, height, padding=16.0)
    else:
        view = preview.Viewport(
            scale=zoom,
            offset_x=(width - design_width * zoom) / 2,
            offset_y=(height - design_height * zoom) / 2,
            design_width=design_width,
            design_height=design_height,
            canvas_width=width,
            canvas_height=height,
        )
    x0, y0, x1, y1 = view.page_rect()
    page = QRectF(x0, y0, x1 - x0, y1 - y0)
    primitives = build_draw_list(slide, template)
    painter.save()
    painter.setClipRect(page)
    painter.fillRect(page, QColor(template.theme.palette.background))
    for primitive in primitives:
        if skip_text and isinstance(primitive, Text):
            continue
        _paint_primitive(painter, primitive, view)
    painter.restore()
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(ui.border)))
    painter.drawRect(page)
    if with_overflow_hint:
        issues = preview.overflow_issues(primitives)
        if issues:
            painter.setPen(QPen(QColor(ui.muted)))
            font = QFont(QApplication.font())
            font.setPixelSize(12)
            painter.setFont(font)
            painter.drawText(
                QRectF(10.0, height - 24.0, max(width - 20.0, 10.0), 18.0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                f"提示：{len(issues)} 个文本框可能溢出",
            )


def _paint_message(painter: QPainter, width: float, height: float, text: str, color: str) -> None:
    """画布中央的空状态 / 错误提示。"""
    painter.setPen(QPen(QColor(color)))
    font = QFont(QApplication.font())
    font.setPixelSize(14)
    painter.setFont(font)
    painter.drawText(
        QRectF(0.0, 0.0, width, height),
        int(Qt.AlignmentFlag.AlignCenter),
        text,
    )


class _PreviewCanvas(QWidget):  # pragma: no cover - Qt 控件
    """PPT 预览画布：按当前模板与缩放绘制一页。"""

    def __init__(self, window: PptmakeQtWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._window = window
        self.setMinimumHeight(240)

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        ui = self._window.theme_menu.theme().palette
        width = float(self.width())
        height = float(self.height())
        painter.fillRect(self.rect(), QColor(ui.background))
        if width <= 2 or height <= 2:
            return
        template = self._window.template
        if not template.slides:
            _paint_message(painter, width, height, "尚无页面\n请从左侧「布局库」双击添加", ui.muted)
            return
        slide = template.slides[self._window.selected]
        try:
            _paint_slide(
                painter,
                template,
                slide,
                width,
                height,
                zoom=self._window.zoom,
                ui=ui,
                with_overflow_hint=True,
            )
        except core.TemplateError as exc:
            _paint_message(painter, width, height, f"预览失败\n{exc}", ui.muted)


class PptmakeQtWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """模板工具条 + 三栏设计器（页面 / 预览 / 设置与配色）。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._own_settings_path = settings_path
        self._template = core.new_template(
            name="未命名模板",
            style=self._settings.style,
            aspect=self._settings.aspect,
            decor=self._settings.decor,
        )
        self.selected = 0
        self.zoom: float | None = None
        self._loading = False
        self._syncing_thumb = False
        super().__init__(
            title=f"pptmake - PPT 模板设计器 v{__version__}",
            config_name="pptmakew",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist_settings,
        )
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(150)
        self._apply_timer.timeout.connect(self._apply_form)
        self._build_menus()
        self._build_widgets()
        self._refresh_all()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_menus(self) -> None:
        """在骨架菜单上补齐文件 / 编辑 / 样式，保持「文件 → 编辑 → 样式 → 视图 → 帮助」顺序。"""
        menubar = self.menuBar()
        file_menu = menubar.actions()[0].menu()
        quit_action = file_menu.actions()[-1]
        for text, handler in (
            ("新建模板", self._new_template),
            ("导入模板 / PPTX…", self._import_file),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            file_menu.insertAction(quit_action, action)
        file_menu.insertSeparator(quit_action)
        for text, handler in (
            ("导出模板…", self._export_template),
            ("导出 PPTX…", self._export_pptx),
            ("精确预览（PDF）…", self._precise_preview),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            file_menu.insertAction(quit_action, action)
        file_menu.insertSeparator(quit_action)

        edit_menu = menubar.addMenu("编辑")
        for text, handler in (
            ("添加页面", self._add_slide),
            ("复制页面", self._duplicate_slide),
            ("删除页面", self._delete_slide),
            ("上移", lambda: self._move_slide(-1)),
            ("下移", lambda: self._move_slide(1)),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            edit_menu.addAction(action)

        style_menu = menubar.addMenu("样式")
        self._style_group = QActionGroup(self)
        self._style_group.setExclusive(True)
        self._style_actions: dict[str, QAction] = {}
        for text, value in STYLE_OPTIONS[: len(core.STYLES)]:
            action = QAction(text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, item=value: self._apply_style(item))
            self._style_group.addAction(action)
            style_menu.addAction(action)
            self._style_actions[value] = action
        style_menu.addSeparator()
        decor_menu = style_menu.addMenu("装饰样式")
        self._decor_group = QActionGroup(self)
        self._decor_group.setExclusive(True)
        self._decor_actions: dict[str, QAction] = {}
        for text, value in DECOR_OPTIONS:
            action = QAction(text, self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, item=value: self._apply_decor(item))
            self._decor_group.addAction(action)
            decor_menu.addAction(action)
            self._decor_actions[value] = action

        # 把「编辑」「样式」移到「视图」之前：菜单栏当前是 文件 / 视图 / 帮助。
        view_action = next(action for action in menubar.actions() if action.text() == "视图")
        for menu in (edit_menu, style_menu):
            menubar.removeAction(menu.menuAction())
            menubar.insertMenu(view_action, menu)

        help_menu = menubar.actions()[-1].menu()
        about_action = help_menu.actions()[-1]
        help_action = QAction("使用说明", self)
        help_action.triggered.connect(self._show_help)
        help_menu.insertAction(about_action, help_action)
        help_menu.insertSeparator(about_action)
        self.menuBar().update()

    def _build_widgets(self) -> None:
        self.add(self._build_top())

        self._splitter = make_splitter(Qt.Orientation.Horizontal, parent=self)
        self._splitter.addWidget(self._build_left())
        self._splitter.addWidget(self._build_center())
        self._splitter.addWidget(self._build_right())
        self._splitter.setSizes([300, 760, 340])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        self.add(self._splitter, 1)

    def _build_top(self) -> QWidget:
        card = Card("模板")
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("模板名称"))
        self._name_edit = QLineEdit(self._template.name)
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit, 1)
        for text, handler in (
            ("新建", self._new_template),
            ("导入", self._import_file),
            ("导出模板", self._export_template),
            ("导出 PPTX", self._export_pptx),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            name_row.addWidget(widget)
        card.body.addLayout(name_row)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self._style_combo = self._make_combo(STYLE_OPTIONS)
        controls.addWidget(QLabel("风格"))
        controls.addWidget(self._style_combo)
        controls.addSpacing(8)
        self._aspect_combo = self._make_combo(ASPECT_OPTIONS)
        controls.addWidget(QLabel("画幅"))
        controls.addWidget(self._aspect_combo)
        controls.addSpacing(8)
        self._decor_combo = self._make_combo(DECOR_OPTIONS)
        controls.addWidget(QLabel("装饰"))
        controls.addWidget(self._decor_combo)
        controls.addSpacing(8)
        self._density_combo = self._make_combo(DENSITY_OPTIONS)
        controls.addWidget(QLabel("密度"))
        controls.addWidget(self._density_combo)
        controls.addSpacing(8)
        controls.addWidget(QLabel("装饰强度"))
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._intensity_slider.setRange(0, 100)
        self._intensity_slider.setFixedWidth(120)
        self._intensity_slider.valueChanged.connect(self._on_decor_intensity)
        controls.addWidget(self._intensity_slider)
        controls.addStretch(1)
        card.body.addLayout(controls)
        return card

    def _make_combo(self, options: tuple[tuple[str, str], ...]) -> QComboBox:
        combo = QComboBox(self)
        for text, value in options:
            combo.addItem(text, value)
        combo.currentIndexChanged.connect(self._on_combo_changed)
        return combo

    def _build_left(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        pages = Card("页面")
        self._slides_list = QListWidget(pages)
        self._slides_list.setMinimumHeight(120)
        self._slides_list.currentRowChanged.connect(self._on_slide_selected)
        pages.body.addWidget(self._slides_list, 1)
        page_buttons = QHBoxLayout()
        page_buttons.setContentsMargins(0, 0, 0, 0)
        page_buttons.setSpacing(4)
        for text, handler in (
            ("添加", self._add_slide),
            ("复制", self._duplicate_slide),
            ("删除", self._delete_slide),
            ("上移", lambda: self._move_slide(-1)),
            ("下移", lambda: self._move_slide(1)),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            page_buttons.addWidget(widget)
        pages.body.addLayout(page_buttons)
        layout.addWidget(pages, 3)

        layouts = Card("布局库（双击添加）")
        self._layout_list = QListWidget(layouts)
        self._layout_list.setMinimumHeight(110)
        for text, _value in LAYOUT_OPTIONS:
            self._layout_list.addItem(text)
        self._layout_list.setCurrentRow(0)
        self._layout_list.itemDoubleClicked.connect(lambda _item: self._add_slide())
        layouts.body.addWidget(self._layout_list, 1)
        layout.addWidget(layouts, 2)
        return panel

    def _build_center(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        card = Card("预览")
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        toolbar.addStretch(1)
        self._zoom_label = QLabel("适应窗口")
        toolbar.addWidget(self._zoom_label)
        for text, handler in (
            ("适应窗口", lambda: self._set_zoom(None)),
            ("100%", lambda: self._set_zoom(1.0)),
            ("+", lambda: self._step_zoom(1.25)),
            ("−", lambda: self._step_zoom(0.8)),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            toolbar.addWidget(widget)
        card.body.addLayout(toolbar)
        self._canvas = _PreviewCanvas(self, card)
        card.body.addWidget(self._canvas, 1)

        card.body.addWidget(QLabel("页面缩略图"))
        self._thumbs = QListWidget(card)
        self._thumbs.setViewMode(QListView.ViewMode.IconMode)
        self._thumbs.setFlow(QListView.Flow.LeftToRight)
        self._thumbs.setWrapping(False)
        self._thumbs.setResizeMode(QListView.ResizeMode.Adjust)
        self._thumbs.setMovement(QListView.Movement.Static)
        self._thumbs.setSpacing(4)
        self._thumbs.setFixedHeight(THUMB_STRIP_HEIGHT)
        self._thumbs.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self._thumbs.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumbs.currentRowChanged.connect(self._on_thumb_selected)
        card.body.addWidget(self._thumbs)
        layout.addWidget(card, 1)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form_card = Card("页面设置")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._layout_combo = QComboBox(form_card)
        for text, value in LAYOUT_OPTIONS:
            self._layout_combo.addItem(text, value)
        self._layout_combo.currentIndexChanged.connect(lambda _index: self._schedule_apply())
        self._title_edit = QLineEdit(form_card)
        self._title_edit.textChanged.connect(lambda _text: self._schedule_apply())
        self._subtitle_edit = QLineEdit(form_card)
        self._subtitle_edit.textChanged.connect(lambda _text: self._schedule_apply())
        self._bullets_text = QPlainTextEdit(form_card)
        self._bullets_text.setMinimumHeight(120)
        self._bullets_text.textChanged.connect(self._schedule_apply)
        self._note_text = QPlainTextEdit(form_card)
        self._note_text.setMinimumHeight(64)
        self._note_text.textChanged.connect(self._schedule_apply)
        for row, (label, widget) in enumerate(
            (
                ("布局", self._layout_combo),
                ("标题", self._title_edit),
                ("副标题", self._subtitle_edit),
                ("要点", self._bullets_text),
                ("备注", self._note_text),
            )
        ):
            grid.addWidget(QLabel(label), row, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, row, 1)
        grid.setRowStretch(3, 1)
        form_card.body.addLayout(grid, 1)
        layout.addWidget(form_card, 3)

        colors = Card("配色（点击修改）")
        color_grid = QGridLayout()
        color_grid.setContentsMargins(0, 0, 0, 0)
        color_grid.setSpacing(4)
        self._color_buttons: dict[str, QWidget] = {}
        for index, (name, label) in enumerate(core.PALETTE_FIELDS):
            widget = button(label)
            widget.setMinimumWidth(96)
            widget.clicked.connect(lambda _checked=False, field=name: self._pick_color(field))
            color_grid.addWidget(widget, index // 2, index % 2)
            self._color_buttons[name] = widget
        color_grid.setColumnStretch(0, 1)
        color_grid.setColumnStretch(1, 1)
        colors.body.addLayout(color_grid)
        layout.addWidget(colors)
        layout.addStretch(1)
        return panel

    # ------------------------------------------------------------------ #
    # 状态同步
    # ------------------------------------------------------------------ #
    @property
    def template(self) -> core.TemplateSpec:
        """当前编辑中的模板（画布与缩略图共用）。"""
        return self._template

    def _refresh_all(self) -> None:
        self._refresh_style_controls()
        self._refresh_slide_list()
        self._refresh_form()
        self._refresh_color_buttons()
        self._refresh_thumbnails()
        self._canvas.update()

    def _refresh_style_controls(self) -> None:
        self._loading = True
        try:
            self._name_edit.setText(self._template.name)
            self._select_value(self._style_combo, self._template.theme.style)
            self._select_value(self._aspect_combo, self._template.aspect)
            self._select_value(self._decor_combo, self._template.theme.decor)
            self._select_value(self._density_combo, self._template.theme.tokens.density)
            self._intensity_slider.setValue(round(self._template.theme.tokens.decor_intensity * 100))
            style = self._template.theme.style
            self._style_group.setExclusive(False)
            for value, action in self._style_actions.items():
                action.setChecked(value == style)
            self._style_group.setExclusive(True)
            decor = self._template.theme.decor
            for value, action in self._decor_actions.items():
                action.setChecked(value == decor)
        finally:
            self._loading = False

    @staticmethod
    def _select_value(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _refresh_slide_list(self) -> None:
        self._slides_list.blockSignals(True)
        self._slides_list.clear()
        for index, slide in enumerate(self._template.slides, start=1):
            self._slides_list.addItem(slide_label(index, slide))
        if self._template.slides:
            self.selected = min(self.selected, len(self._template.slides) - 1)
            self._slides_list.setCurrentRow(self.selected)
            item = self._slides_list.item(self.selected)
            if item is not None:
                self._slides_list.scrollToItem(item)
        else:
            self.selected = 0
        self._slides_list.blockSignals(False)

    def _refresh_form(self) -> None:
        self._loading = True
        try:
            if not self._template.slides:
                self._select_value(self._layout_combo, core.LAYOUT_BULLETS)
                self._title_edit.clear()
                self._subtitle_edit.clear()
                self._bullets_text.clear()
                self._note_text.clear()
                return
            slide = self._template.slides[self.selected]
            self._select_value(self._layout_combo, slide.layout)
            self._title_edit.setText(slide.title)
            self._subtitle_edit.setText(slide.subtitle)
            self._bullets_text.setPlainText("\n".join(slide.bullets))
            self._note_text.setPlainText(slide.note)
        finally:
            self._loading = False

    def _refresh_color_buttons(self) -> None:
        palette = self._template.theme.palette
        for name, widget in self._color_buttons.items():
            value = getattr(palette, name)
            foreground = "#111111" if core.relative_luminance(value) > 0.5 else "#FFFFFF"
            widget.setStyleSheet(
                f"background-color: {value}; color: {foreground}; border: 1px solid rgba(0, 0, 0, 60);"
                f"border-radius: 6px; padding: 4px 6px;"
            )

    def _refresh_thumbnails(self) -> None:
        self._syncing_thumb = True
        try:
            self._thumbs.clear()
            if not self._template.slides:
                return
            design_width, design_height = core.ASPECT_DESIGN[self._template.aspect]
            scale = THUMB_HEIGHT / design_height
            page_width = round(design_width * scale)
            dpr = self.devicePixelRatioF()
            self._thumbs.setIconSize(QSize(page_width, round(THUMB_HEIGHT)))
            ui = self.theme_menu.theme().palette
            for index, slide in enumerate(self._template.slides):
                pixmap = QPixmap(round(page_width * dpr), round(THUMB_HEIGHT * dpr))
                pixmap.setDevicePixelRatio(dpr)
                pixmap.fill(QColor(self._template.theme.palette.background))
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                with contextlib.suppress(core.TemplateError):
                    _paint_slide(
                        painter,
                        self._template,
                        slide,
                        float(page_width),
                        THUMB_HEIGHT,
                        zoom=scale,
                        ui=ui,
                        skip_text=True,
                    )
                painter.end()
                item = QListWidgetItem(QIcon(pixmap), str(index + 1))
                item.setSizeHint(QSize(page_width + 8, round(THUMB_HEIGHT) + 24))
                self._thumbs.addItem(item)
            self._thumbs.setCurrentRow(self.selected)
        finally:
            self._syncing_thumb = False

    # ------------------------------------------------------------------ #
    # 控件回调
    # ------------------------------------------------------------------ #
    def _on_name_changed(self, text: str) -> None:
        if self._loading:
            return
        self._template = replace(self._template, name=text)
        self.set_status("模板名称已更新")

    def _on_combo_changed(self, _index: int) -> None:
        if self._loading:
            return
        sender = self.sender()
        if sender is self._style_combo:
            self._apply_style(str(self._style_combo.currentData()))
        elif sender is self._aspect_combo:
            aspect = str(self._aspect_combo.currentData())
            self._template = replace(self._template, aspect=aspect)
            self._refresh_thumbnails()
            self._canvas.update()
            self.set_status(f"已切换画幅：{core.ASPECT_TEXT[aspect]}")
        elif sender is self._decor_combo:
            self._apply_decor(str(self._decor_combo.currentData()))
        elif sender is self._density_combo:
            density = str(self._density_combo.currentData())
            theme = self._template.theme
            self._template = replace(
                self._template, theme=replace(theme, tokens=replace(theme.tokens, density=density))
            )
            self._refresh_style_controls()
            self._canvas.update()
            self.set_status(f"已切换密度：{tokens.DENSITY_TEXT[density]}")

    def _apply_style(self, style: str) -> None:
        if style == core.STYLE_CUSTOM:
            return
        theme = replace(self._template.theme, style=style, palette=core.palette_for_style(style))
        self._template = replace(self._template, theme=theme)
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status(f"已切换风格：{core.style_label(style)}")

    def _apply_decor(self, decor: str) -> None:
        theme = replace(self._template.theme, decor=core.normalize_decor(decor))
        self._template = replace(self._template, theme=theme)
        self._refresh_style_controls()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status(f"已切换装饰：{core.DECOR_TEXT.get(decor, decor)}")

    def _on_decor_intensity(self, value: int) -> None:
        if self._loading:
            return
        intensity = max(0.0, min(1.0, value / 100))
        theme = self._template.theme
        if round(theme.tokens.decor_intensity, 3) == round(intensity, 3):
            return
        self._template = replace(
            self._template, theme=replace(theme, tokens=replace(theme.tokens, decor_intensity=intensity))
        )
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status(f"装饰强度：{intensity * 100:.0f}%")

    def _on_slide_selected(self, index: int) -> None:
        if self._syncing_thumb or index < 0:
            return
        self.selected = index
        self._refresh_form()
        self._syncing_thumb = True
        try:
            self._thumbs.setCurrentRow(self.selected)
        finally:
            self._syncing_thumb = False
        self._canvas.update()

    def _on_thumb_selected(self, index: int) -> None:
        if self._syncing_thumb or index < 0:
            return
        self.selected = index
        self._slides_list.blockSignals(True)
        self._slides_list.setCurrentRow(self.selected)
        item = self._slides_list.item(self.selected)
        if item is not None:
            self._slides_list.scrollToItem(item)
        self._slides_list.blockSignals(False)
        self._refresh_form()
        self._canvas.update()

    def _schedule_apply(self) -> None:
        if self._loading:
            return
        self._apply_timer.start()

    def _apply_form(self) -> None:
        if not self._template.slides:
            return
        slide = self._template.slides[self.selected]
        bullets = tuple(line.strip() for line in self._bullets_text.toPlainText().splitlines() if line.strip())
        updated = replace(
            slide,
            layout=str(self._layout_combo.currentData()),
            title=self._title_edit.text(),
            subtitle=self._subtitle_edit.text(),
            bullets=bullets,
            note=self._note_text.toPlainText(),
        )
        self._template = core.replace_slide(self._template, self.selected, updated)
        item = self._slides_list.item(self.selected)
        if item is not None:
            item.setText(slide_label(self.selected + 1, updated))
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status(f"第 {self.selected + 1} 页已更新")

    # ------------------------------------------------------------------ #
    # 页面动作
    # ------------------------------------------------------------------ #
    def _add_slide(self) -> None:
        current = self._layout_list.currentRow()
        layout = core.LAYOUT_BULLETS if current < 0 else LAYOUT_OPTIONS[current][1]
        slide = core.new_slide(layout)
        self._template = replace(self._template, slides=(*self._template.slides, slide))
        self.selected = len(self._template.slides) - 1
        self._refresh_slide_list()
        self._refresh_form()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status(f"已添加「{core.layout_label(layout)}」页面")

    def _duplicate_slide(self) -> None:
        if not self._template.slides:
            return
        slide = replace(
            self._template.slides[self.selected], title=f"{self._template.slides[self.selected].title}（副本）"
        )
        slides = list(self._template.slides)
        slides.insert(self.selected + 1, slide)
        self._template = replace(self._template, slides=tuple(slides))
        self.selected += 1
        self._refresh_slide_list()
        self._refresh_form()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status("已复制当前页面")

    def _delete_slide(self) -> None:
        if not self._template.slides:
            return
        slides = list(self._template.slides)
        slides.pop(self.selected)
        self._template = replace(self._template, slides=tuple(slides))
        self.selected = min(self.selected, max(len(slides) - 1, 0))
        self._refresh_slide_list()
        self._refresh_form()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status("已删除当前页面")

    def _move_slide(self, delta: int) -> None:
        slides = list(self._template.slides)
        target = self.selected + delta
        if not slides or target < 0 or target >= len(slides):
            return
        slides[self.selected], slides[target] = slides[target], slides[self.selected]
        self._template = replace(self._template, slides=tuple(slides))
        self.selected = target
        self._refresh_slide_list()
        self._refresh_form()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status("页面顺序已调整")

    def _pick_color(self, field: str) -> None:
        current = getattr(self._template.theme.palette, field)
        label = _PALETTE_LABELS[field]
        chosen = QColorDialog.getColor(QColor(current), self, f"选择{label}颜色")
        if not chosen.isValid():
            return
        try:
            normalized = core.normalize_hex(chosen.name())
        except core.TemplateError as exc:
            QMessageBox.critical(self, "颜色无效", str(exc))
            return
        palette = replace(self._template.theme.palette, **{field: normalized})
        self._template = replace(
            self._template,
            theme=replace(self._template.theme, style=core.STYLE_CUSTOM, palette=palette),
        )
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._refresh_thumbnails()
        self._canvas.update()
        self.set_status(f"已更新{label}颜色：{normalized}")

    # ------------------------------------------------------------------ #
    # 文件动作
    # ------------------------------------------------------------------ #
    def _new_template(self) -> None:
        answer = QMessageBox.question(
            self,
            "新建模板",
            "新建模板会丢弃当前未导出的编辑，继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._template = core.new_template(
            name="未命名模板",
            style=self._settings.style,
            aspect=self._settings.aspect,
            decor=self._settings.decor,
        )
        self.selected = 0
        self._refresh_all()
        self.set_status("已新建模板")

    def _import_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "导入模板或 PPTX",
            self._settings.last_dir or "",
            "PPT 模板 (*.pptmtpl.json);;PowerPoint (*.pptx);;JSON (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        source = Path(path)
        try:
            template = import_pptx(source) if source.suffix.lower() == ".pptx" else core.load_template(source)
        except core.TemplateError as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        self._settings.last_dir = str(source.parent)
        self._set_template(template)

    def _export_template(self) -> None:
        default_name = f"{safe_filename(self._template.name)}{core.TEMPLATE_SUFFIX}"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出模板",
            str(Path(self._settings.last_dir) / default_name) if self._settings.last_dir else default_name,
            f"PPT 模板 (*{core.TEMPLATE_SUFFIX});;JSON (*.json);;所有文件 (*.*)",
        )
        if not path:
            return
        target = Path(path)
        try:
            core.save_template(self._template, target)
        except core.TemplateError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._settings.last_dir = str(target.parent)
        self.set_status(f"模板已导出：{target}")

    def _export_pptx(self) -> None:
        default_name = f"{safe_filename(self._template.name)}.pptx"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出 PPTX",
            str(Path(self._settings.last_dir) / default_name) if self._settings.last_dir else default_name,
            "PowerPoint (*.pptx);;所有文件 (*.*)",
        )
        if not path:
            return
        target = Path(path)
        try:
            count = export_pptx(self._template, target)
        except core.TemplateError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._settings.last_dir = str(target.parent)
        self.set_status(f"PPTX 已导出：{target}（{count} 页）")
        QMessageBox.information(self, "导出完成", f"已生成 {count} 页：\n{target}")

    def _precise_preview(self) -> None:
        if _office.find_soffice() is None:
            QMessageBox.information(
                self,
                "精确预览不可用",
                "未检测到 LibreOffice（soffice）。\n当前使用画布预览；安装 LibreOffice 后可导出 PDF 精确核对版式。",
            )
            return
        workdir = Path(tempfile.mkdtemp(prefix="pptmake-preview-"))
        source = workdir / f"{safe_filename(self._template.name)}.pptx"
        try:
            count = export_pptx(self._template, source)
            pdf = _office.convert_to_pdf(source, workdir)
        except (core.TemplateError, _office.OfficeError) as exc:
            QMessageBox.critical(self, "精确预览失败", str(exc))
            return
        webbrowser.open(pdf.as_uri())
        self.set_status(f"已生成精确预览：{pdf}（{count} 页）")

    def _set_template(self, template: core.TemplateSpec) -> None:
        self._template = template
        self.selected = 0
        self._refresh_all()
        self.set_status(f"已载入：{template.name}（{len(template.slides)} 页）")

    def _show_help(self) -> None:
        QMessageBox.information(self, "使用说明", HELP_TEXT)

    # ------------------------------------------------------------------ #
    # 预览缩放 / 主题 / 持久化
    # ------------------------------------------------------------------ #
    def _set_zoom(self, zoom: float | None) -> None:
        """设置绝对缩放；``None`` 表示适应窗口。"""
        self.zoom = None if zoom is None else min(max(float(zoom), 0.05), 4.0)
        self._zoom_label.setText("适应窗口" if self.zoom is None else f"{self.zoom * 100:.0f}%")
        self._canvas.update()

    def _step_zoom(self, factor: float) -> None:
        current = self.zoom
        if current is None:
            width = max(self._canvas.width(), 2)
            height = max(self._canvas.height(), 2)
            design_width, design_height = core.ASPECT_DESIGN[self._template.aspect]
            current = preview.fit_viewport(design_width, design_height, width, height, padding=16.0).scale
        self._set_zoom(current * factor)

    def set_theme(self, theme: Any) -> None:
        """切换主题后重画画布与缩略图（其余控件由全局样式表处理）。"""
        super().set_theme(theme)
        self._refresh_thumbnails()
        self._canvas.update()

    def _persist_settings(self, geometry: str) -> None:
        """关闭时把风格 / 画幅 / 装饰、最近目录与窗口位置写回设置文件。"""
        settings = PptmakeSettings(
            style=self._template.theme.style,
            aspect=self._template.aspect,
            decor=self._template.theme.decor,
            last_dir=self._settings.last_dir,
            geometry=geometry,
        )
        save_settings(settings, self._own_settings_path)


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.qt.app import run_window

    run_window(lambda: PptmakeQtWindow(settings_path), "PPT 模板设计器")
