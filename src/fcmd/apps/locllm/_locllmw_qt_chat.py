"""locllmw（Qt）对话页：整合原 llmchatw 的多轮流式对话。

服务下拉框读取 :mod:`fcmd.apps.locllm.llmenv` 登记的服务并回填地址 / 模型 /
API Key，也可直接填任意 OpenAI 兼容地址。流式回复在后台线程读取，
思考型模型的 ``reasoning_content`` 以灰色斜体单独展示，正文按增量追加。
对话状态保存在页面控件中，切页不丢失；关闭窗口时取消进行中的生成。

连接参数复用 Tk 版的 ``format_session_markdown`` 与 :class:`ChatSession`，
Qt 侧只负责把回调转成信号与富文本追加。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm._locllmw_chat import _CUSTOM, format_session_markdown
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
from fcmd.apps.locllm.llmserve import process_alive
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.widgets import Card, Collapsible, FormGrid, HintLabel, button
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["ChatPage", "endpoint_from_form", "service_labels"]

_DEFAULT_URL = http_base_url(DEFAULT_HOST, DEFAULT_PORT)


def service_labels(states: list[ServeState]) -> list[tuple[str, ServeState]]:
    """生成服务下拉项：``自定义`` + 每个已登记服务（标注是否运行中）。"""
    options: list[tuple[str, ServeState]] = []
    for state in states:
        suffix = "" if process_alive(state.pid, state.create_time) else "（已退出）"
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
        self._session: ChatSession | None = None
        self._running = False
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        connection = Card("连接")
        form = FormGrid(connection)
        self.service_combo = QComboBox(connection)
        self.service_combo.setEditable(False)
        self.service_combo.currentIndexChanged.connect(lambda _index: self._on_service_changed())
        self.model_edit = QLineEdit(DEFAULT_ALIAS, connection)
        form.add_pair("服务", self.service_combo, "模型", self.model_edit)
        self.url_edit = QLineEdit(_DEFAULT_URL, connection)
        self.api_key_edit = QLineEdit(DEFAULT_API_KEY, connection)
        form.add_pair("服务地址", self.url_edit, "API Key", self.api_key_edit)
        connection.body.addWidget(form)

        self.settings_section = Collapsible("生成参数", expanded=bool(self.state.settings.chat_settings_open))
        settings_form = FormGrid(self.settings_section.content)
        self.system_edit = QLineEdit("", self.settings_section.content)
        self.system_edit.setPlaceholderText("系统提示（留空不发送）")
        settings_form.add_row("系统提示", self.system_edit)
        self.max_tokens_edit = QLineEdit("2048", self.settings_section.content)
        self.temperature_edit = QLineEdit("0.7", self.settings_section.content)
        self.top_p_edit = QLineEdit("0.9", self.settings_section.content)
        settings_form.add_pair("输出上限", self.max_tokens_edit, "temperature", self.temperature_edit)
        settings_form.add_row("top_p", self.top_p_edit)
        self.settings_section.body.addWidget(settings_form)
        self.settings_section.toggle.toggled.connect(self._on_settings_toggled)
        connection.body.addWidget(self.settings_section)
        layout.addWidget(connection)

        transcript_card = Card("对话")
        self.transcript = QTextEdit(transcript_card)
        self.transcript.setReadOnly(True)
        transcript_card.body.addWidget(self.transcript, 1)
        layout.addWidget(transcript_card, 1)

        self.status = HintLabel("未开始对话")
        layout.addWidget(self.status)

        input_row = QHBoxLayout()
        self.input_edit = QPlainTextEdit(self)
        self.input_edit.setPlaceholderText("输入问题，Ctrl+Enter 发送")
        self.input_edit.setFixedHeight(72)
        input_row.addWidget(self.input_edit, 1)
        buttons = QVBoxLayout()
        self.send_button = button("发送", kind="primary")
        self.stop_button = button("停止", kind="danger")
        self.stop_button.setEnabled(False)
        self.reset_button = button("新对话", kind="ghost")
        self.export_button = button("导出记录", kind="ghost")
        for widget in (self.send_button, self.stop_button, self.reset_button, self.export_button):
            buttons.addWidget(widget)
        input_row.addLayout(buttons)
        layout.addLayout(input_row)

        self.send_button.clicked.connect(self.send)
        self.stop_button.clicked.connect(self.stop)
        self.reset_button.clicked.connect(self.reset)
        self.export_button.clicked.connect(self.export_markdown)

    # ------------------------------------------------------------------ #
    # 服务选择
    # ------------------------------------------------------------------ #
    def refresh_services(self) -> None:
        """按最新状态刷新服务下拉项（保留当前选择）。"""
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
        if current:
            self.service_combo.setCurrentText(current)
        self.service_combo.blockSignals(False)

    def _selected_state(self) -> ServeState | None:
        label = self.service_combo.currentText()
        for candidate, state in getattr(self, "_services", []):
            if candidate == label:
                return state
        return None

    def _on_service_changed(self) -> None:
        """选中已登记服务时回填地址 / 模型 / API Key。"""
        state = self._selected_state()
        if state is None:
            return
        self.url_edit.setText(state.base_url)
        self.model_edit.setText(state.alias)
        self.api_key_edit.setText(state.api_key)
        self.status.setText(f"已选择服务 {state.name}（{state.base_url}）")

    # ------------------------------------------------------------------ #
    # 发送 / 停止
    # ------------------------------------------------------------------ #
    def send(self) -> None:
        """发送一轮提问，流式把回复写入对话区。"""
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
            top_p = float(self.top_p_edit.text().strip() or "0.9")
        except (ServeError, ValueError) as exc:
            self.status.setText(f"参数错误：{exc}")
            return
        if self._session is None or self._session.endpoint != endpoint:
            self._session = ChatSession(
                endpoint=endpoint,
                system=self.system_edit.text().strip() or None,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
        else:
            self._session.max_tokens = max_tokens
            self._session.temperature = temperature
            self._session.top_p = top_p
        self.input_edit.clear()
        self._append_text(f"你：{prompt}\n", "user")
        self._append_text("助手：", "assistant")
        self._running = True
        self.send_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.status.setText("正在生成…")
        submit(
            self._session.ask,
            prompt,
            on_delta=self._bridge.delta.emit,
            on_reasoning=self._bridge.reasoning.emit,
            on_done=self._bridge.done.emit,
            on_error=self._bridge.failed.emit,
        )

    def stop(self) -> None:
        """停止当前流式生成。"""
        if self._session is not None:
            self._session.cancel()
        self.status.setText("正在停止…")

    def _on_done(self, _reply: str) -> None:
        self._append_text("\n\n", "assistant")
        self._running = False
        self.send_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText("生成完成")

    def _on_failed(self, message: str) -> None:
        self._append_text(f"\n[失败] {message}\n\n", "error")
        self._running = False
        self.send_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.status.setText("生成失败：" + message)

    def reset(self) -> None:
        """清空对话历史与记录。"""
        if self._session is not None:
            self._session.reset()
        self.transcript.clear()
        self.status.setText("已开始新对话")

    def is_running(self) -> bool:
        """是否有生成在进行中。"""
        return self._running

    def close(self) -> None:
        """窗口关闭时取消生成。"""
        if self._session is not None:
            self._session.cancel()

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

    def _append_text(self, text: str, kind: str) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, self._format(kind))
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

    def _append_delta(self, text: str) -> None:
        self._append_text(text, "assistant")

    def _append_reasoning(self, text: str) -> None:
        self._append_text(f"[思考] {text}", "reasoning")

    # ------------------------------------------------------------------ #
    # 设置与导出
    # ------------------------------------------------------------------ #
    def _on_settings_toggled(self, checked: bool) -> None:
        self.state.settings.chat_settings_open = checked
        self.state.persist()

    def export_markdown(self) -> None:
        """把当前对话导出为 Markdown（复用 Tk 版的格式化函数）。"""
        messages: list[ChatMessage] = self._session.messages if self._session is not None else []
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
