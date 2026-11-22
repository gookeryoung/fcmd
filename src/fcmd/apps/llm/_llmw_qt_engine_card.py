"""可配置的已安装引擎卡片：llama.cpp 与 sd.cpp 共用同一套布局与交互。

两张卡片此前各写一份「标题行刷新 / 目录行 / 紧凑引擎行 / 空态 / 列表伸缩」，
字段名和尺寸逐渐漂移。这里把结构与交互收进 :class:`EngineCard`，各引擎只提供
:class:`EngineCardConfig`：

- ``load``：读取已安装引擎列表，归一化为 :class:`EngineOption`；
- ``use``：设为默认；``install`` / ``manage``：各自打开安装或管理对话框；
- ``root_widget`` / ``refresh_root``：目录行控件（推理引擎是可编辑 ``PathRow``，
  sd.cpp 是只读路径）由配置提供；
- ``row_badge`` 已包含在 ``EngineOption`` 里（CUDA/CPU 或后端胶囊）。

卡片暴露 ``engines_list`` / ``engines_empty`` / ``refresh_button`` / ``install_button`` /
``root_label`` / ``open_button`` 等字段，旧调用方与测试无需改字段名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QWidget,
)

from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    RowListWidget,
    SelectionRow,
    StatusTag,
    button,
    hint_label,
    link_button,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["EngineCard", "EngineCardConfig", "EngineDirAction", "EngineOption", "EngineOptionRow"]


@dataclass(frozen=True, slots=True)
class EngineOption:
    """一张引擎行所需的归一化数据（llama.cpp / sd.cpp 共用）。"""

    tag: str
    exe: Path
    current: bool
    display_name: str
    badge: str
    badge_tone: str = "muted"
    badge_tooltip: str = ""
    tooltip: str = ""


@dataclass(frozen=True, slots=True)
class EngineDirAction:
    """目录行右侧的额外动作按钮（如「导入压缩包」）。"""

    text: str
    tooltip: str
    callback: Callable[[], None]
    kind: str = "ghost"


@dataclass(frozen=True, slots=True)
class EngineCardConfig:
    """一张引擎卡片的差异配置；布局与列表行为由 :class:`EngineCard` 统一。"""

    title: str
    list_object_name: str
    empty_text: str
    load: Callable[[], list[EngineOption]]
    use: Callable[[str], None]
    install: Callable[[EngineCard], None]
    install_text: str
    install_tooltip: str
    root_text: Callable[[], str]
    open_root: Callable[[], None] | None = None
    open_text: str = "打开"
    open_tooltip: str = "在文件管理器中打开引擎目录"
    row_use_tooltip: str = "把该引擎设为默认引擎"
    row_current_tooltip: str = ""
    manage: Callable[[EngineCard], None] | None = None
    manage_text: str = "管理…"
    manage_tooltip: str = "备份 / 删除引擎，清理下载缓存与源码"
    header_widgets: Sequence[QWidget] = field(default=())
    extra_actions: Sequence[EngineDirAction] = field(default=())
    root_widget: Callable[[QWidget], QWidget] | None = None
    refresh_root: Callable[[QWidget], None] | None = None


class EngineOptionRow(SelectionRow, QFrame):  # pragma: no cover - Qt 控件
    """单行紧凑引擎行：名称 + 可执行文件 + 默认 / 设为默认 + 类别胶囊。"""

    useRequested = Signal(str)

    def __init__(
        self,
        option: EngineOption,
        *,
        use_tooltip: str = "",
        current_tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.option = option
        # 兼容旧测试 / 调用方字段名（sd.cpp 行曾叫 engine）。
        self.engine = option
        self.info = option
        self.setObjectName("EngineRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("selected", "false")
        self.setToolTip(option.tooltip or f"{option.tag}\n{option.exe}")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(8)

        name = QLabel(option.display_name, self)
        name.setObjectName("EngineItemName")
        name.setMaximumWidth(220)
        name.setToolTip(f"引擎 tag：{option.tag}")
        row.addWidget(name, 0)

        exe = ElidedLabel(str(option.exe), mode=Qt.TextElideMode.ElideMiddle, parent=self)
        exe.setObjectName("Hint")
        row.addWidget(exe, 1)

        self.use_button = None
        if option.current:
            mark = StatusTag("默认", tone="success", parent=self)
            mark.setToolTip(current_tooltip or "当前默认引擎")
            row.addWidget(mark, 0)
        else:
            self.use_button = button("设为默认")
            self.use_button.setToolTip(use_tooltip or "把该引擎设为默认引擎")
            self.use_button.clicked.connect(lambda: self.useRequested.emit(option.tag))
            row.addWidget(self.use_button, 0)

        badge = StatusTag(option.badge, tone=option.badge_tone, parent=self)
        badge.setToolTip(option.badge_tooltip or option.badge)
        row.addWidget(badge, 0)
        self.setMinimumHeight(self.fontMetrics().height() + 14)


class EngineCard(Card):  # pragma: no cover - Qt 控件
    """通用引擎卡片：按 :class:`EngineCardConfig` 渲染 llama.cpp / sd.cpp。"""

    changed = Signal()

    def __init__(self, config: EngineCardConfig, parent: QWidget | None = None) -> None:
        super().__init__(config.title, parent)
        self.config = config
        self._engines: list[EngineOption] = []

        for widget in config.header_widgets:
            self.add_title_action(widget)
        self.manage_button = None
        if config.manage is not None:
            self.manage_button = link_button(config.manage_text, tooltip=config.manage_tooltip)
            self.manage_button.clicked.connect(lambda: config.manage(self) if config.manage else None)
            self.add_title_action(self.manage_button)
        self.refresh_button = link_button("刷新", tooltip=f"重新读取已安装 {config.title} 与默认引擎")
        self.refresh_button.clicked.connect(self.refresh)
        self.add_title_action(self.refresh_button)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(hint_label("引擎目录", wrap=False))
        if config.root_widget is not None:
            self.root_widget = config.root_widget(self)
        else:
            self.root_widget = ElidedLabel(config.root_text(), parent=self)
            self.root_widget.setObjectName("Hint")
        row.addWidget(self.root_widget, 1)
        # root_label 兼容旧的只读路径字段名。
        self.root_label = self.root_widget
        self.open_button = None
        if config.open_root is not None:
            self.open_button = button(config.open_text, kind="ghost")
            self.open_button.setToolTip(config.open_tooltip)
            self.open_button.clicked.connect(config.open_root)
            row.addWidget(self.open_button)
        self.install_button = button(config.install_text, kind="ghost")
        self.install_button.setToolTip(config.install_tooltip)
        self.install_button.clicked.connect(lambda: config.install(self))
        row.addWidget(self.install_button)
        self.extra_buttons: list[Any] = []
        for action in config.extra_actions:
            widget = button(action.text, kind=action.kind)
            widget.setToolTip(action.tooltip)
            widget.clicked.connect(action.callback)
            row.addWidget(widget)
            self.extra_buttons.append(widget)
        self.body.addLayout(row)

        self.engines_list = RowListWidget(self)
        self.engines_list.setObjectName(config.list_object_name)
        self.engines_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self.engines_list.setSpacing(4)
        self.engines_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.engines_list.setToolTip("双击一行可直接设为默认引擎")
        self.body.addWidget(self.engines_list)
        self.engines_empty = hint_label(config.empty_text)
        self.engines_empty.setVisible(False)
        self.body.addWidget(self.engines_empty)
        self.engine_hint = hint_label("")
        self.engine_hint.setVisible(False)
        self.body.addWidget(self.engine_hint)
        self.engines_list.currentRowChanged.connect(self._on_row_selected)
        self.engines_list.itemDoubleClicked.connect(self._on_double_clicked)
        self.refresh()

    # ------------------------------------------------------------------ #
    # 列表
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """重新读取引擎列表并刷新目录行。"""
        try:
            self._engines = list(self.config.load())
        except OSError as exc:
            self._engines = []
            self.engines_list.clear()
            self.engines_list.setVisible(False)
            self.engines_empty.setText(f"引擎信息不可用：{exc}")
            self.engines_empty.setVisible(True)
            return
        if self.config.refresh_root is not None:
            self.config.refresh_root(self.root_widget)
        self.engines_list.clear()
        for option in self._engines:
            list_item = QListWidgetItem(self.engines_list)
            list_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            row = EngineOptionRow(
                option,
                use_tooltip=self.config.row_use_tooltip,
                current_tooltip=self.config.row_current_tooltip,
                parent=self.engines_list,
            )
            row.useRequested.connect(self._use)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.engines_list.setItemWidget(list_item, row)
        self.engines_list.sync_row_widths()
        self.engines_list.setVisible(bool(self._engines))
        self._fit_engines_list()
        self.engines_empty.setText(self.config.empty_text)
        self.engines_empty.setVisible(not self._engines)

    # 兼容旧名：sd.cpp 卡曾用 _fit_list。
    def _fit_engines_list(self) -> None:
        """把列表高度收到内容高度，超过 6 行后再出现滚动条。"""
        count = self.engines_list.count()
        if count == 0:
            self.engines_list.setFixedHeight(0)
            return
        heights = [self.engines_list.sizeHintForRow(index) for index in range(count)]
        spacing = self.engines_list.spacing()
        content = sum(heights) + spacing * (count + 2) + 2
        cap = max(heights) * 6 + spacing * 8 + 2
        self.engines_list.setFixedHeight(min(content, cap))

    _fit_list = _fit_engines_list

    def _on_row_selected(self, current: int) -> None:
        """同步行选中态（只重抛光旧 / 新两行）。"""
        self.engines_list.sync_selection(current)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        """双击引擎行直接设为默认。"""
        index = self.engines_list.row(item)
        if 0 <= index < len(self._engines):
            self._use(self._engines[index].tag)

    def _use(self, tag: str) -> None:
        """调用配置的设为默认回调；失败由回调负责提示。"""
        try:
            self.config.use(tag)
        except (OSError, RuntimeError) as exc:  # 配置回调抛 ServeError 时给出提示
            QMessageBox.critical(self, "切换引擎失败", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def install(self) -> None:
        """打开配置提供的安装对话框。"""
        self.config.install(self)
