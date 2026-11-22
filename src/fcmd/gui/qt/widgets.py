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

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QElapsedTimer, QEvent, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QIcon,
    QPainter,
    QPaintEvent,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyleOptionToolButton,
    QStylePainter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.qt import icons as _qt_icons
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.layout import parse_sizes, serialize_sizes

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Card",
    "ChoiceCard",
    "ChoiceGroup",
    "Collapsible",
    "ElidedLabel",
    "FormGrid",
    "HintLabel",
    "ItemRow",
    "KeyValueGrid",
    "LoadingBar",
    "LogView",
    "MetricGrid",
    "PageScroll",
    "PathField",
    "PathRow",
    "ProgressRow",
    "RowListWidget",
    "SegmentBar",
    "SelectionRow",
    "StatusTag",
    "ToggleChip",
    "apply_splitter_sizes",
    "button",
    "confirm",
    "fit_dialog_to_content",
    "hint_label",
    "icon_button",
    "link_button",
    "make_splitter",
    "make_table",
    "make_table_item",
    "mono_font",
    "mono_label",
    "pill_tabs",
    "section_header",
    "segment_band",
    "segment_fill",
    "set_button_kind",
    "set_button_text",
    "splitter_sizes",
    "value_label",
]


def set_button_text(widget: QPushButton, text: str, *, tone: str | None = None) -> None:
    """改写按钮文字并按新文案更新图标（保留原色调，未命中时清空图标）。

    动态文案（启动 / 停止服务、下载中…、转换中等）不能只 ``setText``：
    否则图标还停在创建时那个动作上，看起来与文字不匹配。
    """
    from PySide6.QtGui import QIcon

    from fcmd.gui.qt.icons import ICON_NAME_PROPERTY, icon_for_text, set_icon

    widget.setText(text)
    name = icon_for_text(text)
    if name:
        mapped = tone if tone is not None else widget.property("fcmdIconTone")
        set_icon(widget, name, tone=mapped if isinstance(mapped, str) and mapped else "text")
        return
    widget.setIcon(QIcon())
    widget.setProperty(ICON_NAME_PROPERTY, None)


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

    ``icon`` 为图标名（见 :mod:`fcmd.gui.qt.icons`）；省略时按按钮文案自动推断，
    传空串可显式禁用图标。图标不改变按钮文字，便于保留既有文案与测试口径。
    """
    from fcmd.gui.qt.icons import icon_for_text, set_icon

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
    :func:`fcmd.gui.qt.icons.refresh_icons` 按记录色调重绘。

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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ToggleChip")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setText(text)
        self.setFixedHeight(self._HEIGHT)
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
        width = 2 * self._H_PADDING + icon_width + gap + metrics.horizontalAdvance(self.text())
        return QSize(width, self._HEIGHT)

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
        x = (self.width() - icon_width - gap - metrics.horizontalAdvance(text)) // 2
        if icon_width:
            painter.drawPixmap(x, (self.height() - icon_size.height()) // 2, icon.pixmap(icon_size))
            x += icon_width + gap
        painter.setPen(self._content_color())
        # 用文字实际墨迹框（去掉字体上下 leading）居中，避免 CJK 比图标偏低。
        bounds = metrics.tightBoundingRect(text)
        painter.drawText(x, (self.height() - bounds.height()) // 2 - bounds.top(), text)

    def _content_color(self) -> QColor:
        """内容色与 QSS 语义色一致：选中 accent、悬停 text、其余 muted。"""
        palette = _qt_theme.active_theme().palette
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
        from fcmd.gui.qt.icons import set_icon

        if self.isChecked():
            tone = "accent"
        elif self._hovered:
            tone = "text"
        else:
            tone = "muted"
        set_icon(self, self._icon_name, tone=tone)


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
    """等宽字体，字号不小于 ``point_size``（日志 / 代码 / 十六进制视图共用）。

    优先用界面约定的拉丁字体 Cascadia Code（GUI 英文一律这一族），系统没装时回退
    到 Qt 的系统等宽字体；中文由系统的逐字回退补上。
    """
    from fcmd.gui.qt import theme as _qt_theme
    from fcmd.gui.theme import MONO_FONT_CANDIDATES

    base = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).pointSize()
    size = max(base, point_size)
    families = _qt_theme.font_families()
    font = QFont(_qt_theme.pick_font_family(families, MONO_FONT_CANDIDATES)) if families else QFont()
    if not families:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(size)
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
    bar.setObjectName("PillTabBar")
    bar.setDrawBase(False)
    bar.setExpanding(False)
    bar.setUsesScrollButtons(False)
    bar.setElideMode(Qt.TextElideMode.ElideNone)
    return tabs


class RowListWidget(QListWidget):  # pragma: no cover - Qt 控件
    """自定义行列表：行宽始终跟随视口，避免反复拖宽 / 拖窄后行宽度残留。

    ``QListWidget.setItemWidget`` 挂上去的行只在 item rect 变大时被 Qt 重新
    摆正；窗口反复拖拉后行会保留上一次的更大宽度，右半部分被推到视口之外。
    这里在每次尺寸变化后按当前可视区域重新摆正每行几何。
    """

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_row_widths()

    def sync_selection(self, current: int) -> None:
        """只更新「上一行 / 当前行」的选中态，避免每次切换重抛光整列行卡。

        行数是几十到几百，逐行 ``set_selected`` 会为每行跑一次 QSS 重抛光
        （实测 200 行单次切换约 7ms，按键连按就明显卡顿）；这里记住上一次的
        当前行，只动两行。
        """
        previous = getattr(self, "_selected_row", -1)
        for index in {previous, current}:
            if not 0 <= index < self.count():
                continue
            widget = self.itemWidget(self.item(index))
            if isinstance(widget, SelectionRow):
                widget.set_selected(index == current)
        self._selected_row = current

    def sync_row_widths(self) -> None:
        """把每行自定义控件的几何对齐到列表当前可视区域。"""
        for index in range(self.count()):
            item = self.item(index)
            widget = self.itemWidget(item)
            if widget is not None:
                widget.setGeometry(self.visualRect(self.indexFromItem(item)))


class SelectionRow:
    """自定义行控件的选中态 mixin。

    与 QSS 的 ``[selected="true"]`` 选择器配套：列表当前行变化时由页面调用，
    切换动态属性并立即重新抛光，无需重建控件。

    抛光（unpolish + polish）要重解析该控件的 QSS，几百行时每次切选中都很贵；
    因此这里做两件事：属性没变就直接返回（幂等），调用方用
    :meth:`RowListWidget.sync_selection` 只处理旧 / 新两行，而不是遍历全部行。
    """

    def is_selected(self: QWidget) -> bool:
        """当前是否处于选中态（读动态属性，避免重复抛光）。"""
        return self.property("selected") == "true"

    def set_selected(self: QWidget, selected: bool) -> None:
        """切换选中态边框；状态未变化时不重复抛光。"""
        value = "true" if selected else "false"
        if self.property("selected") == value:
            return
        self.setProperty("selected", value)
        self.style().unpolish(self)
        self.style().polish(self)


_METRICS_SAMPLES = ("000.0 GiB",)
"""右侧数值 / 状态列的默认宽度样本：按它测量一次并固定列宽。

同一列表里的行共用同一组样本（例如镜像行把「仅 wsl --install」也加进去），
所以体积 / 状态文案长短不同时，分隔线与动作按钮仍逐行对齐。
"""


def _click_through(widget: QWidget) -> None:
    """让行卡里的文字标签不拦鼠标：点击落到列表上，才能选中整行。

    ``ElidedLabel`` 与 ``StatusTag`` 默认是 ``TextSelectableByMouse``，落在标题 /
    状态胶囊上的点击会被标签吃掉（连 ``QListWidget`` 都收不到），用户就会觉得
    「点这一行没反应」。
    """
    if isinstance(widget, QLabel):
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)


class ItemRow(SelectionRow, QFrame):  # pragma: no cover - Qt 控件
    """可展开行卡骨架：标题 + 副标题 / 标签行 + 右侧数值与动作 + 行内展开区。

    这是 llmw 模型列表行卡（``_ModelItemRow``）的公共结构，wslmanw 的发行版 / 镜像
    行卡与它同构，故上提到共享件：

    - 上半行 ``header``：左侧 ``title_label``（``ElidedLabel``）在 ``title_row``，
      下面是 ``sub_label`` + ``note_row``（副标题后跟状态胶囊）；右侧 ``header_row``
      依次是 ``set_metrics`` 数值、``add_action`` 动作按钮、末尾固定展开箭头；
    - 下半行 ``detail_host`` / ``detail_layout``：详情视图由页面在展开时挂进来；
      同一时间只展开一行由页面负责，行本身只管显隐与箭头图标。

    子类只写自己的业务渲染与状态位；追加控件用 ``add_title_widget``（标题右侧，
    如发布方）、``add_note_widget``（副标题之后、拉伸之前）、``add_tag``（拉伸之后
    的状态胶囊 / 进度小字，追加顺序即显示顺序）、``add_action``（拉伸之后、箭头之前）。

    外观是**一份**共享契约，不按应用 / 子类分别开对象名：行卡挂动态属性
    ``itemRow="true"``（主题里所有行卡规则都用它选），标题标签统一
    ``QLabel#ItemRowName``。应用想加自己的样式再另外设 objectName，不会丢掉
    共享行卡外观。

    行卡内的文字一律不抢鼠标（``NoTextInteraction``）：``ElidedLabel`` / ``StatusTag``
    默认允许拖选文本，会把落在标题 / 胶囊上的点击吃掉，列表就选不中这一行，
    表现为「点名字没反应、得再点旁边」。
    """

    toggleRequested = Signal(object)

    def __init__(  # noqa: PLR0913 - 骨架的可选项由调用方显式给出
        self,
        title: str = "",
        subtitle: str = "",
        *,
        elide: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        expand_hint: str = "展开详情",
        collapse_hint: str = "收起详情",
        metrics_samples: Sequence[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._expanded = False
        self._metrics_samples = tuple(metrics_samples) if metrics_samples else _METRICS_SAMPLES
        self._expand_hint = expand_hint
        self._collapse_hint = collapse_hint
        self.metrics_label: QLabel | None = None
        self._note_extras = 0
        self._chip_count = 0
        # 副标题行是否真的有内容：决定右侧动作是与第二行（标签 / 版本）对齐还是整行居中。
        self._note_has_content = bool(subtitle)
        self.setObjectName("ItemRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 共享行卡外观的选择器：所有应用的可展开行卡共用这一条契约。
        self.setProperty("itemRow", "true")
        self.setProperty("selected", "false")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QWidget(self)
        self.header_row = QHBoxLayout(self.header)
        # 行卡内边距略放宽，标题 / 副标题与右侧动作之间留呼吸；动作按钮彼此靠近，
        # 与文字区用一条细分隔线隔开（见 add_action）。
        self.header_row.setContentsMargins(14, 9, 12, 9)
        self.header_row.setSpacing(8)

        self.left = QVBoxLayout()
        self.left.setContentsMargins(0, 0, 0, 0)
        self.left.setSpacing(4)
        self.title_row = QHBoxLayout()
        self.title_row.setContentsMargins(0, 0, 0, 0)
        self.title_row.setSpacing(8)
        self.title_label = ElidedLabel(title, mode=elide, parent=self.header)
        self.title_label.setObjectName("ItemRowName")
        self.title_label.setToolTip(title)
        _click_through(self.title_label)
        self.title_row.addWidget(self.title_label, 1)
        self.left.addLayout(self.title_row)

        self.note_row = QHBoxLayout()
        self.note_row.setContentsMargins(0, 0, 0, 0)
        self.note_row.setSpacing(6)
        self.sub_label = QLabel(subtitle, self.header)
        self.sub_label.setObjectName("Hint")
        self.sub_label.setWordWrap(False)
        self.sub_label.setMinimumWidth(0)
        self.sub_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.sub_label.setToolTip(subtitle)
        # 空副标题不占位：否则单行行卡会多出一条空白行，文字与右侧按钮对不齐。
        self.sub_label.setVisible(bool(subtitle))
        _click_through(self.sub_label)
        self.note_row.addWidget(self.sub_label, 0)
        self.note_row.addStretch(1)
        self.left.addLayout(self.note_row)
        self.header_row.addLayout(self.left, 1)

        # 动作区与文字区之间的细分隔线：首次 add_action / set_metrics 时才插入，
        # 没有动作的行不显示，避免留下一条悬空竖线。
        self.action_divider = QFrame(self.header)
        self.action_divider.setObjectName("RowActionDivider")
        self.action_divider.setFrameShape(QFrame.Shape.VLine)
        self.action_divider.setFixedWidth(1)
        self.action_divider.setFixedHeight(20)
        self.action_divider.setProperty("selected", "false")
        self.action_divider.setVisible(False)
        self._action_divider_added = False

        self.expand_button = icon_button("", tooltip=expand_hint)
        self.expand_button.setIconSize(QSize(16, 16))
        _qt_icons.set_icon(self.expand_button, "chevron-down", tone="muted")
        self.expand_button.setAccessibleName(expand_hint)
        self.expand_button.clicked.connect(lambda: self.toggleRequested.emit(self))
        self.header_row.addWidget(self.expand_button, 0)
        outer.addWidget(self.header)

        self.detail_host = QWidget(self)
        self.detail_host.setVisible(False)
        self.detail_layout = QVBoxLayout(self.detail_host)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(0)
        outer.addWidget(self.detail_host)
        # 初始对齐：有副标题 / 标签就与第二行对齐，否则整行居中。
        self._sync_note_state()

    # ------------------------------------------------------------------ #
    # 内容插槽
    # ------------------------------------------------------------------ #
    def set_title(self, text: str) -> None:
        """换标题文字（同时同步 tooltip，轮询就地刷新行时用）。"""
        self.title_label.setText(text)
        self.title_label.setToolTip(text)

    def set_subtitle(self, text: str) -> None:
        """换副标题（版本 / 占用、架构 / 来源等）；空串时收起这一行。"""
        self.sub_label.setText(text)
        self.sub_label.setToolTip(text)
        self.sub_label.setVisible(bool(text))
        self._sync_note_state()

    def _sync_note_state(self) -> None:
        """记录副标题行是否有内容，并把右侧动作对齐到「标签行」或整行居中。

        行卡是「标题 + 副标题 / 标签」两行结构：右侧按钮与第二行**居中对齐**（两者
        中线重合）看起来最稳；没有第二行（只有标题）时改为整行垂直居中，避免按钮
        贴在行底、标题悬空。
        """
        self._note_has_content = bool(self.sub_label.text()) or self._note_extras > 0 or bool(self._chip_count)
        align = Qt.AlignmentFlag.AlignBottom if self._note_has_content else Qt.AlignmentFlag.AlignVCenter
        if getattr(self, "_action_align", None) == align:
            return  # 轮询里每条状态刷新都调到这里，对齐没变就别再触发一次布局
        self._action_align = align
        left_index = self.header_row.indexOf(self.left)
        for index in range(max(left_index, 0) + 1, self.header_row.count()):
            item = self.header_row.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                self.header_row.setAlignment(widget, align)

    def add_title_widget(self, widget: QWidget) -> None:
        """标题右侧的小标记（如发布方）。"""
        _click_through(widget)
        self.title_row.addWidget(widget, 0)

    def add_note_widget(self, widget: QWidget, *, stretch: int = 0) -> None:
        """副标题之后、状态胶囊之前的小字（如发布时间）。

        ``stretch=1`` 表示这块小字吃掉剩余宽度（放不下时由控件自己省略），
        拉伸空位让给它，后面的状态胶囊仍贴着行尾。
        """
        # 顺序恒为「副标题 → 小字 → 拉伸空位 → 状态胶囊」，与调用先后无关。
        _click_through(widget)
        index = 1 + self._note_extras
        self.note_row.insertWidget(index, widget, stretch)
        self._action_align = None
        self._note_extras += 1
        if stretch:
            self.note_row.setStretch(index + 1, 0)
        self._sync_note_state()

    def add_tag(self, widget: QWidget) -> None:
        """副标题行右端的状态胶囊 / 进度小字，调用顺序即显示顺序。

        标记 ``rowChip`` 动态属性，行卡内的胶囊（状态 / 量化 / 发布方 / 下载进度）
        共享一套高度、圆角、内边距与字号，只按对象名区分颜色语义。
        """
        _click_through(widget)
        widget.setProperty("rowChip", "true")
        self.note_row.addWidget(widget, 0)
        self._chip_count += 1
        self._action_align = None
        self._sync_note_state()

    def add_action(self, widget: QWidget) -> None:
        """右侧动作按钮：始终插在展开箭头之前，并在首次调用时补上动作分隔线。"""
        index = self.header_row.indexOf(self.expand_button)
        index = max(index, 0)
        if not self._action_divider_added:
            self.header_row.insertWidget(index, self.action_divider, 0)
            self.action_divider.setVisible(True)
            self._action_divider_added = True
            index += 1
        self.header_row.insertWidget(index, widget, 0)
        self._action_align = None  # 新控件也要套用同一对齐
        self._sync_note_state()

    def set_selected(self, selected: bool) -> None:
        """同步选中态到动作分隔线：选中底较深，必须换成更亮的线才能看清。"""
        super().set_selected(selected)
        value = "true" if selected else "false"
        if self.action_divider.property("selected") != value:
            self.action_divider.setProperty("selected", value)
            self.action_divider.style().unpolish(self.action_divider)
            self.action_divider.style().polish(self.action_divider)

    def set_metrics(self, text: str) -> QLabel:
        """右侧数值 / 摘要（模型大小、镜像来源等），位于动作按钮左侧。"""
        if self.metrics_label is None:
            self.metrics_label = QLabel(text, self.header)
            _click_through(self.metrics_label)
            # 右侧数值摘要与副标题区分：更小、加粗、右对齐，扫读时能先看到体积 / 状态。
            self.metrics_label.setObjectName("RowMetrics")
            self.metrics_label.setWordWrap(False)
            self.metrics_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.metrics_label.setToolTip(text)
            self.metrics_label.setFixedWidth(self._metrics_lane_width())
            self.add_action(self.metrics_label)  # 内部会重置对齐缓存并重新套用
            return self.metrics_label
        self.metrics_label.setText(text)
        self.metrics_label.setToolTip(text)
        return self.metrics_label

    def _metrics_lane_width(self) -> int:
        """数值 / 状态列的固定宽度（逻辑像素）。

        各行体积 / 状态文案长短不同时，若按内容宽度分配，右侧分隔线与动作按钮会
        逐行左右跳动，标签看起来「上下不齐」。这里按最宽样本测量一次后固定列宽；
        文案超宽时仍靠 tooltip 给出完整值。
        """
        # 先抛光再测量：``RowMetrics`` 的 8pt 字号来自 QSS，未抛光时读到的是应用默认字号。
        assert self.metrics_label is not None
        self.metrics_label.ensurePolished()
        font_metrics = QFontMetrics(self.metrics_label.font())
        widest = max(font_metrics.horizontalAdvance(sample) for sample in self._metrics_samples)
        return widest + 14

    # ------------------------------------------------------------------ #
    # 展开 / 收起
    # ------------------------------------------------------------------ #
    def is_expanded(self) -> bool:
        """详情区是否展开（同一时间只展开一行由页面保证）。"""
        return self._expanded

    def set_expanded(self, expanded: bool) -> None:
        """展开 / 收起详情区（行高由页面按新尺寸更新）。"""
        self._expanded = expanded
        self.detail_host.setVisible(expanded)
        _qt_icons.set_icon(self.expand_button, "chevron-up" if expanded else "chevron-down", tone="muted")
        self.expand_button.setToolTip(self._collapse_hint if expanded else self._expand_hint)
        self.expand_button.setAccessibleName(self._collapse_hint if expanded else self._expand_hint)


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
        self._frame = frame
        self.title_row: QHBoxLayout | None = None
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

    def add_title_action(self, widget: QWidget) -> None:
        """把控件放到标题行右侧；首次调用时把标题标签改成「标题 + 动作」横排。"""
        if self.title_row is None:
            self.title_row = QHBoxLayout()
            self.title_row.setContentsMargins(0, 0, 0, 0)
            self.title_row.setSpacing(8)
            index = self._frame.indexOf(self.title_label)
            self._frame.removeWidget(self.title_label)
            # 标题行带动作时标题不换行：QLabel 的 wordWrap sizeHint 会把
            # 「推理引擎（llama.cpp）」拆成两行，卡片标题应当单行显示。
            self.title_label.setWordWrap(False)
            self.title_row.addWidget(self.title_label)
            self.title_row.addStretch(1)
            self._frame.insertLayout(index, self.title_row)
        self.title_row.addWidget(widget)

    def add_log(self, *, height: int = 140, minimum_height: int = 0) -> LogView:
        """在卡片正文里追加带工具条的日志区并返回。

        分栏页面（表格 + 日志）需要独立的「日志」卡；这里统一构造，
        避免各窗口重复 ``Card("日志") + LogView + body.addWidget``。
        """
        view = LogView(height=height, parent=self)
        if minimum_height:
            view.setMinimumHeight(minimum_height)
        self.body.addWidget(view)
        return view


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


def fit_dialog_to_content(dialog: QDialog, *, min_width: int | None = None) -> None:
    """把对话框高度收到内容高度（去掉底部空白，避免出现多余滚动条）。

    对话框不再写死 ``resize(w, h)``：表单 / 进度这类内容自适应的窗口先按布局
    求出 ``sizeHint``，再让窗口贴合它；宽度保持当前值（可传 ``min_width`` 兜底）。
    表格 / 日志为主、确实需要用户拖高的窗口不要调用它。
    """
    if min_width is not None:
        dialog.setMinimumWidth(min_width)
    layout = dialog.layout()
    if layout is not None:
        layout.activate()
    dialog.adjustSize()


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


def section_header(title: str, parent: QWidget | None = None) -> tuple[QHBoxLayout, QLabel]:
    """卡片内的分节标题行：``SectionTitle`` 标签 + 弹性空白。

    返回 ``(布局, 标题标签)``，调用方把布局加进 ``Card.body`` 后继续往布局右侧
    追加状态 / 动作控件。与直接使用 ``Card`` 标题的区别是保留 ``SectionTitle``
    的标题字号，避免改变既有卡片的视觉层级。
    """
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(8)
    label = QLabel(title, parent)
    label.setObjectName("SectionTitle")
    header.addWidget(label)
    header.addStretch(1)
    return header, label


class ChoiceCard(QPushButton):  # pragma: no cover - Qt 控件
    """可选项方块：标题 + 下方说明，整块可点击。"""

    def __init__(self, title: str, description: str, *, tone: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChoiceCard")
        if tone:
            self.setProperty("tone", tone)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(72)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        title_label = QLabel(title, self)
        title_label.setObjectName("ChoiceTitle")
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        desc_label = QLabel(description, self)
        desc_label.setObjectName("ChoiceHint")
        desc_label.setWordWrap(True)
        desc_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)


class ChoiceGroup(QWidget):  # pragma: no cover - Qt 控件
    """一组互斥的方块选项，``key()`` / ``set_key()`` 读写当前值。"""

    changed = Signal(str)

    def __init__(
        self,
        options: Sequence[tuple[str, str, str]],
        *,
        current: str = "",
        columns: int = 4,
        tones: dict[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._current = current
        self._buttons: dict[str, ChoiceCard] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for index, (key, title, description) in enumerate(options):
            card = ChoiceCard(title, description, tone=(tones or {}).get(key, ""), parent=self)
            card.clicked.connect(lambda _checked=False, key=key: self._select(key))
            self._group.addButton(card)
            self._buttons[key] = card
            row, column = divmod(index, columns)
            grid.addWidget(card, row, column)
        for column in range(columns):
            grid.setColumnStretch(column, 1)
        self.set_key(current, emit=False)

    def key(self) -> str:
        return self._current

    def set_key(self, key: str, *, emit: bool = False) -> None:
        card = self._buttons.get(key)
        if card is None:
            return
        self._current = key
        card.setChecked(True)
        if emit:
            self.changed.emit(key)

    def _select(self, key: str) -> None:
        if key == self._current:
            return
        self._current = key
        self.changed.emit(key)


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
    """路径输入行：输入框 + 选择按钮（默认「浏览…」）+「恢复默认」按钮。"""

    changed = Signal(str)
    committed = Signal(str)

    def __init__(  # noqa: PLR0913
        self,
        *,
        value: str = "",
        placeholder: str = "",
        directory: bool = True,
        show_reset: bool = False,
        browse_text: str = "浏览…",
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
        self.browse = button(browse_text)
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


class PathField(QWidget):  # pragma: no cover - Qt 控件
    """带路径提示的输入行：输入框 + 浏览按钮 + 第二行省略路径。

    与 :class:`PathRow` 的区别是下方多一行 ``ElidedLabel``，用于表单里显示
    完整路径或「未选择 / 留空为 …」说明。需要保存对话框、扩展名过滤或自定义
    提示文案时，继承本类覆写 :meth:`_pick` / :meth:`_refresh_hint`。
    """

    def __init__(  # noqa: PLR0913 - 控件构造参数逐项显式，便于按行替换
        self,
        *,
        value: str = "",
        placeholder: str = "",
        directory: bool = True,
        browse_text: str = "浏览…",
        title: str = "选择文件",
        pattern: str = "",
        hint_object_name: str = "Mono",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        self._title = title
        self._pattern = pattern
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.edit = QLineEdit(value, self)
        self.edit.setPlaceholderText(placeholder)
        layout.addWidget(self.edit, 0, 0)
        self.browse = button(browse_text)
        self.browse.clicked.connect(self._pick)
        layout.addWidget(self.browse, 0, 1)
        self.hint = ElidedLabel("", parent=self)
        self.hint.setObjectName(hint_object_name)
        layout.addWidget(self.hint, 1, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        self.edit.textChanged.connect(self._refresh_hint)
        self._refresh_hint(self.edit.text())

    def value(self) -> str:
        """当前输入文本（去首尾空白）。"""
        return self.edit.text().strip()

    def set_value(self, text: str) -> None:
        """写入输入文本。"""
        self.edit.setText(text)

    def _refresh_hint(self, text: str) -> None:
        self.hint.setText(str(Path(text).expanduser()) if text.strip() else "未选择")

    def _pick(self) -> None:  # pragma: no cover - 需要真实对话框
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(self, self._title, self.value())
        else:
            filters = f"{self._pattern};;所有文件 (*.*)" if self._pattern else "所有文件 (*.*)"
            chosen, _ = QFileDialog.getOpenFileName(self, self._title, self.value(), filters)
        if chosen:
            self.edit.setText(chosen)


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


_PROGRESS_STEPS = 1000
"""确定进度条的内部刻度：与字节数解耦，避免几十 GB 的商品文件撑爆 QProgressBar 的 int 范围。"""


class LoadingBar(QWidget):  # pragma: no cover - Qt 控件
    """不定进度加载条：说明文字 + 忙碌动画 + 已用时（秒）。

    用于无法预估总量的后台加载（在线搜索、本地目录扫描、硬件检测等）：
    ``start()`` 后每 100ms 刷新已用时，``stop()`` 收起；同一页面同时只有
    一项任务时直接 ``start`` / ``stop``，多项任务由调用方合并且只保留
    最后开始的那条说明。
    """

    def __init__(self, text: str = "加载中…", parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingBar")
        row = QHBoxLayout(self)
        # 紧凑模式给状态栏用：不要上下留白、进度条压细，整条底栏保持一行高。
        if compact:
            row.setContentsMargins(8, 0, 8, 0)
            row.setSpacing(8)
        else:
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
        self.bar.setMinimumWidth(120 if compact else 160)
        if compact:
            self.bar.setFixedHeight(6)
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

    def set_progress(self, done: int, total: int | None) -> None:
        """切到确定进度（下载等能预估总量的任务）；``total`` 未知时回到忙碌动画。

        ``QProgressBar`` 的取值是 32 位 ``int``，几十 GB 的镜像字节数会溢出，所以
        内部统一折算成 ``0.._PROGRESS_STEPS`` 的刻度，只用于画填充比例。
        """
        if not total or total <= 0:
            self.bar.setRange(0, 0)
            return
        ratio = max(0.0, min(1.0, done / total))
        self.bar.setRange(0, _PROGRESS_STEPS)
        self.bar.setValue(int(ratio * _PROGRESS_STEPS))

    def stop(self) -> None:
        """停止计时并收起加载条（进度条回到忙碌态，下次复用时不会残留百分比）。"""
        self._timer.stop()
        self.bar.setRange(0, 0)
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


class ProgressRow(QWidget):  # pragma: no cover - Qt 控件
    """进度条 + 可选状态文字 + 右侧动作区。

    下载 / 转换 / 批处理窗口的通用底栏：``bar`` 由调用方控制确定 / 不定进度，
    ``add_action`` 依次追加按钮；``show_bar=False`` 时只保留状态文字，供只需要
    一行状态提示（如性能测试）的窗口复用同一布局。
    """

    def __init__(
        self,
        text: str = "",
        *,
        show_bar: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.label = hint_label(text, wrap=False)
        self.label.setVisible(bool(text))
        self.bar = QProgressBar(self)
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setMinimumWidth(120)
        self.bar.setVisible(show_bar)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        row.addWidget(self.label, 0)
        row.addWidget(self.bar, 1)
        row.addLayout(self._actions, 0)

    def set_text(self, text: str) -> None:
        """更新状态文字；空文本时收起标签。"""
        self.label.setText(text)
        self.label.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        """把按钮追加到右侧动作区，返回该控件便于链式使用。"""
        self._actions.addWidget(widget)
        return widget


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

    def append_timestamped(self, message: str) -> None:
        """追加一行 ``HH:MM:SS`` 前缀的日志（工具窗口的常用格式）。"""
        self.append(f"{datetime.now():%H:%M:%S} {message}")

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
