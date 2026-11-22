"""侧边栏列表：参考 DeepSeek 对话历史列表的简洁条目样式。

通用组件，不绑定任何业务：条目由调用方提供标题 / 副标题 / 右侧摘要 / 分组，
选中与悬停由自绘 delegate 处理（圆角浅色底、无左侧强调条、文字省略），比默认
``QListWidget`` 的整行方块选中更轻。支持分组标题行（不可选中）。

- :class:`SidebarEntry`：一条数据（``key`` 由调用方定义，``section`` 非空时插入分组标题）；
- :class:`SidebarList`：``QListWidget`` 子类，保留 ``count`` / ``setCurrentRow`` 等原生 API；
  ``current_key()`` / ``set_current_key()`` 按业务 key 读写当前项。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QWidget

from fcmd.gui import style as _qt_theme

__all__ = ["SidebarEntry", "SidebarList"]

TITLE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
SUBTITLE_ROLE = TITLE_ROLE + 1
META_ROLE = TITLE_ROLE + 2
SECTION_ROLE = TITLE_ROLE + 3
KEY_ROLE = TITLE_ROLE + 4
ENABLED_ROLE = TITLE_ROLE + 5


@dataclass(frozen=True, slots=True)
class SidebarEntry:
    """侧边栏列表的一条数据；``section`` 变化时在条目前插入一个分组标题。"""

    title: str
    key: Any = None
    subtitle: str = ""
    meta: str = ""
    section: str = ""
    tooltip: str = ""
    enabled: bool = True


class _SidebarDelegate(QStyledItemDelegate):
    """自绘条目：圆角选中底 + 两行标题 / 副标题 + 右侧摘要。"""

    ROW_HEIGHT = 34
    ROW_HEIGHT_WITH_SUBTITLE = 48
    SECTION_HEIGHT = 26
    H_PADDING = 10
    RADIUS = 8

    def sizeHint(self, _option: QStyleOptionViewItem, index: Any) -> QSize:
        if bool(index.data(SECTION_ROLE)):
            return QSize(0, self.SECTION_HEIGHT)
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        height = self.ROW_HEIGHT_WITH_SUBTITLE if subtitle else self.ROW_HEIGHT
        return QSize(0, height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: Any) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        theme = _theme()
        palette = theme.palette
        rect = option.rect.adjusted(4, 2, -4, -2)
        title = str(index.data(TITLE_ROLE) or "")
        if bool(index.data(SECTION_ROLE)):
            font = QFont(option.font)
            font.setPointSize(theme.fonts.small)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(palette.muted))
            section_rect = QRect(rect.left() + self.H_PADDING, rect.top(), rect.width(), rect.height())
            painter.drawText(section_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            painter.restore()
            return

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        enabled = bool(index.data(ENABLED_ROLE) is not False)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.surface_active))
            painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette.surface_hover))
            painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

        title_font = QFont(option.font)
        title_font.setPointSize(theme.fonts.body)
        if selected:
            title_font.setWeight(QFont.Weight.DemiBold)
        sub_font = QFont(option.font)
        sub_font.setPointSize(theme.fonts.small)
        title_metrics = QFontMetrics(title_font)
        sub_metrics = QFontMetrics(sub_font)
        subtitle = str(index.data(SUBTITLE_ROLE) or "")
        meta = str(index.data(META_ROLE) or "")
        content = rect.adjusted(self.H_PADDING, 0, -self.H_PADDING, 0)
        meta_width = sub_metrics.horizontalAdvance(meta) + 8 if meta else 0
        title_width = max(content.width() - meta_width, 0)
        title_height = title_metrics.height()
        sub_height = sub_metrics.height()
        total_height = title_height + (2 + sub_height if subtitle else 0)
        top = content.top() + max((content.height() - total_height) // 2, 0)
        title_rect = QRect(content.left(), top, title_width, title_height)
        painter.setFont(title_font)
        painter.setPen(QColor(palette.text if (selected or hovered) else palette.muted))
        text = title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, title_width)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if meta:
            meta_rect = QRect(content.right() - meta_width + 8, top, meta_width - 8, title_height)
            painter.setFont(sub_font)
            painter.setPen(QColor(palette.muted))
            painter.drawText(
                meta_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                sub_metrics.elidedText(meta, Qt.TextElideMode.ElideRight, meta_width),
            )
        if subtitle:
            sub_rect = QRect(content.left(), top + title_height + 2, content.width(), sub_height)
            painter.setFont(sub_font)
            painter.setPen(QColor(palette.muted if enabled else palette.border))
            painter.drawText(
                sub_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                sub_metrics.elidedText(subtitle, Qt.TextElideMode.ElideRight, content.width()),
            )
        painter.restore()


def _theme() -> Any:
    """当前生效主题；无主题时回退到调色板默认值（委托只读取 palette / fonts）。"""
    try:
        return _qt_theme.active_theme()
    except Exception:  # pragma: no cover - 早期构造 / 无主题
        from fcmd.gui.theme import THEMES

        return next(iter(THEMES.values()))


class SidebarList(QListWidget):  # pragma: no cover - Qt 控件
    """侧边栏条目列表：圆角选中态、标题省略、可选副标题 / 右侧摘要 / 分组。

    与默认 ``QListWidget`` 的区别只在视觉：条目由 delegate 自绘，没有整行方块和
    左侧粗边框；分组标题行不可选中。``count()`` / ``setCurrentRow()`` /
    ``currentRowChanged`` 等原生 API 不变，接入成本低。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarList")
        self.setMouseTracking(True)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setWordWrap(False)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(_SidebarDelegate(self))

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def add_entry(self, entry: SidebarEntry) -> QListWidgetItem:
        """追加一条（不插入分组标题；需要分组时用 :meth:`set_entries`）。"""
        item = QListWidgetItem()
        _apply_entry(item, entry)
        self.addItem(item)
        return item

    def set_entries(self, entries: list[SidebarEntry]) -> None:
        """整体替换条目；``section`` 变化时自动插入不可选中的分组标题。"""
        self.blockSignals(True)
        try:
            self.clear()
            last_section = ""
            for entry in entries:
                if entry.section and entry.section != last_section:
                    header = QListWidgetItem()
                    header.setData(TITLE_ROLE, entry.section)
                    header.setData(SECTION_ROLE, True)
                    header.setFlags(Qt.ItemFlag.NoItemFlags)
                    self.addItem(header)
                    last_section = entry.section
                self.add_entry(entry)
            self.setCurrentRow(-1)
        finally:
            self.blockSignals(False)

    def current_key(self) -> Any:
        """当前条目的业务 key；没有选中 / 分组标题时返回 ``None``。"""
        item = self.currentItem()
        return None if item is None else item.data(KEY_ROLE)

    def set_current_key(self, key: Any) -> None:
        """按业务 key 选中条目；找不到时不做任何事。"""
        for row in range(self.count()):
            item = self.item(row)
            if item is not None and item.data(KEY_ROLE) == key:
                self.setCurrentRow(row)
                return


def _apply_entry(item: QListWidgetItem, entry: SidebarEntry) -> None:
    item.setData(TITLE_ROLE, entry.title)
    item.setData(SUBTITLE_ROLE, entry.subtitle)
    item.setData(META_ROLE, entry.meta)
    item.setData(KEY_ROLE, entry.key)
    item.setData(ENABLED_ROLE, entry.enabled)
    item.setToolTip(entry.tooltip or entry.title)
    if not entry.enabled:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
