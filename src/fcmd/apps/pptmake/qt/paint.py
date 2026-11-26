"""pptmake 画布绘制助手：把绘制列表渲染到 QPainter（画布 / 缩略图共用）。"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path, PureWindowsPath
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication

from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _preview as preview
from fcmd.apps.pptmake._design import (
    Chart,
    Line,
    Oval,
    Picture,
    Rect,
    Table,
    Text,
    build_draw_list,
    picture_crop,
)

__all__ = ["draw_overflow_badge", "paint_message", "paint_slide", "picture_file"]


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


def _picture_clip(target: QRectF, radius: float) -> QPainterPath:
    """图片裁剪路径：圆角半径有效时用圆角矩形，否则按矩形裁剪。"""
    path = QPainterPath()
    if radius <= 0:
        path.addRect(target)
    else:
        path.addRoundedRect(target, radius, radius)
    return path


def picture_file(primitive: Picture, base_dir: Path | None) -> str:
    """把图片图元路径解析成可加载的文件名；相对路径按模板所在目录解析。"""
    if not primitive.path:
        return ""
    path = Path(primitive.path)
    # 盘符/UNC 形式的 Windows 绝对路径在 Linux 下 Path.is_absolute() 为假，需按 Windows 语义补充判定。
    absolute = path.is_absolute() or PureWindowsPath(primitive.path).is_absolute()
    if not absolute and base_dir is not None:
        path = base_dir / path
    return str(path)


def _paint_picture(
    painter: QPainter,
    primitive: Picture,
    view: preview.Viewport,
    ui: Any,
    base_dir: Path | None = None,
) -> None:
    """按 cover / contain 把本地图片画进图元矩形；支持焦点裁剪、圆角与压暗层。"""
    scale = view.scale
    x, y = view.to_canvas(primitive.x, primitive.y)
    width = primitive.w * scale
    height = primitive.h * scale
    if width <= 0 or height <= 0:
        return
    target = QRectF(x, y, width, height)
    pixmap = QPixmap(picture_file(primitive, base_dir)) if primitive.path else QPixmap()
    painter.save()
    painter.setClipPath(_picture_clip(target, primitive.radius * scale))
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
    else:
        crop_left, crop_right, crop_top, crop_bottom = picture_crop(primitive.focus, image_ratio, box_ratio)
        source = QRectF(
            crop_left * image_width,
            crop_top * image_height,
            max((1 - crop_left - crop_right) * image_width, 1.0),
            max((1 - crop_top - crop_bottom) * image_height, 1.0),
        )
        painter.drawPixmap(target, pixmap, source)
    if primitive.overlay > 0:
        overlay = QColor(primitive.overlay_color)
        overlay.setAlphaF(min(max(primitive.overlay, 0.0), 1.0))
        painter.fillRect(target, overlay)
    painter.restore()


def _paint_table(painter: QPainter, primitive: Table, view: preview.Viewport) -> None:
    """按 draw list 的行列几何绘制表格，与导出共用同一组尺寸。"""
    scale = view.scale
    x0, y0 = view.to_canvas(primitive.x, primitive.y)
    aligns = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "center": Qt.AlignmentFlag.AlignHCenter,
        "right": Qt.AlignmentFlag.AlignRight,
    }
    y = y0
    for row_index, height in enumerate(primitive.row_heights):
        row = primitive.rows[row_index] if row_index < len(primitive.rows) else ()
        fill = primitive.row_fill
        if primitive.header and row_index == 0:
            fill = primitive.header_fill
        elif row_index % 2 == 0:
            fill = primitive.band_fill
        x = x0
        for column_index, width in enumerate(primitive.col_widths):
            cell = row[column_index] if column_index < len(row) else None
            rect = QRectF(x, y, width * scale, height * scale)
            if fill:
                painter.fillRect(rect, QColor(fill))
            if cell is not None and cell.text:
                size = primitive.header_size if row_index == 0 else primitive.body_size
                font = QFont(primitive.font or QApplication.font().family())
                font.setPixelSize(max(round(size * scale), 1))
                font.setBold(cell.bold or (primitive.header and row_index == 0))
                painter.setFont(font)
                painter.setPen(QPen(QColor(cell.color or primitive.text_color)))
                flags = int(aligns.get(cell.align, Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
                inset = primitive.pad_x * scale
                painter.drawText(rect.adjusted(inset, 0, -inset, 0), flags, cell.text)
            x += width * scale
        y += height * scale
        painter.setPen(_pen(primitive.line_color, primitive.line_width * scale))
        painter.drawLine(QPointF(x0, y), QPointF(x0 + primitive.w * scale, y))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(_pen(primitive.line_color, primitive.line_width * scale))
    painter.drawRect(QRectF(x0, y0, primitive.w * scale, primitive.h * scale))


def _chart_values(primitive: Chart) -> list[float]:
    """图表所有系列的数值，供预览计算刻度。"""
    return [value for series in primitive.series for value in series.values]


def _paint_chart_legend(  # noqa: PLR0913, PLR0917 - 图例需要图元、区域与缩放
    painter: QPainter, primitive: Chart, x: float, y: float, width: float, height: float, scale: float
) -> None:
    """底部图例：柱/折线按系列，饼图按类别。"""
    if height <= 0:
        return
    colors = primitive.colors or tuple(series.color for series in primitive.series)
    entries = (
        [(series.name, series.color) for series in primitive.series]
        if primitive.kind != "pie"
        else [(name, colors[index % len(colors)] if colors else "") for index, name in enumerate(primitive.categories)]
    )
    cursor = x
    for name, color in entries:
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        painter.setBrush(_brush(color))
        painter.drawRect(QRectF(cursor, y + height * 0.3, height * 0.4, height * 0.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(primitive.muted_color)))
        text_width = preview.measure_text(name, primitive.size) * scale
        painter.drawText(
            QRectF(cursor + height * 0.6, y, text_width + 8, height),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            name,
        )
        cursor += height * 0.6 + text_width + 20
        if cursor > x + width:
            break


def _paint_chart(painter: QPainter, primitive: Chart, view: preview.Viewport) -> None:
    """近似绘制图表：柱/折线含 0 轴，饼图按占比分片；导出仍是原生图表。"""
    scale = view.scale
    x, y = view.to_canvas(primitive.x, primitive.y)
    width = primitive.w * scale
    height = primitive.h * scale
    if width <= 0 or height <= 0 or not primitive.categories:
        return
    font = QFont(primitive.font or QApplication.font().family())
    font.setPixelSize(max(round(primitive.size * scale), 6))
    painter.setFont(font)
    legend_height = round(primitive.size * 2.0 * scale) if primitive.legend else 0.0
    if primitive.kind == "pie":
        _paint_pie_chart(painter, primitive, QRectF(x, y, width, height - legend_height))
    else:
        _paint_axis_chart(painter, primitive, QRectF(x, y, width, height - legend_height), scale)
    if legend_height > 0:
        _paint_chart_legend(painter, primitive, x, y + height - legend_height, width, legend_height, scale)


def _paint_axis_chart(painter: QPainter, primitive: Chart, rect: QRectF, scale: float) -> None:
    """柱状图 / 折线图：网格、刻度、0 轴、柱与数据标签。"""
    plot = QRectF(
        rect.x() + 48 * scale,
        rect.y() + 16 * scale,
        max(rect.width() - 64 * scale, 10.0),
        max(rect.height() - 52 * scale, 10.0),
    )
    values = _chart_values(primitive)
    low = min([*values, 0.0])
    high = max([*values, 0.0])
    span = (high - low) or 1.0

    def to_y(value: float) -> float:
        return plot.bottom() - (value - low) / span * plot.height()

    ticks = 4
    for step in range(ticks + 1):
        value = low + span * step / ticks
        gy = to_y(value)
        painter.setPen(_pen(primitive.grid_color, 1))
        painter.drawLine(QPointF(plot.left(), gy), QPointF(plot.right(), gy))
        painter.setPen(QPen(QColor(primitive.muted_color)))
        painter.drawText(
            QRectF(rect.x(), gy - 10 * scale, 42 * scale, 20 * scale),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"{value:g}",
        )
    zero_y = to_y(0.0)
    painter.setPen(_pen(primitive.axis_color, 2))
    painter.drawLine(QPointF(plot.left(), zero_y), QPointF(plot.right(), zero_y))
    count = len(primitive.categories)
    slot = plot.width() / max(count, 1)
    series_count = max(len(primitive.series), 1)
    bar_width = slot * 0.62 / series_count
    for series_index, series in enumerate(primitive.series):
        points: list[QPointF] = []
        for index, value in enumerate(series.values[:count]):
            center = plot.left() + slot * (index + 0.5)
            top = to_y(value)
            if primitive.kind == "line":
                points.append(QPointF(center, top))
                painter.setPen(QPen(QColor(series.color), max(2.0, 2 * scale)))
                painter.setBrush(QColor(series.color))
                painter.drawEllipse(QRectF(center - 3 * scale, top - 3 * scale, 6 * scale, 6 * scale))
            else:
                left = center - slot * 0.31 + series_index * bar_width
                painter.setPen(QPen(Qt.PenStyle.NoPen))
                painter.setBrush(QColor(series.color))
                painter.drawRect(QRectF(left, min(top, zero_y), bar_width * 0.9, abs(zero_y - top)))
            if primitive.show_labels:
                painter.setPen(QPen(QColor(primitive.text_color)))
                painter.drawText(
                    QRectF(center - slot / 2, min(top, zero_y) - 18 * scale, slot, 16 * scale),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                    f"{value:g}",
                )
        if primitive.kind == "line" and len(points) > 1:
            painter.setPen(QPen(QColor(series.color), max(2.0, 2 * scale)))
            for start, end in pairwise(points):
                painter.drawLine(start, end)
    painter.setPen(QPen(QColor(primitive.muted_color)))
    for index, name in enumerate(primitive.categories):
        center = plot.left() + slot * (index + 0.5)
        painter.drawText(
            QRectF(center - slot / 2, plot.bottom() + 6 * scale, slot, 20 * scale),
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
            name,
        )


def _paint_pie_chart(painter: QPainter, primitive: Chart, rect: QRectF) -> None:
    """饼图：按正数占比分片，颜色取 ``Chart.colors``。"""
    values = [max(value, 0.0) for value in _chart_values(primitive)[: len(primitive.categories)]]
    total = sum(values) or 1.0
    side = min(rect.width(), rect.height()) * 0.86
    box = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    start = 90.0
    painter.setPen(QPen(Qt.PenStyle.NoPen))
    for index, value in enumerate(values):
        span = value / total * 360.0
        color = primitive.colors[index % len(primitive.colors)] if primitive.colors else primitive.axis_color
        painter.setBrush(_brush(color))
        painter.drawPie(box, round(start * 16), round(span * 16))
        start += span
    painter.setBrush(Qt.BrushStyle.NoBrush)


def _paint_primitive(  # noqa: PLR0911 - 图元分派按类型逐条返回
    painter: QPainter, primitive: Any, view: preview.Viewport, ui: Any, base_dir: Path | None = None
) -> None:
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
        _paint_picture(painter, primitive, view, ui, base_dir)
        return
    if isinstance(primitive, Table):
        _paint_table(painter, primitive, view)
        return
    if isinstance(primitive, Chart):
        _paint_chart(painter, primitive, view)
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
    x += round(primitive.indent * primitive.level * scale)
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


def paint_slide(  # noqa: PLR0913 - 画布、缩略图与错误提示共用入口
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
    base_dir: Path | None = None,
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
    primitives = build_draw_list(slide, template, page_number=page_number, page_total=page_total, hints=True)
    painter.save()
    painter.setClipRect(page)
    painter.fillRect(page, QColor(template.theme.palette.background))
    for primitive in primitives:
        if skip_text and isinstance(primitive, Text):
            continue
        _paint_primitive(painter, primitive, view, ui, base_dir)
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


def draw_overflow_badge(painter: QPainter, width: float, count: int, ui: Any) -> None:
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


def paint_message(painter: QPainter, width: float, height: float, text: str, color: str) -> None:
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
