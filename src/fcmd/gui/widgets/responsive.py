"""自适应窗口辅助：内容高度下限、宽度驱动的字号与流式等宽分配。

这些策略用于「窗口可以缩小，但内容不能被遮挡」的场景：

- :class:`ContentHeightGuard`：窗口变矮时按内容最小高度抬高下限，避免滚动条 /
  裁剪成为唯一可读方式；
- :class:`ResponsiveLabel`：按 owner 宽度在 min/max 字号之间缩放，适合大号数字 /
  标题，不会因为固定 QSS 字号把窄窗口撑破；
- :func:`fit_flow_widths`：让 FlowLayout 里的固定宽度按钮按可用宽度均分，宽窗封顶、
  窄窗收缩，避免一排按钮右侧突然留白或溢出。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QLabel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtWidgets import QScrollArea, QWidget

__all__ = ["ContentHeightGuard", "ResponsiveLabel", "fit_flow_widths"]


class ContentHeightGuard(QObject):  # pragma: no cover - Qt 控件
    """让 owner 的最小高度始终容得下 ``scroll`` 内容（至少 ``floor``）。

    在 owner 的 resize 事件后延迟一次同步：先让布局按新宽度 / FlowLayout 换行算好
    内容最小高度，再抬高 ``minimumHeight``。这样缩高度时窗口会停在刚好容纳内容的
    位置，而不是出现纵向滚动条把内容藏起来。
    """

    def __init__(self, owner: QWidget, scroll: QScrollArea, *, floor: int) -> None:
        super().__init__(owner)
        self._owner = owner
        self._scroll = scroll
        self._floor = max(0, floor)
        self._pending = False
        self._syncing = False
        owner.installEventFilter(self)
        QTimer.singleShot(0, self.sync)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._owner and event.type() == QEvent.Type.Resize and not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._apply)
        return super().eventFilter(watched, event)

    def _apply(self) -> None:
        self._pending = False
        self.sync()

    def sync(self) -> None:
        """立即按当前内容高度校正 owner 的最小高度。"""
        if not self._owner.isVisible() or self._syncing:
            return
        body = self._scroll.widget()
        viewport = self._scroll.viewport()
        if body is None or viewport.height() <= 0:
            return
        chrome = max(0, self._owner.height() - viewport.height())
        body_height = max(body.minimumSizeHint().height(), body.minimumSize().height())
        target = max(self._floor, body_height + chrome)
        if target == self._owner.minimumHeight():
            return
        self._syncing = True
        try:
            self._owner.setMinimumHeight(target)
        finally:
            self._syncing = False


class ResponsiveLabel(QLabel):  # pragma: no cover - Qt 控件
    """按 owner 宽度缩放字号的标签；字号变化只写 ``font-size``，保留主题其它样式。"""

    def __init__(  # noqa: PLR0913 - 自适应参数成组传入
        self,
        text: str = "",
        *,
        owner: QWidget,
        min_point: int = 24,
        max_point: int = 50,
        width_reserve: int = 96,
        points_per_px: float = 10.7,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._owner = owner
        self._min_point = max(1, min_point)
        self._max_point = max(self._min_point, max_point)
        self._width_reserve = max(0, width_reserve)
        self._points_per_px = max(1.0, points_per_px)
        self._applying = False
        self._point_size = 0
        owner.installEventFilter(self)
        self.sync_font()

    @property
    def point_size(self) -> int:
        """当前生效的字号（未同步时为 0）。"""
        return self._point_size

    def sync_font(self) -> None:
        """按 owner 当前宽度重算字号；字号未变化时直接返回。"""
        if self._applying:
            return
        available = max(300, self._owner.width() - self._width_reserve)
        point = max(self._min_point, min(self._max_point, round(available / self._points_per_px)))
        if point == self._point_size:
            return
        self._point_size = point
        self._applying = True
        try:
            self.setStyleSheet(f"font-size: {point}pt;")
        finally:
            self._applying = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._owner and event.type() == QEvent.Type.Resize:
            self.sync_font()
        return super().eventFilter(watched, event)


def fit_flow_widths(  # noqa: PLR0913 - 宽度策略参数成组传入
    host: QWidget,
    widgets: Sequence[QWidget],
    available: int,
    *,
    spacing: int = 4,
    min_width: int = 42,
    max_width: int = 70,
) -> int:
    """把 FlowLayout 内的控件按 ``available`` 均分，返回实际占用总宽度。

    ``available`` 应由 owner 宽度推导（不要读 host 当前宽度：宽窗缩窄时 host 可能
    仍被上一轮固定宽度撑着，读到旧值会让布局永远不收缩）。返回总宽度供调用方
    同步相邻控件（例如「输入框 + 主按钮」容器）。
    """
    count = len(widgets)
    if count <= 0 or available <= 0:
        return 0
    target = max(min_width, min(max_width, (available - spacing * (count - 1)) // count))
    for widget in widgets:
        widget.setFixedWidth(target)
    layout = host.layout()
    if layout is not None:
        layout.invalidate()
        layout.activate()
    return target * count + spacing * (count - 1)
