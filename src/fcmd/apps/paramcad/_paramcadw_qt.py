"""paramcadw 的 Qt 主窗口：三页几何预览 + 右侧参数摘要。

几何采样仍来自 :mod:`fcmd.apps.paramcad._preview`，坐标映射沿用本模块的
:class:`CanvasMap` 纯函数；三个预览页都用 ``QPainter`` 直接绘制，颜色取自
当前主题，切换主题即时重绘。

布局口径：预览页放进 ``QTabWidget``，右侧摘要与页签用 ``QSplitter`` 分隔，
分栏比例写入 ``ui/paramcad.json``；画布每次按控件逻辑像素重算映射，缩放
窗口不裁切、不模糊。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.paramcad import _export, _preview
from fcmd.apps.paramcad._model import TubeModel, format_summary
from fcmd.apps.paramcad._quantity import ParamcadError
from fcmd.apps.paramcad.paramcadw import (
    CanvasMap,
    Settings,
    __version__,
    load_settings,
    save_settings,
)
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import LogView, apply_splitter_sizes, button, splitter_sizes

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.paramcad._preview import CrossSection, Curve

__all__ = ["ParamcadWindow", "run_qt"]

DEFAULT_SIZE = (1120, 720)
MIN_SIZE = (900, 600)
_DEFAULT_SPLIT = (800, 360)
_DASH = [4.0, 6.0]


def _symmetric_map(section: CrossSection, width: float, height: float) -> CanvasMap:
    """截面预览的对称映射（±半长轴等比居中）。"""
    half = section.half_extent_mm if section.half_extent_mm > 0 else 1.0
    return CanvasMap(width, height, -half, half, -half, half)


class _PreviewCanvas(QWidget):  # pragma: no cover - Qt 控件
    """单个预览页：绘制委托给窗口，主题变化时自动重绘。"""

    def __init__(self, owner: ParamcadWindow, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._kind = kind
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002 - Qt 回调签名
        self._owner.paint_canvas(self._kind, self)

    def changeEvent(self, event: Any) -> None:
        """主题 / 调色板变化时重绘，避免深浅切换后残留旧色。"""
        super().changeEvent(event)
        self.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.update()


class ParamcadWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """参数化建模预览主窗口。"""

    def __init__(self, model_file: Path | None = None) -> None:
        self._settings = load_settings()
        self._model: TubeModel | None = None
        self._model_path = Path(self._settings.model_path) if self._settings.model_path else None
        super().__init__(
            title=f"参数化建模预览 v{__version__}",
            config_name="paramcad",
            default_size=DEFAULT_SIZE,
            min_size=MIN_SIZE,
            persist=self._persist,
        )
        self._build()
        self._install_menu_actions()
        initial = model_file or (self._model_path if self._model_path and self._model_path.is_file() else None)
        if initial is not None:
            QTimer.singleShot(60, lambda: self._load_path(initial))

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """工具栏 + 预览页 / 摘要分栏。"""
        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)
        open_model = button("打开模型…", kind="primary")
        open_model.clicked.connect(self._choose_file)
        toolbar_layout.addWidget(open_model)
        example = button("载入示例")
        example.clicked.connect(self._load_example)
        toolbar_layout.addWidget(example)
        export = button("导出模型…")
        export.clicked.connect(self._export)
        toolbar_layout.addWidget(export)
        toolbar_layout.addStretch(1)
        self.add(toolbar)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._tabs = QTabWidget(self._splitter)
        self._tabs.setDocumentMode(True)
        self._section_canvas = _PreviewCanvas(self, "section", self._tabs)
        self._axial_canvas = _PreviewCanvas(self, "axial", self._tabs)
        self._curve_canvas = _PreviewCanvas(self, "curves", self._tabs)
        self._tabs.addTab(self._section_canvas, "二维截面")
        self._tabs.addTab(self._axial_canvas, "轴向剖面")
        self._tabs.addTab(self._curve_canvas, "分布曲线")
        self._splitter.addWidget(self._tabs)
        side = QWidget(self._splitter)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self._summary = LogView(height=420, parent=side)
        side_layout.addWidget(self._summary)
        self._splitter.addWidget(side)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 1)
        sizes = list(self._settings.splitter) if self._settings.splitter else list(_DEFAULT_SPLIT)
        apply_splitter_sizes(self._splitter, tuple(sizes))
        self.body.addWidget(self._splitter, 1)
        self.set_status("载入模型或示例后预览")

    def _install_menu_actions(self) -> None:
        """把打开 / 示例 / 导出插入「文件」，位置在退出之前。"""
        from PySide6.QtGui import QAction

        file_menu = self._menu("文件")
        insert_at = file_menu.actions()[0] if file_menu.actions() else None
        for text, slot in (
            ("打开模型…", self._choose_file),
            ("载入示例", self._load_example),
            ("导出模型…", self._export),
        ):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if insert_at is not None:
                file_menu.insertAction(insert_at, action)
            else:
                file_menu.addAction(action)
        if insert_at is not None:
            file_menu.insertSeparator(insert_at)

    def _menu(self, title: str) -> QMenu:
        """按标题取出骨架菜单。"""
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is not None and action.text() == title:
                return menu
        raise LookupError(f"缺少菜单：{title}")

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 dynacalc 模型文件",
            "",
            "模型 YAML (*.yaml *.yml);;全部文件 (*.*)",
        )
        if selected:
            self._load_path(Path(selected))

    def _load_example(self) -> None:
        self._set_model(_export.example_model(), path=None)

    def _load_path(self, path: Path) -> None:
        try:
            models = _export.load_models(path)
        except ParamcadError as exc:
            self._show_error(str(exc))
            return
        self._set_model(models[0], path=path, extra=len(models))

    def _set_model(self, model: TubeModel, *, path: Path | None, extra: int = 1) -> None:
        self._model = model
        self._model_path = path
        self._render_summary()
        self._redraw()
        location = str(path) if path is not None else "示例（内存）"
        suffix = f"，文件含 {extra} 个模型，显示第一个" if extra > 1 else ""
        self.set_status(f"{model.model_id} · {location}{suffix}")

    def _render_summary(self) -> None:
        if self._model is None:
            return
        self._summary.clear()
        for line in format_summary(self._model).splitlines():
            self._summary.append(line)

    def _export(self) -> None:
        if self._model is None:
            self._show_error("请先载入模型或示例")
            return
        suggested = f"{self._model.model_id}.yaml"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出 dynacalc 模型",
            suggested,
            "模型 YAML (*.yaml);;全部文件 (*.*)",
        )
        if not selected:
            return
        target = Path(selected)
        overwrite = False
        if target.exists():
            answer = QMessageBox.question(self, "覆盖确认", f"{target} 已存在，是否覆盖？")
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        try:
            _export.export_model(self._model, target, overwrite=overwrite)
        except ParamcadError as exc:
            self._show_error(str(exc))
            return
        self.set_status(f"已导出：{target}")
        QMessageBox.information(
            self,
            "导出完成",
            f"已写入\n{target}\n\n指纹：{_export.model_digest(self._model)}",
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "paramcad", message)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _redraw(self) -> None:
        for canvas in (self._section_canvas, self._axial_canvas, self._curve_canvas):
            canvas.update()

    def paint_canvas(self, kind: str, widget: QWidget) -> None:
        """按页签类型绘制；无模型时统一居中提示。"""
        painter = QPainter(widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(widget.rect(), QColor(self.theme().palette.surface))
        if self._model is None:
            self._text(painter, widget.width() / 2, widget.height() / 2, "载入模型或示例后显示预览", anchor="center")
            return
        if kind == "section":
            self._paint_section(painter, widget)
        elif kind == "axial":
            self._paint_axial(painter, widget)
        else:
            self._paint_curves(painter, widget)

    def _text(  # noqa: PLR0913 - 绘制辅助，锚点 / 样式参数成组出现
        self,
        painter: QPainter,
        x: float,
        y: float,
        text: str,
        *,
        anchor: str = "nw",
        color: str | None = None,
        heading: bool = False,
        small: bool = False,
    ) -> None:
        """按画布风格的锚点绘制单行文字（nw / ne / sw / center）。"""
        palette = self.theme().palette
        font = QFont(painter.font())
        font.setBold(heading)
        if heading:
            font.setPointSizeF(font.pointSizeF() + 2)
        if small:
            font.setPointSizeF(max(font.pointSizeF() - 1, 7))
        painter.setFont(font)
        painter.setPen(QColor(color or palette.text))
        metrics = QFontMetrics(font)
        if anchor == "nw":
            position = QPointF(x, y + metrics.ascent())
        elif anchor == "ne":
            position = QPointF(x - metrics.horizontalAdvance(text), y + metrics.ascent())
        elif anchor == "sw":
            position = QPointF(x, y - metrics.descent())
        else:
            position = QPointF(x - metrics.horizontalAdvance(text) / 2, y + metrics.ascent() / 2)
        painter.drawText(position, text)

    def _note(self, painter: QPainter, x: float, y: float, width: float, text: str) -> None:
        """底部提示文字：按可用宽度自动换行。"""
        palette = self.theme().palette
        font = QFont(painter.font())
        font.setPointSizeF(max(font.pointSizeF() - 1, 7))
        painter.setFont(font)
        painter.setPen(QColor(palette.warning))
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom | Qt.TextFlag.TextWordWrap
        painter.drawText(QRectF(x, y - 44, max(width, 100), 40), flags, text)

    def _polyline(
        self,
        painter: QPainter,
        mapper: CanvasMap,
        points: Sequence[tuple[float, float]],
        color: str,
        width: int,
    ) -> None:
        if not points:
            return
        rows = mapper.polyline(points)
        polygon = QPolygonF([QPointF(rows[i], rows[i + 1]) for i in range(0, len(rows), 2)])
        pen = QPen(QColor(color), width)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(polygon)

    def _paint_section(self, painter: QPainter, widget: QWidget) -> None:
        """二维截面：外轮廓 / 内孔 / 药柱 + 十字虚线 + 角度标注。"""
        section = _preview.section_at(self._model)  # type: ignore[arg-type]
        palette = self.theme().palette
        width, height = float(widget.width()), float(widget.height())
        if not section.available:
            self._text(painter, width / 2, height / 2, section.note, anchor="center")
            return
        mapper = _symmetric_map(section, width, height)
        self._fill_polygon(painter, mapper, section.outer, palette.surface_active, palette.accent, 2)
        self._fill_polygon(painter, mapper, section.inner, palette.background, palette.border_strong, 1)
        if section.propellant:
            fill = QColor(palette.warning)
            fill.setAlpha(96)
            self._fill_polygon(painter, mapper, section.propellant, fill, palette.warning, 1)

        center_x, center_y = mapper.to_pixel(0.0, 0.0)
        pen = QPen(QColor(palette.border))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern(_DASH)
        painter.setPen(pen)
        painter.drawLine(QPointF(center_x, 0), QPointF(center_x, height))
        painter.drawLine(QPointF(0, center_y), QPointF(width, center_y))

        for annotation in section.annotations:
            self._paint_section_annotation(painter, mapper, annotation)

        half = section.half_extent_mm
        label = "拉拔后" if section.state_label == "after" else "拉拔前"
        self._text(painter, 12, 12, f"{label}截面 · 外径 {2 * half:.1f} mm", heading=True)
        if section.note:
            self._note(painter, 12, height - 12, width - 24, section.note)

    def _fill_polygon(  # noqa: PLR0913, PLR0917 - 填充 / 描边参数成组出现
        self,
        painter: QPainter,
        mapper: CanvasMap,
        points: Sequence[tuple[float, float]],
        fill: str | QColor,
        outline: str,
        width: int,
    ) -> None:
        rows = mapper.polyline(points)
        polygon = QPolygonF([QPointF(rows[i], rows[i + 1]) for i in range(0, len(rows), 2)])
        painter.setPen(QPen(QColor(outline), width))
        painter.setBrush(QBrush(QColor(fill)))
        painter.drawPolygon(polygon)

    def _paint_section_annotation(self, painter: QPainter, mapper: CanvasMap, annotation: Any) -> None:
        """角度标注：沿外径外圈画粗弧线 + 中点标签。"""
        palette = self.theme().palette
        half = max(mapper.max_x, abs(mapper.min_x))
        radius = half * 1.02
        span = (annotation.end_deg - annotation.start_deg) % 360.0 or 6.0
        steps = max(int(span / 6.0), 2)
        points: list[QPointF] = []
        for index in range(steps + 1):
            degree = annotation.start_deg + span * index / steps
            x = radius * math.cos(math.radians(degree))
            y = radius * math.sin(math.radians(degree))
            pixel_x, pixel_y = mapper.to_pixel(x, y)
            points.append(QPointF(pixel_x, pixel_y))
        painter.setPen(QPen(QColor(palette.danger), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(QPolygonF(points))
        mid = annotation.start_deg + span / 2.0
        label_x, label_y = mapper.to_pixel(
            radius * 1.12 * math.cos(math.radians(mid)),
            radius * 1.12 * math.sin(math.radians(mid)),
        )
        self._text(painter, label_x, label_y, annotation.label, anchor="center", color=palette.danger, small=True)

    def _paint_axial(self, painter: QPainter, widget: QWidget) -> None:
        """轴向剖面：外壁 / 内壁 / 药柱曲线 + 位置虚线。"""
        section = _preview.axial_section(self._model)  # type: ignore[arg-type]
        palette = self.theme().palette
        width, height = float(widget.width()), float(widget.height())
        if not section.available:
            self._text(painter, width / 2, height / 2, section.note, anchor="center")
            return
        half = section.half_extent_mm if section.half_extent_mm > 0 else 1.0
        mapper = CanvasMap(width, height, 0.0, max(section.length_mm, 1e-6), -half, half)
        self._polyline(painter, mapper, section.outer_top, palette.accent, 2)
        self._polyline(painter, mapper, section.outer_bottom, palette.accent, 2)
        self._polyline(painter, mapper, section.inner_top, palette.border_strong, 1)
        self._polyline(painter, mapper, section.inner_bottom, palette.border_strong, 1)
        if section.propellant_top and section.propellant_bottom:
            self._polyline(painter, mapper, section.propellant_top, palette.warning, 1)
            self._polyline(painter, mapper, section.propellant_bottom, palette.warning, 1)
        pen = QPen(QColor(palette.danger))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([3.0, 5.0])
        for annotation in section.annotations:
            x, _y = mapper.to_pixel(section.length_mm * annotation.position, 0.0)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, 8), QPointF(x, height - 8))
            self._text(painter, x + 4, 16, annotation.label, color=palette.danger, small=True)
        self._text(
            painter,
            12,
            12,
            f"轴向剖面 · L={section.length_mm:.1f} mm（径向已放大，非等比）",
            heading=True,
        )
        if section.note:
            self._note(painter, 12, height - 12, width - 24, section.note)

    def _paint_curves(self, painter: QPainter, widget: QWidget) -> None:
        """分布曲线：每个指标占一条横带，带内独立映射。"""
        curves = _preview.distribution_curves(self._model)  # type: ignore[arg-type]
        width, height = float(widget.width()), float(widget.height())
        if not curves:
            self._text(painter, width / 2, height / 2, "没有可绘制的分布曲线", anchor="center")
            return
        band = height / len(curves)
        palette = self.theme().palette
        for index, curve in enumerate(curves):
            top = index * band
            self._paint_curve_band(painter, curve, top, top + band, width, palette)
        if len(curves) > 1:
            painter.setPen(QPen(QColor(palette.border)))
            for index in range(1, len(curves)):
                y = index * band
                painter.drawLine(QPointF(0, y), QPointF(width, y))

    def _paint_curve_band(  # noqa: PLR0913, PLR0917 - 单条横带的绘制参数成组出现
        self,
        painter: QPainter,
        curve: Curve,
        top: float,
        bottom: float,
        width: float,
        palette: Any,
    ) -> None:
        """单条横带：曲线 + 标题 / 来源 / 纵轴范围。"""
        if not curve.points:
            return
        min_x = min(point[0] for point in curve.points)
        max_x = max(point[0] for point in curve.points)
        min_y = min(point[1] for point in curve.points)
        max_y = max(point[1] for point in curve.points)
        if max_x == min_x:
            max_x = min_x + 1.0
        if max_y == min_y:
            max_y = min_y + 1.0
        mapper = CanvasMap(width, bottom - top, min_x, max_x, min_y, max_y, padding=14.0)
        rows = mapper.polyline(curve.points)
        polygon = QPolygonF(
            [QPointF(rows[i], rows[i + 1] + top) for i in range(0, len(rows), 2)],
        )
        painter.setPen(QPen(QColor(palette.accent), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolyline(polygon)
        self._text(painter, 10, top + 8, f"{curve.title}（{curve.x_label} / {curve.y_label}）")
        source = f"来源：{curve.source}" + (f" · {curve.note}" if curve.note else "")
        self._text(painter, 10, top + 26, source, color=palette.muted, small=True)
        self._text(
            painter, width - 10, top + 8, f"{min_y:.3g} – {max_y:.3g}", anchor="ne", color=palette.muted, small=True
        )

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回 geometry、上次模型与分栏比例。"""
        save_settings(
            Settings(
                geometry=geometry,
                model_path=str(self._model_path) if self._model_path is not None else "",
                splitter=splitter_sizes(self._splitter),
            )
        )


def run_qt(model: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: ParamcadWindow(model), app_name="paramcadw")
