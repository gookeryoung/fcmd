"""图像缩放计算与预览标签：滚轮缩放 / 适配窗口的共享实现。

缩放**倍率计算**是纯函数（无 Qt 依赖），各窗口自己的画布、视口尺寸仍由窗口掌握——
reshack 的 Ctrl+滚轮、paramesh 的 wheelEvent、cmder 的快速查看面板只在“怎么触发”
和“画在哪”上不同，倍率的钳制口径一致，才对得上手。
"""

from __future__ import annotations

from math import isnan
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel

if TYPE_CHECKING:
    from PySide6.QtGui import QResizeEvent

__all__ = [
    "FIT_MAX_UPSCALE",
    "MAX_ZOOM",
    "MIN_ZOOM",
    "ZOOM_STEP",
    "ImagePreviewLabel",
    "clamp_zoom",
    "fit_zoom",
    "zoom_in",
    "zoom_out",
]

ZOOM_STEP = 1.25
"""每次缩放的倍率（滚轮一格 / 点一次按钮）。"""

MIN_ZOOM = 0.05
"""最小缩放倍率。"""

MAX_ZOOM = 32.0
"""最大缩放倍率。"""

FIT_MAX_UPSCALE = 1.0
"""「适配窗口」允许的最大放大倍率；1.0 表示只缩小不放大。"""


def clamp_zoom(value: float, *, minimum: float = MIN_ZOOM, maximum: float = MAX_ZOOM) -> float:
    """把倍率钳到 ``[minimum, maximum]``；``NaN`` 按 1.0 处理。"""
    if isnan(value):
        return 1.0
    return min(maximum, max(minimum, value))


def zoom_in(zoom: float, *, step: float = ZOOM_STEP) -> float:
    """放大一档（钳到 ``MAX_ZOOM``）。"""
    return clamp_zoom(zoom * step)


def zoom_out(zoom: float, *, step: float = ZOOM_STEP) -> float:
    """缩小一档（钳到 ``MIN_ZOOM``）。"""
    return clamp_zoom(zoom / step)


def fit_zoom(
    image_w: int,
    image_h: int,
    view_w: int,
    view_h: int,
    *,
    max_upscale: float = FIT_MAX_UPSCALE,
) -> float:
    """按「适配窗口」算倍率：等比缩到窗口内，默认不放大超过原尺寸。

    任一尺寸非正时返回 1.0（还没量出视口 / 图还没加载）。
    """
    if image_w <= 0 or image_h <= 0 or view_w <= 0 or view_h <= 0:
        return 1.0
    ratio = min(view_w / image_w, view_h / image_h)
    return clamp_zoom(min(ratio, max_upscale))


class ImagePreviewLabel(QLabel):  # pragma: no cover - Qt 控件
    """按窗口等比缩放的图像标签（不放大超过原尺寸）。

    适用于「快速查看」「缩略预览」这类只要铺满可视区的场景；需要滚轮缩放 /
    拖动定位的画布仍各自实现，只是倍率计算复用上面的纯函数。
    """

    def __init__(self, parent: object = None) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self._fit = True
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)

    def source(self) -> QPixmap:
        """当前源图（未缩放的原始像素）。"""
        return self._source

    def set_source(self, pixmap: QPixmap) -> None:
        """设置源图并按当前窗口重排。"""
        self._source = pixmap
        self._refresh()

    def clear_source(self) -> None:
        """清空图像（保留占位尺寸）。"""
        self._source = QPixmap()
        self.clear()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """窗口尺寸变化后重新等比缩放。"""
        super().resizeEvent(event)
        if self._fit:
            self._refresh()

    def _refresh(self) -> None:
        if self._source.isNull():
            self.clear()
            return
        scaled = self._source.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
