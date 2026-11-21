"""llmw（Qt）对话页：整合原 llmchatw 的多轮流式对话。

页面上只保留对话列表、正文与底部对话框；服务下拉、地址 / 模型 /
API Key 与生成参数收进独立的「模型与生成配置」窗口（点对话框左下角齿轮打开），
不占用对话区域。对话状态（服务是否登记 / 生成进度 / 历史操作结果）统一写到
窗口底部状态栏，切到其它页再切回时仍然可见。流式回复在后台线程读取，
思考型模型的 ``reasoning_content`` 以灰色斜体单独展示，正文按增量追加。
对话列表 / 已渲染内容 / 多轮 ``ChatSession`` 会落盘到 ``llmw.json`` 的
``chat_history`` / ``chat_active``，重启后恢复最近 20 个对话并可继续追问；
关闭窗口时取消进行中的生成，并再落盘一次最新历史。

Qt 侧只负责把回调转成信号与富文本追加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextCursor,
    QTextImageFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import _llmpaint, llmenv
from fcmd.apps.llm._llmw_chat import _CUSTOM, _first_available, _state_available
from fcmd.apps.llm._llmw_qt_paint import (
    PaintSampleSection,
    components_from_settings,
    generate_images,
    parse_paint_settings,
    request_from_settings,
    settings_summary,
)
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
from fcmd.gui.qt.widgets import (
    FormGrid,
    PageScroll,
    StatusTag,
    ToggleChip,
    button,
    hint_label,
    icon_button,
    make_splitter,
)
from fcmd.gui.qt.workers import submit, submit_daemon

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


class _ChatModelCombo(QComboBox):  # pragma: no cover - Qt 控件
    """模型选择器：非编辑、无原生下拉框行为，点开为上浮的圆角菜单。

    DSH 一类对话产品的模型选择是「当前模型胶囊 + 弹出菜单」，而不是传统
    QComboBox 的下拉列表（带输入框 / 系统箭头 / 向下弹层）。这里保留 QComboBox
    的 item API 便于程序化更新，但 ``showPopup`` 换成自绘 QMenu：选项带当前项
    勾选、圆角、无边框，并在输入框上方弹出，避免遮挡对话框。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu: QMenu | None = None

    def model_menu(self) -> QMenu:
        """构造当前模型列表菜单（供 showPopup 与测试复用）。"""
        menu = QMenu(self)
        menu.setObjectName("ChatModelMenu")
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        current = self.currentIndex()
        for index in range(self.count()):
            action = menu.addAction(self.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == current)
            action.triggered.connect(lambda _checked=False, row=index: self.setCurrentIndex(row))
        return menu

    def showPopup(self) -> None:
        """在模型胶囊上方弹出菜单；不再调用 QComboBox 的原生下拉列表。"""
        menu = self.model_menu()
        self._menu = menu
        menu.adjustSize()
        anchor = self.mapToGlobal(QPoint(0, 0))
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        y = anchor.y() - menu.height() - 6
        x = anchor.x()
        if screen is not None:
            available = screen.availableGeometry()
            x = min(max(x, available.left()), available.right() - menu.width())
            y = max(y, available.top())
        menu.popup(QPoint(x, y))

    def hidePopup(self) -> None:
        """关闭自绘菜单（QComboBox 内部调用时保持兼容）。"""
        if self._menu is not None:
            self._menu.hide()
            self._menu = None

    def paintEvent(self, event: object) -> None:
        """在右侧画一个细 chevron，替代系统下拉箭头。"""
        super().paintEvent(event)  # type: ignore[arg-type]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.palette().color(self.foregroundRole()))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        center_x = self.width() - 13
        center_y = self.height() // 2
        painter.drawLine(center_x - 4, center_y - 2, center_x, center_y + 2)
        painter.drawLine(center_x, center_y + 2, center_x + 4, center_y - 2)
        painter.end()


class _ChatConfigDialog(QDialog):  # pragma: no cover - Qt 控件
    """模型、生成参数与画图配置的独立窗口。

    对话页本身只保留「模型下拉 + ⚙」入口，对话与画图配置都不占正文区域；
    修改即时生效，关闭窗口不需要额外保存步骤。
    """

    def __init__(self, owner: ChatPage) -> None:
        super().__init__(owner)
        self.setWindowTitle("模型与生成配置")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        scroll = PageScroll(self, margins=(0, 0, 4, 0))
        scroll.setMinimumHeight(520)
        layout.addWidget(scroll, 1)
        body = scroll.body

        body.addWidget(_dialog_title("模型配置", self))
        service_row = QHBoxLayout()
        service_row.setSpacing(8)
        owner.service_combo = QComboBox(self)
        owner.service_combo.setEditable(False)
        service_row.addWidget(owner.service_combo, 1)
        owner.service_tag = StatusTag("未连接", tone="muted", parent=self)
        service_row.addWidget(owner.service_tag)
        owner.refresh_button = button("刷新", kind="ghost")
        service_row.addWidget(owner.refresh_button)
        body.addLayout(service_row)

        connection_form = FormGrid(self)
        owner.model_edit = QLineEdit(DEFAULT_ALIAS, self)
        owner.url_edit = QLineEdit(_DEFAULT_URL, self)
        owner.api_key_edit = QLineEdit(DEFAULT_API_KEY, self)
        connection_form.add_row("模型", owner.model_edit)
        connection_form.add_row("服务地址", owner.url_edit)
        connection_form.add_row("API Key", owner.api_key_edit)
        body.addWidget(connection_form)

        body.addWidget(_dialog_title("生成参数", self))
        settings_form = FormGrid(self)
        owner.system_edit = QLineEdit("", self)
        owner.system_edit.setPlaceholderText("系统提示（留空不发送）")
        owner.max_tokens_edit = QLineEdit("2048", self)
        owner.temperature_edit = QLineEdit("0.7", self)
        settings_form.add_row("系统提示", owner.system_edit)
        settings_form.add_pair("输出上限", owner.max_tokens_edit, "temperature", owner.temperature_edit)
        body.addWidget(settings_form)

        # 画图采样 / 输出进对话配置；模型与引擎配置在运行页「画图」页签。
        body.addWidget(_dialog_title("画图（采样 / 输出）", self))
        owner.paint_section = PaintSampleSection(owner.paint_settings(), self)
        owner.paint_section.changed.connect(owner._on_paint_settings_changed)
        body.addWidget(owner.paint_section)
        body.addStretch(1)

        layout.addWidget(
            hint_label(
                "修改即时生效；对话历史按各自对话保存的模型与参数继续。画图模型 / 引擎配置在运行页「画图」页签。",
                wrap=True,
            )
        )
        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = button("关闭")
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        self.resize(660, 780)


class ChatPage(QWidget):  # pragma: no cover - Qt 控件
    """对话页：连接参数 + 流式对话记录 + 输入区。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._conversations, self._active = self._restore_history()
        self._running = False
        self._services: list[tuple[str, ServeState]] = []
        self._manual_choice = False
        self._paint_settings = parse_paint_settings(getattr(self.state.settings, "paint", None))
        self._paint_save_timer = QTimer(self)
        self._paint_save_timer.setSingleShot(True)
        self._paint_save_timer.setInterval(400)
        self._paint_save_timer.timeout.connect(self._persist_paint)
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
        outer.setSpacing(0)
        # 侧边栏宽度可拖动：默认记住上次宽度，向左收窄、向右展开；
        # 与右侧对话区之间放共享 splitter，不再写死固定宽度。
        self._sidebar_width = max(120, self.state.settings.chat_sidebar_width)
        self._sidebar_collapsed = self.state.settings.chat_sidebar_collapsed
        self._splitter = make_splitter(
            Qt.Orientation.Horizontal,
            sizes=(0 if self._sidebar_collapsed else self._sidebar_width, 760),
            parent=self,
        )
        self._splitter.setObjectName("ChatSplitter")
        self._splitter.setHandleWidth(4)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        outer.addWidget(self._splitter, 1)

        sidebar = QFrame(self._splitter)
        sidebar.setObjectName("ChatSidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setMinimumWidth(120)
        sidebar.setMaximumWidth(480)
        sidebar.setVisible(not self._sidebar_collapsed)
        self.sidebar = sidebar
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(8)
        self.new_chat_button = button("新对话", kind="primary")
        # 与运行页顶部「启动服务」同一尺寸：只比常规按钮略高，左右内边距更宽。
        self.new_chat_button.setObjectName("RunPrimary")
        self.new_chat_button.clicked.connect(self.new_conversation)
        side.addWidget(self.new_chat_button)
        self.history_list = QListWidget(sidebar)
        self.history_list.setObjectName("ChatHistory")
        self.history_list.setSpacing(2)
        # 对话列表只占最小宽度：长标题右侧省略（tooltip 保留全名），不出现横向滚动条，
        # 也不让内容把侧栏撑宽；用户仍可拖动 splitter 展开。
        self.history_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.history_list.setWordWrap(False)
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.setMinimumWidth(0)
        self.history_list.currentRowChanged.connect(self._select_conversation)
        side.addWidget(self.history_list, 1)
        # 底部操作条：无边框透明底，三个图标按钮等宽铺满一行，不抢正文视线。
        self.history_actions = QFrame(sidebar)
        self.history_actions.setObjectName("ChatSidebarActions")
        self.history_actions.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        history_actions = QHBoxLayout(self.history_actions)
        history_actions.setContentsMargins(2, 2, 2, 2)
        history_actions.setSpacing(2)
        self.copy_chat_button = icon_button("", tooltip="复制当前对话内容")
        self.copy_chat_button.setObjectName("ChatIconButton")
        self.copy_chat_button.setFixedSize(30, 30)
        _set_icon(self.copy_chat_button, "copy", tone="muted")
        self.copy_chat_button.setAccessibleName("复制当前对话")
        self.copy_chat_button.clicked.connect(self.copy_transcript)
        self.delete_chat_button = icon_button("", tooltip="删除当前选中的对话")
        self.delete_chat_button.setObjectName("ChatIconButton")
        self.delete_chat_button.setFixedSize(30, 30)
        _set_icon(self.delete_chat_button, "trash", tone="danger")
        self.delete_chat_button.setAccessibleName("删除当前对话")
        self.delete_chat_button.clicked.connect(self.delete_conversation)
        self.clear_chats_button = icon_button("", tooltip="清空全部对话记录")
        self.clear_chats_button.setObjectName("ChatIconButton")
        self.clear_chats_button.setFixedSize(30, 30)
        _set_icon(self.clear_chats_button, "x", tone="danger")
        self.clear_chats_button.setAccessibleName("清空全部对话")
        self.clear_chats_button.clicked.connect(self.clear_conversations)
        for widget in (self.copy_chat_button, self.delete_chat_button, self.clear_chats_button):
            history_actions.addWidget(widget, 1)
        side.addWidget(self.history_actions)
        self._splitter.addWidget(sidebar)

        right = QWidget(self._splitter)
        layout = QVBoxLayout(right)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(8)
        self._splitter.addWidget(right)
        # 构造 splitter 时还没有子控件，sizes 会被忽略；加完两侧后重新套一次，
        # 否则 Qt 按 sizeHint 分配，侧栏实际会撑到 300px 以上。
        self._splitter.setSizes([0 if self._sidebar_collapsed else self._sidebar_width, 760])

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        self.sidebar_toggle = icon_button("", tooltip="收起对话列表")
        self.sidebar_toggle.setObjectName("ChatIconButton")
        self.sidebar_toggle.setFixedSize(28, 28)
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)
        top_row.addWidget(self.sidebar_toggle, 0)
        top_row.addStretch(1)
        layout.addLayout(top_row)
        self._sync_sidebar_toggle()

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
        input_area.setContentsMargins(12, 10, 12, 10)
        input_area.setSpacing(8)
        self.input_edit = QPlainTextEdit(composer)
        self.input_edit.setObjectName("ChatInput")
        self.input_edit.setPlaceholderText("输入问题，Ctrl+Enter 发送")
        self.input_edit.setMinimumHeight(64)
        self.input_edit.setMaximumHeight(120)
        self.input_edit.installEventFilter(self)
        input_area.addWidget(self.input_edit)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.model_combo = _ChatModelCombo(composer)
        self.model_combo.setObjectName("ChatModelCombo")
        self.model_combo.setMinimumWidth(200)
        self.model_combo.setMaximumWidth(300)
        self.model_combo.setFixedHeight(34)
        self.model_combo.setToolTip("选择当前对话使用的模型（可输入自定义模型名）")
        self.model_combo.currentTextChanged.connect(self._on_model_combo_changed)
        buttons.addWidget(self.model_combo, 0)
        self.config_button = icon_button("", tooltip="模型与生成配置（服务地址 / API Key / 采样参数）")
        self.config_button.setObjectName("ChatIconButton")
        self.config_button.setFixedSize(34, 34)
        _set_icon(self.config_button, "settings", tone="muted")
        self.config_button.setAccessibleName("模型与生成配置")
        buttons.addWidget(self.config_button, 0)
        # 对话 / 画图模式：参考元宝输入区的圆角胶囊开关，互斥选择发送内容。
        self.chat_toggle = ToggleChip(
            "对话",
            icon="chat",
            tooltip="发送文字问题，由所选模型回答",
            parent=composer,
        )
        self.chat_toggle.setAccessibleName("对话")
        buttons.addWidget(self.chat_toggle, 0)
        self.paint_toggle = ToggleChip(
            "画图",
            icon="paint",
            tooltip="用提示词生成图片（sd.cpp）；采样 / 输出在 ⚙ 配置里，模型在运行页「画图」页签",
            parent=composer,
        )
        self.paint_toggle.setAccessibleName("画图")
        buttons.addWidget(self.paint_toggle, 0)
        # 恢复上次模式：上次启用画图则选中「画图」，否则默认「对话」；
        # 初始状态先于信号连接，避免启动时多写一次盘 / 闪一条状态。
        paint_enabled = bool(self._paint_settings.get("enabled"))
        self.paint_toggle.setChecked(paint_enabled)
        self.chat_toggle.setChecked(not paint_enabled)
        self.chat_toggle.toggled.connect(self._on_chat_toggled)
        self.paint_toggle.toggled.connect(self._on_paint_toggled)
        buttons.addStretch(1)
        # 工具行与模型下拉统一 34px 高；发送 / 停止合并到同一个图标按钮。
        self.send_button = icon_button("", tooltip="发送（Ctrl+Enter）")
        self.send_button.setObjectName("ChatSend")
        self.send_button.setFixedSize(34, 34)
        # 与齿轮图标按钮同一无边框风格，只用 accent 图标色表达主操作。
        _set_icon(self.send_button, "send", tone="accent")
        self.send_button.setAccessibleName("发送")
        buttons.addWidget(self.send_button)
        input_area.addLayout(buttons)
        layout.addWidget(composer)

        # 模型 / 连接 / 生成参数收进独立配置窗口，只保留对话框里的「模型 + ⚙」入口。
        self.config_dialog = _ChatConfigDialog(self)
        self.service_combo.currentIndexChanged.connect(lambda _index: self._on_service_changed())
        self.refresh_button.clicked.connect(self.refresh_services)
        self.model_edit.textChanged.connect(lambda _text: self._sync_model_combo())
        self.config_button.clicked.connect(self.open_config)
        self.send_button.clicked.connect(self._on_send_or_stop)
        self._sync_paint_placeholder()
        self._refresh_history()
        self._render_conversation()

    # ------------------------------------------------------------------ #
    # 服务选择
    # ------------------------------------------------------------------ #
    def _set_status(self, text: str) -> None:
        """状态统一写到窗口底部状态栏（归属对话页，切走再切回仍可见）。"""
        self.state.page_status("chat", text)

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
            self._set_status(
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
        index = self.model_combo.findText(text)
        self.model_combo.blockSignals(True)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        elif text.strip():
            self.model_combo.addItem(text)
            self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
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
            self._set_status("自定义地址：脱离已登记服务，直接按下方地址与模型接入")
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
            self._set_status(f"已选择服务 {state.name}")
        else:
            self.service_tag.set_status("已退出", "danger")
            self._set_status(f"服务 {state.name} 的进程已退出：去「运行」页重新启动，或点「刷新服务列表」")

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

    def _select_conversation(self, row: int) -> None:
        if row < 0 or row == self._active:
            return
        if self._running:
            self._refresh_history()
            self._set_status("正在生成，停止后才能切换对话")
            return
        self._active = row
        self._render_conversation()
        self._restore_session_form()
        self._set_status("已切换对话")
        self._persist_history()

    def _render_conversation(self) -> None:
        self.transcript.clear()
        for kind, text in self._conversation().blocks:
            if kind == "image":
                self._insert_image(Path(text))
            else:
                self._insert_text(text, kind)

    def delete_conversation(self) -> None:
        """删除当前选中的对话；删掉最后一个时回到空白新对话。"""
        if self._running:
            self._set_status("正在生成，停止后才能清理对话")
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
        self._set_status("已删除当前对话")
        self._persist_history()

    def clear_conversations(self) -> None:
        """清空全部对话记录（需确认）；完成后回到空白新对话。"""
        if self._running:
            self._set_status("正在生成，停止后才能清理对话")
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
        self._set_status("已清空全部对话")
        self._persist_history()

    def new_conversation(self) -> None:
        """新建一个空白对话；空对话不重复创建。"""
        if self._running:
            self._set_status("正在生成，停止后才能新建对话")
            return
        current = self._conversation()
        if not current.blocks and current.session is None:
            self._render_conversation()
            self._set_status("已开始新对话")
            return
        self._conversations.append(_Conversation())
        self._active = len(self._conversations) - 1
        self._refresh_history()
        self.transcript.clear()
        self._set_status("已开始新对话")
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
        """发送一轮提问：勾选「画图」时按画图配置出图，否则流式对话。"""
        if self._running:
            return
        prompt = self.input_edit.toPlainText().strip()
        if not prompt:
            self._set_status("提示词不能为空")
            return
        if self.paint_toggle.isChecked():
            self._send_paint(prompt)
            return
        try:
            endpoint = endpoint_from_form(self.url_edit.text(), self.api_key_edit.text(), self.model_edit.text())
            max_tokens = int(self.max_tokens_edit.text().strip() or "2048")
            temperature = float(self.temperature_edit.text().strip() or "0.7")
            top_p = 0.9
        except (ServeError, ValueError) as exc:
            self._set_status(f"参数错误：{exc}")
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
        self._begin_generation("正在生成…")
        submit(
            conversation.session.ask,
            prompt,
            on_delta=self._bridge.delta.emit,
            on_reasoning=self._bridge.reasoning.emit,
            on_done=self._bridge.done.emit,
            on_error=self._bridge.failed.emit,
        )

    # ------------------------------------------------------------------ #
    # 画图
    # ------------------------------------------------------------------ #
    def paint_settings(self) -> dict[str, Any]:
        """完整画图配置：运行页的引擎 / 模型设置 + 对话页的采样 / 输出 + 启用标记。"""
        settings = parse_paint_settings(getattr(self.state.settings, "paint", None))
        section = getattr(self, "paint_section", None)
        if section is not None:
            settings.update(section.settings())
        settings["enabled"] = (
            self.paint_toggle.isChecked() if hasattr(self, "paint_toggle") else bool(settings.get("enabled"))
        )
        self._paint_settings = settings
        return settings

    def _on_chat_toggled(self, checked: bool) -> None:
        """「对话」胶囊：与「画图」互斥，始终保留一个模式。"""
        if checked:
            if self.paint_toggle.isChecked():
                self.paint_toggle.setChecked(False)
        elif not self.paint_toggle.isChecked():
            self.chat_toggle.setChecked(True)

    def _on_paint_toggled(self, checked: bool) -> None:
        """「画图」胶囊：切换输入提示并持久化启用状态。"""
        if checked:
            if self.chat_toggle.isChecked():
                self.chat_toggle.setChecked(False)
        elif not self.chat_toggle.isChecked():
            # 两个都不选中时补回「对话」，避免模式为空。
            self.chat_toggle.setChecked(True)
        self._paint_settings = self.paint_settings()
        self._sync_paint_placeholder()
        self._persist_paint()
        self._set_status("已启用画图：发送提示词即生成图片" if checked else "已关闭画图：发送即对话")

    def _sync_paint_placeholder(self) -> None:
        if self.paint_toggle.isChecked():
            self.input_edit.setPlaceholderText("输入画图提示词，Ctrl+Enter 生成图片")
        else:
            self.input_edit.setPlaceholderText("输入问题，Ctrl+Enter 发送")

    def _on_paint_settings_changed(self) -> None:
        """配置弹窗里的画图采样参数变化：延迟写盘，避免逐字符 IO。"""
        self._paint_settings = self.paint_settings()
        self._paint_save_timer.start()

    def _persist_paint(self) -> None:
        self.state.settings.paint = dict(self._paint_settings)
        self.state.persist()

    def _send_paint(self, prompt: str) -> None:
        """用当前画图配置出图，结果作为图片消息追加到当前对话。"""
        settings = self.paint_settings()
        try:
            _llmpaint.validate_components(components_from_settings(settings))
            _llmpaint.validate_request(request_from_settings(settings))
        except ServeError as exc:
            self._append_text(f"[画图失败] {exc}\n\n", "error")
            self._set_status(f"画图参数错误：{exc}")
            return
        self._paint_settings = settings
        self._persist_paint()
        self.input_edit.clear()
        self._set_title_from_prompt(prompt)
        self._append_text(f"你：{prompt}\n", "user")
        self._begin_generation(f"正在生成图片…（{settings_summary(settings)}）")
        self.state.log(f"画图请求：{prompt[:60]}")
        submit_daemon(
            generate_images,
            settings,
            on_done=self._paint_done,
            on_error=self._paint_failed,
            on_finished=self._paint_finished,
        )

    def _paint_done(self, result: tuple[list[Path], bool]) -> None:
        paths, _started = result
        self._append_text("助手（图片）：\n", "assistant")
        for path in paths:
            self._append_image(path)
        self._append_text("\n", "assistant")
        self._set_status(f"已生成 {len(paths)} 张 · {paths[0].parent}")
        self.state.log("画图完成：" + "、".join(str(path) for path in paths))
        self._persist_history()

    def _paint_failed(self, message: str) -> None:
        self._append_text(f"\n[画图失败] {message}\n\n", "error")
        self._set_status("画图失败：" + message.splitlines()[0])
        self.state.log(f"画图失败：{message}")
        self._persist_history()

    def _paint_finished(self) -> None:
        self._finish_running()

    def _on_send_or_stop(self) -> None:
        """同一个按钮：空闲时发送，生成中变为停止。"""
        if self._running:
            self.stop()
        else:
            self.send()

    def _sync_send_button(self) -> None:
        """把发送 / 停止状态同步到同一个图标按钮。"""
        if self._running:
            self.send_button.setObjectName("ChatStop")
            _set_icon(self.send_button, "stop", tone="danger")
            self.send_button.setToolTip("停止生成")
            self.send_button.setAccessibleName("停止生成")
        else:
            self.send_button.setObjectName("ChatSend")
            _set_icon(self.send_button, "send", tone="accent")
            self.send_button.setToolTip("发送（Ctrl+Enter）")
            self.send_button.setAccessibleName("发送")
        self.send_button.style().unpolish(self.send_button)
        self.send_button.style().polish(self.send_button)

    def stop(self) -> None:
        """停止当前流式生成。"""
        session = self._conversation().session
        if session is not None:
            session.cancel()
        self._set_status("正在停止…")

    def _begin_generation(self, status: str) -> None:
        """进入生成状态：锁住发送 / 历史操作，避免中途切换对话。"""
        self._running = True
        self._sync_send_button()
        self.history_list.setEnabled(False)
        self.new_chat_button.setEnabled(False)
        self.delete_chat_button.setEnabled(False)
        self.clear_chats_button.setEnabled(False)
        self._set_status(status)

    def _finish_running(self) -> None:
        self._running = False
        self._sync_send_button()
        self.history_list.setEnabled(True)
        self.new_chat_button.setEnabled(True)
        self.delete_chat_button.setEnabled(True)
        self.clear_chats_button.setEnabled(True)

    def _on_done(self, _reply: str) -> None:
        conversation = self._conversation()
        if conversation.assistant_started or conversation.reasoning_started:
            self._append_text("\n\n", "assistant")
        self._finish_running()
        self._set_status("生成完成")
        self._persist_history()

    def _on_failed(self, message: str) -> None:
        self._append_text(f"\n[失败] {message}\n\n", "error")
        self._finish_running()
        self._set_status("生成失败：" + message)
        self._persist_history()

    def reset(self) -> None:
        """清空当前对话并开一个新的空对话。"""
        self.new_conversation()

    def is_running(self) -> bool:
        """是否有生成在进行中。"""
        return self._running

    def toggle_sidebar(self) -> None:
        """收起 / 展开对话列表：收起后正文占满宽度，只留一个小展开键。"""
        if self._sidebar_collapsed:
            self._sidebar_collapsed = False
            self.sidebar.setVisible(True)
            self._splitter.setSizes([self._sidebar_width, max(400, self.width() - self._sidebar_width)])
        else:
            sizes = self._splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = min(max(sizes[0], 120), 480)
            self._sidebar_collapsed = True
            self.sidebar.setVisible(False)
        self._sync_sidebar_toggle()
        self.state.settings.chat_sidebar_width = self._sidebar_width
        self.state.settings.chat_sidebar_collapsed = self._sidebar_collapsed
        self.state.persist()

    def _sync_sidebar_toggle(self) -> None:
        """展开 / 收起键的图标与提示跟随状态。"""
        if self._sidebar_collapsed:
            _set_icon(self.sidebar_toggle, "chevron-right", tone="muted")
            self.sidebar_toggle.setToolTip("展开对话列表")
            self.sidebar_toggle.setAccessibleName("展开对话列表")
        else:
            _set_icon(self.sidebar_toggle, "chevron-left", tone="muted")
            self.sidebar_toggle.setToolTip("收起对话列表")
            self.sidebar_toggle.setAccessibleName("收起对话列表")

    def close(self) -> None:
        """窗口关闭时取消进行中的生成，并把最新对话历史落盘。"""
        for conversation in self._conversations:
            if conversation.session is not None:
                conversation.session.cancel()
        if not self._sidebar_collapsed:
            sizes = self._splitter.sizes()
            if sizes and sizes[0] > 0:
                self._sidebar_width = min(max(sizes[0], 120), 480)
        self.state.settings.chat_sidebar_width = self._sidebar_width
        self.state.settings.chat_sidebar_collapsed = self._sidebar_collapsed
        self._persist_paint()
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

    def _append_image(self, path: Path) -> None:
        """追加一条图片消息（历史里只存路径，重开对话时按路径重绘）。"""
        self._conversation().blocks.append(("image", str(path)))
        self._insert_image(path)

    def _insert_image(self, path: Path) -> None:
        cursor = self.transcript.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not path.is_file():
            cursor.insertText(f"[图片已不存在] {path}\n", self._format("error"))
        else:
            image = QTextImageFormat()
            image.setName(str(path))
            image.setWidth(320)
            cursor.insertImage(image)
            cursor.insertText("\n", self._format("assistant"))
        self.transcript.setTextCursor(cursor)
        self.transcript.ensureCursorVisible()

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
    # 复制
    # ------------------------------------------------------------------ #
    def copy_transcript(self) -> None:
        """复制当前对话正文到剪贴板。"""
        text = self.transcript.toPlainText()
        if not text.strip():
            self._set_status("当前对话为空，没有可复制的内容")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            self._set_status("已复制当前对话内容")
