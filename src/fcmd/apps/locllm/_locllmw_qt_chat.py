"""locllmw（Qt）对话页：整合原 llmchatw 的多轮流式对话。

服务下拉框读取 :mod:`fcmd.apps.locllm.llmenv` 登记的服务并回填地址 / 模型 /
API Key，也可直接填任意 OpenAI 兼容地址。流式回复在后台线程读取，
思考型模型的 ``reasoning_content`` 以灰色斜体单独展示，正文按增量追加。
对话状态保存在页面控件中，切页不丢失；关闭窗口时取消进行中的生成。

连接参数复用既有的 ``format_session_markdown`` 与 :class:`ChatSession`，
Qt 侧只负责把回调转成信号与富文本追加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm._locllmw_chat import _CUSTOM, _first_available, _state_available, format_session_markdown
from fcmd.apps.locllm.llmbase import (
    DEFAULT_ALIAS,
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    Endpoint,
    ServeError,
    ServeState,
    http_base_url,
)
from fcmd.apps.locllm.llmchat import ChatMessage, ChatSession
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.widgets import Card, Collapsible, FormGrid, HintLabel, button, icon_button
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["ChatPage", "endpoint_from_form", "service_labels"]

_DEFAULT_URL = http_base_url(DEFAULT_HOST, DEFAULT_PORT)


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


class _ChatBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台生成线程 → GUI 的增量信号桥。"""

    delta = Signal(str)
    reasoning = Signal(str)
    done = Signal(str)
    failed = Signal(str)


class ChatPage(QWidget):  # pragma: no cover - Qt 控件
    """对话页：连接参数 + 流式对话记录 + 输入区。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._conversations: list[_Conversation] = [_Conversation()]
        self._active = 0
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
        self.refresh_services()

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
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(8)
        self.new_chat_button = button("＋ 新对话", kind="primary")
        self.new_chat_button.clicked.connect(self.new_conversation)
        side.addWidget(self.new_chat_button)
        self.history_list = QListWidget(sidebar)
        self.history_list.setObjectName("ChatHistory")
        self.history_list.currentRowChanged.connect(self._select_conversation)
        side.addWidget(self.history_list, 1)
        outer.addWidget(sidebar)

        right = QWidget(self)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addWidget(right, 1)

        connection = Card("对话服务")
        service_row = QHBoxLayout()
        service_row.setContentsMargins(0, 0, 0, 0)
        service_row.setSpacing(8)
        self.service_combo = QComboBox(connection)
        self.service_combo.setEditable(False)
        self.service_combo.currentIndexChanged.connect(lambda _index: self._on_service_changed())
        service_row.addWidget(self.service_combo, 1)
        self.refresh_button = button("刷新", kind="ghost")
        self.refresh_button.clicked.connect(self.refresh_services)
        service_row.addWidget(self.refresh_button)
        self.connection_button = icon_button("🔗", tooltip="连接参数（自动回填，可自定义）", checkable=True)
        self.generation_button = icon_button("⚙", tooltip="生成参数", checkable=True)
        service_row.addWidget(self.connection_button)
        service_row.addWidget(self.generation_button)
        connection.body.addLayout(service_row)
        self.status = HintLabel("未开始对话")
        connection.body.addWidget(self.status)

        self.connection_section = Collapsible("连接参数（自动回填，可自定义）", expanded=False, header=False)
        form = FormGrid(self.connection_section.content)
        self.model_edit = QLineEdit(DEFAULT_ALIAS, self.connection_section.content)
        self.url_edit = QLineEdit(_DEFAULT_URL, self.connection_section.content)
        self.api_key_edit = QLineEdit(DEFAULT_API_KEY, self.connection_section.content)
        form.add_row("模型", self.model_edit)
        form.add_row("服务地址", self.url_edit)
        form.add_row("API Key", self.api_key_edit)
        self.connection_section.body.addWidget(form)
        connection.body.addWidget(self.connection_section)

        self.settings_section = Collapsible(
            "生成参数",
            expanded=bool(self.state.settings.chat_settings_open),
            header=False,
        )
        self.connection_button.setChecked(self.connection_section.is_expanded())
        self.generation_button.setChecked(self.settings_section.is_expanded())
        self.connection_button.toggled.connect(self.connection_section.set_expanded)
        self.generation_button.toggled.connect(self.settings_section.set_expanded)
        self.connection_section.toggle.toggled.connect(self.connection_button.setChecked)
        self.settings_section.toggle.toggled.connect(self.generation_button.setChecked)
        settings_form = FormGrid(self.settings_section.content)
        self.system_edit = QLineEdit("", self.settings_section.content)
        self.system_edit.setPlaceholderText("系统提示（留空不发送）")
        settings_form.add_row("系统提示", self.system_edit)
        self.max_tokens_edit = QLineEdit("2048", self.settings_section.content)
        self.temperature_edit = QLineEdit("0.7", self.settings_section.content)
        settings_form.add_pair("输出上限", self.max_tokens_edit, "temperature", self.temperature_edit)
        self.settings_section.body.addWidget(settings_form)
        self.settings_section.toggle.toggled.connect(self._on_settings_toggled)
        connection.body.addWidget(self.settings_section)
        layout.addWidget(connection)

        transcript_card = Card("对话")
        self.transcript = QTextEdit(transcript_card)
        self.transcript.setReadOnly(True)
        transcript_card.body.addWidget(self.transcript, 1)
        layout.addWidget(transcript_card, 1)

        input_area = QVBoxLayout()
        input_area.setSpacing(6)
        self.input_edit = QPlainTextEdit(self)
        self.input_edit.setPlaceholderText("输入问题，Ctrl+Enter 发送")
        self.input_edit.setMinimumHeight(72)
        self.input_edit.setMaximumHeight(120)
        self.input_edit.installEventFilter(self)
        input_area.addWidget(self.input_edit)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        buttons.addStretch(1)
        self.send_button = button("发送", kind="primary")
        self.stop_button = button("停止", kind="danger")
        self.stop_button.setEnabled(False)
        self.export_button = button("导出记录", kind="ghost")
        for widget in (self.send_button, self.stop_button, self.export_button):
            buttons.addWidget(widget)
        input_area.addLayout(buttons)
        layout.addLayout(input_area)

        self.send_button.clicked.connect(self.send)
        self.stop_button.clicked.connect(self.stop)
        self.export_button.clicked.connect(self.export_markdown)
        self._refresh_history()

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
        state = self._selected_state()
        if state is None:
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

    def _on_service_changed(self) -> None:
        """选中已登记服务时回填地址 / 模型 / API Key，并提示进程是否仍在运行。"""
        self._manual_choice = True
        state = self._selected_state()
        if state is None:
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
            self.status.setText(f"已选择服务 {state.name}（运行中）")
        else:
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

    def _refresh_history(self) -> None:
        self.history_list.blockSignals(True)
        self.history_list.clear()
        for conversation in self._conversations:
            self.history_list.addItem(conversation.title)
        self.history_list.setCurrentRow(self._active)
        self.history_list.blockSignals(False)

    def _select_conversation(self, row: int) -> None:
        if row < 0 or row == self._active:
            return
        if self._running:
            self._refresh_history()
            self.status.setText("正在生成，停止后才能切换对话")
            return
        self._active = row
        self._render_conversation()
        self.status.setText("已切换对话")

    def _render_conversation(self) -> None:
        self.transcript.clear()
        for kind, text in self._conversation().blocks:
            self._insert_text(text, kind)

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

    def _set_title_from_prompt(self, prompt: str) -> None:
        conversation = self._conversation()
        if conversation.title != "新对话":
            return
        title = prompt.splitlines()[0].strip()[:24] or "新对话"
        conversation.title = title
        self._refresh_history()

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

    def _on_done(self, _reply: str) -> None:
        conversation = self._conversation()
        if conversation.assistant_started or conversation.reasoning_started:
            self._append_text("\n\n", "assistant")
        self._finish_running()
        self.status.setText("生成完成")

    def _on_failed(self, message: str) -> None:
        self._append_text(f"\n[失败] {message}\n\n", "error")
        self._finish_running()
        self.status.setText("生成失败：" + message)

    def reset(self) -> None:
        """清空当前对话并开一个新的空对话。"""
        self.new_conversation()

    def is_running(self) -> bool:
        """是否有生成在进行中。"""
        return self._running

    def close(self) -> None:
        """窗口关闭时取消所有对话中正在进行的生成。"""
        for conversation in self._conversations:
            if conversation.session is not None:
                conversation.session.cancel()

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
    def _on_settings_toggled(self, checked: bool) -> None:
        self.state.settings.chat_settings_open = checked
        self.state.persist()

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
        path, _ = QFileDialog.getSaveFileName(self, "导出对话记录", "locllmw-chat.md", "Markdown (*.md)")
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
