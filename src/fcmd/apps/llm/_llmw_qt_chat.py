"""llmw（Qt）对话页：整合原 llmchatw 的多轮流式对话。

页面上只保留对话列表、状态行、正文与底部对话框；服务下拉、地址 / 模型 /
API Key 与生成参数收进独立的「模型与生成配置」窗口（点对话框左下角齿轮打开），
不占用对话区域。流式回复在后台线程读取，思考型模型的 ``reasoning_content``
以灰色斜体单独展示，正文按增量追加。
对话列表 / 已渲染内容 / 多轮 ``ChatSession`` 会落盘到 ``llmw.json`` 的
``chat_history`` / ``chat_active``，重启后恢复最近 20 个对话并可继续追问；
关闭窗口时取消进行中的生成，并再落盘一次最新历史。

连接参数复用既有的 ``format_session_markdown`` 与 :class:`ChatSession`，
Qt 侧只负责把回调转成信号与富文本追加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import llmenv
from fcmd.apps.llm._llmw_chat import _CUSTOM, _first_available, _state_available, format_session_markdown
from fcmd.apps.llm.llmbase import (
    DEFAULT_ALIAS,
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    Endpoint,
    ServeError,
    ServeState,
    http_base_url,
)
from fcmd.apps.llm.llmchat import ChatMessage, ChatRole, ChatSession
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.icons import set_icon as _set_icon
from fcmd.gui.qt.widgets import FormGrid, HintLabel, StatusTag, button, hint_label, icon_button
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = [
    "MAX_SAVED_CONVERSATIONS",
    "ChatPage",
    "conversation_from_data",
    "conversation_to_data",
    "endpoint_from_form",
    "history_payload",
    "service_labels",
]

_DEFAULT_URL = http_base_url(DEFAULT_HOST, DEFAULT_PORT)

MAX_SAVED_CONVERSATIONS = 20
"""最多持久化的对话条数；超出时保留最近 N 个，避免 llmw.json 无限膨胀。"""

_CHAT_ROLES: tuple[str, ...] = ("system", "user", "assistant")


def service_labels(states: list[ServeState]) -> list[tuple[str, ServeState]]:
    """生成服务下拉项：每个已登记服务都标注「运行中 / 已退出」。"""
    options: list[tuple[str, ServeState]] = []
    for state in states:
        suffix = "（运行中）" if _state_available(state) else "（已退出）"
        options.append((f"{state.name}{suffix}", state))
    return options


def endpoint_from_form(base_url: str, api_key: str, model: str) -> Endpoint:
    """把表单里的连接参数转成 :class:`Endpoint`（去掉多余的 ``/v1`` 后缀）。"""
    url = base_url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    if not url:
        raise ServeError("服务地址不能为空")
    if not model.strip():
        raise ServeError("模型名不能为空")
    return Endpoint(url, api_key.strip() or DEFAULT_API_KEY, model.strip())


@dataclass
class _Conversation:
    """一个对话历史条目：会话、已渲染块与思考状态。"""

    title: str = "新对话"
    session: ChatSession | None = None
    blocks: list[tuple[str, str]] = field(default_factory=list)
    assistant_started: bool = False
    reasoning_started: bool = False


def conversation_to_data(conversation: _Conversation) -> dict[str, Any]:
    """把一个对话（标题 / 已渲染块 / 会话历史）转成可写入 JSON 的结构。"""
    data: dict[str, Any] = {
        "title": conversation.title,
        "blocks": [[kind, text] for kind, text in conversation.blocks],
    }
    session = conversation.session
    if session is not None:
        data["session"] = {
            "endpoint": {
                "base_url": session.endpoint.base_url,
                "api_key": session.endpoint.api_key,
                "model": session.endpoint.model,
            },
            "system": session.system,
            "max_tokens": session.max_tokens,
            "temperature": session.temperature,
            "top_p": session.top_p,
            "messages": [{"role": message.role, "content": message.content} for message in session.messages],
        }
    return data


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def conversation_from_data(raw: Any) -> _Conversation | None:
    """从 JSON 结构恢复对话；结构不合法时返回 ``None``，不让单个坏条目拖垮历史。"""
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    conversation = _Conversation(
        title=title.strip() if isinstance(title, str) and title.strip() else "新对话",
        blocks=[
            (item[0], item[1])
            for item in raw.get("blocks") or []
            if isinstance(item, (list, tuple))
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], str)
        ],
    )
    session_raw = raw.get("session")
    if not isinstance(session_raw, dict):
        return conversation
    endpoint_raw = session_raw.get("endpoint")
    if not isinstance(endpoint_raw, dict):
        return conversation
    base_url = endpoint_raw.get("base_url")
    api_key = endpoint_raw.get("api_key")
    model = endpoint_raw.get("model")
    if not all(isinstance(value, str) and value.strip() for value in (base_url, api_key, model)):
        return conversation
    system = session_raw.get("system")
    session = ChatSession(
        endpoint=Endpoint(cast("str", base_url), cast("str", api_key), cast("str", model)),
        system=system if isinstance(system, str) and system.strip() else None,
        max_tokens=max(1, int(_number(session_raw.get("max_tokens"), 2048))),
        temperature=_number(session_raw.get("temperature"), 0.7),
        top_p=_number(session_raw.get("top_p"), 0.9),
    )
    messages: list[ChatMessage] = []
    for item in session_raw.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in _CHAT_ROLES and isinstance(content, str):
            messages.append(ChatMessage(cast("ChatRole", role), content))
    session.messages = messages
    conversation.session = session
    return conversation


def _dialog_title(text: str, parent: QWidget) -> QLabel:
    """配置窗口里的小节标题（与页面 SectionTitle 同一套字号 / 颜色）。"""
    label = QLabel(text, parent)
    label.setObjectName("SectionTitle")
    return label


def history_payload(
    conversations: list[_Conversation],
    active: int,
) -> tuple[list[dict[str, Any]], int]:
    """裁剪到最近 :data:`MAX_SAVED_CONVERSATIONS` 条，并把选中下标换算到裁剪后的列表。"""
    kept = conversations[-MAX_SAVED_CONVERSATIONS:]
    offset = len(conversations) - len(kept)
    index = active - offset
    index = min(max(index, 0), len(kept) - 1) if kept else 0
    return [conversation_to_data(conversation) for conversation in kept], index


class _ChatBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台生成线程 → GUI 的增量信号桥。"""

    delta = Signal(str)
    reasoning = Signal(str)
    done = Signal(str)
    failed = Signal(str)


class _ChatConfigDialog(QDialog):  # pragma: no cover - Qt 控件
    """模型与生成参数的独立配置窗口。

    对话页本身只保留「模型下拉 + ⚙」入口，配置项不占正文 / 对话框区域；
    修改即时生效，关闭窗口不需要额外保存步骤。
    """

    def __init__(self, owner: ChatPage) -> None:
        super().__init__(owner)
        self.setWindowTitle("模型与生成配置")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        layout.addWidget(_dialog_title("模型配置", self))
        service_row = QHBoxLayout()
        service_row.setSpacing(8)
        owner.service_combo = QComboBox(self)
        owner.service_combo.setEditable(False)
        service_row.addWidget(owner.service_combo, 1)
        owner.service_tag = StatusTag("未连接", tone="muted", parent=self)
        service_row.addWidget(owner.service_tag)
        owner.refresh_button = button("刷新", kind="ghost")
        service_row.addWidget(owner.refresh_button)
        layout.addLayout(service_row)

        connection_form = FormGrid(self)
        owner.model_edit = QLineEdit(DEFAULT_ALIAS, self)
        owner.url_edit = QLineEdit(_DEFAULT_URL, self)
        owner.api_key_edit = QLineEdit(DEFAULT_API_KEY, self)
        connection_form.add_row("模型", owner.model_edit)
        connection_form.add_row("服务地址", owner.url_edit)
        connection_form.add_row("API Key", owner.api_key_edit)
        layout.addWidget(connection_form)

        layout.addWidget(_dialog_title("生成参数", self))
        settings_form = FormGrid(self)
        owner.system_edit = QLineEdit("", self)
        owner.system_edit.setPlaceholderText("系统提示（留空不发送）")
        owner.max_tokens_edit = QLineEdit("2048", self)
        owner.temperature_edit = QLineEdit("0.7", self)
        settings_form.add_row("系统提示", owner.system_edit)
        settings_form.add_pair("输出上限", owner.max_tokens_edit, "temperature", owner.temperature_edit)
        layout.addWidget(settings_form)

        layout.addWidget(hint_label("修改即时生效；对话历史按各自对话保存的模型与参数继续。", wrap=True))
        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = button("关闭")
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)


class ChatPage(QWidget):  # pragma: no cover - Qt 控件
    """对话页：连接参数 + 流式对话记录 + 输入区。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._conversations, self._active = self._restore_history()
        self._running = False
        self._services: list[tuple[str, ServeState]] = []
        self._manual_choice = False
        self._bridge = _ChatBridge(self)
        self._bridge.delta.connect(self._append_delta)
        self._bridge.reasoning.connect(self._append_reasoning)
        self._bridge.done.connect(self._on_done)
        self._bridge.failed.connect(self._on_failed)
        self._build()
        self.state.servicesChanged.connect(lambda _states: self.refresh_services())
        self.state.modelsChanged.connect(lambda _outcome: self._refresh_model_choices())
        self.refresh_services()
        self._restore_session_form()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        sidebar = QFrame(self)
        sidebar.setObjectName("ChatSidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(240)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(8)
        self.new_chat_button = button("＋ 新对话", kind="primary")
        # 与运行页顶部「启动服务」同一尺寸（40px 高、标题字号、更大的左右内边距）。
        self.new_chat_button.setObjectName("RunPrimary")
        self.new_chat_button.clicked.connect(self.new_conversation)
        side.addWidget(self.new_chat_button)
        history_header = QHBoxLayout()
        history_header.setContentsMargins(2, 2, 2, 0)
        history_title = QLabel("对话记录")
        history_title.setObjectName("SectionTitle")
        self.history_count = QLabel("")
        self.history_count.setObjectName("Hint")
        history_header.addWidget(history_title)
        history_header.addStretch(1)
        history_header.addWidget(self.history_count)
        side.addLayout(history_header)
        self.history_list = QListWidget(sidebar)
        self.history_list.setObjectName("ChatHistory")
        self.history_list.setSpacing(2)
        self.history_list.currentRowChanged.connect(self._select_conversation)
        side.addWidget(self.history_list, 1)
        history_actions = QHBoxLayout()
        history_actions.setContentsMargins(0, 0, 0, 0)
        history_actions.setSpacing(6)
        self.delete_chat_button = button("删除", kind="ghost")
        self.delete_chat_button.setToolTip("删除当前选中的对话")
        self.delete_chat_button.clicked.connect(self.delete_conversation)
        self.clear_chats_button = button("清空全部", kind="ghost")
        self.clear_chats_button.setToolTip("删除全部对话记录")
        self.clear_chats_button.clicked.connect(self.clear_conversations)
        history_actions.addWidget(self.delete_chat_button)
        history_actions.addWidget(self.clear_chats_button)
        side.addLayout(history_actions)
        outer.addWidget(sidebar)

        right = QWidget(self)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addWidget(right, 1)

        self.status = HintLabel("未开始对话")
        layout.addWidget(self.status)

        # 对话区不再套卡片边框：正文直接铺在页面底色上，更接近主流对话产品的阅读区。
        self.transcript = QTextEdit(self)
        self.transcript.setObjectName("ChatTranscript")
        self.transcript.setReadOnly(True)
        layout.addWidget(self.transcript, 1)

        # 输入区（对话框）：大圆角卡片 + 底部工具行，模型选择放在发送按钮左侧。
        composer = QFrame(self)
        composer.setObjectName("ChatComposer")
        composer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        input_area = QVBoxLayout(composer)
        input_area.setContentsMargins(10, 8, 10, 8)
        input_area.setSpacing(6)
        self.input_edit = QPlainTextEdit(composer)
        self.input_edit.setObjectName("ChatInput")
        self.input_edit.setPlaceholderText("输入问题，Ctrl+Enter 发送")
        self.input_edit.setMinimumHeight(64)
        self.input_edit.setMaximumHeight(120)
        self.input_edit.installEventFilter(self)
        input_area.addWidget(self.input_edit)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.model_combo = QComboBox(composer)
        self.model_combo.setObjectName("ChatModelCombo")
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setToolTip("选择当前对话使用的模型（可输入自定义模型名）")
        self.model_combo.currentTextChanged.connect(self._on_model_combo_changed)
        buttons.addWidget(self.model_combo, 0)
        self.config_button = icon_button("", tooltip="模型与生成配置（服务地址 / API Key / 采样参数）")
        _set_icon(self.config_button, "settings")
        self.config_button.setAccessibleName("模型与生成配置")
        buttons.addWidget(self.config_button, 0)
        buttons.addStretch(1)
        self.send_button = button("发送", kind="primary")
        self.stop_button = button("停止", kind="danger")
        self.stop_button.setEnabled(False)
        self.export_button = button("导出记录", kind="ghost")
        for widget in (self.export_button, self.stop_button, self.send_button):
            buttons.addWidget(widget)
        input_area.addLayout(buttons)
        layout.addWidget(composer)

        # 模型 / 连接 / 生成参数收进独立配置窗口，只保留对话框里的「模型 + ⚙」入口。
        self.config_dialog = _ChatConfigDialog(self)
        self.service_combo.currentIndexChanged.connect(lambda _index: self._on_service_changed())
        self.refresh_button.clicked.connect(self.refresh_services)
        self.model_edit.textChanged.connect(lambda _text: self._sync_model_combo())
        self.config_button.clicked.connect(self.open_config)
        self.send_button.clicked.connect(self.send)
        self.stop_button.clicked.connect(self.stop)
        self.export_button.clicked.connect(self.export_markdown)
        self._refresh_history()
        self._render_conversation()

    # ------------------------------------------------------------------ #
    # 服务选择
    # ------------------------------------------------------------------ #
    def refresh_services(self) -> None:
        """按最新状态刷新服务下拉项，默认选中第一个可用服务并刷新状态提示。"""
        try:
            states = llmenv.list_states()
        except ServeError:
            states = []
        current = self.service_combo.currentText()
        self.service_combo.blockSignals(True)
        self.service_combo.clear()
        self.service_combo.addItem(_CUSTOM)
        self._services = service_labels(states)
        for label, _state in self._services:
            self.service_combo.addItem(label)
        labels = [_CUSTOM, *[label for label, _state in self._services]]
        if current not in labels:
            current = _CUSTOM
        if current == _CUSTOM and not self._manual_choice:
            first = _first_available(states)
            if first is not None:
                current = next(label for label, state in self._services if state.name == first.name)
            elif self._services:
                # 都不可用时也选中第一个服务，让状态栏说明“进程已退出”而不是笼统的未选择。
                current = self._services[0][0]
        self.service_combo.setCurrentText(current)
        self.service_combo.blockSignals(False)
        self._refresh_model_choices()
        state = self._selected_state()
        if state is None:
            self.service_tag.set_status("未连接", "muted")
            self.status.setText(
                "没有已登记的服务：去「运行」页启动，或点「刷新服务列表」"
                if not states
                else "未选择已登记服务：可直接填写下方地址接入其它 OpenAI 兼容服务"
            )
            return
        self._apply_state(state)
        self._report_state(state)

    def _selected_state(self) -> ServeState | None:
        label = self.service_combo.currentText()
        for candidate, state in getattr(self, "_services", []):
            if candidate == label:
                return state
        return None

    def _refresh_model_choices(self) -> None:
        """把已登记服务的模型名填进输入框左侧的模型下拉（保留当前自定义值）。"""
        current = self.model_edit.text().strip()
        names: list[str] = []
        for _label, state in self._services:
            alias = state.alias.strip()
            if alias and alias not in names:
                names.append(alias)
        if current and current not in names:
            names.insert(0, current)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(names)
        self.model_combo.setCurrentText(current or (names[0] if names else DEFAULT_ALIAS))
        self.model_combo.blockSignals(False)

    def _on_model_combo_changed(self, text: str) -> None:
        """选择模型：同步模型名；命中已登记服务时一并切换服务地址。"""
        model = text.strip()
        if not model:
            return
        if model != self.model_edit.text():
            self.model_edit.setText(model)
        for index, (_label, state) in enumerate(self._services, start=1):
            if state.alias.strip() != model or self.service_combo.currentIndex() == index:
                continue
            # 静默切换服务（不触发「已切换到…」正文提示），避免污染对话记录。
            self.service_combo.blockSignals(True)
            self.service_combo.setCurrentIndex(index)
            self.service_combo.blockSignals(False)
            self._manual_choice = True
            self._apply_state(state)
            self._report_state(state)
            return

    def _sync_model_combo(self) -> None:
        """连接参数变化（服务回填 / 恢复历史）时反向同步模型下拉。"""
        text = self.model_edit.text()
        if self.model_combo.currentText() == text:
            return
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentText(text)
        self.model_combo.blockSignals(False)

    def open_config(self) -> None:
        """显示独立的模型 / 生成参数配置窗口（非模态，不阻塞对话）。"""
        self.config_dialog.show()
        self.config_dialog.raise_()
        self.config_dialog.activateWindow()

    def _on_service_changed(self) -> None:
        """选中已登记服务时回填地址 / 模型 / API Key，并提示进程是否仍在运行。"""
        self._manual_choice = True
        state = self._selected_state()
        if state is None:
            self.service_tag.set_status("自定义", "accent")
            self.status.setText("自定义地址：脱离已登记服务，直接按下方地址与模型接入")
            return
        self._apply_state(state)
        if _state_available(state):
            self._append_text(f"[服务] 已切换到 {state.name}：{state.base_url}\n\n", "info")
        else:
            self._append_text(f"[错误] 服务 {state.name} 不可用：{state.base_url}\n\n", "error")
        self._report_state(state)

    def _apply_state(self, state: ServeState) -> None:
        """把服务状态的地址 / 模型 / API Key 回填到表单。"""
        self.url_edit.setText(state.base_url)
        self.model_edit.setText(state.alias)
        self.api_key_edit.setText(state.api_key)

    def _report_state(self, state: ServeState) -> None:
        """状态栏文案：运行中 / 进程已退出。"""
        if _state_available(state):
            self.service_tag.set_status("运行中", "success")
            self.status.setText(f"已选择服务 {state.name}")
        else:
            self.service_tag.set_status("已退出", "danger")
            self.status.setText(f"服务 {state.name} 的进程已退出：去「运行」页重新启动，或点「刷新服务列表」")

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        """Ctrl+Enter 发送；其余事件交给父类。"""
        if obj is self.input_edit and event.type() == QEvent.Type.KeyPress:
            key_event = event if isinstance(event, QKeyEvent) else None
            if (
                key_event is not None
                and key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and key_event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self.send()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # 对话历史
    # ------------------------------------------------------------------ #
    def _conversation(self) -> _Conversation:
        return self._conversations[self._active]

    def _restore_history(self) -> tuple[list[_Conversation], int]:
        """从 ``llmw.json`` 恢复对话列表与选中下标；没有可用记录时给一个空对话。"""
        conversations = [
            conversation
            for item in self.state.settings.chat_history or []
            if (conversation := conversation_from_data(item)) is not None
        ]
        if not conversations:
            return [_Conversation()], 0
        active = self.state.settings.chat_active
        return conversations, active if 0 <= active < len(conversations) else len(conversations) - 1

    def _persist_history(self) -> None:
        """把当前对话列表与选中项写回 ``llmw.json``（只保留最近 N 条）。"""
        payload, active = history_payload(self._conversations, self._active)
        self.state.settings.chat_history = payload
        self.state.settings.chat_active = active
        self.state.persist()

    def _restore_session_form(self) -> None:
        """切回带会话的对话时，把连接参数与采样参数同步回表单，便于继续追问。"""
        session = self._conversation().session
        if session is None:
            return
        self.url_edit.setText(session.endpoint.base_url)
        self.api_key_edit.setText(session.endpoint.api_key)
        self.model_edit.setText(session.endpoint.model)
        self.max_tokens_edit.setText(str(session.max_tokens))
        self.temperature_edit.setText(str(session.temperature))
        self.system_edit.setText(session.system or "")

    def _refresh_history(self) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        for conversation in self._conversations:
            item = QListWidgetItem(conversation.title)
            item.setToolTip(conversation.title)
            self.history_list.addItem(item)
        self.history_list.setCurrentRow(self._active)
        self.history_list.blockSignals(False)
        self.history_count.setText(f"{len(self._conversations)} 个")

    def _select_conversation(self, row: int) -> None:
        if row < 0 or row == self._active:
            return
        if self._running:
            self._refresh_history()
            self.status.setText("正在生成，停止后才能切换对话")
            return
        self._active = row
        self._render_conversation()
        self._restore_session_form()
        self.status.setText("已切换对话")
        self._persist_history()

    def _render_conversation(self) -> None:
        self.transcript.clear()
        for kind, text in self._conversation().blocks:
            self._insert_text(text, kind)

    def delete_conversation(self) -> None:
        """删除当前选中的对话；删掉最后一个时回到空白新对话。"""
        if self._running:
            self.status.setText("正在生成，停止后才能清理对话")
            return
        if not self._conversations:
            return
        index = self._active
        self._conversations.pop(index)
        if not self._conversations:
            self._conversations = [_Conversation()]
            self._active = 0
            self._refresh_history()
            self.transcript.clear()
        else:
            self._active = min(index, len(self._conversations) - 1)
            self._refresh_history()
            self._render_conversation()
        self.status.setText("已删除当前对话")
        self._persist_history()

    def clear_conversations(self) -> None:
        """清空全部对话记录（需确认）；完成后回到空白新对话。"""
        if self._running:
            self.status.setText("正在生成，停止后才能清理对话")
            return
        answer = QMessageBox.question(
            self,
            "清空全部对话",
            f"将删除全部 {len(self._conversations)} 个对话记录，确定继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._conversations = [_Conversation()]
        self._active = 0
        self._refresh_history()
        self.transcript.clear()
        self.status.setText("已清空全部对话")
        self._persist_history()

    def new_conversation(self) -> None:
        """新建一个空白对话；空对话不重复创建。"""
        if self._running:
            self.status.setText("正在生成，停止后才能新建对话")
            return
        current = self._conversation()
        if not current.blocks and current.session is None:
            self._render_conversation()
            self.status.setText("已开始新对话")
            return
        self._conversations.append(_Conversation())
        self._active = len(self._conversations) - 1
        self._refresh_history()
        self.transcript.clear()
        self.status.setText("已开始新对话")
        self._persist_history()

    def _set_title_from_prompt(self, prompt: str) -> None:
        conversation = self._conversation()
        if conversation.title != "新对话":
            return
        title = prompt.splitlines()[0].strip()[:24] or "新对话"
        conversation.title = title
        self._refresh_history()
        self._persist_history()

    # ------------------------------------------------------------------ #
    # 发送 / 停止
    # ------------------------------------------------------------------ #
    def send(self) -> None:
        """发送一轮提问，流式把回复写入当前对话。"""
        if self._running:
            return
        prompt = self.input_edit.toPlainText().strip()
        if not prompt:
            self.status.setText("提示词不能为空")
            return
        try:
            endpoint = endpoint_from_form(self.url_edit.text(), self.api_key_edit.text(), self.model_edit.text())
            max_tokens = int(self.max_tokens_edit.text().strip() or "2048")
            temperature = float(self.temperature_edit.text().strip() or "0.7")
            top_p = 0.9
        except (ServeError, ValueError) as exc:
            self.status.setText(f"参数错误：{exc}")
            return
        conversation = self._conversation()
        if conversation.session is None or conversation.session.endpoint != endpoint:
            conversation.session = ChatSession(
                endpoint=endpoint,
                system=self.system_edit.text().strip() or None,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
        else:
            conversation.session.max_tokens = max_tokens
            conversation.session.temperature = temperature
            conversation.session.top_p = top_p
        self.input_edit.clear()
        self._set_title_from_prompt(prompt)
        self._append_text(f"你：{prompt}\n", "user")
        conversation.assistant_started = False
        conversation.reasoning_started = False
        self._running = True
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.history_list.setEnabled(False)
        self.new_chat_button.setEnabled(False)
        self.delete_chat_button.setEnabled(False)
        self.clear_chats_button.setEnabled(False)
        self.status.setText("正在生成…")
        submit(
            conversation.session.ask,
            prompt,
            on_delta=self._bridge.delta.emit,
            on_reasoning=self._bridge.reasoning.emit,
            on_done=self._bridge.done.emit,
            on_error=self._bridge.failed.emit,
        )

    def stop(self) -> None:
        """停止当前流式生成。"""
        session = self._conversation().session
        if session is not None:
            session.cancel()
        self.status.setText("正在停止…")

    def _finish_running(self) -> None:
        self._running = False
        self.send_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.history_list.setEnabled(True)
        self.new_chat_button.setEnabled(True)
        self.delete_chat_button.setEnabled(True)
        self.clear_chats_button.setEnabled(True)

    def _on_done(self, _reply: str) -> None:
        conversation = self._conversation()
        if conversation.assistant_started or conversation.reasoning_started:
            self._append_text("\n\n", "assistant")
        self._finish_running()
        self.status.setText("生成完成")
        self._persist_history()

    def _on_failed(self, message: str) -> None:
        self._append_text(f"\n[失败] {message}\n\n", "error")
        self._finish_running()
        self.status.setText("生成失败：" + message)
        self._persist_history()

    def reset(self) -> None:
        """清空当前对话并开一个新的空对话。"""
        self.new_conversation()

    def is_running(self) -> bool:
        """是否有生成在进行中。"""
        return self._running

    def close(self) -> None:
        """窗口关闭时取消进行中的生成，并把最新对话历史落盘。"""
        for conversation in self._conversations:
            if conversation.session is not None:
                conversation.session.cancel()
        self._persist_history()

    # ------------------------------------------------------------------ #
    # 文本追加
    # ------------------------------------------------------------------ #
    def _format(self, kind: str) -> QTextCharFormat:
        palette = _guitheme.load_theme().palette
        fmt = QTextCharFormat()
        color = {
            "user": palette.accent,
            "reasoning": palette.muted,
            "assistant": palette.text,
            "error": palette.danger,
        }.get(kind, palette.text)
        fmt.setForeground(QColor(color))
        if kind == "user":
            font = QFont(self.transcript.font())
            font.setBold(True)
            fmt.setFont(font)
        if kind == "reasoning":
            font = QFont(self.transcript.font())
            font.setItalic(True)
            fmt.setFont(font)
        return fmt

    def _insert_text(self, text: str, kind: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, self._format(kind))
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _append_text(self, text: str, kind: str) -> None:
        self._conversation().blocks.append((kind, text))
        self._insert_text(text, kind)

    def _append_delta(self, text: str) -> None:
        conversation = self._conversation()
        if not conversation.assistant_started:
            prefix = "\n\n助手：" if conversation.reasoning_started else "助手："
            self._append_text(prefix, "assistant")
            conversation.assistant_started = True
        self._append_text(text, "assistant")

    def _append_reasoning(self, text: str) -> None:
        conversation = self._conversation()
        if not conversation.reasoning_started:
            self._append_text("助手（思考）：\n", "reasoning")
            conversation.reasoning_started = True
        self._append_text(text, "reasoning")

    # ------------------------------------------------------------------ #
    # 设置与导出
    # ------------------------------------------------------------------ #
    def export_markdown(self) -> None:
        """把当前对话导出为 Markdown（复用既有格式化函数）。"""
        session = self._conversation().session
        messages: list[ChatMessage] = session.messages if session is not None else []
        if not messages:
            self.status.setText("尚无对话内容可导出")
            return
        text = format_session_markdown(
            messages,
            model=self.model_edit.text().strip(),
            base_url=self.url_edit.text().strip(),
        )
        path, _ = QFileDialog.getSaveFileName(self, "导出对话记录", "llmw-chat.md", "Markdown (*.md)")
        if not path:
            return
        from pathlib import Path

        Path(path).write_text(text, encoding="utf-8")
        self.status.setText(f"已导出: {path}")

    def copy_transcript(self) -> None:
        """复制对话区全部文本。"""
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.transcript.toPlainText())
