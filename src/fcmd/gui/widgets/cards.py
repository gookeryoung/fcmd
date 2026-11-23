"""卡片与容器：卡片、滚动页、折叠区、互斥选项卡片与对话框适配。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.widgets.buttons import ButtonStateMixin
from fcmd.gui.widgets.log import LogView
from fcmd.gui.widgets.text import ToneLabelMixin

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "Card",
    "ChoiceCard",
    "ChoiceGroup",
    "Collapsible",
    "PageScroll",
    "fit_dialog_to_content",
]


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


class ChoiceCard(ButtonStateMixin, ToneLabelMixin, QPushButton):  # pragma: no cover - Qt 控件
    """可选项方块：标题 + 下方说明，整块可点击。"""

    def __init__(self, title: str, description: str, *, tone: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChoiceCard")
        if tone:
            self.set_tone(tone)
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

    def set_option_enabled(self, key: str, enabled: bool, tooltip: str = "") -> None:
        """单独禁用某项（如当前平台不支持的引擎），并补充说明。"""
        card = self._buttons.get(key)
        if card is None:
            return
        card.setEnabled(enabled)
        if tooltip:
            card.setToolTip(tooltip)

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
