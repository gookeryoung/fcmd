"""可展开行卡与行列表：选中态、数值列对齐、详情区与展开态管理。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.widgets.buttons import icon_button
from fcmd.gui.widgets.text import ElidedLabel

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ItemRow",
    "RowDetailMixin",
    "RowListWidget",
    "SelectionRow",
]


class RowListWidget(QListWidget):  # pragma: no cover - Qt 控件
    """自定义行列表：行宽始终跟随视口，避免反复拖宽 / 拖窄后行宽度残留。

    ``QListWidget.setItemWidget`` 挂上去的行只在 item rect 变大时被 Qt 重新
    摆正；窗口反复拖拉后行会保留上一次的更大宽度，右半部分被推到视口之外。
    这里在每次尺寸变化后按当前可视区域重新摆正每行几何。

    外观是**一份**共享契约：构造时挂 ``rowList="true"`` 动态属性，主题按属性选
    择器统一行列表的透明背景与条目样式（与 :class:`ItemRow` 的 ``itemRow`` 契约
    同构），各应用不再按自己的对象名分别开样式。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("rowList", "true")

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
        # 子控件也可能按祖先的 ``[selected="true"]`` 匹配（如行内 Ghost 按钮），
        # 只重抛光父级不会刷新它们的 QSS，边框会停在旧配色上；这里连带子控件一起
        # 重抛光，选中态的所有后代样式才会同步更新。
        for child in self.findChildren(QWidget):
            child.style().unpolish(child)
            child.style().polish(child)


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
        copy_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._expanded = False
        self._copy_text = ""
        self._copy_button: QToolButton | None = None
        self._copy_timer: QTimer | None = None
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
        self.expand_button.set_icon_name("chevron-down", tone="muted")
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
        if copy_text:
            self.set_copy_text(copy_text)

    # ------------------------------------------------------------------ #
    # 内容插槽
    # ------------------------------------------------------------------ #
    def set_title(self, text: str) -> None:
        """换标题文字（同时同步 tooltip，轮询就地刷新行时用）；文字未变不重绘。"""
        if self.title_label.fullText() == text:
            return
        self.title_label.setText(text)
        self.title_label.setToolTip(text)

    def set_subtitle(self, text: str) -> None:
        """换副标题（版本 / 占用、架构 / 来源等）；空串时收起这一行。"""
        if self.sub_label.text() == text and self.sub_label.isHidden() == bool(text):
            return
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

    # ------------------------------------------------------------------ #
    # 复制全称（可配置）
    # ------------------------------------------------------------------ #
    def set_copy_text(self, text: str) -> None:
        """配置标题右侧的「复制」小按钮；空串时收起（不占用标题行）。

        通用能力：需要复制完整名称的行（如 llmw 模型行复制 ``组织/模型``）传文本
        即可，其它行不传就不显示；按钮只复制，不改变选中行。
        """
        text = text.strip()
        self._copy_text = text
        if not text:
            if self._copy_button is not None:
                self._copy_button.hide()
                self._copy_button.setToolTip("")
            return
        button = self._ensure_copy_button()
        button.show()
        button.setToolTip(f"复制 {text}")

    def copy_text(self) -> str:
        """当前配置的复制文本（空串表示没有复制按钮）。"""
        return self._copy_text

    def copy_button(self) -> QToolButton | None:
        """标题右侧的复制按钮（测试 / 调试用；未配置时为 ``None``）。"""
        return self._copy_button

    def _ensure_copy_button(self) -> QToolButton:
        """首次配置时创建小号复制按钮（图标按钮，不抢行卡选中）。"""
        if self._copy_button is None:
            button = icon_button("", tooltip="复制完整名称")
            button.setProperty("rowCopy", "true")
            button.setFixedSize(QSize(22, 22))
            button.setIconSize(QSize(14, 14))
            button.set_icon_name("copy", tone="muted")
            button.clicked.connect(self._copy_to_clipboard)
            self.title_row.addWidget(button, 0)
            self._copy_button = button
        return self._copy_button

    def _copy_to_clipboard(self) -> None:
        if not self._copy_text:
            return
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._copy_text)
        self._flash_copied()

    def _flash_copied(self) -> None:
        """复制反馈：图标换成对勾、tooltip 改「已复制」，1.2 秒后复原。"""
        button = self._copy_button
        if button is None:
            return
        button.set_icon_name("check", tone="success")
        button.setToolTip("已复制")
        if self._copy_timer is None:
            self._copy_timer = QTimer(self)
            self._copy_timer.setSingleShot(True)
            self._copy_timer.timeout.connect(self._reset_copy_button)
        self._copy_timer.start(1200)

    def _reset_copy_button(self) -> None:
        if self._copy_button is None or not self._copy_text:
            return
        self._copy_button.set_icon_name("copy", tone="muted")
        self._copy_button.setToolTip(f"复制 {self._copy_text}")

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
        if self.metrics_label.text() != text:
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
        self.expand_button.set_icon_name("chevron-up" if expanded else "chevron-down", tone="muted")
        self.expand_button.setToolTip(self._collapse_hint if expanded else self._expand_hint)
        self.expand_button.setAccessibleName(self._collapse_hint if expanded else self._expand_hint)


class RowDetailMixin:
    """可展开行卡列表的展开态 mixin：同一时间只展开一行，详情控件在行内复用。

    页面把「列表 + 一个复用的详情控件」注册到 ``slot`` 下（同一窗口有多份列表时
    各用一个 slot 区分），展开 / 收起、把详情挂进 ``row.detail_layout``、重算行高
    都由这里处理。详情内容由页面覆盖 :meth:`render_row_detail` 在展开时刷新。

    行卡骨架来自 :class:`ItemRow`；本 mixin 只碰 ``is_expanded`` / ``set_expanded``
    / ``detail_layout`` 与列表的 ``itemWidget`` / ``item`` / ``sync_row_widths``，
    不假设行内的业务控件。
    """

    _row_details: dict[str, tuple[RowListWidget, QWidget]]
    _expanded_rows: dict[str, ItemRow | None]

    def bind_row_detail(self, slot: str, rows: RowListWidget, detail: QWidget) -> None:
        """注册一组「行列表 + 复用详情控件」；同一 slot 可重复注册（列表重建后）。"""
        if not hasattr(self, "_row_details"):
            self._row_details = {}
            self._expanded_rows = {}
        self._row_details[slot] = (rows, detail)
        self._expanded_rows.setdefault(slot, None)

    def expanded_row(self, slot: str) -> ItemRow | None:
        """当前展开的行；没有展开行时返回 ``None``。"""
        return self._expanded_rows.get(slot)

    def render_row_detail(self, row: ItemRow) -> None:
        """展开时刷新详情内容；默认无操作，页面按需覆盖。"""

    def toggle_row(self, slot: str, row: ItemRow) -> None:
        """展开 / 收起一行，并把该行设为当前行。"""
        self.focus_row(slot, row)
        if row.is_expanded():
            row.set_expanded(False)
            self._row_details[slot][1].setVisible(False)
            self._expanded_rows[slot] = None
            self.update_row_height(slot, row)
            return
        self.expand_row(slot, row)

    def expand_row(self, slot: str, row: ItemRow) -> None:
        """展开指定行：先收起上一行，再挂详情并重算行高。"""
        detail = self._row_details[slot][1]
        current = self._expanded_rows.get(slot)
        if current is not None and current is not row:
            current.set_expanded(False)
            self.update_row_height(slot, current)
        row.detail_layout.addWidget(detail)
        detail.setVisible(True)
        row.set_expanded(True)
        self._expanded_rows[slot] = row
        self.render_row_detail(row)
        self.update_row_height(slot, row)

    def focus_row(self, slot: str, row: ItemRow) -> None:
        """把行设为列表当前行（用于双击 / 行内动作前的聚焦）。"""
        rows, _detail = self._row_details[slot]
        for index in range(rows.count()):
            if rows.itemWidget(rows.item(index)) is row:
                rows.setCurrentRow(index)
                return

    def detach_row_detail(self, slot: str) -> None:
        """列表重建前把复用的详情控件从旧行摘下来，避免随旧行一起销毁。"""
        _rows, detail = self._row_details[slot]
        detail.setParent(None)
        detail.setVisible(False)
        current = self._expanded_rows.get(slot)
        if current is not None:
            current.set_expanded(False)
        self._expanded_rows[slot] = None

    def refresh_expanded_height(self, slot: str) -> None:
        """详情内容变化后重算当前展开行的高度。"""
        row = self._expanded_rows.get(slot)
        if row is not None:
            self.update_row_height(slot, row)

    def update_row_height(self, slot: str, row: ItemRow | None) -> None:
        """按行卡当前 sizeHint 更新对应列表项的尺寸提示。"""
        if row is None:
            return
        rows, _detail = self._row_details[slot]
        layout = row.layout()
        if layout is not None:
            layout.activate()
        for index in range(rows.count()):
            item = rows.item(index)
            if rows.itemWidget(item) is row:
                item.setSizeHint(QSize(0, row.sizeHint().height()))
                rows.sync_row_widths()
                return
