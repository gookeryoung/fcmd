"""llmw「工作流」页：极简配置入口 + 智能推荐 + 节点链编辑与后台执行。

工作流模型（DAG / 端口类型 / 校验）在 :mod:`fcmd.apps.llm._llmw_flow`，推荐口径在
:mod:`fcmd.apps.llm._llmw_flow_recommend`，本模块只负责 Qt 表单与执行进度。

页面结构与对话页一致：左侧工作流列表（新建 / 删除），右侧是「配置 → 推荐 → 节点链 →
运行结果」四段。配置只保留名称与任务属性，场景由节点链自动判定；节点参数分「常用」
与「更多参数」两层，默认只露出会直接影响结果的一两项，避免首屏被参数淹没。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
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

from fcmd.apps.llm import _llmpaint
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
from fcmd.apps.llm._llmw_flow_nodes import IMAGE_SAVE, IMAGE_TXT2IMG, LLM_CHAT, TEXT_INPUT
from fcmd.apps.llm._llmw_flow_recommend import (
    ATTRIBUTES,
    SCENES,
    FlowRecommendation,
    detect_attribute,
    detect_scene,
    recommend_flows,
)
from fcmd.apps.llm._llmw_qt_paint import model_roots, parse_paint_settings
from fcmd.gui.icons import set_icon
from fcmd.gui.page import AppPage
from fcmd.gui.widgets import (
    Card,
    Collapsible,
    ElidedLabel,
    FormGrid,
    PageScroll,
    PathField,
    SidebarActionButton,
    SidebarEntry,
    SidebarList,
    StatusTag,
    ToggleChip,
    button,
    confirm,
    hint_label,
    icon_button,
    make_splitter,
    value_label,
)
from fcmd.gui.workers import submit, submit_daemon

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["WorkflowPage"]

#: 节点在首屏只展开这些参数，其余收进「更多参数」。
_NODE_ESSENTIAL: dict[str, tuple[str, ...]] = {
    TEXT_INPUT: ("text",),
    LLM_CHAT: ("model", "temperature"),
    IMAGE_TXT2IMG: ("width", "height", "steps"),
    IMAGE_SAVE: ("directory",),
}


class _FlowBridge(QObject):  # pragma: no cover - Qt 信号桥
    """工作线程 → GUI 线程的执行事件。"""

    event = Signal(object)


class _NodeCard(Card):  # pragma: no cover - Qt 控件
    """单个节点卡片：标题行动作 + 常用参数 + 可折叠的其余参数。"""

    def __init__(  # noqa: PLR0913 - 节点卡片参数成组出现
        self,
        definition: NodeDefinition,
        node: FlowNode,
        *,
        index: int,
        on_change: Callable[[], None],
        on_move: Callable[[int], None],
        on_delete: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(f"{index + 1}. {definition.label}", parent)
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

        essential = _NODE_ESSENTIAL.get(definition.type, ())
        for spec in definition.params:
            if spec.name in essential:
                self.body.addLayout(self._build_param_row(spec, node.params.get(spec.name, spec.default)))
        rest = [spec for spec in definition.params if spec.name not in essential]
        if rest:
            more = Collapsible(f"更多参数（{len(rest)}）", parent=self)
            for spec in rest:
                more.body.addLayout(self._build_param_row(spec, node.params.get(spec.name, spec.default)))
            self.body.addWidget(more)

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
    """工作流页：左侧工作流列表 + 右侧配置 / 推荐 / 节点链 / 结果。"""

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
        self._attribute = "balanced"
        self._image_models: list[Path] = []
        self._paint_assets: _llmpaint.PaintAssets | None = None
        self._recommendations: tuple[FlowRecommendation, ...] = ()
        self._build()
        self._start_asset_scan()
        self._load_flows()
        self._connect_state()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """左侧工作流列表 + 右侧配置 / 推荐 / 节点链。"""
        splitter = make_splitter(Qt.Orientation.Horizontal, sizes=(220, 880), parent=self)
        splitter.setObjectName("ChatSplitter")
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.body.addWidget(splitter, 1)

        sidebar = QFrame(splitter)
        sidebar.setObjectName("ChatSidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(360)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(8)
        self._new_button = SidebarActionButton("新建工作流", icon="plus", parent=sidebar)
        self._new_button.setToolTip("新建空白工作流，或从内置模板开始")
        self._new_button.clicked.connect(self._show_new_menu)
        new_row = QHBoxLayout()
        new_row.setContentsMargins(4, 0, 4, 0)
        new_row.setSpacing(0)
        new_row.addWidget(self._new_button)
        side.addLayout(new_row)
        self._flow_list = SidebarList(sidebar, accent_selection=True)
        self._flow_list.setMinimumWidth(0)
        self._flow_list.currentRowChanged.connect(self._on_flow_row_changed)
        side.addWidget(self._flow_list, 1)
        actions = QFrame(sidebar)
        actions.setObjectName("ChatSidebarActions")
        actions.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(2, 2, 2, 2)
        actions_row.setSpacing(2)
        self._delete_button = icon_button("", tooltip="删除当前工作流")
        self._delete_button.setObjectName("ChatIconButton")
        self._delete_button.setFixedSize(30, 30)
        set_icon(self._delete_button, "trash", tone="danger")
        self._delete_button.clicked.connect(self._delete_current)
        actions_row.addStretch(1)
        actions_row.addWidget(self._delete_button)
        side.addWidget(actions)
        splitter.addWidget(sidebar)

        right = QWidget(splitter)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(self._build_toolbar())
        self._editor = self._build_editor()
        layout.addWidget(self._editor, 1)
        splitter.addWidget(right)

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addStretch(1)
        self._save_button = button("保存", kind="ghost", tooltip="保存当前工作流到 ${FCMD_HOME}/flows")
        self._save_button.clicked.connect(self._save_current)
        self._run_button = button("运行", kind="primary", tooltip="按节点顺序执行工作流")
        self._run_button.clicked.connect(self._on_run_clicked)
        toolbar.addWidget(self._save_button)
        toolbar.addWidget(self._run_button)
        return toolbar

    def _build_editor(self) -> PageScroll:
        editor = PageScroll(parent=self)
        editor.body.addWidget(self._build_config_card(editor))
        editor.body.addWidget(self._build_recommend_card(editor))
        editor.body.addWidget(self._build_node_card(editor))
        editor.body.addWidget(self._build_result_card(editor))
        editor.body.addStretch(1)
        return editor

    def _build_config_card(self, parent: QWidget) -> Card:
        card = Card("工作流配置", parent)
        self._scene_tag = StatusTag("文本处理", tone="muted")
        card.add_title_action(self._scene_tag)
        form = FormGrid(card)
        self._name_edit = QLineEdit(card)
        self._name_edit.setPlaceholderText("工作流名称")
        self._name_edit.textChanged.connect(lambda _text: self._mark_dirty())
        form.add_row("名称", self._name_edit)
        chips = QWidget(card)
        chip_row = QHBoxLayout(chips)
        chip_row.setContentsMargins(0, 0, 0, 0)
        chip_row.setSpacing(6)
        self._attr_group = QButtonGroup(self)
        self._attr_group.setExclusive(True)
        self._attr_chips: dict[str, ToggleChip] = {}
        for key, title, description in ATTRIBUTES:
            chip = ToggleChip(title, tooltip=description, parent=chips)
            chip.setChecked(key == self._attribute)
            chip.clicked.connect(lambda _checked=False, key=key: self._set_attribute(key))
            self._attr_group.addButton(chip)
            self._attr_chips[key] = chip
            chip_row.addWidget(chip)
        chip_row.addStretch(1)
        form.add_row("任务属性", chips)
        card.body.addWidget(form)
        card.body.addWidget(hint_label("场景由节点链自动判定；场景与任务属性共同决定推荐方案，应用后写回节点参数。"))
        return card

    def _build_recommend_card(self, parent: QWidget) -> Card:
        card = Card("智能推荐", parent)
        self._rec_tag = StatusTag("等待模型", tone="muted")
        card.add_title_action(self._rec_tag)
        self._rec_model = value_label("")
        self._rec_model.setWordWrap(True)
        card.body.addWidget(self._rec_model)
        self._rec_config = hint_label("")
        card.body.addWidget(self._rec_config)
        self._rec_basis = ElidedLabel("", parent=card)
        self._rec_basis.setObjectName("Hint")
        card.body.addWidget(self._rec_basis)
        apply_row = QHBoxLayout()
        apply_row.setContentsMargins(0, 0, 0, 0)
        apply_row.setSpacing(6)
        apply_row.addStretch(1)
        self._apply_button = button("应用推荐", kind="primary", tooltip="把推荐模型与参数写回当前工作流")
        self._apply_button.clicked.connect(self._apply_recommendation)
        apply_row.addWidget(self._apply_button)
        card.body.addLayout(apply_row)
        self._rec_card = card
        return card

    def _build_node_card(self, parent: QWidget) -> Card:
        card = Card("节点链", parent)
        self._node_host = QWidget(card)
        self._node_layout = QVBoxLayout(self._node_host)
        self._node_layout.setContentsMargins(0, 0, 0, 0)
        self._node_layout.setSpacing(8)
        card.body.addWidget(self._node_host)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.addStretch(1)
        self._add_button = button("添加节点…", kind="ghost")
        self._add_button.clicked.connect(self._show_add_menu)
        action_row.addWidget(self._add_button)
        card.body.addLayout(action_row)
        return card

    def _build_result_card(self, parent: QWidget) -> Card:
        card = Card("运行结果", parent)
        open_button = icon_button("", tooltip="打开输出目录")
        set_icon(open_button, "folder", tone="muted")
        open_button.clicked.connect(self._open_output_dir)
        card.add_title_action(open_button)
        self._result_list = QListWidget(card)
        self._result_list.setMinimumHeight(90)
        self._result_list.itemDoubleClicked.connect(self._open_result)
        card.body.addWidget(self._result_list)
        return card

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
        current_id = select_id or (self._current.id if self._current is not None else "")
        self._flow_list.blockSignals(True)
        self._flow_list.set_entries(
            [
                SidebarEntry(
                    title=flow.name,
                    key=flow.id,
                    subtitle=f"{len(flow.nodes)} 个节点 · {SCENES[detect_scene(flow)]}",
                    tooltip=flow.description or flow.name,
                )
                for flow in self._flows
            ]
        )
        self._flow_list.blockSignals(False)
        if current_id:
            self._flow_list.set_current_key(current_id)
        elif self._flows:
            self._flow_list.setCurrentRow(0)

    def _on_flow_row_changed(self, _row: int) -> None:
        flow_id = self._flow_list.current_key()
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
        self._attribute = detect_attribute(flow)
        self._sync_attribute_chips()
        self._rebuild_cards()
        self._refresh_scene()

    def _show_new_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("空白工作流", self._new_flow)
        menu.addSeparator()
        for template in builtin_templates():
            action = menu.addAction(f"模板：{template.name}")
            action.triggered.connect(lambda _checked=False, flow=template: self._install_flow(self._renamed(flow)))
        menu.exec(self._new_button.mapToGlobal(self._new_button.rect().bottomLeft()))

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
            self._refresh_scene()
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
            self._refresh_recommendation()
            return
        for index, node in enumerate(self._current.nodes):
            definition = self._registry.get(node.type)
            if definition is None:
                continue
            card = _NodeCard(
                definition,
                node,
                index=index,
                on_change=self._mark_dirty,
                on_move=lambda delta, node_id=node.id: self._move_node(node_id, delta),
                on_delete=lambda node_id=node.id: self._delete_node(node_id),
                parent=self._node_host,
            )
            self._cards.append(card)
            self._node_layout.addWidget(card)
        self._sync_node_buttons()
        self._refresh_recommendation()

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
            self._refresh_scene()
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
        self._refresh_scene()
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
        self._refresh_scene()
        self._mark_dirty()

    def _mark_dirty(self) -> None:
        self._sync_node_buttons()

    def _sync_node_buttons(self) -> None:
        running = self._cancel is not None
        has_cards = bool(self._cards)
        self._run_button.setEnabled(has_cards)
        self._run_button.set_kind("danger" if running else "primary")
        self._run_button.set_text("停止" if running else "运行")
        self._run_button.setToolTip("取消当前工作流" if running else "按节点顺序执行工作流")
        self._save_button.setEnabled(has_cards and not running)
        self._add_button.setEnabled(self._current is not None and not running)

    # ------------------------------------------------------------------ #
    # 场景 / 推荐
    # ------------------------------------------------------------------ #
    def _refresh_scene(self) -> None:
        scene = detect_scene(self._current) if self._current is not None else "text"
        self._scene_tag.set_status(SCENES[scene], tone="accent" if scene != "text" else "muted")

    def _sync_attribute_chips(self) -> None:
        for key, chip in self._attr_chips.items():
            chip.setChecked(key == self._attribute)

    def _set_attribute(self, key: str) -> None:
        if key == self._attribute:
            return
        self._attribute = key
        self._refresh_recommendation()

    def _connect_state(self) -> None:
        for name in ("modelsChanged", "hardwareChanged"):
            signal = getattr(self.state, name, None)
            if signal is not None:
                signal.connect(lambda *_args: self._refresh_recommendation())

    def _start_asset_scan(self) -> None:
        """先用画图页当前模型兜底，再后台扫描扩散模型补全推荐候选。"""
        paint = parse_paint_settings(getattr(self.state.settings, "paint", None) or {})
        current = paint.get("model")
        if isinstance(current, str) and current.strip():
            self._image_models = [Path(current).expanduser()]
        try:
            roots = model_roots(paint)
        except Exception:  # pragma: no cover - 配置异常时跳过扫描
            return
        submit(
            _llmpaint.scan_assets,
            roots,
            on_done=self._on_assets_scanned,
            on_error=lambda _message: None,
        )

    def _on_assets_scanned(self, assets: object) -> None:
        if not isinstance(assets, _llmpaint.PaintAssets):
            return
        self._paint_assets = assets
        self._image_models = [asset.path for asset in assets.main_models()]
        try:
            self._refresh_recommendation()
        except RuntimeError:  # pragma: no cover - 页面已销毁时扫描才返回
            return

    def _refresh_recommendation(self) -> None:
        """按当前场景 / 属性 / 参数重算推荐，并把最优组合渲染到卡片。"""
        if self._current is None:
            self._recommendations = ()
            self._render_recommendation()
            return
        self._recommendations = recommend_flows(
            self._current,
            list(getattr(self.state, "models", []) or []),
            getattr(self.state, "hardware", None),
            getattr(self.state, "measured", None),
            self._image_models,
            attribute=self._attribute,
        )
        self._render_recommendation()

    def _render_recommendation(self) -> None:
        best = self._recommendations[0] if self._recommendations else None
        if best is None:
            self._rec_tag.set_status("暂无方案", tone="muted")
            self._rec_model.setText("先准备模型")
            self._rec_config.setText("在「运行」页启动对话模型，或在「模型」页下载画图模型后会自动重新匹配。")
            self._rec_basis.setText("")
            self._apply_button.setEnabled(False)
            return
        tone = {"excellent": "success", "good": "accent", "fair": "warning"}.get(best.score_band, "danger")
        extra = f" · 另有 {len(self._recommendations) - 1} 个备选" if len(self._recommendations) > 1 else ""
        self._rec_tag.set_status(best.score_text, tone=tone)
        self._rec_model.setText(best.model_text)
        self._rec_config.setText(f"{best.config_text}；{best.rationale}{extra}")
        self._rec_basis.setText(best.basis)
        self._rec_basis.setToolTip(best.basis)
        self._apply_button.setEnabled(True)

    def _apply_recommendation(self) -> None:
        """把最优组合写回节点参数；画图模型同步到运行页图像配置。"""
        if self._current is None or not self._recommendations:
            return
        best = self._recommendations[0]
        changed = 0
        for card in self._cards:
            node = card.node
            params = card.values()
            if node.type == LLM_CHAT and best.llm_path is not None:
                params.update(self._llm_params_for(best))
                changed += 1
            elif node.type == IMAGE_TXT2IMG:
                params.update(
                    {
                        "width": best.width,
                        "height": best.height,
                        "steps": best.steps,
                        "cfg_scale": best.cfg_scale,
                    }
                )
                changed += 1
            node.params.update(params)
        applied_image = self._apply_paint_model(best)
        self._rebuild_cards()
        self._refresh_scene()
        self._mark_dirty()
        message = f"已应用推荐：{best.model_text}（{changed} 个节点）"
        if best.image_path is not None and not applied_image:
            message += "；画图模型未写入，请到运行页确认"
        self.status(message)

    def _llm_params_for(self, best: FlowRecommendation) -> dict[str, Any]:
        params: dict[str, Any] = {
            "temperature": best.llm_temperature,
            "max_tokens": best.llm_max_tokens,
            "system": best.llm_system,
        }
        state = self.state.state_for_model(best.llm_path) if best.llm_path is not None else None
        if state is not None:
            params["model"] = state.alias
            params["host"] = state.host
            params["port"] = state.port
        return params

    def _apply_paint_model(self, best: FlowRecommendation) -> bool:
        """把推荐画图模型写入运行页配置；有扫描结果时同时配对组件。"""
        if best.image_path is None:
            return False
        paint = parse_paint_settings(getattr(self.state.settings, "paint", None) or {})
        paint["model"] = str(best.image_path)
        if self._paint_assets is not None:
            suggestion = _llmpaint.suggest_components(self._paint_assets, best.image_path)
            for role in ("vae", "clip_l", "clip_g", "t5xxl", "llm"):
                value = getattr(suggestion, role, None)
                paint[role] = str(value) if value is not None else ""
        self.state.settings.paint = paint
        # 运行页的图像配置是同一份设置的控件镜像；已构建时一起回填，避免两处显示分叉。
        apply_paint = getattr(getattr(self.window(), "run_page", None), "apply_paint_settings", None)
        if callable(apply_paint):
            apply_paint(paint)
        persist = getattr(self.state, "persist", None)
        if callable(persist):
            persist()
        return True

    # ------------------------------------------------------------------ #
    # 运行
    # ------------------------------------------------------------------ #
    def _on_run_clicked(self) -> None:
        if self._cancel is not None:
            self._stop()
        else:
            self._run()

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
        self._sync_node_buttons()
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
