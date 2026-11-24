"""pptmakew 的 Qt 主窗口与入口。

窗口只做编排：模板数据结构、布局绘制列表与 PPTX 导入导出全部复用
:mod:`fcmd.apps.pptmake._core` / ``_design`` / ``_preview`` / ``_pptx``；
预览画布用 ``QPainter`` 按同一份绘制列表渲染，缩略图同样来自该列表，
因此画布、缩略图与导出的 PPTX 共用一套几何。

布局口径：沿用 llmw / wslmanw / filescanw 的侧边栏分页（:class:`~fcmd.gui.sidebar.SidebarNav`）：
- ``设计`` 页：三栏分栏（页面 / 布局库 | 预览 + 缩略图 | 页面设置），中间栏占满
  剩余宽度，跳页与改内容都在这里实时看到画布；
- ``样式`` 页：模板名称、风格 / 画幅 / 装饰 / 密度 / 装饰强度、六个语义色，底部
  一张实时预览，调样式不用来回切页。
两页都放在共享页面滚动容器里：窗口变矮时整页纵向滚动，画布保留最小高度，
不把表单压出卡片；页标题行挂「新建 / 导入 / 导出」快捷动作。
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
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from fcmd import __version__
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
    PAGE_KEYS,
    STYLE_OPTIONS,
    PptmakeSettings,
    load_settings,
    safe_filename,
    save_settings,
    slide_label,
)
from fcmd.gui.page import AppPage, PageSpec
from fcmd.gui.widgets import Card, PageScroll, button, make_splitter
from fcmd.gui.window import QtMainWindow

__all__ = ["PAGES", "PptmakeQtWindow", "run_qt"]

DEFAULT_SIZE = (1380, 860)
NAV_TITLE = "PPT 模板"

PAGES: tuple[PageSpec, ...] = (
    PageSpec("design", "设计", "页面列表与布局库、实时预览与缩略图、当前页内容编辑", "pencil"),
    PageSpec("style", "样式", "模板名称、风格 / 画幅 / 装饰 / 密度、六个语义色与实时预览", "paint"),
)
"""侧边栏页面顺序与提示：与 llmw / wslmanw / filescanw 共用同一套 ``SidebarNav``。"""

assert tuple(spec.key for spec in PAGES) == PAGE_KEYS
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


class _PptmakePage(AppPage):  # pragma: no cover - Qt 控件
    """pptmake 页面骨架：统一滚动页 + 页标题行动作。

    设计页与样式页都可能比窗口高（三栏面板 / 实时预览），统一附在共享
    :class:`~fcmd.gui.widgets.PageScroll` 里：窗口变矮时整页滚动，控件不被压出卡片。
    """

    def __init__(
        self,
        state: Any,
        key: str,
        *,
        margins: tuple[int, int, int, int] = (0, 0, 4, 8),
        spacing: int = 8,
    ) -> None:
        super().__init__(state, spacing=0)
        self.PAGE_KEY = key
        self._header: tuple[QWidget, ...] = ()
        self.scroll = PageScroll(self, margins=margins)
        self.body.addWidget(self.scroll, 1)
        self.content = self.scroll.body
        self.content.setSpacing(spacing)

    def set_header_widgets(self, widgets: tuple[QWidget, ...]) -> None:
        """登记页标题行右侧的快捷动作（窗口收集后只在当前页显示）。"""
        self._header = widgets

    def header_widgets(self) -> tuple[QWidget, ...]:
        """页标题行动作：设计页的「新建 / 导入 / 导出」快捷按钮。"""
        return self._header


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
        ui = self._window.theme().palette
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


class PptmakeQtWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """侧边栏设计器：设计（页面 / 预览 / 设置）+ 样式（模板与配色 + 实时预览）。"""

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
        self._canvases: list[_PreviewCanvas] = []
        super().__init__(
            title=f"pptmake - PPT 模板设计器 v{__version__}",
            config_name="pptmakew",
            nav_title=NAV_TITLE,
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist_settings,
            sidebar_collapsed=self._settings.sidebar_collapsed,
            settings_path=settings_path,
            theme_app="pptmakew",
        )
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(150)
        self._apply_timer.timeout.connect(self._apply_form)
        self._build_menus()
        self._header_buttons = self._make_header_buttons()
        self._design_page = self._build_design_page()
        self._style_page = self._build_style_page()
        self.add_pages(
            PAGES,
            {
                "design": lambda: self._design_page,
                "style": lambda: self._style_page,
            },
        )
        self.nav.collapsedChanged.connect(self._persist_sidebar_collapsed)
        self._refresh_all()
        # 恢复上次停留的页；首屏设计页已由 add_pages 自动打开，同页时不重复切换。
        saved_page = self._settings.active_page
        if saved_page in PAGE_KEYS and saved_page != self.current_page():
            self.show_page(saved_page)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_menus(self) -> None:
        """在骨架菜单上补齐文件 / 编辑 / 样式，保持「文件 → 编辑 → 样式 → 视图 → 帮助」顺序。"""
        quit_action = self.file_menu.actions()[-1]
        for text, handler in (
            ("新建模板", self._new_template),
            ("导入模板 / PPTX…", self._import_file),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            self.file_menu.insertAction(quit_action, action)
        self.file_menu.insertSeparator(quit_action)
        for text, handler in (
            ("导出模板…", self._export_template),
            ("导出 PPTX…", self._export_pptx),
            ("精确预览（PDF）…", self._precise_preview),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            self.file_menu.insertAction(quit_action, action)
        self.file_menu.insertSeparator(quit_action)

        edit_menu = QMenu("编辑", self)
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

        style_menu = QMenu("样式", self)
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

        # 「编辑」「样式」插到「视图」之前：菜单栏保持 文件 / 编辑 / 样式 / 视图 / 帮助。
        for menu in (edit_menu, style_menu):
            self.menuBar().insertMenu(self.view_menu.menuAction(), menu)

        # 公共「任务日志…」已在骨架里插到「关于」之前；应用帮助项排在它前面。
        anchor = self.log_action or self.about_action
        help_action = QAction("使用说明", self)
        help_action.triggered.connect(self._show_help)
        self.help_menu.insertAction(anchor, help_action)
        self.help_menu.insertSeparator(anchor)
        self.menuBar().update()

    def _make_header_buttons(self) -> tuple[QWidget, ...]:
        """设计页标题行的快捷动作：新建 / 导入 / 导出模板 / 导出 PPTX。"""
        widgets: list[QWidget] = []
        for text, icon, handler in (
            ("新建", "plus", self._new_template),
            ("导入", "folder-open", self._import_file),
            ("导出模板", "save", self._export_template),
            ("导出 PPTX", "export", self._export_pptx),
        ):
            widget = button(text, icon=icon)
            widget.clicked.connect(handler)
            widgets.append(widget)
        return tuple(widgets)

    def _build_design_page(self) -> _PptmakePage:
        """设计页：页面 / 布局库 | 预览 + 缩略图 | 页面设置，三栏可拖动。"""
        page = _PptmakePage(self, "design", margins=(0, 0, 0, 0))
        page.set_header_widgets(self._header_buttons)
        self._splitter = make_splitter(Qt.Orientation.Horizontal, parent=page)
        self._splitter.addWidget(self._build_pages_pane(page))
        self._splitter.addWidget(self._build_preview_pane(page))
        self._splitter.addWidget(self._build_inspector_pane(page))
        self._splitter.setSizes([260, 660, 320])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        page.content.addWidget(self._splitter, 1)
        return page

    def _build_style_page(self) -> _PptmakePage:
        """样式页：模板名称、风格 / 画幅 / 装饰 / 密度 / 装饰强度、配色与实时预览。"""
        page = _PptmakePage(self, "style")
        body = page.content

        template = Card("模板", page)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("模板名称", template))
        self._name_edit = QLineEdit(self._template.name, template)
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit, 1)
        template.body.addLayout(name_row)
        body.addWidget(template)

        controls = Card("风格与画幅", page)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._style_combo = self._make_combo(STYLE_OPTIONS)
        self._aspect_combo = self._make_combo(ASPECT_OPTIONS)
        self._decor_combo = self._make_combo(DECOR_OPTIONS)
        self._density_combo = self._make_combo(DENSITY_OPTIONS)
        self._intensity_slider = QSlider(Qt.Orientation.Horizontal, controls)
        self._intensity_slider.setRange(0, 100)
        self._intensity_slider.valueChanged.connect(self._on_decor_intensity)
        self._intensity_value = QLabel("", controls)
        self._intensity_value.setMinimumWidth(44)
        pairs = (
            ("风格", self._style_combo, "画幅", self._aspect_combo),
            ("装饰", self._decor_combo, "密度", self._density_combo),
        )
        for row, (left_label, left_widget, right_label, right_widget) in enumerate(pairs):
            grid.addWidget(QLabel(left_label, controls), row, 0)
            grid.addWidget(left_widget, row, 1)
            grid.addWidget(QLabel(right_label, controls), row, 2)
            grid.addWidget(right_widget, row, 3)
        grid.addWidget(QLabel("装饰强度", controls), 2, 0)
        grid.addWidget(self._intensity_slider, 2, 1, 1, 2)
        grid.addWidget(self._intensity_value, 2, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        controls.body.addLayout(grid)
        body.addWidget(controls)

        colors = Card("配色（点击修改）", page)
        color_grid = QGridLayout()
        color_grid.setContentsMargins(0, 0, 0, 0)
        color_grid.setSpacing(4)
        self._color_buttons: dict[str, QWidget] = {}
        for index, (name, label) in enumerate(core.PALETTE_FIELDS):
            widget = button(label)
            widget.setMinimumWidth(96)
            widget.clicked.connect(lambda _checked=False, field=name: self._pick_color(field))
            color_grid.addWidget(widget, index // 3, index % 3)
            self._color_buttons[name] = widget
        for column in range(3):
            color_grid.setColumnStretch(column, 1)
        colors.body.addLayout(color_grid)
        body.addWidget(colors)

        preview = Card("实时预览", page)
        self._style_canvas = _PreviewCanvas(self, preview)
        self._style_canvas.setMinimumHeight(220)
        self._canvases.append(self._style_canvas)
        preview.body.addWidget(self._style_canvas, 1)
        body.addWidget(preview, 1)
        return page

    def _build_pages_pane(self, parent: QWidget) -> QWidget:
        """设计页左栏：页面列表 + 布局库（双击添加）。"""
        panel = QWidget(parent)
        panel.setMinimumWidth(190)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        pages = Card("页面", panel)
        self._slides_list = QListWidget(pages)
        self._slides_list.setMinimumHeight(100)
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

        layouts = Card("布局库（双击添加）", panel)
        self._layout_list = QListWidget(layouts)
        self._layout_list.setMinimumHeight(90)
        for text, _value in LAYOUT_OPTIONS:
            self._layout_list.addItem(text)
        self._layout_list.setCurrentRow(0)
        self._layout_list.itemDoubleClicked.connect(lambda _item: self._add_slide())
        layouts.body.addWidget(self._layout_list, 1)
        layout.addWidget(layouts, 2)
        return panel

    def _build_preview_pane(self, parent: QWidget) -> QWidget:
        """设计页中栏：缩放工具栏 + 实时预览 + 页面缩略图。"""
        card = Card("预览", parent)
        card.setMinimumWidth(220)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        toolbar.addStretch(1)
        self._zoom_label = QLabel("适应窗口", card)
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
        self._canvas.setMinimumHeight(200)
        self._canvases.append(self._canvas)
        card.body.addWidget(self._canvas, 1)

        card.body.addWidget(QLabel("页面缩略图", card))
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
        return card

    def _build_inspector_pane(self, parent: QWidget) -> QWidget:
        """设计页右栏：当前页的布局 / 标题 / 副标题 / 要点 / 备注。"""
        panel = QWidget(parent)
        panel.setMinimumWidth(220)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form_card = Card("页面设置", panel)
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
            grid.addWidget(QLabel(label, form_card), row, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, row, 1)
        grid.setRowStretch(3, 1)
        form_card.body.addLayout(grid, 1)
        layout.addWidget(form_card, 1)
        return panel

    def _make_combo(self, options: tuple[tuple[str, str], ...]) -> QComboBox:
        combo = QComboBox(self)
        for text, value in options:
            combo.addItem(text, value)
        combo.currentIndexChanged.connect(self._on_combo_changed)
        return combo

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
        self._refresh_canvases()

    def _refresh_canvases(self) -> None:
        """重画所有预览画布（设计页实时预览 + 样式页实时预览）。"""
        for canvas in self._canvases:
            canvas.update()

    def _refresh_style_controls(self) -> None:
        self._loading = True
        try:
            self._name_edit.setText(self._template.name)
            self._select_value(self._style_combo, self._template.theme.style)
            self._select_value(self._aspect_combo, self._template.aspect)
            self._select_value(self._decor_combo, self._template.theme.decor)
            self._select_value(self._density_combo, self._template.theme.tokens.density)
            intensity = round(self._template.theme.tokens.decor_intensity * 100)
            self._intensity_slider.setValue(intensity)
            self._intensity_value.setText(f"{intensity}%")
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
            ui = self.theme().palette
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
            self._refresh_canvases()
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
            self._refresh_canvases()
            self.set_status(f"已切换密度：{tokens.DENSITY_TEXT[density]}")

    def _apply_style(self, style: str) -> None:
        if style == core.STYLE_CUSTOM:
            return
        theme = replace(self._template.theme, style=style, palette=core.palette_for_style(style))
        self._template = replace(self._template, theme=theme)
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._refresh_thumbnails()
        self._refresh_canvases()
        self.set_status(f"已切换风格：{core.style_label(style)}")

    def _apply_decor(self, decor: str) -> None:
        theme = replace(self._template.theme, decor=core.normalize_decor(decor))
        self._template = replace(self._template, theme=theme)
        self._refresh_style_controls()
        self._refresh_thumbnails()
        self._refresh_canvases()
        self.set_status(f"已切换装饰：{core.DECOR_TEXT.get(decor, decor)}")

    def _on_decor_intensity(self, value: int) -> None:
        self._intensity_value.setText(f"{value}%")
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
        self._refresh_canvases()
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
        self._refresh_canvases()

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
        self._refresh_canvases()

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
        self._refresh_canvases()
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
        self._refresh_canvases()
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
        self._refresh_canvases()
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
        self._refresh_canvases()
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
        self._refresh_canvases()
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
        self._refresh_canvases()
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
        self._refresh_canvases()

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
        self._refresh_canvases()

    def _persist_settings(self, geometry: str) -> None:
        """关闭时把风格 / 画幅 / 装饰、当前页 / 侧边栏、最近目录与窗口位置写回设置文件。"""
        settings = PptmakeSettings(
            style=self._template.theme.style,
            aspect=self._template.aspect,
            decor=self._template.theme.decor,
            last_dir=self._settings.last_dir,
            geometry=geometry,
            sidebar_collapsed=self.nav.is_collapsed(),
            active_page=self.current_page() or self._settings.active_page,
        )
        self._settings = settings
        save_settings(settings, self._own_settings_path)

    def _persist_sidebar_collapsed(self, collapsed: bool) -> None:
        """侧边栏折叠状态随窗口设置落盘（与其它侧边栏窗口同一口径）。"""
        self._settings = replace(self._settings, sidebar_collapsed=collapsed)
        self._persist_settings(self.geometry_text())


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.app import run_window

    run_window(lambda: PptmakeQtWindow(settings_path), app_name="PPT 模板设计器")
