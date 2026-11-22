"""llmw「工作流」页：节点链编辑、模板、保存与后台执行。

工作流模型（DAG / 端口类型 / 校验）在 :mod:`fcmd.apps.llm._llmw_flow`，本模块只负责
Qt 表单与执行进度。第一阶段只提供线性链编辑：节点按顺序自动连线；分支 / 画布在
后续阶段接入同一份 ``FlowGraph``。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm._llmw_flow import (
    ExecutionResult,
    FlowError,
    FlowGraph,
    FlowNode,
    NodeDefinition,
    NodeEvent,
    ParamSpec,
    builtin_templates,
    default_output_dir,
    default_registry,
    delete_flow,
    execute,
    list_flows,
    save_flow,
)
from fcmd.apps.llm._llmw_flow_nodes import IMAGE_SAVE, IMAGE_TXT2IMG, TEXT_INPUT
from fcmd.gui.icons import set_icon
from fcmd.gui.page import AppPage
from fcmd.gui.widgets import (
    Card,
    PageScroll,
    PathField,
    button,
    confirm,
    hint_label,
    icon_button,
    make_splitter,
)
from fcmd.gui.workers import submit_daemon

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["WorkflowPage"]


class _FlowBridge(QObject):  # pragma: no cover - Qt 信号桥
    """工作线程 → GUI 线程的执行事件。"""

    event = Signal(object)


class _NodeCard(Card):  # pragma: no cover - Qt 控件
    """单个节点卡片：标题行动作 + 按 ParamSpec 生成的参数表单。"""

    def __init__(  # noqa: PLR0913 - 节点卡片参数成组出现
        self,
        definition: NodeDefinition,
        node: FlowNode,
        *,
        on_change: Callable[[], None],
        on_move: Callable[[int], None],
        on_delete: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(definition.label, parent)
        self.definition = definition
        self.node = node
        self._on_change = on_change
        self._getters: list[tuple[ParamSpec, Callable[[], Any]]] = []
        self.setToolTip(definition.description)

        up = icon_button("", tooltip="上移")
        set_icon(up, "chevron-up", tone="muted")
        up.clicked.connect(lambda: on_move(-1))
        down = icon_button("", tooltip="下移")
        set_icon(down, "chevron-down", tone="muted")
        down.clicked.connect(lambda: on_move(1))
        remove = icon_button("", tooltip="删除节点")
        set_icon(remove, "trash", tone="danger")
        remove.clicked.connect(on_delete)
        self.add_title_action(up)
        self.add_title_action(down)
        self.add_title_action(remove)
        self.body.addWidget(hint_label(definition.description))
        for spec in definition.params:
            self.body.addLayout(self._build_param_row(spec, node.params.get(spec.name, spec.default)))

    def _build_param_row(self, spec: ParamSpec, value: Any) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(spec.label, self)
        label.setMinimumWidth(84)
        row.addWidget(label)
        widget, getter, connect = _param_widget(spec, value, self)
        row.addWidget(widget, 1)
        connect(self._on_change)
        self._getters.append((spec, getter))
        if spec.hint:
            row.addWidget(hint_label(spec.hint, wrap=False))
        return row

    def values(self) -> dict[str, Any]:
        """读取当前表单值（写回节点参数前调用）。"""
        return {spec.name: getter() for spec, getter in self._getters}


def _param_widget(
    spec: ParamSpec,
    value: Any,
    parent: QWidget,
) -> tuple[QWidget, Callable[[], Any], Callable[[Callable[[], None]], None]]:
    """按 ParamSpec 创建参数控件，返回 ``(控件, 取值器, 变更信号连接器)``。"""
    if spec.kind == "bool":
        check = QCheckBox(parent)
        check.setChecked(bool(value))
        return check, check.isChecked, lambda callback: check.toggled.connect(lambda _checked: callback())
    if spec.kind == "int":
        spin = QSpinBox(parent)
        spin.setRange(spec.minimum, spec.maximum)
        spin.setValue(int(value or 0))
        return spin, spin.value, lambda callback: spin.valueChanged.connect(lambda _value: callback())
    if spec.kind == "float":
        spin = QDoubleSpinBox(parent)
        spin.setDecimals(2)
        spin.setRange(float(spec.minimum), float(spec.maximum))
        spin.setValue(float(value or 0))
        return spin, spin.value, lambda callback: spin.valueChanged.connect(lambda _value: callback())
    if spec.kind == "choice":
        combo = QComboBox(parent)
        combo.addItems(list(spec.choices))
        index = combo.findText(str(value))
        combo.setCurrentIndex(max(index, 0))
        return combo, combo.currentText, lambda callback: combo.currentTextChanged.connect(lambda _text: callback())
    if spec.kind == "path":
        field = PathField(value=str(value or ""), directory=spec.directory, parent=parent)
        return field, field.value, lambda callback: field.edit.textChanged.connect(lambda _text: callback())
    edit = QLineEdit(str(value or ""), parent)
    return edit, edit.text, lambda callback: edit.textChanged.connect(lambda _text: callback())


class WorkflowPage(AppPage):  # pragma: no cover - Qt 控件
    """工作流页：左侧工作流列表 + 右侧节点链 / 结果。"""

    PAGE_KEY = "flow"

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent, spacing=8)
        self._registry = default_registry()
        self._flows: list[FlowGraph] = []
        self._current: FlowGraph | None = None
        self._cards: list[_NodeCard] = []
        self._cache: dict[str, dict[str, Any]] = {}
        self._cancel: threading.Event | None = None
        self._bridge: _FlowBridge | None = None
        self._build()
        self._load_flows()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        self._new_button = button("新建", kind="ghost", tooltip="新建空白工作流")
        self._new_button.clicked.connect(self._new_flow)
        self._template_button = button("从模板新建…", kind="ghost")
        self._template_button.clicked.connect(self._show_templates)
        self._save_button = button("保存", kind="ghost", tooltip="保存当前工作流到 ${FCMD_HOME}/flows")
        self._save_button.clicked.connect(self._save_current)
        self._run_button = button("运行", kind="primary", tooltip="按节点顺序执行工作流")
        self._run_button.clicked.connect(self._run)
        self._stop_button = button("停止", kind="danger")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._stop)
        for widget in (self._new_button, self._template_button, self._save_button):
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        toolbar.addWidget(self._run_button)
        toolbar.addWidget(self._stop_button)
        self.body.addLayout(toolbar)

        splitter = make_splitter(Qt.Orientation.Horizontal, sizes=(280, 820), parent=self)
        splitter.addWidget(self._build_list_card())
        splitter.addWidget(self._build_editor())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

    def _build_list_card(self) -> Card:
        card = Card("工作流", self)
        card.setMinimumWidth(240)
        self._flow_list = QListWidget(card)
        self._flow_list.setMinimumHeight(220)
        self._flow_list.currentItemChanged.connect(self._on_flow_selected)
        card.body.addWidget(self._flow_list, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        remove = button("删除", kind="danger")
        remove.clicked.connect(self._delete_current)
        row.addWidget(remove)
        row.addStretch(1)
        card.body.addLayout(row)
        card.body.addWidget(
            hint_label(
                "工作流保存在 ${FCMD_HOME}/flows；图像节点可按运行页图像配置自动启动，LLM 节点需先启动对话模型。"
            )
        )
        return card

    def _build_editor(self) -> PageScroll:
        editor = PageScroll(parent=self)
        self._name_edit = QLineEdit(editor)
        self._name_edit.setPlaceholderText("工作流名称")
        self._name_edit.textChanged.connect(lambda _text: self._mark_dirty())
        name_card = Card("名称", editor)
        name_card.body.addWidget(self._name_edit)
        editor.body.addWidget(name_card)

        node_card = Card("节点链", editor)
        self._node_host = QWidget(node_card)
        self._node_layout = QVBoxLayout(self._node_host)
        self._node_layout.setContentsMargins(0, 0, 0, 0)
        self._node_layout.setSpacing(8)
        node_card.body.addWidget(self._node_host)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addStretch(1)
        self._add_button = button("添加节点…", kind="ghost")
        self._add_button.clicked.connect(self._show_add_menu)
        action_row.addWidget(self._add_button)
        node_card.body.addLayout(action_row)
        editor.body.addWidget(node_card)

        result_card = Card("运行结果", editor)
        self._result_list = QListWidget(result_card)
        self._result_list.setMinimumHeight(100)
        self._result_list.itemDoubleClicked.connect(self._open_result)
        result_card.body.addWidget(self._result_list)
        result_row = QHBoxLayout()
        result_row.setContentsMargins(0, 0, 0, 0)
        result_row.addStretch(1)
        open_button = button("打开输出目录", kind="ghost")
        open_button.clicked.connect(self._open_output_dir)
        result_row.addWidget(open_button)
        result_card.body.addLayout(result_row)
        editor.body.addWidget(result_card)
        editor.body.addStretch(1)
        return editor

    # ------------------------------------------------------------------ #
    # 工作流列表 / 持久化
    # ------------------------------------------------------------------ #
    def _load_flows(self) -> None:
        flows = list_flows()
        if not flows:
            flows = builtin_templates()
            for flow in flows:
                try:
                    save_flow(flow)
                except (FlowError, OSError):
                    continue
        self._flows = flows
        self._refresh_list()
        if self._flows:
            self._select_flow(self._flows[0].id)

    def _refresh_list(self, *, select_id: str = "") -> None:
        self._flow_list.blockSignals(True)
        self._flow_list.clear()
        current_id = select_id or (self._current.id if self._current is not None else "")
        selected_row = 0
        for row, flow in enumerate(self._flows):
            item = QListWidgetItem(flow.name)
            item.setData(Qt.ItemDataRole.UserRole, flow.id)
            item.setToolTip(flow.description)
            self._flow_list.addItem(item)
            if flow.id == current_id:
                selected_row = row
        self._flow_list.blockSignals(False)
        if self._flows:
            self._flow_list.setCurrentRow(selected_row)

    def _on_flow_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        flow_id = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(flow_id, str):
            self._select_flow(flow_id)

    def _select_flow(self, flow_id: str) -> None:
        flow = next((item for item in self._flows if item.id == flow_id), None)
        if flow is None:
            return
        self._current = flow
        self._name_edit.blockSignals(True)
        self._name_edit.setText(flow.name)
        self._name_edit.blockSignals(False)
        self._rebuild_cards()

    def _new_flow(self) -> None:
        flow = FlowGraph.from_steps(
            self._unique_name("未命名工作流"),
            [
                (TEXT_INPUT, {"text": ""}),
                (IMAGE_TXT2IMG, {}),
                (IMAGE_SAVE, {"directory": str(default_output_dir())}),
            ],
        )
        self._install_flow(flow)

    def _install_flow(self, flow: FlowGraph) -> None:
        try:
            flow.rechain(self._registry)
            flow.validate(self._registry)
            save_flow(flow)
        except (FlowError, OSError) as exc:
            self.status(str(exc))
            return
        self._remember_flow(flow)
        self._select_flow(flow.id)
        self.status(f"已创建工作流：{flow.name}")

    def _remember_flow(self, flow: FlowGraph) -> None:
        """把保存后的工作流写回左侧列表并保持选中。"""
        self._flows = [item for item in self._flows if item.id != flow.id] + [flow]
        self._flows.sort(key=lambda item: item.name.casefold())
        self._current = flow
        self._refresh_list(select_id=flow.id)

    def _show_templates(self) -> None:
        menu = QMenu(self)
        for template in builtin_templates():
            action = menu.addAction(template.name)
            action.triggered.connect(lambda _checked=False, flow=template: self._install_flow(self._renamed(flow)))
        menu.exec(self._template_button.mapToGlobal(self._template_button.rect().bottomLeft()))

    def _renamed(self, flow: FlowGraph) -> FlowGraph:
        flow.name = self._unique_name(flow.name)
        return flow

    def _unique_name(self, base: str) -> str:
        names = {flow.name for flow in self._flows}
        if base not in names:
            return base
        index = 2
        while f"{base} {index}" in names:
            index += 1
        return f"{base} {index}"

    def _save_current(self) -> None:
        flow = self._sync_flow()
        if flow is None:
            return
        try:
            save_flow(flow)
        except (FlowError, OSError) as exc:
            self.status(f"保存失败：{exc}")
            return
        self._remember_flow(flow)
        self.status(f"已保存工作流：{flow.name}")

    def _delete_current(self) -> None:
        if self._current is None:
            return
        if not confirm(self, "删除工作流", f"删除「{self._current.name}」？此操作不可撤销。"):
            return
        flow_id = self._current.id
        delete_flow(flow_id)
        self._flows = [item for item in self._flows if item.id != flow_id]
        self._current = None
        self._refresh_list()
        if self._flows:
            self._select_flow(self._flows[0].id)
        else:
            self._clear_cards()
            self.status("已删除全部工作流")

    def _sync_flow(self) -> FlowGraph | None:
        if self._current is None:
            return None
        name = self._name_edit.text().strip() or "未命名工作流"
        nodes = [card.node for card in self._cards]
        for card in self._cards:
            card.node.params.update(card.values())
        flow = FlowGraph(
            id=self._current.id,
            name=name,
            description=self._current.description,
            nodes=nodes,
        )
        try:
            flow.rechain(self._registry)
            flow.validate(self._registry)
        except FlowError as exc:
            self.status(str(exc))
            return None
        self._current = flow
        return flow

    # ------------------------------------------------------------------ #
    # 节点编辑
    # ------------------------------------------------------------------ #
    def _rebuild_cards(self) -> None:
        self._clear_cards()
        if self._current is None:
            return
        for node in self._current.nodes:
            definition = self._registry.get(node.type)
            if definition is None:
                continue
            card = _NodeCard(
                definition,
                node,
                on_change=self._mark_dirty,
                on_move=lambda delta, node_id=node.id: self._move_node(node_id, delta),
                on_delete=lambda node_id=node.id: self._delete_node(node_id),
                parent=self._node_host,
            )
            self._cards.append(card)
            self._node_layout.addWidget(card)
        self._sync_node_buttons()

    def _clear_cards(self) -> None:
        for card in self._cards:
            self._node_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._sync_node_buttons()

    def _show_add_menu(self) -> None:
        menu = QMenu(self)
        for category, definitions in self._registry.by_category().items():
            submenu = menu.addMenu(category)
            for definition in definitions:
                action = submenu.addAction(definition.label)
                action.setToolTip(definition.description)
                action.triggered.connect(lambda _checked=False, node_type=definition.type: self._add_node(node_type))
        menu.exec(self._add_button.mapToGlobal(self._add_button.rect().bottomLeft()))

    def _add_node(self, node_type: str) -> None:
        """在能保持线性链合法的位置插入节点（追加失败时向前找）。"""
        if self._current is None:
            return
        definition = self._registry.get(node_type)
        if definition is None:
            return
        node = FlowNode(f"n{len(self._current.nodes) + 1}", node_type, self._default_params(definition))
        for index in range(len(self._current.nodes), -1, -1):
            nodes = list(self._current.nodes)
            nodes.insert(index, node)
            candidate = FlowGraph(
                id=self._current.id,
                name=self._current.name,
                description=self._current.description,
                nodes=nodes,
            )
            try:
                candidate.rechain(self._registry)
                candidate.validate(self._registry)
            except FlowError:
                continue
            self._current = candidate
            self._rebuild_cards()
            self._mark_dirty()
            return
        self.status(f"无法添加「{definition.label}」：与当前节点链的端口不兼容")

    def _default_params(self, definition: NodeDefinition) -> dict[str, Any]:
        return {spec.name: spec.default for spec in definition.params}

    def _delete_node(self, node_id: str) -> None:
        if self._current is None:
            return
        self._current.nodes = [node for node in self._current.nodes if node.id != node_id]
        self._rebuild_cards()
        self._mark_dirty()

    def _move_node(self, node_id: str, delta: int) -> None:
        if self._current is None:
            return
        nodes = self._current.nodes
        index = next((i for i, node in enumerate(nodes) if node.id == node_id), -1)
        target = index + delta
        if index < 0 or not 0 <= target < len(nodes):
            return
        nodes[index], nodes[target] = nodes[target], nodes[index]
        self._rebuild_cards()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._sync_node_buttons()

    def _sync_node_buttons(self) -> None:
        self._run_button.setEnabled(bool(self._cards) and self._cancel is None)
        self._save_button.setEnabled(bool(self._cards))
        self._add_button.setEnabled(self._current is not None)

    # ------------------------------------------------------------------ #
    # 运行
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        flow = self._sync_flow()
        if flow is None:
            return
        try:
            save_flow(flow)
        except (FlowError, OSError) as exc:
            self.status(f"保存失败：{exc}")
            return
        self._remember_flow(flow)
        self._result_list.clear()
        self._cancel = threading.Event()
        self._bridge = _FlowBridge(self)
        self._bridge.event.connect(self._on_event)
        self._run_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        self.status(f"正在执行：{flow.name}")
        self.state.log(f"工作流开始：{flow.name}")
        submit_daemon(
            execute,
            flow,
            cache=self._cache,
            cancel=self._cancel,
            on_event=self._bridge.event.emit,
            on_done=self._on_done,
            on_error=self._on_error,
            on_finished=self._on_finished,
        )

    def _stop(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self._stop_button.setEnabled(False)
            self.status("正在取消工作流…")

    def _on_event(self, event: object) -> None:
        if not isinstance(event, NodeEvent):
            return
        if event.kind == "start":
            self.status(f"正在执行：{event.label}")
        elif event.kind == "cached":
            self.status(f"{event.label}：命中缓存")
        elif event.kind == "log":
            self.state.log(event.message)

    def _on_done(self, result: object) -> None:
        if not isinstance(result, ExecutionResult):
            return
        for path in result.files:
            self._result_list.addItem(path)
        summary = f"工作流完成：{len(result.files)} 个输出，{result.duration:.1f}s"
        self.status(summary)
        self.state.log(summary)
        for line in result.log:
            self.state.log(f"  {line}")

    def _on_error(self, message: str) -> None:
        self.status(f"工作流失败：{message}")
        self.state.log(f"工作流失败：{message}")

    def _on_finished(self) -> None:
        self._cancel = None
        self._bridge = None
        self._stop_button.setEnabled(False)
        self._sync_node_buttons()

    def _open_output_dir(self) -> None:
        for index in range(self._result_list.count()):
            item = self._result_list.item(index)
            if item is not None:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(item.text()).parent)))
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(default_output_dir())))

    def _open_result(self, item: QListWidgetItem) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(item.text()))
