"""药丸页签：透明底圆角页签与页签数量徽标。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QTabBar, QTabWidget, QWidget

from fcmd.gui import style as _qt_theme

__all__ = ["PillTabBar", "badge_text", "pill_tabs", "tab_widget"]


_BADGE_SIZE = 16
"""数量徽标直径（像素）：比正文行高略小，不撑高页签。"""

_BADGE_EXTRA_WIDTH = 18
"""带徽标的页签额外预留的宽度：徽标画在页签右端，不与文字重叠。"""

_BADGE_RIGHT_INSET = 9
"""徽标距页签矩形右端的留白：QSS 给页签留了 6px 右外边距，再加 3px 呼吸空间。

只按 3px 摆放会让徽标压在选中页签的圆角边框上（运行页「画图」页签最明显）。
"""

_BADGE_BOTTOM_INSET = 4
"""页签 QSS 的下外边距（像素）：徽标垂直居中时扣掉，否则整体偏下。"""


def _badge_colors() -> tuple[QColor, QColor]:
    """徽标配色：半透明强调色底 + 强调色数字（跟随当前主题）。"""
    try:
        palette = _qt_theme.active_theme().palette
        fill = QColor(palette.accent)
        text = QColor(palette.accent)
    except Exception:  # pragma: no cover - 无主题 / 早期构造
        fill = QColor("#2f6fa8")
        text = QColor("#2f6fa8")
    fill.setAlpha(85)  # 半透明：底色透出页签自身背景，又不至于看不清数字
    return fill, text


def badge_text(count: int) -> str:
    """徽标文案：三位数封顶为 ``99``，避免圆点被撑破。"""
    return "99" if count > 99 else str(count)


class PillTabBar(QTabBar):  # pragma: no cover - Qt 控件
    """药丸页签栏：支持在页签右侧画半透明圆形数量徽标。

    ``pill_tabs`` 会装好这一类型；调用方只需 ``tabBar().set_badge(index, count)``。
    徽标不改变页签文字：宽度由 :meth:`tabSizeHint` 预留，绘制在页签右端。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._badges: dict[int, int] = {}

    # -- 数据 ---------------------------------------------------------- #
    def set_badge(self, index: int, count: int) -> None:
        """给第 ``index`` 个页签设置数量徽标；``count <= 0`` 时清除。"""
        previous = self._badges.get(index, 0)
        if count <= 0:
            if previous == 0:
                return
            self._badges.pop(index, None)
        else:
            if previous == count:
                return
            self._badges[index] = count
        has_badge_changed = bool(previous > 0) != bool(count > 0)
        self.update()
        if has_badge_changed:
            self._relayout()

    def badge(self, index: int) -> int:
        """第 ``index`` 个页签的徽标数量（0 表示没有徽标）。"""
        return self._badges.get(index, 0)

    def _relayout(self) -> None:
        """宽度变化后让 QTabBar 重新布局：Qt 没有公开的 relayout，重设一次文本即可。"""
        for index in range(self.count()):
            self.setTabText(index, self.tabText(index))
        self.updateGeometry()

    # -- 布局与绘制 ---------------------------------------------------- #
    def tabSizeHint(self, index: int) -> QSize:
        size = super().tabSizeHint(index)
        if self._badges.get(index):
            size.setWidth(size.width() + _BADGE_EXTRA_WIDTH)
        return size

    def _badge_rect(self, index: int) -> QRectF:
        """徽标矩形：扣掉页签 QSS 的右 / 下外边距，圆点落在页签内部。"""
        rect = self.tabRect(index)
        return QRectF(
            rect.right() - _BADGE_SIZE - _BADGE_RIGHT_INSET,
            rect.center().y() - _BADGE_SIZE / 2 - _BADGE_BOTTOM_INSET / 2 + 0.5,
            _BADGE_SIZE,
            _BADGE_SIZE,
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._badges:
            return
        fill, text_color = _badge_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setBold(True)
        # 圆点里要塞下一两位数字：比页签字号小 2pt，最小 7.5pt 保证可读。
        font.setPointSizeF(max(7.5, font.pointSizeF() - 2.0))
        painter.setFont(font)
        for index, count in self._badges.items():
            if not 0 <= index < self.count():
                continue
            if self.tabRect(index).isNull():
                continue
            circle = self._badge_rect(index)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawEllipse(circle)
            painter.setPen(text_color)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, badge_text(count))


def pill_tabs(tabs: QTabWidget, *, object_name: str = "PillTabs") -> QTabWidget:  # pragma: no cover - Qt 控件
    """把页签容器改成「药丸」外观：无整条基线、透明底 + 圆角选中块。

    主题 QSS 里唯一的页签外观契约（``QTabWidget#PillTabs`` / ``QTabBar#PillTabBar``）：
    页签与内容同底色衔接，不再出现页签上方 / 下方的多余横条。运行页的
    「对话 / 编程 / 画图…」与模型行展开后的「详细信息 / 文件列表」都用它。

    ``object_name`` 用于换容器底色：行卡展开区用 ``RowTabs``（与行卡同为
    ``$surface``），页面级用法保持默认的透明底。
    """
    tabs.setDocumentMode(True)
    tabs.setObjectName(object_name)
    bar = tabs.tabBar()
    if not isinstance(bar, PillTabBar):
        # 换成支持数量徽标的页签栏；页面内容 / 顺序不受影响。
        bar = PillTabBar(tabs)
        tabs.setTabBar(bar)
    bar.setObjectName("PillTabBar")
    bar.setDrawBase(False)
    bar.setExpanding(False)
    bar.setUsesScrollButtons(False)
    bar.setElideMode(Qt.TextElideMode.ElideNone)
    return tabs


def tab_widget(parent: QWidget | None = None, *, object_name: str = "PillTabs") -> QTabWidget:
    """创建药丸页签容器；项目内 **只允许** 通过本工厂创建 ``QTabWidget``。

    默认 ``QTabWidget`` 页签栏带整条基线 / 边框，与卡片、行卡的圆角边框叠在一起
    会露出错位的横线。这里一次性套好 :func:`pill_tabs`，调用方只需
    ``tab_widget(self)``（行卡展开区传 ``object_name="RowTabs"``）。
    """
    return pill_tabs(QTabWidget(parent), object_name=object_name)
