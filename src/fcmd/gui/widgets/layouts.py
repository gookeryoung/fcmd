"""流式布局：控件按行铺开，超出可用宽度自动换行。

用于动作条（按钮 + 分隔线）这类「数量固定、宽度随窗口变化」的横排：窗口收窄时
按钮不压缩到文字被截断，而是整块换到下一行。``heightForWidth`` 会按当前宽度
返回换行后的高度，外层 ``QVBoxLayout`` 因而能正确给它留出多行空间。

用法与普通 ``QHBoxLayout`` 相同（``addWidget``），但**不要**再调 ``addStretch``：
流式布局按内容宽度逐行排列，剩余空间留在行尾。
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget

__all__ = ["FlowLayout"]


class FlowLayout(QLayout):  # pragma: no cover - Qt 控件
    """从左到右、自上而下换行的布局。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margin: int = 0,
        h_spacing: int = 6,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ------------------------------------------------------------------ #
    # QLayout 协议
    # ------------------------------------------------------------------ #
    def addItem(self, item: QLayoutItem) -> None:
        """追加一个布局项（``addWidget`` 最终走这里）。"""
        self._items.append(item)

    def count(self) -> int:
        """布局项数量。"""
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        """按下标取布局项；越界返回 ``None``（QLayout 协议要求）。"""
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        """按下标移除并返回布局项；越界返回 ``None``。"""
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:
        """不主动占满剩余空间：行尾留白，不把最后一行拉成整宽。"""
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        """高度取决于可用宽度（换行数）。"""
        return True

    def heightForWidth(self, width: int) -> int:
        """给定宽度下排版所需的高度。"""
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        """实际排版：按当前矩形逐行放置控件。"""
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        """首选尺寸：所有项排成一行时的宽高（高度给外层布局一个可见的起点）。"""
        margins = self.contentsMargins()
        width = 0
        height = 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            width += hint.width() + self._h_spacing
            height = max(height, hint.height())
        width = max(width - self._h_spacing, 0)
        return QSize(width, height) + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:
        """最小尺寸：最宽的一项 + 边距，避免整行被压到文字截断。"""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # ------------------------------------------------------------------ #
    # 排版
    # ------------------------------------------------------------------ #
    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        """把控件逐行摆进 ``rect``；``test_only`` 为真时只算高度不改几何。"""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = area.x()
        y = area.y()
        line_height = 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h_spacing
            # 当前行已放过控件且再放会越界 → 换行（单个超宽项仍独占一行）。
            if next_x - self._h_spacing > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._v_spacing
                next_x = x + hint.width() + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()
