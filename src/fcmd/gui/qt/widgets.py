"""Qt 常用控件：卡片、折叠区、表单行、指标网格、日志区、表格与路径选择行。

这些控件是各窗口的「展示积木」：只做布局与最小交互，不含业务逻辑。
需要业务判断的窗口把自己的回调接到信号 / 按钮上，便于单测纯逻辑。

约定：

- 卡片统一用 ``QGroupBox#Card``，标题由主题 QSS 上色；
- 数值 / 说明类文字用 ``HintLabel``（次要色）与 ``value_label``（强调）；
- 日志区固定 ``QPlainTextEdit#Log`` + 工具条（复制 / 清空 / 导出），
  与既有日志区行为对齐；
- 全部控件类标注 ``pragma: no cover``（CI 无显示环境，不追求控件覆盖率）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.qt.layout import parse_sizes, serialize_sizes

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Card",
    "Collapsible",
    "ElidedLabel",
    "FormGrid",
    "HintLabel",
    "KeyValueGrid",
    "LogView",
    "MetricGrid",
    "PageScroll",
    "PathRow",
    "apply_splitter_sizes",
    "button",
    "hint_label",
    "make_splitter",
    "make_table",
    "make_table_item",
    "mono_label",
    "splitter_sizes",
    "value_label",
]


def button(text: str, *, kind: str = "", tooltip: str = "") -> QPushButton:  # pragma: no cover - Qt 控件
    """创建按钮；``kind`` 取 ``primary`` / ``danger`` / ``ghost`` 时套用主题变体。"""
    widget = QPushButton(text)
    if kind:
        widget.setObjectName(kind.capitalize())
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


def hint_label(text: str = "", *, wrap: bool = True) -> QLabel:  # pragma: no cover - Qt 控件
    """次要说明文字（灰色小字），默认自动换行。"""
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def value_label(text: str = "") -> QLabel:  # pragma: no cover - Qt 控件
    """强调数值文字（用于指标网格）。"""
    label = QLabel(text)
    label.setObjectName("Value")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def mono_label(text: str = "") -> QLabel:  # pragma: no cover - Qt 控件
    """等宽小字（命令、路径、CSV 片段）。"""
    label = QLabel(text)
    label.setObjectName("Mono")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def make_table_item(text: str) -> QTableWidgetItem:  # pragma: no cover - Qt 控件
    """只读表格单元格。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class Card(QFrame):  # pragma: no cover - Qt 控件
    """卡片容器：圆角 ``QFrame`` + 独立标题 ``QLabel`` + 纵向布局 ``body``。

    不用 ``QGroupBox``：它的 ``::title`` 是子控件，QSS 里给标题加背景会在圆角
    边框上留下方形色块，``subcontrol-origin: margin`` 又会把标题顶到卡片外面。
    自绘标题（普通 ``QLabel``）是 Qt 自定义卡片的最佳实践：标题与正文同一套
    布局，主题只负责颜色 / 字重，不依赖样式子控件的位置规则。
    """

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame = QVBoxLayout(self)
        frame.setContentsMargins(12, 10, 12, 10)
        frame.setSpacing(8)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("CardTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.title_label.setVisible(bool(title))
        frame.addWidget(self.title_label)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(6)
        frame.addLayout(self.body, 1)

    def title(self) -> str:
        """卡片标题文本（空串表示无标题）。"""
        return self.title_label.text()

    def setTitle(self, title: str) -> None:
        """更新标题；空串时隐藏标题行，只留正文。"""
        self.title_label.setText(title)
        self.title_label.setVisible(bool(title))


class PageScroll(QScrollArea):  # pragma: no cover - Qt 控件
    """纵向滚动页面：内容高于窗口时滚动，而不是把控件压扁 / 裁掉。

    约定：页面把卡片加进 :attr:`body`，不要给页面自身再加 ``QVBoxLayout``；
    ``widgetResizable=True`` 让内容在窗口变高时跟着拉伸（卡片内的 stretch 生效），
    内容最小高度超过视口时自动出现纵向滚动条。横向不滚动，避免卡片被推来推去。

    ``margins`` 供窗口骨架统一留白：单页工具窗口传 ``(0, 0, 0, 0)``，让外层
    容器负责边距；表单页保留默认的右侧 / 底部余量，避免滚动条压住卡片边框。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = (0, 0, 4, 8),
    ) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        container = QWidget(self)
        container.setObjectName("PageBody")
        self.body = QVBoxLayout(container)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(8)
        self.setWidget(container)


class HintLabel(QLabel):  # pragma: no cover - Qt 控件
    """次要说明文字，``text`` 可随时替换（替代旧版的可变提示行）。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Hint")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


class FormGrid(QWidget):  # pragma: no cover - Qt 控件
    """两列表单网格：每行「标签 + 控件」或「标签 + 控件 + 标签 + 控件」。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)
        self._row = 0

    def add_row(self, label: str, widget: QWidget) -> None:
        """整行：标签在 0 列，控件跨 1–3 列。"""
        self.grid.addWidget(QLabel(label), self._row, 0, Qt.AlignmentFlag.AlignRight)
        self.grid.addWidget(widget, self._row, 1, 1, 3)
        self._row += 1

    def add_pair(
        self,
        label: str,
        left: QWidget,
        right_label: str = "",
        right: QWidget | None = None,
    ) -> None:
        """一行放两对「标签 + 控件」；``right`` 省略时左控件跨 1–3 列。"""
        self.grid.addWidget(QLabel(label), self._row, 0, Qt.AlignmentFlag.AlignRight)
        self.grid.addWidget(left, self._row, 1)
        if right is not None:
            self.grid.addWidget(QLabel(right_label), self._row, 2, Qt.AlignmentFlag.AlignRight)
            self.grid.addWidget(right, self._row, 3)
        else:
            self.grid.addWidget(left, self._row, 1, 1, 3)
        self._row += 1

    def row_count(self) -> int:
        """已占用的行数（供测试与调试）。"""
        return self._row


class MetricGrid(QWidget):  # pragma: no cover - Qt 控件
    """指标网格：按键写入文本，每行 ``columns`` 个「标签: 数值」单元。"""

    def __init__(self, keys: Sequence[tuple[str, str]], *, columns: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, QLabel] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for index, (label, key) in enumerate(keys):
            row, column = divmod(index, columns)
            cell = QWidget(self)
            cell.setMinimumWidth(150)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            cell_layout.addWidget(hint_label(f"{label}:", wrap=False), 0, Qt.AlignmentFlag.AlignTop)
            value = value_label("-")
            value.setWordWrap(True)
            cell_layout.addWidget(value, 1, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(cell, row, column)
            self._values[key] = value
        for column in range(columns):
            grid.setColumnStretch(column, 1)

    def set_value(self, key: str, text: str) -> None:
        """写入单个指标；未知 key 静默忽略，避免面板改版导致崩溃。"""
        label = self._values.get(key)
        if label is not None:
            label.setText(text)

    def set_values(self, values: dict[str, str]) -> None:
        """批量写入指标。"""
        for key, text in values.items():
            self.set_value(key, text)

    def reset(self, text: str = "-") -> None:
        """把所有指标重置为占位文本。"""
        for label in self._values.values():
            label.setText(text)

    def value(self, key: str) -> str:
        """读取当前显示文本（测试用）。"""
        label = self._values.get(key)
        return "" if label is None else label.text()


class KeyValueGrid(QWidget):  # pragma: no cover - Qt 控件
    """只读「项目 / 路径 / 来源」三列表格，供设置页与硬件信息使用。"""

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = make_table(headers, parent=self)
        layout.addWidget(self.table)

    def set_rows(self, rows: Sequence[Sequence[str]]) -> None:
        """整体替换行内容。"""
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                self.table.setItem(row, column, make_table_item(str(text)))


class PathRow(QWidget):  # pragma: no cover - Qt 控件
    """路径输入行：输入框 + 「浏览…」+「恢复默认」按钮。"""

    changed = Signal(str)

    def __init__(
        self,
        *,
        value: str = "",
        placeholder: str = "",
        directory: bool = True,
        show_reset: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        layout.addWidget(self.edit, 1)
        self.browse = button("浏览…")
        self.browse.clicked.connect(self._pick)
        layout.addWidget(self.browse)
        self.reset = button("恢复默认", kind="ghost") if show_reset else None
        if self.reset is not None:
            self.reset.setVisible(False)
            layout.addWidget(self.reset)

    def value(self) -> str:
        """当前输入文本（已去除首尾空白）。"""
        return self.edit.text().strip()

    def set_value(self, text: str) -> None:
        """写入输入文本（不触发额外副作用）。"""
        self.edit.setText(text)

    def _pick(self) -> None:  # pragma: no cover - 需要真实对话框
        if self._directory:
            path = QFileDialog.getExistingDirectory(self, "选择目录", self.value())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", self.value())
        if path:
            self.edit.setText(path)


class Collapsible(QWidget):  # pragma: no cover - Qt 控件
    """可折叠分区：标题按钮 + 内容容器（默认收起状态可配置）。"""

    def __init__(self, title: str, *, expanded: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.toggle = QToolButton(self)
        self.toggle.setObjectName("Collapsible")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setText(title)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)
        self.content = QWidget(self)
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(12, 2, 0, 0)
        self.body.setSpacing(6)
        self.content.setVisible(expanded)
        layout.addWidget(self.content)
        self._title = title

    def _on_toggled(self, checked: bool) -> None:
        self.content.setVisible(checked)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def set_expanded(self, expanded: bool) -> None:
        """展开 / 收起（同步箭头与内容可见性）。"""
        self.toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        """当前是否展开。"""
        return self.toggle.isChecked()


class LogView(QWidget):  # pragma: no cover - Qt 控件
    """只读日志区 + 工具条（复制全部 / 清空 / 导出）。"""

    def __init__(self, *, height: int = 180, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch(1)
        self.copy_button = button("复制全部", kind="ghost")
        self.clear_button = button("清空", kind="ghost")
        self.export_button = button("导出…", kind="ghost")
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)
        self.view = QPlainTextEdit(self)
        self.view.setObjectName("Log")
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(height)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.view, 1)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(max(font.pointSize(), 9))
        self.view.setFont(font)
        self.clear_button.clicked.connect(self.clear)
        self.copy_button.clicked.connect(self.copy_all)
        self.export_button.clicked.connect(self._export)

    def append(self, message: str) -> None:
        """追加一行并按需滚动到底部。"""
        self.view.appendPlainText(message)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def text(self) -> str:
        """全部日志文本。"""
        return self.view.toPlainText()

    def clear(self) -> None:
        """清空日志。"""
        self.view.clear()

    def copy_all(self) -> None:
        """复制全部日志到剪贴板。"""
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.text())

    def _export(self) -> None:  # pragma: no cover - 需要真实对话框
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "locllmw.log", "日志 (*.log);;所有文件 (*.*)")
        if not path:
            return
        from pathlib import Path

        Path(path).write_text(self.text(), encoding="utf-8")


def make_table(
    headers: Sequence[str],
    *,
    widths: Sequence[int] | None = None,
    stretch_columns: Sequence[int] = (),
    stretch_last: bool = False,
    parent: QWidget | None = None,
) -> QTableWidget:  # pragma: no cover - Qt 控件
    """创建只读、整行选择、交替行色的表格。"""
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    for index in range(len(headers)):
        if index in stretch_columns:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(index, widths[index] if widths and index < len(widths) else 120)
    if stretch_last and len(headers) and len(headers) - 1 not in stretch_columns:
        header.setStretchLastSection(True)
    return table


class ElidedLabel(QLabel):  # pragma: no cover - Qt 控件
    """长文本标签：按可用宽度中间省略，完整内容放进 tooltip。

    用于长路径、长 URL、长命令。水平尺寸策略为 ``Ignored``，避免超长文本
    反过来把窗口撑宽；窗口放大后自动显示更多字符。
    """

    def __init__(
        self,
        text: str = "",
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_text = text
        self._elide_mode = mode
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._refresh()

    def setText(self, text: str) -> None:
        """设置完整文本（显示内容按当前宽度省略）。"""
        self._full_text = text
        self._refresh()

    def fullText(self) -> str:
        """完整文本（未省略）。"""
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        """按当前宽度重算显示文本与 tooltip。"""
        available = self.contentsRect().width()
        if available <= 0:
            super().setText(self._full_text)
            super().setToolTip("")
            return
        shown = self.fontMetrics().elidedText(self._full_text, self._elide_mode, available)
        super().setText(shown)
        super().setToolTip(self._full_text if shown != self._full_text else "")


def make_splitter(
    orientation: Qt.Orientation = Qt.Orientation.Horizontal,
    *,
    sizes: Sequence[int] = (),
    parent: QWidget | None = None,
) -> QSplitter:  # pragma: no cover - Qt 控件
    """创建不可折叠的分栏容器（可拖动，尺寸可持久化）。"""
    splitter = QSplitter(orientation, parent)
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(6)
    if sizes:
        splitter.setSizes(list(sizes))
    return splitter


def splitter_sizes(splitter: QSplitter) -> list[int]:  # pragma: no cover - Qt 控件
    """读取分栏尺寸（写入设置文件用）。"""
    return serialize_sizes(splitter.sizes())


def apply_splitter_sizes(splitter: QSplitter, sizes: object) -> bool:  # pragma: no cover - Qt 控件
    """按记忆尺寸恢复分栏；非法内容保持当前布局并返回 ``False``。"""
    parsed = parse_sizes(sizes)
    if parsed is None:
        return False
    splitter.setSizes(parsed)
    return True
