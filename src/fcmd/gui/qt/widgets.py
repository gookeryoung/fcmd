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

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QProgressBar,
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

from fcmd.gui.qt import theme as _qt_theme
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
    "LoadingBar",
    "LogView",
    "MetricGrid",
    "PageScroll",
    "PathRow",
    "RowListWidget",
    "SegmentBar",
    "StatusTag",
    "apply_splitter_sizes",
    "button",
    "hint_label",
    "icon_button",
    "link_button",
    "make_splitter",
    "make_table",
    "make_table_item",
    "mono_font",
    "mono_label",
    "segment_band",
    "segment_fill",
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


_STATUS_TONES = frozenset({"muted", "success", "warning", "danger", "accent"})


class StatusTag(QLabel):  # pragma: no cover - Qt 控件
    """状态标签（胶囊）：短状态文本 + 语义色背景，用于「运行中 / 监控中 / 成功」等。

    与 ``Hint`` 的区别是自带底色与边框，能在卡片标题行、工具行里作为独立状态块；
    配色按 ``tone`` 动态属性由 QSS 决定，切主题后自动跟随。
    """

    def __init__(self, text: str = "", *, tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusTag")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def tone(self) -> str:
        """当前语义色：``muted`` / ``success`` / ``warning`` / ``danger`` / ``accent``。"""
        return str(self.property("tone") or "muted")

    def set_tone(self, tone: str) -> None:
        """切换语义色；未知值回落到 ``muted``。"""
        normalized = tone if tone in _STATUS_TONES else "muted"
        if self.property("tone") == normalized:
            return
        self.setProperty("tone", normalized)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_status(self, text: str, tone: str | None = None) -> None:
        """一次写入文案与可选语义色（状态刷新最常用）。"""
        self.setText(text)
        if tone is not None:
            self.set_tone(tone)


def mono_font(point_size: int = 9) -> QFont:  # pragma: no cover - Qt 控件
    """系统等宽字体，字号不小于 ``point_size``（日志 / 代码 / 十六进制视图共用）。"""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(max(font.pointSize(), point_size))
    return font


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


class RowListWidget(QListWidget):  # pragma: no cover - Qt 控件
    """自定义行列表：行宽始终跟随视口，避免反复拖宽 / 拖窄后行宽度残留。

    ``QListWidget.setItemWidget`` 挂上去的行只在 item rect 变大时被 Qt 重新
    摆正；窗口反复拖拉后行会保留上一次的更大宽度，右半部分被推到视口之外。
    这里在每次尺寸变化后按当前可视区域重新摆正每行几何。
    """

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_row_widths()

    def sync_row_widths(self) -> None:
        """把每行自定义控件的几何对齐到列表当前可视区域。"""
        for index in range(self.count()):
            item = self.item(index)
            widget = self.itemWidget(item)
            if widget is not None:
                widget.setGeometry(self.visualRect(self.indexFromItem(item)))


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


_LOAD_BAND_THRESHOLDS = ((85.0, "danger"), (60.0, "warning"), (0.0, "success"))


def segment_band(percent: float | None) -> str:
    """占用率档位：≥85% 告急（danger）、≥60% 偏紧（warning）、其余充足（success）。

    未采样时返回 ``idle``，由调用方保留默认文字色，不误导为「充足」。
    """
    if percent is None:
        return "idle"
    for threshold, band in _LOAD_BAND_THRESHOLDS:
        if percent >= threshold:
            return band
    return "success"


def segment_fill(percent: float | None, segments: int) -> int:
    """把 0–100 占用率换算为点亮的格数：非零至少点亮 1 格，上限 ``segments``。"""
    if percent is None or segments <= 0:
        return 0
    clamped = min(max(float(percent), 0.0), 100.0)
    if clamped <= 0:
        return 0
    return min(max(round(clamped * segments / 100), 1), segments)


class SegmentBar(QWidget):  # pragma: no cover - Qt 控件
    """分段负载条（格图）：按占用率点亮格子，颜色随档位（充足 / 偏紧 / 告急）变化。

    用固定格数而不是连续进度条：低负载时也能一眼看出「用了几成」，格与格之间
    留缝，和概览页硬件卡负载块的余量分档配色（绿 / 琥珀 / 红）保持一致。
    """

    def __init__(
        self,
        *,
        segments: int = 24,
        height: int = 12,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._segments = max(int(segments), 1)
        self._percent: float | None = None
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("负载条")
        self.setToolTip("等待采样")

    def set_percent(self, percent: float | None) -> None:
        """写入 0–100 占用率；``None`` 表示尚未采样（全部格子保持空）。"""
        self._percent = None if percent is None else min(max(float(percent), 0.0), 100.0)
        self.setToolTip("等待采样" if self._percent is None else f"占用率 {self._percent:.1f}%")
        self.update()

    def percent(self) -> float | None:
        """当前占用率（供测试与调用方读取）。"""
        return self._percent

    def paintEvent(self, event: object) -> None:  # noqa: ARG002 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        track = palette.color(QPalette.ColorRole.Mid)
        theme_palette = _qt_theme.load_theme().palette
        band = segment_band(self._percent)
        fill = QColor(
            {
                "danger": theme_palette.danger,
                "warning": theme_palette.warning,
                "success": theme_palette.success,
                # 尚未采样时全格为空，颜色只作占位，避免首帧 KeyError。
                "idle": theme_palette.success,
            }[band]
        )
        gap = 2.0
        segment_width = (self.width() - gap * (self._segments - 1)) / self._segments
        if segment_width <= 0:
            return
        lit = segment_fill(self._percent, self._segments)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(self._segments):
            painter.setBrush(fill if index < lit else track)
            painter.drawRoundedRect(
                QRectF(index * (segment_width + gap), 0.0, segment_width, float(self.height())),
                2.0,
                2.0,
            )


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
    committed = Signal(str)

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
        self.edit.editingFinished.connect(lambda: self.committed.emit(self.value()))
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
            self.committed.emit(self.value())


class Collapsible(QWidget):  # pragma: no cover - Qt 控件
    """可折叠分区：标题按钮 + 内容容器（默认收起状态可配置）。

    ``header=False`` 时隐藏内置标题按钮，由调用方的图标按钮控制展开状态
    （对话页把「连接参数 / 生成参数」入口收进顶部工具行）。
    """

    def __init__(
        self,
        title: str,
        *,
        expanded: bool = False,
        header: bool = True,
        parent: QWidget | None = None,
    ) -> None:
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
        self.toggle.setVisible(header)
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

    def set_title(self, title: str) -> None:
        """更新分区标题（如追加文件数量等动态信息）。"""
        self._title = title
        self.toggle.setText(title)

    def set_expanded(self, expanded: bool) -> None:
        """展开 / 收起（同步箭头与内容可见性）。"""
        self.toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        """当前是否展开。"""
        return self.toggle.isChecked()


class LoadingBar(QWidget):  # pragma: no cover - Qt 控件
    """不定进度加载条：说明文字 + 忙碌动画 + 已用时（秒）。

    用于无法预估总量的后台加载（在线搜索、本地目录扫描、硬件检测等）：
    ``start()`` 后每 100ms 刷新已用时，``stop()`` 收起；同一页面同时只有
    一项任务时直接 ``start`` / ``stop``，多项任务由调用方合并且只保留
    最后开始的那条说明。
    """

    def __init__(self, text: str = "加载中…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(10)
        self.text_label = QLabel(text, self)
        self.text_label.setObjectName("LoadingText")
        self.elapsed_label = QLabel("0.0s", self)
        self.elapsed_label.setObjectName("Hint")
        self.elapsed_label.setToolTip("本次加载已用时")
        self.bar = QProgressBar(self)
        self.bar.setRange(0, 0)  # 0/0 = 忙碌态，不显示百分比
        self.bar.setTextVisible(False)
        self.bar.setMinimumWidth(160)
        row.addWidget(self.text_label, 0)
        row.addWidget(self.bar, 1)
        row.addWidget(self.elapsed_label, 0)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    def start(self, text: str | None = None) -> None:
        """显示加载条并重新计时；``text`` 为 ``None`` 时沿用上次说明。"""
        if text:
            self.text_label.setText(text)
        self._clock.start()
        self.elapsed_label.setText("0.0s")
        self._timer.start()
        self.setVisible(True)

    def set_text(self, text: str) -> None:
        """更新说明文字（已用时不清零）。"""
        self.text_label.setText(text)

    def stop(self) -> None:
        """停止计时并收起加载条。"""
        self._timer.stop()
        self.setVisible(False)

    def is_running(self) -> bool:
        """是否正在计时（供测试与调用方判断）。"""
        return self._timer.isActive()

    def elapsed_text(self) -> str:
        """当前已用时文本（形如 ``1.2s``）。"""
        return self.elapsed_label.text()

    def _tick(self) -> None:
        seconds = self._clock.elapsed() / 1000
        self.elapsed_label.setText(f"{seconds:.1f}s")


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
        self.view.setFont(mono_font())
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
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "llmw.log", "日志 (*.log);;所有文件 (*.*)")
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
