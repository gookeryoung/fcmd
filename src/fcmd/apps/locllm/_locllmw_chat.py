"""locllmw「对话」页：整合原 llmchatw 的多轮流式对话。

服务下拉框读取 ``llmenv`` 登记的服务并回填地址 / 模型 / API Key，也可直接填
任意 OpenAI 兼容地址；流式回复在后台线程读取，思考型模型的 ``reasoning_content``
以灰色单独展示。对话状态保存在页面控件中，切页不丢失；关闭窗口时取消进行中的
生成。原独立工具 ``llmchatw`` 已移除，入口统一为 ``locllmw`` 的对话页。
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fcmd.apps.locllm import llmenv
from fcmd.apps.locllm.llmbase import (
    DEFAULT_ALIAS,
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    Endpoint,
    ServeError,
    ServeState,
    http_base_url,
    human_size,
)
from fcmd.apps.locllm.llmchat import Attachment, ChatSession
from fcmd.apps.locllm.llmserve import process_alive
from fcmd.gui import theme as _guitheme

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

__all__: list[str] = []

_DEFAULT_URL = http_base_url(DEFAULT_HOST, DEFAULT_PORT)
_CUSTOM = "自定义"
_UNAVAILABLE = "不可用"
#: 单条消息允许的图片数量上限
_MAX_IMAGES = 4


def _state_available(state: ServeState) -> bool:
    """服务状态对应的进程仍存活即视为可用。"""
    return process_alive(state.pid, state.create_time)


def _first_available(states: Sequence[ServeState]) -> ServeState | None:
    """按登记顺序返回第一个可用服务。"""
    return next((state for state in states if _state_available(state)), None)


class _LocllmWindowChat:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """LocllmWindow 的「对话」页：服务选择、参数配置、流式对话与历史管理。"""

    if TYPE_CHECKING:
        root: Any
        _tk: Any
        _ttk: Any
        _theme: Theme
        _messagebox: Any
        _filedialog: Any
        _closing: bool
        _settings: Any

        def _persist_settings(self) -> None: ...

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_chat_page(self, parent: Any) -> None:
        self._init_chat_state()
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        self._build_chat_header(parent)
        self._build_chat_connection(parent)
        self._build_chat_params(parent)
        self._build_chat_view(parent)
        self._build_chat_input(parent)
        status = self._ttk.Label(
            parent,
            textvariable=self._chat_status_var,
            anchor="w",
            relief="sunken",
            padding=(self._theme.spacing.sm, self._theme.spacing.xs),
        )
        status.grid(row=5, column=0, sticky="ew", pady=(self._theme.spacing.sm, 0))
        self._chat_settings_open = bool(self._settings.chat_settings_open)
        self._apply_chat_settings_visibility()
        self._chat_refresh_services()

    def _build_chat_header(self, parent: Any) -> None:
        """顶部工具条：始终可见的「设置」入口，一键展开 / 收起连接与生成参数。"""
        ttk = self._ttk
        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        self._chat_settings_btn = ttk.Button(bar, text="设置", width=10, command=self._toggle_chat_settings)
        self._chat_settings_btn.pack(side="left")
        ttk.Label(bar, text="连接与生成参数", style="Hint.TLabel").pack(side="left", padx=(self._theme.spacing.sm, 0))

    def _toggle_chat_settings(self) -> None:
        """展开 / 收起设置区，并立即持久化，下次进入保持同一状态。"""
        self._chat_settings_open = not self._chat_settings_open
        self._settings.chat_settings_open = self._chat_settings_open
        self._apply_chat_settings_visibility()
        self._persist_settings()

    def _apply_chat_settings_visibility(self) -> None:
        """收起时用 grid_remove 让出空间，而不是留下空白。"""
        if self._chat_settings_open:
            self._chat_connection_box.grid()
            self._chat_params_box.grid()
            self._chat_settings_btn.configure(text="设置 ▾")
        else:
            self._chat_connection_box.grid_remove()
            self._chat_params_box.grid_remove()
            self._chat_settings_btn.configure(text="设置 ▸")

    def _init_chat_state(self) -> None:
        tk = self._tk
        self._chat_session: ChatSession | None = None
        self._chat_thread: threading.Thread | None = None
        self._chat_messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._chat_running = False
        self._chat_manual_choice = False
        self._chat_saw_reasoning = False
        self._chat_answer_started = False
        self._chat_states: list[ServeState] = []
        self._chat_service_labels: dict[str, str] = {}
        self._chat_attachments: list[Attachment] = []
        self._chat_service_var = tk.StringVar(value=_CUSTOM)
        self._chat_url_var = tk.StringVar(value=_DEFAULT_URL)
        self._chat_model_var = tk.StringVar(value=DEFAULT_ALIAS)
        self._chat_api_key_var = tk.StringVar(value=DEFAULT_API_KEY)
        self._chat_system_var = tk.StringVar()
        self._chat_max_tokens_var = tk.StringVar(value="2048")
        self._chat_temperature_var = tk.StringVar(value="0.7")
        self._chat_top_p_var = tk.StringVar(value="0.9")
        self._chat_attach_var = tk.StringVar(value="未选择图片")
        self._chat_status_var = tk.StringVar(value=_UNAVAILABLE)

    def _on_page_changed(self, key: str) -> None:
        """切到对话页时刷新可用服务，避免启动服务后列表过期。"""
        if key == "chat":
            self._chat_refresh_services()

    def _close_chat(self) -> None:
        """关窗时取消进行中的生成并等待后台线程收尾。"""
        if self._chat_session is not None:
            self._chat_session.cancel()
        thread = self._chat_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _build_chat_connection(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="连接", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=1, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        self._chat_connection_box = box
        box.columnconfigure(1, weight=3)
        box.columnconfigure(4, weight=2)

        ttk.Label(box, text="服务").grid(
            row=0, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        self._chat_service_box = ttk.Combobox(box, textvariable=self._chat_service_var, state="readonly", width=22)
        self._chat_service_box.grid(
            row=0, column=1, sticky="ew", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        self._chat_service_box.bind("<<ComboboxSelected>>", self._chat_on_service_selected)
        self._chat_refresh_btn = ttk.Button(box, text="刷新服务列表", command=self._chat_refresh_services, width=12)
        self._chat_refresh_btn.grid(
            row=0,
            column=2,
            sticky="w",
            padx=(0, self._theme.spacing.lg + self._theme.spacing.xxs),
            pady=self._theme.spacing.xxs,
        )

        ttk.Label(box, text="模型").grid(
            row=0, column=3, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._chat_model_var).grid(
            row=0, column=4, sticky="ew", pady=self._theme.spacing.xxs
        )

        ttk.Label(box, text="地址").grid(
            row=1, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._chat_url_var).grid(
            row=1, column=1, columnspan=4, sticky="ew", pady=self._theme.spacing.xxs
        )

        ttk.Label(box, text="API Key").grid(
            row=2, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._chat_api_key_var, show="*").grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(0, self._theme.spacing.lg + self._theme.spacing.xxs),
            pady=self._theme.spacing.xxs,
        )
        ttk.Label(box, text="系统提示").grid(
            row=2, column=3, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._chat_system_var).grid(
            row=2, column=4, sticky="ew", pady=self._theme.spacing.xxs
        )

    def _build_chat_params(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="生成参数", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=2, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        self._chat_params_box = box
        box.columnconfigure(6, weight=1)

        ttk.Label(box, text="max_tokens").grid(row=0, column=0, sticky="e", padx=(0, self._theme.spacing.sm))
        ttk.Spinbox(box, from_=1, to=131072, increment=64, textvariable=self._chat_max_tokens_var, width=9).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(box, text="temperature").grid(
            row=0, column=2, sticky="e", padx=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.sm)
        )
        ttk.Spinbox(box, from_=0.0, to=2.0, increment=0.1, textvariable=self._chat_temperature_var, width=9).grid(
            row=0, column=3, sticky="w"
        )
        ttk.Label(box, text="top_p").grid(
            row=0, column=4, sticky="e", padx=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.sm)
        )
        ttk.Spinbox(box, from_=0.0, to=1.0, increment=0.05, textvariable=self._chat_top_p_var, width=9).grid(
            row=0, column=5, sticky="w"
        )

        self._chat_clear_btn = ttk.Button(box, text="清空对话", command=self._chat_clear, width=10)
        self._chat_clear_btn.grid(row=0, column=7, sticky="e")

    def _build_chat_view(self, parent: Any) -> None:
        box = self._ttk.LabelFrame(parent, text="对话", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=3, column=0, sticky="nsew", pady=(0, self._theme.spacing.sm))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self._chat_text = self._tk.Text(
            box,
            wrap="word",
            state="disabled",
            height=12,
            padx=self._theme.spacing.md,
            pady=self._theme.spacing.sm,
            font=_guitheme.BODY_FONT,
        )
        scrollbar = self._ttk.Scrollbar(box, orient="vertical", command=self._chat_text.yview)
        self._chat_text.configure(yscrollcommand=scrollbar.set)
        self._chat_text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(self._theme.spacing.sm, 0))
        palette = self._theme.palette
        self._chat_text.tag_configure("user", foreground=palette.accent, spacing1=10, spacing3=2)
        self._chat_text.tag_configure("ai", foreground=palette.success, spacing1=2, spacing3=10)
        self._chat_text.tag_configure("reason", foreground=palette.muted, spacing3=4)
        self._chat_text.tag_configure("info", foreground=palette.muted, spacing1=4, spacing3=4)
        self._chat_text.tag_configure("error", foreground=palette.danger, spacing3=8)

    def _build_chat_input(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="输入", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=4, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(box)
        toolbar.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, self._theme.spacing.sm))
        self._chat_image_btn = ttk.Button(toolbar, text="选择图片", command=self._chat_pick_images, width=10)
        self._chat_image_btn.pack(side="left")
        self._chat_image_clear_btn = ttk.Button(toolbar, text="移除图片", command=self._chat_clear_images, width=10)
        self._chat_image_clear_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        ttk.Label(toolbar, textvariable=self._chat_attach_var, foreground=self._theme.palette.muted).pack(
            side="left", padx=(self._theme.spacing.md, 0)
        )

        self._chat_input = self._tk.Text(
            box,
            height=4,
            wrap="word",
            padx=self._theme.spacing.sm,
            pady=self._theme.spacing.sm,
            undo=True,
            font=_guitheme.BODY_FONT,
        )
        self._chat_input.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self._chat_input.bind("<Control-Return>", self._chat_on_send_shortcut)

        ttk.Label(box, text="Ctrl+Enter 发送 · Enter 换行", foreground=self._theme.palette.muted).grid(
            row=2, column=0, sticky="w", pady=(self._theme.spacing.sm, 0)
        )
        self._chat_stop_btn = ttk.Button(
            box, text="停止", command=self._chat_stop, state="disabled", width=10, style="Danger.TButton"
        )
        self._chat_stop_btn.grid(
            row=2, column=1, sticky="e", padx=(self._theme.spacing.sm, 0), pady=(self._theme.spacing.sm, 0)
        )
        self._chat_send_btn = ttk.Button(box, text="发送", command=self._chat_send, width=10, style="Accent.TButton")
        self._chat_send_btn.grid(
            row=2, column=2, sticky="e", padx=(self._theme.spacing.sm, 0), pady=(self._theme.spacing.sm, 0)
        )
        self._chat_input.focus_set()

    # ------------------------------------------------------------------ #
    # 图片附件
    # ------------------------------------------------------------------ #
    def _chat_pick_images(self) -> None:
        """选择图片作为下一条消息的附件（单轮最多 4 张）。"""
        paths = self._filedialog.askopenfilenames(
            title="选择图片（png / jpeg / webp）",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.webp"), ("全部文件", "*.*")],
        )
        if not paths:
            return
        for raw in paths:
            if len(self._chat_attachments) >= _MAX_IMAGES:
                self._chat_status_var.set(f"单条消息最多 {_MAX_IMAGES} 张图片")
                break
            try:
                self._chat_attachments.append(Attachment.image(raw))
            except ServeError as exc:
                self._messagebox.showwarning("无法添加图片", str(exc))
        self._chat_refresh_attach_label()

    def _chat_clear_images(self) -> None:
        """清空已选图片。"""
        self._chat_attachments.clear()
        self._chat_refresh_attach_label()

    def _chat_refresh_attach_label(self) -> None:
        """刷新附件计数与总体积提示。"""
        if not self._chat_attachments:
            self._chat_attach_var.set("未选择图片")
            return
        total = sum(item.size for item in self._chat_attachments)
        self._chat_attach_var.set(f"已选 {len(self._chat_attachments)} 张图片 · {human_size(total)}")

    def _chat_on_send_shortcut(self, _event: Any) -> str:
        """Ctrl+Enter 发送并阻止插入换行。"""
        self._chat_send()
        return "break"

    # ------------------------------------------------------------------ #
    # 服务
    # ------------------------------------------------------------------ #
    def _chat_service_name(self) -> str:
        """下拉框显示文案 → 服务名；自定义地址返回 ``_CUSTOM``。"""
        return self._chat_service_labels.get(self._chat_service_var.get(), _CUSTOM)

    def _chat_refresh_services(self) -> None:
        """重新读取 llmenv 登记的服务并刷新下拉框，默认选中第一个可用服务。"""
        try:
            self._chat_states = llmenv.list_states()
        except OSError as exc:
            self._chat_states = []
            self._chat_append("error", f"读取服务状态失败: {exc}\n")
        labels = [
            _CUSTOM,
            *[f"{state.name}（{'运行中' if _state_available(state) else '已退出'}）" for state in self._chat_states],
        ]
        self._chat_service_labels = {
            label: state.name for label, state in zip(labels[1:], self._chat_states, strict=True)
        }
        self._chat_service_box.configure(values=labels)

        current = self._chat_service_var.get()
        if current not in labels:
            current = _CUSTOM
            self._chat_service_var.set(_CUSTOM)
        if current == _CUSTOM and not self._chat_manual_choice:
            first = _first_available(self._chat_states)
            if first is not None:
                current = next(label for label, name in self._chat_service_labels.items() if name == first.name)
                self._chat_service_var.set(current)
            elif self._chat_states:
                # 都不可用时也选中第一个服务，让状态栏说明“进程已退出”而不是笼统的未选择。
                current = labels[1]
                self._chat_service_var.set(current)

        state = self._chat_selected_state()
        if state is None:
            self._chat_status_var.set(
                "没有已登记的服务：去「运行」页启动，或点「刷新服务列表」"
                if not self._chat_states
                else "未选择已登记服务：可直接填写下方地址接入其它 OpenAI 兼容服务"
            )
            return
        self._chat_apply_state(state)
        if _state_available(state):
            self._chat_status_var.set(f"已选择服务 {state.name}（运行中）")
        else:
            self._chat_status_var.set(f"服务 {state.name} 的进程已退出：去「运行」页重新启动，或点「刷新服务列表」")

    def _chat_selected_state(self) -> ServeState | None:
        name = self._chat_service_name()
        return next((item for item in self._chat_states if item.name == name), None)

    def _chat_on_service_selected(self, _event: Any = None) -> None:
        """选中已登记服务后回填地址 / 模型 / API Key，并提示是否可用。"""
        self._chat_manual_choice = True
        state = self._chat_selected_state()
        if state is None:
            self._chat_status_var.set("自定义地址：脱离已登记服务，直接按下方地址与模型接入")
            return
        self._chat_apply_state(state)
        if _state_available(state):
            self._chat_append("info", f"已切换到服务 {state.name}：{state.endpoint.base_url}\n")
            self._chat_status_var.set(f"已选择服务 {state.name}（运行中）")
        else:
            self._chat_append("error", f"服务 {state.name} 不可用：{state.endpoint.base_url}\n")
            self._chat_status_var.set(f"服务 {state.name} 的进程已退出：去「运行」页重新启动，或点「刷新服务列表」")

    def _chat_apply_state(self, state: ServeState) -> None:
        """把服务状态的地址 / 模型 / API Key 回填到表单。"""
        endpoint = state.endpoint
        self._chat_url_var.set(endpoint.base_url)
        self._chat_model_var.set(endpoint.model)
        self._chat_api_key_var.set(endpoint.api_key)

    def _chat_endpoint(self) -> Endpoint:
        """把表单值转换为接入点。"""
        url = self._chat_url_var.get().strip().rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        if not url.startswith(("http://", "https://")):
            raise ServeError("服务地址需以 http:// 或 https:// 开头")
        model = self._chat_model_var.get().strip() or DEFAULT_ALIAS
        return Endpoint(url, self._chat_api_key_var.get().strip(), model)

    # ------------------------------------------------------------------ #
    # 对话
    # ------------------------------------------------------------------ #
    def _chat_make_session(self) -> ChatSession:
        """按当前表单构造 / 复用会话（地址或系统提示变化时重建历史）。"""
        endpoint = self._chat_endpoint()
        system = self._chat_system_var.get().strip() or None
        session = self._chat_session
        if session is None or session.endpoint != endpoint or session.system != system:
            session = ChatSession(endpoint, system=system)
            self._chat_session = session
        session.max_tokens = self._chat_int_value(self._chat_max_tokens_var, "max_tokens")
        session.temperature = self._chat_float_value(self._chat_temperature_var, "temperature")
        session.top_p = self._chat_float_value(self._chat_top_p_var, "top_p", upper=1.0)
        return session

    def _chat_send(self) -> None:
        """把输入框内容发送给模型，并在后台线程读取流式回复。"""
        if self._chat_running:
            return
        text = self._chat_input.get("1.0", "end").strip()
        attachments = tuple(self._chat_attachments)
        if not text and not attachments:
            self._chat_status_var.set("请输入内容或选择图片")
            return
        try:
            session = self._chat_make_session()
        except ServeError as exc:
            self._chat_append("error", f"错误: {exc}\n")
            self._chat_status_var.set(str(exc))
            return

        if attachments and not self._chat_current_vision():
            self._chat_append("info", "提示: 当前服务启动时未加载 mmproj（vision=false），图片可能被忽略。\n")
        suffix = f"（图片 x{len(attachments)}）" if attachments else ""
        self._chat_append("user", f"你: {text or '（仅图片）'}{suffix}\n")
        self._chat_input.delete("1.0", "end")
        self._chat_saw_reasoning = False
        self._chat_answer_started = False
        self._chat_running = True
        self._chat_set_running(True)
        self._chat_status_var.set("思考中…")
        self._chat_messages = queue.Queue()
        self._chat_thread = threading.Thread(target=self._chat_work, args=(session, text, attachments), daemon=True)
        self._chat_thread.start()
        self.root.after(60, self._chat_poll)

    def _chat_current_vision(self) -> bool:
        """当前选中服务是否以视觉模式启动（自定义地址无法判断，按 True 处理）。"""
        state = self._chat_selected_state()
        if state is None:
            return True
        return state.vision

    def _chat_work(self, session: ChatSession, prompt: str, attachments: Sequence[Attachment]) -> None:
        """后台线程：执行一轮对话，把思考 / 回答增量与结果放入队列。"""
        try:
            reply = session.ask(
                prompt,
                on_delta=lambda delta: self._chat_messages.put(("delta", delta)),
                on_reasoning=lambda text: self._chat_messages.put(("reasoning", text)),
                attachments=attachments,
            )
        except (ServeError, OSError, ValueError) as exc:
            self._chat_messages.put(("error", str(exc)))
        except Exception as exc:  # 后台线程兜底，避免界面失去反馈
            self._chat_messages.put(("error", f"{type(exc).__name__}: {exc}"))
        else:
            self._chat_messages.put(("done", reply))

    def _chat_poll(self) -> None:
        """主线程轮询队列，实时渲染思考与回答增量。"""
        if self._closing:
            return
        try:
            while True:
                kind, payload = self._chat_messages.get_nowait()
                if kind == "reasoning":
                    self._chat_render_reasoning(str(payload))
                elif kind == "delta":
                    self._chat_render_answer(str(payload))
                elif kind == "error":
                    self._chat_finish(str(payload), ok=False)
                else:
                    self._chat_finish(str(payload), ok=True)
        except queue.Empty:
            pass
        if self._chat_running:
            self.root.after(60, self._chat_poll)

    def _chat_render_reasoning(self, text: str) -> None:
        """思考增量以灰色显示，并区分思考 / 回答阶段。"""
        if not self._chat_saw_reasoning:
            self._chat_saw_reasoning = True
            self._chat_append("info", "助手 · 思考中…\n")
            self._chat_status_var.set("思考中…")
        self._chat_append("reason", text)

    def _chat_render_answer(self, text: str) -> None:
        """首个回答增量前补「助手: 」标签，思考块与回答之间空一行。"""
        if not self._chat_answer_started:
            self._chat_answer_started = True
            if self._chat_saw_reasoning:
                self._chat_append("ai", "\n")
            self._chat_append("ai", "助手: ")
            self._chat_status_var.set("生成中…")
        self._chat_append("ai", text)

    def _chat_finish(self, reply: str, *, ok: bool) -> None:
        """收尾：恢复按钮状态并补全换行 / 错误提示。"""
        self._chat_running = False
        self._chat_thread = None
        self._chat_set_running(False)
        if ok:
            self._chat_clear_images()
        if not ok:
            self._chat_append("error", f"\n错误: {reply}\n")
            self._chat_status_var.set("失败")
        elif reply:
            if not self._chat_answer_started:
                self._chat_append("ai", "助手: ")
            self._chat_append("ai", "\n")
            self._chat_status_var.set("完成")
        elif self._chat_saw_reasoning:
            self._chat_append("ai", "助手: （仅生成思考内容，未在 max_tokens 内产出回答，请调大 max_tokens）\n")
            self._chat_status_var.set("完成（未产出回答）")
        else:
            self._chat_append("ai", "助手: （无输出）\n")
            self._chat_status_var.set("完成（无输出）")

    def _chat_stop(self) -> None:
        """请求停止当前生成。"""
        if not self._chat_running:
            return
        if self._chat_session is not None:
            self._chat_session.cancel()
        self._chat_status_var.set("正在停止…")

    def _chat_clear(self) -> None:
        """清空聊天区与对话历史。"""
        if self._chat_running:
            self._chat_status_var.set("生成中，请先停止再清空")
            return
        if self._chat_session is not None:
            self._chat_session.reset()
        self._chat_text.configure(state="normal")
        self._chat_text.delete("1.0", "end")
        self._chat_text.configure(state="disabled")
        self._chat_status_var.set("已清空")

    def _chat_set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self._chat_send_btn.configure(state=state)
        self._chat_clear_btn.configure(state=state)
        self._chat_image_btn.configure(state=state)
        self._chat_image_clear_btn.configure(state=state)
        self._chat_stop_btn.configure(state="normal" if running else "disabled")

    def _chat_append(self, tag: str, text: str) -> None:
        """向聊天区追加文本并滚动到底部。"""
        self._chat_text.configure(state="normal")
        self._chat_text.insert("end", text, tag)
        self._chat_text.see("end")
        self._chat_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 参数校验
    # ------------------------------------------------------------------ #
    def _chat_int_value(self, var: Any, label: str) -> int:
        try:
            value = int(var.get().strip())
        except ValueError as exc:
            raise ServeError(f"{label} 必须是整数") from exc
        if value < 1:
            raise ServeError(f"{label} 必须大于 0")
        return value

    def _chat_float_value(self, var: Any, label: str, *, upper: float | None = None) -> float:
        try:
            value = float(var.get().strip())
        except ValueError as exc:
            raise ServeError(f"{label} 必须是数字") from exc
        if value < 0 or (upper is not None and value > upper):
            raise ServeError(f"{label} 超出范围")
        return value
