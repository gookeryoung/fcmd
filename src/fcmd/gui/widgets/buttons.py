"""按钮类控件：主 / 危险 / 次要按钮、图标按钮、胶囊开关与确认框。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QColor, QEnterEvent, QIcon, QPaintEvent
from PySide6.QtWidgets import (
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QWidget,
)

from fcmd.gui import style as _qt_theme

__all__ = [
    "ToggleChip",
    "button",
    "confirm",
    "icon_button",
    "link_button",
    "set_button_icon",
    "set_button_kind",
    "set_button_text",
]


def set_button_text(widget: QPushButton, text: str, *, tone: str | None = None) -> None:
    """改写按钮文字并按新文案更新图标（保留原色调，未命中时清空图标）。

    动态文案（启动 / 停止服务、下载中…、转换中等）不能只 ``setText``：
    否则图标还停在创建时那个动作上，看起来与文字不匹配。
    """
    from PySide6.QtGui import QIcon

    from fcmd.gui.icons import ICON_NAME_PROPERTY, ICON_TONE_PROPERTY, icon_for_text, set_icon

    name = icon_for_text(text)
    mapped = tone if tone is not None else widget.property("fcmdIconTone")
    mapped = mapped if isinstance(mapped, str) and mapped else "text"
    # 轮询会反复调用到这里：文案 / 图标 / 色调都没变时直接返回，避免每 3 秒
    # 重新生成一次位图图标并触发按钮重绘（实例列表会看起来在闪）。
    if widget.text() == text:
        if name and widget.property(ICON_NAME_PROPERTY) == name and widget.property(ICON_TONE_PROPERTY) == mapped:
            return
        if not name and widget.property(ICON_NAME_PROPERTY) in (None, ""):
            return
    widget.setText(text)
    if name:
        set_icon(widget, name, tone=mapped)
        return
    widget.setIcon(QIcon())
    widget.setProperty(ICON_NAME_PROPERTY, None)


def set_button_icon(widget: QPushButton, name: str, *, tone: str = "text") -> None:
    """只换按钮图标、不改文字；图标名 / 色调未变时直接返回。

    纯图标按钮（实例行内的启动 / 停止）不能再走 :func:`set_button_text`：
    空文案会让它清空图标。
    """
    from fcmd.gui.icons import ICON_NAME_PROPERTY, ICON_TONE_PROPERTY, set_icon

    mapped = tone or "text"
    if widget.property(ICON_NAME_PROPERTY) == name and widget.property(ICON_TONE_PROPERTY) == mapped:
        return
    set_icon(widget, name, tone=mapped)


def set_button_kind(widget: QPushButton, kind: str) -> None:
    """运行时切换按钮变体（``primary`` / ``danger`` / ``ghost`` / 空）。

    动态动作按钮（启动 ↔ 停止）不能只改文案：配色 / 边框由 ``#Primary`` /
    ``#Danger`` 的 QSS 决定，必须同时换 objectName 并重抛光；动作状态未变化时
    直接返回，避免轮询里反复重解析样式。图标色调由 :func:`set_button_text` 负责。
    """
    name = kind.capitalize() if kind else ""
    if widget.objectName() == name:
        return
    widget.setObjectName(name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def button(
    text: str,
    *,
    kind: str = "",
    tooltip: str = "",
    icon: str | None = None,
) -> QPushButton:  # pragma: no cover - Qt 控件
    """创建按钮；``kind`` 取 ``primary`` / ``danger`` / ``ghost`` 时套用主题变体。

    ``icon`` 为图标名（见 :mod:`fcmd.gui.icons`）；省略时按按钮文案自动推断，
    传空串可显式禁用图标。图标不改变按钮文字，便于保留既有文案与测试口径。
    """
    from fcmd.gui.icons import icon_for_text, set_icon

    widget = QPushButton(text)
    name = icon_for_text(text) if icon is None else icon
    if name:
        # 图标配色跟随按钮变体：主按钮用 on_accent、危险按钮用 danger、
        # 次要按钮用 muted，避免图标与文字一深一浅。
        tone = {"primary": "on_accent", "danger": "danger", "ghost": "muted"}.get(kind, "text")
        set_icon(widget, name, tone=tone)
    if kind:
        widget.setObjectName(kind.capitalize())
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


def link_button(text: str, *, tooltip: str = "") -> QPushButton:  # pragma: no cover - Qt 控件
    """文字链接按钮：透明背景、强调色、无边框，用于「修改 / 重新检测」等轻量动作。"""
    widget = QPushButton(text)
    widget.setObjectName("Link")
    widget.setFlat(True)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


def icon_button(text: str, *, tooltip: str = "", checkable: bool = False) -> QToolButton:  # pragma: no cover
    """方形图标按钮：用一个字形作图标，必配 tooltip；``checkable`` 用于展开 / 收起开关。"""
    widget = QToolButton()
    widget.setObjectName("IconButton")
    widget.setText(text)
    widget.setCheckable(checkable)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.setToolTip(tooltip)
    return widget


class ToggleChip(QToolButton):  # pragma: no cover - Qt 控件
    """圆角胶囊开关：图标 + 文字，选中时淡强调色底与强调色文字。

    参考元宝等对话产品输入区的模式开关：未选中是浅灰胶囊，悬停加深；选中回到
    强调色系。图标色调随选中状态在 ``muted`` / ``accent`` 间切换，主题切换后由
    :func:`fcmd.gui.icons.refresh_icons` 按记录色调重绘。

    QSS 只负责底色 / 圆角 / hover 与选中态；图标与文字由 :meth:`paintEvent`
    按 ``_H_PADDING`` 均分左右留白后整体居中——QToolButton 自带排布会把 QSS
    ``padding`` 和图标位置分开计算，胶囊内容会偏左、右侧留白偏大。
    """

    _HEIGHT = 30
    _H_PADDING = 12
    _ICON_GAP = 6

    def __init__(
        self,
        text: str,
        *,
        icon: str = "",
        tooltip: str = "",
        tall: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToggleChip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setText(text)
        # 输入区工具行里的胶囊与 34px 图标按钮同高，外框上下沿对齐。
        self._height = 34 if tall else self._HEIGHT
        # 输入区胶囊略增左右留白：图标 + 文字有呼吸空间，视觉上更容易读成居中。
        self._h_padding = 16 if tall else self._H_PADDING
        if tall:
            self.setProperty("tall", "true")
        self.setFixedHeight(self._height)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._icon_name = icon
        self._hovered = False
        if tooltip:
            self.setToolTip(tooltip)
        self._refresh_icon()
        self.toggled.connect(lambda _checked: self._refresh_icon())

    def sizeHint(self) -> QSize:
        """左右等宽内边距 + 图标 + 间距 + 文字，宽度不随选中态变化。"""
        metrics = self.fontMetrics()
        icon_width = self.iconSize().width() if not self.icon().isNull() else 0
        gap = self._ICON_GAP if icon_width else 0
        width = 2 * self._h_padding + icon_width + gap + metrics.horizontalAdvance(self.text())
        return QSize(width, self._height)

    def minimumSizeHint(self) -> QSize:
        """布局压缩时也不低于内容宽度，避免文字贴边。"""
        return self.sizeHint()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """样式只画 QSS 底色；图标与文字按同一垂直中心整体居中。"""
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = QIcon()
        option.toolButtonStyle = Qt.ToolButtonStyle.ToolButtonIconOnly
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)

        metrics = painter.fontMetrics()
        icon = self.icon()
        icon_size = self.iconSize()
        icon_width = icon_size.width() if not icon.isNull() else 0
        gap = self._ICON_GAP if icon_width else 0
        text = self.text()
        ink = metrics.tightBoundingRect(text)
        # 用文字墨迹宽度（而非 advance）与图标一起做光学居中：advance 尾部留白会
        # 让整组内容视觉偏左，胶囊右侧看起来比左侧空。
        group = icon_width + gap + ink.width()
        x = (self.width() - group) // 2
        if icon_width:
            painter.drawPixmap(x, (self.height() - icon_size.height()) // 2, icon.pixmap(icon_size))
            x += icon_width + gap
        painter.setPen(self._content_color())
        # 按字体 ascent / descent 的几何中心放基线（与 Qt 原生按钮一致），
        # 用墨迹中心会在部分 CJK 字体上把文字推得偏高 / 偏低。
        baseline = (self.height() + metrics.ascent() - metrics.descent()) // 2
        painter.drawText(x - ink.left(), baseline, text)

    def _content_color(self) -> QColor:
        """内容色与 QSS 语义色一致：禁用 disabled、选中 accent、悬停 text、其余 muted。"""
        palette = _qt_theme.active_theme().palette
        if not self.isEnabled():
            return QColor(palette.disabled_fg)
        if self.isChecked():
            return QColor(palette.accent)
        if self._hovered:
            return QColor(palette.text)
        return QColor(palette.muted)

    def enterEvent(self, event: QEnterEvent) -> None:
        """悬停时文字转正文色，图标同步换 tone。"""
        self._hovered = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self._refresh_icon()
        super().leaveEvent(event)

    def _refresh_icon(self) -> None:
        """按选中 / 悬停状态切换图标色调，与自绘文字色保持一致。"""
        if not self._icon_name:
            return
        from fcmd.gui.icons import set_icon

        if not self.isEnabled():
            tone = "disabled_fg"
        elif self.isChecked():
            tone = "accent"
        elif self._hovered:
            tone = "text"
        else:
            tone = "muted"
        set_icon(self, self._icon_name, tone=tone)

    def changeEvent(self, event: QEvent) -> None:  # pragma: no cover - Qt 控件
        """启用 / 禁用切换时重绘自绘内容与图标（QSS 不会触发 paintEvent 之外的更新）。"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._refresh_icon()
            self.update()


def confirm(parent: QWidget, title: str, text: str) -> bool:
    """是 / 否确认框，默认焦点在「否」。"""
    answer = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes
