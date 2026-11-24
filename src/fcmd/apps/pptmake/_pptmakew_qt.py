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
import time
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd import __version__
from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _office
from fcmd.apps.pptmake import _preview as preview
from fcmd.apps.pptmake import _tokens as tokens
from fcmd.apps.pptmake._design import Line, Oval, Picture, Rect, Text, build_draw_list
from fcmd.apps.pptmake._history import TemplateHistory
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
    remember_path,
    safe_filename,
    save_settings,
    slide_label,
)
from fcmd.gui.icons import set_icon as _set_icon
from fcmd.gui.page import AppPage, PageSpec
from fcmd.gui.widgets import (
    Card,
    FlowLayout,
    PageScroll,
    apply_splitter_sizes,
    button,
    icon_button,
    make_splitter,
    splitter_sizes,
    tab_widget,
)
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

_PALETTE_LABELS = dict(core.PALETTE_FIELDS)

_LAYOUT_HINTS: dict[str, str] = {
    core.LAYOUT_METRICS: "数据页要点写成「98%|交付准时率」，每行一个指标。",
    core.LAYOUT_TIMELINE: "时间线要点写成「01|调研」，序号在前、标题在后。",
    core.LAYOUT_TWO_COLUMN: "双栏页副标题写成「左栏标题|右栏标题」，要点按行分配。",
    core.LAYOUT_COMPARISON: "对比矩阵要点写成「维度|方案A|方案B」，每行一个维度。",
    core.LAYOUT_CHART: "图表要点写成「Q1|42」，竖线前的标签作为横轴。",
    core.LAYOUT_CONTACT: "联系页要点写成「邮箱|hello@example.com」，每行一个渠道。",
}
_LAYOUT_HINT_DEFAULT = "切换布局后会保留标题、要点与备注内容；格式提示按布局变化。"


def _action_button(icon_name: str, tooltip: str, *, text: str = "") -> QWidget:
    """紧凑动作按钮：只显示图标（或极短标记）+ tooltip，窄面板不会被文字撑爆。"""
    widget = icon_button(text, tooltip=tooltip)
    _set_icon(widget, icon_name, tone="muted")
    return widget


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


def _paint_picture(painter: QPainter, primitive: Picture, view: preview.Viewport, ui: Any) -> None:
    """按 cover / contain 把本地图片画进图元矩形；缺失时画占位提示。"""
    scale = view.scale
    x, y = view.to_canvas(primitive.x, primitive.y)
    width = primitive.w * scale
    height = primitive.h * scale
    if width <= 0 or height <= 0:
        return
    target = QRectF(x, y, width, height)
    pixmap = QPixmap(primitive.path) if primitive.path else QPixmap()
    painter.save()
    painter.setClipRect(target)
    if pixmap.isNull():
        painter.setPen(QPen(QColor(ui.muted)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target)
        painter.drawText(target, int(Qt.AlignmentFlag.AlignCenter), "图片缺失")
        painter.restore()
        return
    image_width = pixmap.width()
    image_height = pixmap.height()
    box_ratio = width / height
    image_ratio = image_width / max(image_height, 1)
    if primitive.fit == "contain":
        scaled = pixmap.scaled(
            round(width),
            round(height),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        left = x + (width - scaled.width()) / 2
        top = y + (height - scaled.height()) / 2
        painter.drawPixmap(QRectF(left, top, scaled.width(), scaled.height()), scaled, QRectF(scaled.rect()))
        painter.restore()
        return
    if image_ratio > box_ratio:
        source_width = image_height * box_ratio
        source = QRectF((image_width - source_width) / 2, 0, source_width, image_height)
    else:
        source_height = image_width / box_ratio
        source = QRectF(0, (image_height - source_height) / 2, image_width, source_height)
    painter.drawPixmap(target, pixmap, source)
    painter.restore()


def _paint_primitive(painter: QPainter, primitive: Any, view: preview.Viewport, ui: Any) -> None:
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
    if isinstance(primitive, Picture):
        _paint_picture(painter, primitive, view, ui)
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
    page_number: int | None = None,
    page_total: int | None = None,
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
    primitives = build_draw_list(slide, template, page_number=page_number, page_total=page_total)
    painter.save()
    painter.setClipRect(page)
    painter.fillRect(page, QColor(template.theme.palette.background))
    for primitive in primitives:
        if skip_text and isinstance(primitive, Text):
            continue
        _paint_primitive(painter, primitive, view, ui)
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


def _draw_overflow_badge(painter: QPainter, width: float, count: int, ui: Any) -> None:
    """缩略图右上角的溢出角标：数字表示该页预计溢出的文本框数量。"""
    text = str(count)
    font = QFont(QApplication.font())
    font.setPixelSize(10)
    font.setBold(True)
    painter.setFont(font)
    badge_width = max(14.0, painter.fontMetrics().horizontalAdvance(text) + 8.0)
    badge = QRectF(width - badge_width - 3.0, 3.0, badge_width, 14.0)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ui.warning))
    painter.drawRoundedRect(badge, 7.0, 7.0)
    painter.setPen(QPen(QColor(ui.background)))
    painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), text)
    painter.restore()


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


class _ExportOptionsDialog(QDialog):  # pragma: no cover - 对话框
    """导出选项：页码范围与是否同时导出 PDF。"""

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出 PPTX")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        range_row = QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.addWidget(QLabel("页码范围", self))
        self.start = QSpinBox(self)
        self.start.setRange(1, max(total, 1))
        self.start.setValue(1)
        self.end = QSpinBox(self)
        self.end.setRange(1, max(total, 1))
        self.end.setValue(max(total, 1))
        range_row.addWidget(self.start)
        range_row.addWidget(QLabel("至", self))
        range_row.addWidget(self.end)
        range_row.addStretch(1)
        layout.addLayout(range_row)
        self.pdf = QCheckBox("同时导出 PDF（需要 LibreOffice）", self)
        self.pdf.setChecked(False)
        layout.addWidget(self.pdf)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(360, self.sizeHint().height())

    def options(self) -> tuple[int, int, bool]:
        """返回 ``(起始页, 结束页, 是否导出 PDF)``；页码从 1 开始。"""
        start = min(self.start.value(), self.end.value())
        end = max(self.start.value(), self.end.value())
        return start, end, self.pdf.isChecked()


class _PresenterWindow(QWidget):  # pragma: no cover - 需要 Qt 事件循环
    """F5 全屏放映：←/→ 翻页、Home/End 跳转、Esc 退出。"""

    def __init__(self, window: PptmakeQtWindow, start: int) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self._window = window
        self._index = max(0, min(start, len(window.template.slides) - 1))
        self.setWindowTitle("pptmake 放映")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: black;")

    def paintEvent(self, event: Any) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        ui = self._window.theme().palette
        painter.fillRect(self.rect(), QColor("#000000"))
        template = self._window.template
        if not template.slides:
            _paint_message(painter, float(self.width()), float(self.height()), "没有可放映的页面", ui.muted)
            return
        slide = template.slides[self._index]
        _paint_slide(
            painter,
            template,
            slide,
            float(self.width()),
            float(self.height()),
            zoom=None,
            ui=ui,
            page_number=self._index + 1,
            page_total=len(template.slides),
        )

    def keyPressEvent(self, event: Any) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_F5):
            self.close()
            return
        if key in (
            Qt.Key.Key_Right,
            Qt.Key.Key_Down,
            Qt.Key.Key_Space,
            Qt.Key.Key_PageDown,
        ):
            self._go(self._index + 1)
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Backspace, Qt.Key.Key_PageUp):
            self._go(self._index - 1)
            return
        if key == Qt.Key.Key_Home:
            self._go(0)
            return
        if key == Qt.Key.Key_End:
            self._go(len(self._window.template.slides) - 1)
            return
        super().keyPressEvent(event)

    def _go(self, index: int) -> None:
        total = len(self._window.template.slides)
        if total == 0:
            return
        self._index = max(0, min(index, total - 1))
        self.update()


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
                page_number=self._window.selected + 1,
                page_total=len(template.slides),
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
        self._history = TemplateHistory(self._template)
        self._dirty = False
        self._layout_icons: dict[str, QIcon] = {}
        self._sorter_view = self._settings.sorter_view
        self._inspector_visible = self._settings.inspector_visible
        super().__init__(
            title=f"pptmake - PPT 模板设计器 v{__version__}",
            config_name="pptmakew",
            nav_title=NAV_TITLE,
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist_settings,
            sidebar_collapsed=self._settings.sidebar_collapsed,
            settings_path=settings_path,
            theme_app="pptmakew",
        )
        self._base_title = self.windowTitle()
        self._draft_path = self.settings_path.parent / "pptmakew-draft.pptmtpl.json"
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(150)
        self._apply_timer.timeout.connect(self._apply_form)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
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
        self._autosave_timer.start()
        # 恢复上次停留的页；首屏设计页已由 add_pages 自动打开，同页时不重复切换。
        saved_page = self._settings.active_page
        if saved_page in PAGE_KEYS and saved_page != self.current_page():
            self.show_page(saved_page)
        # 草稿恢复放在窗口显示后：避免构造期弹框挡住主窗口（演示 / 测试也不阻塞）。
        self._recover_timer = QTimer(self)
        self._recover_timer.setSingleShot(True)
        self._recover_timer.timeout.connect(self._maybe_recover_draft)
        self._recover_timer.start(300)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_menus(self) -> None:
        """在骨架菜单上补齐文件 / 编辑 / 样式，保持「文件 → 编辑 → 样式 → 视图 → 帮助」顺序。"""
        quit_action = self.file_menu.actions()[-1]
        new_action: QAction | None = None
        for text, handler in (
            ("新建模板", self._new_template),
            ("导入模板 / PPTX…", self._import_file),
        ):
            action = QAction(text, self)
            action.triggered.connect(handler)
            self.file_menu.insertAction(quit_action, action)
            if text == "新建模板":
                new_action = action
        sample_menu = QMenu("从示例模板新建", self)
        for style in core.STYLES:
            action = QAction(core.STYLE_TEXT[style], self)
            action.triggered.connect(lambda _checked=False, item=style: self._new_from_sample(item))
            sample_menu.addAction(action)
        if new_action is not None:
            self.file_menu.insertMenu(new_action, sample_menu)
        self.file_menu.insertSeparator(quit_action)
        self._recent_menu = QMenu("最近模板", self)
        self.file_menu.insertMenu(quit_action, self._recent_menu)
        self._rebuild_recent_menu()
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
        self._undo_action = QAction("撤销", self)
        self._undo_action.setShortcut("Ctrl+Z")
        self._undo_action.triggered.connect(self._undo)
        self._redo_action = QAction("重做", self)
        self._redo_action.setShortcuts(["Ctrl+Y", "Ctrl+Shift+Z"])
        self._redo_action.triggered.connect(self._redo)
        edit_menu.addAction(self._undo_action)
        edit_menu.addAction(self._redo_action)
        edit_menu.addSeparator()
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

        self._present_action = QAction("放映预览", self)
        self._present_action.setShortcut("F5")
        self._present_action.triggered.connect(self._present)
        self.view_menu.addAction(self._present_action)
        self._sync_history_actions()
        self.menuBar().update()

    def _rebuild_recent_menu(self) -> None:
        """按设置里的最近模板重建「文件 → 最近模板」子菜单。"""
        self._recent_menu.clear()
        if not self._settings.recent_templates:
            empty = self._recent_menu.addAction("（暂无）")
            empty.setEnabled(False)
            return
        for item in self._settings.recent_templates:
            action = self._recent_menu.addAction(item)
            action.triggered.connect(lambda _checked=False, path=item: self._open_recent(path))

    def _remember_recent(self, path: Path) -> None:
        """把路径写入最近模板并刷新菜单 / 设置。"""
        self._settings = replace(self._settings, recent_templates=remember_path(self._settings.recent_templates, path))
        self._rebuild_recent_menu()

    def _open_recent(self, raw: str) -> None:
        """打开最近模板；文件已不存在时从列表移除。"""
        path = Path(raw)
        if not path.is_file():
            self._settings = replace(
                self._settings,
                recent_templates=tuple(item for item in self._settings.recent_templates if item != raw),
            )
            self._rebuild_recent_menu()
            self.set_status(f"最近模板已不存在：{raw}")
            return
        try:
            template = core.load_template(path)
        except core.TemplateError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self._remember_recent(path)
        self._set_template(template)

    def _make_header_buttons(self) -> tuple[QWidget, ...]:
        """设计页标题行的快捷动作（仅图标）：新建 / 导入 / 导出模板 / 导出 PPTX。"""
        widgets: list[QWidget] = []
        for icon, tooltip, handler in (
            ("plus", "新建模板", self._new_template),
            ("folder-open", "导入模板 / PPTX", self._import_file),
            ("save", "导出模板（JSON）", self._export_template),
            ("export", "导出 PPTX", self._export_pptx),
        ):
            widget = _action_button(icon, tooltip)
            widget.clicked.connect(handler)
            widgets.append(widget)
        self._inspector_toggle = _action_button(
            "panel-left-close" if self._inspector_visible else "panel-left-open",
            "收起 / 展开页面设置",
        )
        self._inspector_toggle.clicked.connect(self._toggle_inspector)
        widgets.append(self._inspector_toggle)
        return tuple(widgets)

    def _build_design_page(self) -> _PptmakePage:
        """设计页：页面 / 布局库 | 预览 + 缩略图 | 页面设置，三栏可拖动。"""
        page = _PptmakePage(self, "design", margins=(0, 0, 0, 0))
        page.set_header_widgets(self._header_buttons)
        self._splitter = make_splitter(Qt.Orientation.Horizontal, parent=page)
        self._splitter.addWidget(self._build_pages_pane(page))
        self._splitter.addWidget(self._build_preview_pane(page))
        self._inspector_pane = self._build_inspector_pane(page)
        self._inspector_pane.setVisible(self._inspector_visible)
        self._splitter.addWidget(self._inspector_pane)
        self._splitter.setSizes([260, 660, 320])
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 0)
        apply_splitter_sizes(self._splitter, list(self._settings.design_splitter))
        page.content.addWidget(self._splitter, 1)
        return page

    def _build_style_page(self) -> _PptmakePage:
        """样式页：左侧设置卡、右侧实时预览，宽屏并排、窄屏自动换行。"""
        page = _PptmakePage(self, "style")
        body = page.content

        left = QWidget(page)
        left.setMinimumWidth(320)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        template = Card("模板", left)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(QLabel("模板名称", template))
        self._name_edit = QLineEdit(self._template.name, template)
        self._name_edit.textChanged.connect(self._on_name_changed)
        name_row.addWidget(self._name_edit, 1)
        template.body.addLayout(name_row)
        left_layout.addWidget(template)

        controls = Card("风格与画幅", left)
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
        self._footer_edit = QLineEdit(self._template.theme.footer, controls)
        self._footer_edit.setPlaceholderText("页脚文字（空则不显示）")
        self._footer_edit.textChanged.connect(self._on_footer_changed)
        self._page_number_check = QCheckBox("显示页码", controls)
        self._page_number_check.toggled.connect(self._on_page_number_toggled)
        grid.addWidget(QLabel("页脚", controls), 3, 0)
        grid.addWidget(self._footer_edit, 3, 1, 1, 2)
        grid.addWidget(self._page_number_check, 3, 3)
        self._heading_font_combo = QFontComboBox(controls)
        self._heading_font_combo.currentFontChanged.connect(lambda _font: self._on_font_changed(True))
        self._body_font_combo = QFontComboBox(controls)
        self._body_font_combo.currentFontChanged.connect(lambda _font: self._on_font_changed(False))
        grid.addWidget(QLabel("标题字体", controls), 4, 0)
        grid.addWidget(self._heading_font_combo, 4, 1)
        grid.addWidget(QLabel("正文字体", controls), 4, 2)
        grid.addWidget(self._body_font_combo, 4, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        controls.body.addLayout(grid)
        left_layout.addWidget(controls)

        colors = Card("配色（点击修改）", left)
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
        left_layout.addWidget(colors)
        left_layout.addStretch(1)

        preview = Card("实时预览", page)
        preview.setMinimumWidth(260)
        self._style_canvas = _PreviewCanvas(self, preview)
        self._style_canvas.setMinimumHeight(260)
        self._canvases.append(self._style_canvas)
        preview.body.addWidget(self._style_canvas, 1)

        self._style_splitter = make_splitter(Qt.Orientation.Horizontal, parent=page)
        self._style_splitter.addWidget(left)
        self._style_splitter.addWidget(preview)
        self._style_splitter.setSizes([380, 640])
        self._style_splitter.setStretchFactor(0, 0)
        self._style_splitter.setStretchFactor(1, 1)
        apply_splitter_sizes(self._style_splitter, list(self._settings.style_splitter))
        body.addWidget(self._style_splitter, 1)
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
        self._slides_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._slides_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._slides_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._slides_list.setDropIndicatorShown(True)
        self._slides_list.currentRowChanged.connect(self._on_slide_selected)
        self._slides_list.model().rowsMoved.connect(self._on_slides_reordered)
        pages.body.addWidget(self._slides_list, 1)
        # 动作条用流式布局：窄面板下整块换行，不再把后面的按钮裁掉。
        page_buttons = FlowLayout(h_spacing=4, v_spacing=4)
        for icon, tooltip, handler in (
            ("plus", "添加页面", self._add_slide),
            ("copy", "复制页面", self._duplicate_slide),
            ("trash", "删除页面", self._delete_slide),
            ("chevron-up", "上移页面", lambda: self._move_slide(-1)),
            ("chevron-down", "下移页面", lambda: self._move_slide(1)),
        ):
            widget = _action_button(icon, tooltip)
            widget.clicked.connect(handler)
            page_buttons.addWidget(widget)
        self._view_button = _action_button("log", "切换为列表视图")
        self._view_button.clicked.connect(self._toggle_sorter_view)
        page_buttons.addWidget(self._view_button)
        self._sync_sorter_view_button()
        pages.body.addLayout(page_buttons)
        layout.addWidget(pages, 3)

        layouts = Card("布局库（双击添加）", panel)
        self._layout_list = QListWidget(layouts)
        self._layout_list.setMinimumHeight(110)
        self._layout_list.setViewMode(QListView.ViewMode.IconMode)
        self._layout_list.setResizeMode(QListView.ResizeMode.Adjust)
        self._layout_list.setMovement(QListView.Movement.Static)
        self._layout_list.setWrapping(True)
        self._layout_list.setSpacing(4)
        self._layout_list.setIconSize(QSize(88, 50))
        self._layout_list.setGridSize(QSize(110, 82))
        for text, value in LAYOUT_OPTIONS:
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self._layout_list.addItem(item)
        self._layout_list.setCurrentRow(0)
        self._layout_list.itemDoubleClicked.connect(lambda _item: self._add_slide())
        self._refresh_layout_icons()
        layouts.body.addWidget(self._layout_list, 1)
        layout.addWidget(layouts, 2)
        return panel

    def _build_preview_pane(self, parent: QWidget) -> QWidget:
        """设计页中栏：缩放工具栏 + 实时预览 + 页面缩略图。"""
        card = Card("预览", parent)
        card.setMinimumWidth(220)
        toolbar = FlowLayout(h_spacing=4, v_spacing=4)
        self._zoom_label = QLabel("适应窗口", card)
        toolbar.addWidget(self._zoom_label)
        for icon, tooltip, text, handler in (
            ("maximize", "适应窗口", "", lambda: self._set_zoom(None)),
            ("scan", "实际大小（100%）", "", lambda: self._set_zoom(1.0)),
            ("zoom-in", "放大", "", lambda: self._step_zoom(1.25)),
            ("zoom-out", "缩小", "", lambda: self._step_zoom(0.8)),
        ):
            widget = _action_button(icon, tooltip, text=text)
            widget.clicked.connect(handler)
            toolbar.addWidget(widget)
        card.body.addLayout(toolbar)
        self._canvas = _PreviewCanvas(self, card)
        self._canvas.setMinimumHeight(200)
        self._canvases.append(self._canvas)
        card.body.addWidget(self._canvas, 1)
        return card

    def _build_inspector_pane(self, parent: QWidget) -> QWidget:
        """设计页右栏：当前页的布局 / 标题 / 副标题 / 要点 / 备注。"""
        panel = QWidget(parent)
        panel.setMinimumWidth(220)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        form_card = Card("页面设置", panel)
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
        self._note_text.setMinimumHeight(120)
        self._note_text.textChanged.connect(self._schedule_apply)

        self._inspector_tabs = tab_widget(form_card)
        content_tab = QWidget(self._inspector_tabs)
        content_grid = QGridLayout(content_tab)
        content_grid.setContentsMargins(4, 6, 4, 4)
        content_grid.setHorizontalSpacing(8)
        content_grid.setVerticalSpacing(6)
        content_grid.setColumnStretch(1, 1)
        for row, (label, widget) in enumerate(
            (
                ("标题", self._title_edit),
                ("副标题", self._subtitle_edit),
                ("要点", self._bullets_text),
            )
        ):
            content_grid.addWidget(
                QLabel(label, content_tab), row, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
            )
            content_grid.addWidget(widget, row, 1)
        content_grid.setRowStretch(2, 1)
        self._inspector_tabs.addTab(content_tab, "内容")

        layout_tab = QWidget(self._inspector_tabs)
        layout_grid = QGridLayout(layout_tab)
        layout_grid.setContentsMargins(4, 6, 4, 4)
        layout_grid.setHorizontalSpacing(8)
        layout_grid.setVerticalSpacing(6)
        layout_grid.setColumnStretch(1, 1)
        layout_grid.addWidget(QLabel("布局", layout_tab), 0, 0)
        layout_grid.addWidget(self._layout_combo, 0, 1)
        self._layout_hint = QLabel("", layout_tab)
        self._layout_hint.setWordWrap(True)
        self._layout_hint.setObjectName("Hint")
        layout_grid.addWidget(self._layout_hint, 1, 0, 1, 2)

        self._image_edit = QLineEdit(layout_tab)
        self._image_edit.setPlaceholderText("图片路径（可留空）")
        self._image_edit.textChanged.connect(lambda _text: self._schedule_apply())
        image_row = QHBoxLayout()
        image_row.setContentsMargins(0, 0, 0, 0)
        image_row.setSpacing(4)
        image_row.addWidget(self._image_edit, 1)
        browse = button("选择…")
        browse.clicked.connect(self._pick_image)
        image_row.addWidget(browse)
        clear = button("清除")
        clear.clicked.connect(self._image_edit.clear)
        image_row.addWidget(clear)
        layout_grid.addWidget(QLabel("图片", layout_tab), 2, 0, Qt.AlignmentFlag.AlignTop)
        layout_grid.addLayout(image_row, 2, 1)

        self._image_fit_combo = QComboBox(layout_tab)
        for value in core.IMAGE_FITS:
            self._image_fit_combo.addItem(core.IMAGE_FIT_TEXT[value], value)
        self._image_fit_combo.currentIndexChanged.connect(lambda _index: self._schedule_apply())
        layout_grid.addWidget(QLabel("填充", layout_tab), 3, 0)
        layout_grid.addWidget(self._image_fit_combo, 3, 1)

        self._accent_value = ""
        self._accent_button = button("本页强调色")
        self._accent_button.setMinimumWidth(120)
        self._accent_button.clicked.connect(self._pick_slide_accent)
        accent_clear = button("恢复默认")
        accent_clear.clicked.connect(self._clear_slide_accent)
        accent_row = QHBoxLayout()
        accent_row.setContentsMargins(0, 0, 0, 0)
        accent_row.setSpacing(4)
        accent_row.addWidget(self._accent_button)
        accent_row.addWidget(accent_clear)
        accent_row.addStretch(1)
        layout_grid.addWidget(QLabel("强调色", layout_tab), 4, 0)
        layout_grid.addLayout(accent_row, 4, 1)
        layout_grid.setRowStretch(5, 1)
        self._inspector_tabs.addTab(layout_tab, "版式")

        notes_tab = QWidget(self._inspector_tabs)
        notes_layout = QVBoxLayout(notes_tab)
        notes_layout.setContentsMargins(4, 6, 4, 4)
        notes_layout.addWidget(self._note_text, 1)
        self._inspector_tabs.addTab(notes_tab, "备注")
        form_card.body.addWidget(self._inspector_tabs, 1)
        layout.addWidget(form_card, 1)
        return panel

    def _make_combo(self, options: tuple[tuple[str, str], ...]) -> QComboBox:
        combo = QComboBox(self)
        for text, value in options:
            combo.addItem(text, value)
        combo.currentIndexChanged.connect(self._on_combo_changed)
        return combo

    # ------------------------------------------------------------------ #
    # 编辑历史 / 草稿
    # ------------------------------------------------------------------ #
    def _commit(self, template: core.TemplateSpec, label: str, *, coalesce_key: str = "") -> bool:
        """记录一次模板变更（撤销快照 + dirty 标记）；模板未变时返回 ``False``。"""
        if template == self._template:
            return False
        self._history.record(template, label, coalesce_key=coalesce_key)
        self._template = template
        self._mark_dirty()
        self._sync_history_actions()
        return True

    def _mark_dirty(self, dirty: bool = True) -> None:
        """更新 dirty 状态与标题栏星号。"""
        self._dirty = dirty
        self._sync_title()

    def _sync_title(self) -> None:
        base = getattr(self, "_base_title", self.windowTitle())
        self.setWindowTitle(f"{base} *" if self._dirty else base)

    def _sync_history_actions(self) -> None:
        """按历史栈刷新撤销 / 重做菜单项的可用状态与提示。"""
        undo = getattr(self, "_undo_action", None)
        redo = getattr(self, "_redo_action", None)
        if undo is not None:
            label = self._history.undo_label()
            undo.setEnabled(bool(label))
            undo.setToolTip(f"撤销「{label}」" if label else "没有可撤销的操作")
        if redo is not None:
            label = self._history.redo_label()
            redo.setEnabled(bool(label))
            redo.setToolTip(f"重做「{label}」" if label else "没有可重做的操作")

    def _undo(self) -> None:
        label = self._history.undo_label()
        template = self._history.undo()
        if template is None:
            return
        self._restore_template(template, f"已撤销：{label}")

    def _redo(self) -> None:
        label = self._history.redo_label()
        template = self._history.redo()
        if template is None:
            return
        self._restore_template(template, f"已重做：{label}")

    def _restore_template(self, template: core.TemplateSpec, message: str) -> None:
        """把撤销 / 重做得到的历史快照套回窗口并全量刷新。"""
        self._template = template
        self.selected = min(self.selected, max(len(template.slides) - 1, 0))
        self._mark_dirty()
        self._sync_history_actions()
        self._refresh_all()
        self.set_status(message)

    def _autosave(self) -> None:
        """定时把未保存的模板写进草稿文件；无改动时不写盘。"""
        if not self._dirty or not self._template.slides:
            return
        try:
            core.save_template(self._template, self._draft_path)
        except (OSError, core.TemplateError):
            return
        self.set_status_note(f"草稿已自动保存 {time.strftime('%H:%M')}")

    def _discard_draft(self) -> None:
        """删除本地草稿（关闭时选择不保存 / 恢复失败）。"""
        with contextlib.suppress(OSError):
            self._draft_path.unlink(missing_ok=True)

    def _maybe_recover_draft(self) -> None:
        """启动后检测上次自动保存的草稿，询问是否恢复。"""
        if not self._draft_path.is_file():
            return
        stamp = time.strftime("%m-%d %H:%M", time.localtime(self._draft_path.stat().st_mtime))
        answer = QMessageBox.question(
            self,
            "恢复草稿",
            f"检测到未保存的草稿（{stamp}），是否恢复？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._discard_draft()
            return
        try:
            template = core.load_template(self._draft_path)
        except (OSError, core.TemplateError):
            self._discard_draft()
            return
        self._set_template(template)
        self._mark_dirty()
        self._discard_draft()
        self.set_status(f"已恢复草稿（{stamp}）")

    def _confirm_close(self) -> bool:
        """关闭前确认未保存的改动：导出模板 / 丢弃 / 取消。"""
        if not self._dirty:
            self._discard_draft()
            return True
        box = QMessageBox(self)
        box.setWindowTitle("未保存的改动")
        box.setText("当前模板有未保存的改动。")
        box.setInformativeText("关闭前要把模板导出为 JSON 吗？")
        save = box.addButton("保存模板", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel:
            return False
        if clicked is save:
            return self._export_template()
        self._discard_draft()
        return True

    # ------------------------------------------------------------------ #
    # 状态同步
    # ------------------------------------------------------------------ #
    @property
    def template(self) -> core.TemplateSpec:
        """当前编辑中的模板（画布与缩略图共用）。"""
        return self._template

    def _refresh_all(self) -> None:
        self._refresh_style_controls()
        self._refresh_form()
        self._refresh_color_buttons()
        self._refresh_layout_icons()
        self._refresh_slides()
        self._refresh_canvases()
        self._sync_history_actions()

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
            theme = self._template.theme
            self._footer_edit.setText(theme.footer)
            self._page_number_check.setChecked(theme.show_page_number)
            self._heading_font_combo.setCurrentFont(QFont(theme.heading_font))
            self._body_font_combo.setCurrentFont(QFont(theme.body_font))
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

    def _refresh_slides(self) -> None:
        """重建页面排序器：缩略图 / 列表两种视图共用同一份页面与角标数据。"""
        self._syncing_thumb = True
        try:
            self._slides_list.blockSignals(True)
            self._slides_list.clear()
            design_width, design_height = core.ASPECT_DESIGN[self._template.aspect]
            scale = THUMB_HEIGHT / design_height
            page_width = round(design_width * scale)
            grid = self._sorter_view == "grid"
            if grid:
                self._slides_list.setViewMode(QListView.ViewMode.IconMode)
                self._slides_list.setFlow(QListView.Flow.LeftToRight)
                self._slides_list.setResizeMode(QListView.ResizeMode.Adjust)
                self._slides_list.setIconSize(QSize(page_width, round(THUMB_HEIGHT)))
                self._slides_list.setGridSize(QSize(page_width + 12, round(THUMB_HEIGHT) + 30))
                self._slides_list.setWrapping(True)
            else:
                self._slides_list.setViewMode(QListView.ViewMode.ListMode)
                self._slides_list.setFlow(QListView.Flow.TopToBottom)
                self._slides_list.setIconSize(QSize(64, 36))
                self._slides_list.setGridSize(QSize())
            dpr = self.devicePixelRatioF()
            ui = self.theme().palette
            for index, slide in enumerate(self._template.slides):
                if grid:
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
                            page_number=index + 1,
                            page_total=len(self._template.slides),
                        )
                    issues = self._slide_overflow_count(index)
                    if issues:
                        _draw_overflow_badge(painter, float(page_width), issues, ui)
                    painter.end()
                    item = QListWidgetItem(QIcon(pixmap), slide_label(index + 1, slide))
                    item.setSizeHint(QSize(page_width + 8, round(THUMB_HEIGHT) + 24))
                else:
                    item = QListWidgetItem(slide_label(index + 1, slide))
                item.setData(Qt.ItemDataRole.UserRole, index)
                self._slides_list.addItem(item)
            if self._template.slides:
                self.selected = min(self.selected, len(self._template.slides) - 1)
                self._slides_list.setCurrentRow(self.selected)
                current = self._slides_list.item(self.selected)
                if current is not None:
                    self._slides_list.scrollToItem(current)
            else:
                self.selected = 0
        finally:
            self._slides_list.blockSignals(False)
            self._syncing_thumb = False
            self._update_status_detail()

    def _refresh_form(self) -> None:
        self._loading = True
        try:
            if not self._template.slides:
                self._select_value(self._layout_combo, core.LAYOUT_BULLETS)
                self._title_edit.clear()
                self._subtitle_edit.clear()
                self._bullets_text.clear()
                self._note_text.clear()
                self._layout_hint.setText("")
                self._image_edit.clear()
                self._accent_value = ""
                self._refresh_accent_button()
                return
            slide = self._template.slides[self.selected]
            self._select_value(self._layout_combo, slide.layout)
            self._title_edit.setText(slide.title)
            self._subtitle_edit.setText(slide.subtitle)
            self._bullets_text.setPlainText("\n".join(slide.bullets))
            self._note_text.setPlainText(slide.note)
            self._layout_hint.setText(_LAYOUT_HINTS.get(slide.layout, _LAYOUT_HINT_DEFAULT))
            self._image_edit.setText(slide.image)
            self._select_value(self._image_fit_combo, slide.image_fit)
            self._accent_value = slide.accent
            self._refresh_accent_button()
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

    def _refresh_layout_icons(self) -> None:
        """按当前风格 / 画幅 / 装饰重绘布局库的版式缩略图（样式页改动后同步）。"""
        self._layout_icons.clear()
        ui = self.theme().palette
        design_width, design_height = core.ASPECT_DESIGN[self._template.aspect]
        scale = 50.0 / design_height
        page_width = round(design_width * scale)
        dpr = self.devicePixelRatioF()
        for row in range(self._layout_list.count()):
            item = self._layout_list.item(row)
            layout = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(layout, str):
                continue
            pixmap = QPixmap(round(page_width * dpr), round(50 * dpr))
            pixmap.setDevicePixelRatio(dpr)
            try:
                template = core.new_template(
                    style=self._template.theme.style,
                    aspect=self._template.aspect,
                    decor=self._template.theme.decor,
                    slides=(core.new_slide(layout),),
                )
                pixmap.fill(QColor(template.theme.palette.background))
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                _paint_slide(
                    painter,
                    template,
                    template.slides[0],
                    float(page_width),
                    50.0,
                    zoom=scale,
                    ui=ui,
                    skip_text=True,
                )
                painter.end()
            except core.TemplateError:
                continue
            icon = QIcon(pixmap)
            self._layout_icons[layout] = icon
            item.setIcon(icon)
            item.setSizeHint(QSize(104, 76))

    def _toggle_sorter_view(self) -> None:
        """在缩略图网格与紧凑列表之间切换页面排序器。"""
        self._sorter_view = "list" if self._sorter_view == "grid" else "grid"
        self._sync_sorter_view_button()
        self._refresh_slides()
        self.set_status("页面视图：列表" if self._sorter_view == "list" else "页面视图：缩略图网格")

    def _sync_sorter_view_button(self) -> None:
        """视图切换按钮的图标 / 提示跟随当前视图（显示的是「切过去」的动作）。"""
        button = getattr(self, "_view_button", None)
        if button is None:
            return
        if self._sorter_view == "grid":
            _set_icon(button, "log", tone="muted")
            button.setToolTip("切换为列表视图")
        else:
            _set_icon(button, "overview", tone="muted")
            button.setToolTip("切换为缩略图网格")

    def _toggle_inspector(self) -> None:
        """收起 / 展开右侧页面设置栏（窄窗口下把画布让出来）。"""
        self._inspector_visible = not self._inspector_visible
        self._inspector_pane.setVisible(self._inspector_visible)
        _set_icon(
            self._inspector_toggle,
            "panel-left-close" if self._inspector_visible else "panel-left-open",
            tone="muted",
        )
        self._inspector_toggle.setToolTip("收起页面设置" if self._inspector_visible else "展开页面设置")
        self.set_status("已展开页面设置" if self._inspector_visible else "已收起页面设置")

    def _slide_overflow_count(self, index: int) -> int:
        """当前模板第 ``index`` 页的预计溢出文本框数量（构建失败时记 0）。"""
        if not 0 <= index < len(self._template.slides):
            return 0
        try:
            primitives = build_draw_list(
                self._template.slides[index],
                self._template,
                page_number=index + 1,
                page_total=len(self._template.slides),
            )
        except core.TemplateError:
            return 0
        return len(preview.overflow_issues(primitives))

    def _update_status_detail(self) -> None:
        """设计页状态栏右侧：当前页码 / 总页数与溢出数量。"""
        total = len(self._template.slides)
        if not total:
            self.set_page_status_detail("design", "尚无页面")
            return
        index = min(self.selected, total - 1)
        self.set_page_status_detail("design", f"第 {index + 1}/{total} 页 · {self._slide_overflow_count(index)} 处溢出")

    # ------------------------------------------------------------------ #
    # 控件回调
    # ------------------------------------------------------------------ #
    def _on_name_changed(self, text: str) -> None:
        if self._loading:
            return
        self._commit(replace(self._template, name=text), "修改模板名称", coalesce_key="name")
        self.set_status("模板名称已更新")

    def _on_footer_changed(self, text: str) -> None:
        if self._loading:
            return
        theme = replace(self._template.theme, footer=text)
        if self._commit(replace(self._template, theme=theme), "修改页脚", coalesce_key="footer"):
            self._refresh_slides()
            self._refresh_canvases()

    def _on_page_number_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        theme = replace(self._template.theme, show_page_number=checked)
        if self._commit(replace(self._template, theme=theme), "切换页码"):
            self._refresh_slides()
            self._refresh_canvases()

    def _on_font_changed(self, heading: bool) -> None:
        if self._loading:
            return
        combo = self._heading_font_combo if heading else self._body_font_combo
        family = combo.currentFont().family().strip()
        if not family:
            return
        theme = self._template.theme
        key = "heading_font" if heading else "body_font"
        if getattr(theme, key) == family:
            return
        updated = replace(theme, **{key: family})
        if self._commit(replace(self._template, theme=updated), f"切换{'标题' if heading else '正文'}字体"):
            self._refresh_slides()
            self._refresh_canvases()

    def _on_combo_changed(self, _index: int) -> None:
        if self._loading:
            return
        sender = self.sender()
        if sender is self._style_combo:
            self._apply_style(str(self._style_combo.currentData()))
        elif sender is self._aspect_combo:
            aspect = str(self._aspect_combo.currentData())
            self._commit(replace(self._template, aspect=aspect), "切换画幅")
            self._refresh_layout_icons()
            self._refresh_slides()
            self._refresh_canvases()
            self.set_status(f"已切换画幅：{core.ASPECT_TEXT[aspect]}")
        elif sender is self._decor_combo:
            self._apply_decor(str(self._decor_combo.currentData()))
        elif sender is self._density_combo:
            density = str(self._density_combo.currentData())
            theme = self._template.theme
            self._commit(
                replace(self._template, theme=replace(theme, tokens=replace(theme.tokens, density=density))),
                "切换密度",
            )
            self._refresh_style_controls()
            self._refresh_canvases()
            self.set_status(f"已切换密度：{tokens.DENSITY_TEXT[density]}")

    def _apply_style(self, style: str) -> None:
        if style == core.STYLE_CUSTOM:
            return
        theme = replace(self._template.theme, style=style, palette=core.palette_for_style(style))
        self._commit(replace(self._template, theme=theme), "切换风格")
        self._refresh_layout_icons()
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._refresh_slides()
        self._refresh_canvases()
        self.set_status(f"已切换风格：{core.style_label(style)}")

    def _apply_decor(self, decor: str) -> None:
        theme = replace(self._template.theme, decor=core.normalize_decor(decor))
        self._commit(replace(self._template, theme=theme), "切换装饰")
        self._refresh_layout_icons()
        self._refresh_style_controls()
        self._refresh_slides()
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
        self._commit(
            replace(self._template, theme=replace(theme, tokens=replace(theme.tokens, decor_intensity=intensity))),
            "调整装饰强度",
            coalesce_key="intensity",
        )
        self._refresh_slides()
        self._refresh_canvases()
        self.set_status(f"装饰强度：{intensity * 100:.0f}%")

    def _on_slide_selected(self, index: int) -> None:
        if self._syncing_thumb or index < 0:
            return
        self.selected = index
        self._refresh_form()
        self._refresh_canvases()
        self._update_status_detail()

    def _schedule_apply(self) -> None:
        if self._loading:
            return
        self._apply_timer.start()

    def _pick_image(self) -> None:
        """选择本页图片：写入路径，预览立即刷新。"""
        start = self._settings.last_dir or str(Path.home())
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            start,
            "图片 (*.png *.jpg *.jpeg *.webp *.bmp);;所有文件 (*.*)",
        )
        if not path:
            return
        self._settings.last_dir = str(Path(path).parent)
        self._image_edit.setText(path)

    def _pick_slide_accent(self) -> None:
        """选择本页强调色（写入 ``SlideSpec.accent``）。"""
        current = self._accent_value or self._template.theme.palette.accent
        chosen = QColorDialog.getColor(QColor(current), self, "选择本页强调色")
        if not chosen.isValid():
            return
        self._accent_value = core.normalize_hex(chosen.name())
        self._refresh_accent_button()
        self._schedule_apply()

    def _clear_slide_accent(self) -> None:
        """清除本页强调色，回落到模板强调色。"""
        self._accent_value = ""
        self._refresh_accent_button()
        self._schedule_apply()

    def _refresh_accent_button(self) -> None:
        """强调色按钮的底色 / 文字色随当前值刷新。"""
        value = self._accent_value or self._template.theme.palette.accent
        foreground = "#111111" if core.relative_luminance(value) > 0.5 else "#FFFFFF"
        suffix = "" if self._accent_value else "（默认）"
        self._accent_button.setText(f"本页强调色{suffix}")
        self._accent_button.setStyleSheet(
            f"background-color: {value}; color: {foreground}; border: 1px solid rgba(0, 0, 0, 60);"
            f"border-radius: 6px; padding: 4px 6px;"
        )

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
            image=self._image_edit.text().strip(),
            image_fit=str(self._image_fit_combo.currentData()),
            accent=self._accent_value,
        )
        self._commit(
            core.replace_slide(self._template, self.selected, updated),
            "编辑页面内容",
            coalesce_key="form",
        )
        self._refresh_slides()
        self._refresh_canvases()
        self.set_status(f"第 {self.selected + 1} 页已更新")

    # ------------------------------------------------------------------ #
    # 页面动作
    # ------------------------------------------------------------------ #
    def _add_slide(self) -> None:
        current = self._layout_list.currentRow()
        layout = core.LAYOUT_BULLETS if current < 0 else LAYOUT_OPTIONS[current][1]
        slide = core.new_slide(layout)
        self._commit(replace(self._template, slides=(*self._template.slides, slide)), "添加页面")
        self.selected = len(self._template.slides) - 1
        self._refresh_after_slide_change()
        self.set_status(f"已添加「{core.layout_label(layout)}」页面")

    def _selected_rows(self) -> list[int]:
        """页面列表当前选中的行（多选时按升序；无选中时退回当前页）。"""
        rows = sorted(index.row() for index in self._slides_list.selectedIndexes())
        if not rows:
            rows = [self.selected]
        return rows

    def _duplicate_slide(self) -> None:
        if not self._template.slides:
            return
        rows = [row for row in self._selected_rows() if 0 <= row < len(self._template.slides)]
        if not rows:
            return
        slides = list(self._template.slides)
        insert_at = rows[-1] + 1
        copies = [replace(slides[row], title=f"{slides[row].title}（副本）") for row in rows]
        slides[insert_at:insert_at] = copies
        self._commit(replace(self._template, slides=tuple(slides)), "复制页面")
        self.selected = insert_at
        self._refresh_after_slide_change()
        self.set_status("已复制选中页面" if len(rows) > 1 else "已复制当前页面")

    def _delete_slide(self) -> None:
        if not self._template.slides:
            return
        rows = [row for row in self._selected_rows() if 0 <= row < len(self._template.slides)]
        if not rows:
            return
        slides = list(self._template.slides)
        for row in reversed(rows):
            slides.pop(row)
        self._commit(replace(self._template, slides=tuple(slides)), "删除页面")
        self.selected = min(rows[0], max(len(slides) - 1, 0))
        self._refresh_after_slide_change()
        self.set_status("已删除选中页面" if len(rows) > 1 else "已删除当前页面")

    def _move_slide(self, delta: int) -> None:
        slides = list(self._template.slides)
        target = self.selected + delta
        if not slides or target < 0 or target >= len(slides):
            return
        slides[self.selected], slides[target] = slides[target], slides[self.selected]
        self._commit(replace(self._template, slides=tuple(slides)), "调整页面顺序")
        self.selected = target
        self._refresh_after_slide_change()
        self.set_status("页面顺序已调整")

    def _on_slides_reordered(self, *_args: object) -> None:
        """拖拽排序后按列表项保存的原索引重建页面顺序。"""
        order: list[int] = []
        for row in range(self._slides_list.count()):
            item = self._slides_list.item(row)
            value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(value, int) and 0 <= value < len(self._template.slides):
                order.append(value)
        if len(order) != len(self._template.slides) or order == list(range(len(order))):
            return
        self._commit(
            replace(self._template, slides=tuple(self._template.slides[index] for index in order)),
            "拖拽调整页面顺序",
        )
        self.selected = max(self._slides_list.currentRow(), 0)
        self._refresh_after_slide_change()
        self.set_status("页面顺序已调整")

    def _refresh_after_slide_change(self) -> None:
        """页面增删 / 排序后的统一刷新。"""
        self._refresh_form()
        self._refresh_slides()
        self._refresh_canvases()

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
        self._commit(
            replace(
                self._template,
                theme=replace(self._template.theme, style=core.STYLE_CUSTOM, palette=palette),
            ),
            "修改配色",
        )
        self._refresh_layout_icons()
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._refresh_slides()
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
        self._history.reset(self._template)
        self.selected = 0
        self._mark_dirty(False)
        self._refresh_all()
        self.set_status("已新建模板")

    def _new_from_sample(self, style: str) -> None:
        """按内置风格新建一份完整示例模板（覆盖封面到结尾的演示页）。"""
        self._template = core.default_template(style=style, aspect=self._template.aspect)
        self._history.reset(self._template)
        self.selected = 0
        self._mark_dirty(True)
        self._refresh_all()
        self.set_status(f"已从示例模板新建：{core.STYLE_TEXT.get(style, style)}")

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
        if source.suffix.lower() != ".pptx":
            self._remember_recent(source)
        self._set_template(template)

    def _export_template(self) -> bool:
        """导出模板 JSON；返回是否真正写入（关闭确认里据此决定能否关窗）。"""
        default_name = f"{safe_filename(self._template.name)}{core.TEMPLATE_SUFFIX}"
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "导出模板",
            str(Path(self._settings.last_dir) / default_name) if self._settings.last_dir else default_name,
            f"PPT 模板 (*{core.TEMPLATE_SUFFIX});;JSON (*.json);;所有文件 (*.*)",
        )
        if not path:
            return False
        target = Path(path)
        try:
            core.save_template(self._template, target)
        except core.TemplateError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return False
        self._settings.last_dir = str(target.parent)
        self._remember_recent(target)
        self._mark_dirty(False)
        self._discard_draft()
        self.set_status(f"模板已导出：{target}")
        return True

    def _export_pptx(self) -> None:
        if not self._template.slides:
            QMessageBox.information(self, "导出 PPTX", "当前模板还没有页面。")
            return
        options = _ExportOptionsDialog(len(self._template.slides), self)
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        start, end, with_pdf = options.options()
        sliced = replace(self._template, slides=self._template.slides[start - 1 : end])
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
            count = export_pptx(sliced, target)
        except core.TemplateError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._settings.last_dir = str(target.parent)
        pdf_note = ""
        if with_pdf:
            if _office.find_soffice() is None:
                pdf_note = "\n未检测到 LibreOffice，PDF 未导出。"
            else:
                try:
                    pdf = _office.convert_to_pdf(target, target.parent)
                except _office.OfficeError as exc:
                    pdf_note = f"\nPDF 导出失败：{exc}"
                else:
                    pdf_note = f"\nPDF：{pdf}"
        self.set_status(f"PPTX 已导出：{target}（{count} 页）")
        QMessageBox.information(self, "导出完成", f"已生成 {count} 页：\n{target}{pdf_note}")

    def _present(self) -> None:
        """F5 从当前页开始全屏放映。"""
        if not self._template.slides:
            return
        self._presenter = _PresenterWindow(self, self.selected)
        self._presenter.showFullScreen()
        self._presenter.setFocus()

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
        self._history.reset(template)
        self.selected = 0
        self._mark_dirty(False)
        self._refresh_all()
        self._sync_history_actions()
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
        """切换主题后重画画布、缩略图与布局库（其余控件由全局样式表处理）。"""
        super().set_theme(theme)
        self._refresh_layout_icons()
        self._refresh_slides()
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
            design_splitter=tuple(splitter_sizes(self._splitter)),
            style_splitter=tuple(splitter_sizes(self._style_splitter)),
            recent_templates=self._settings.recent_templates,
            sorter_view=self._sorter_view,
            inspector_visible=self._inspector_visible,
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
