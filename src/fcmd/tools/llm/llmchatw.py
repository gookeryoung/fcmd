"""llmchatw - OpenAI 兼容服务多轮对话的 Tk 窗口版。

基于 :mod:`fcmd.tools.llm.llmchat` 的 :class:`~fcmd.tools.llm.llmchat.ChatSession`：

* 服务下拉框读取 ``llmenv`` 登记的服务，选中即回填地址 / 模型 / API Key；
  也可直接填任意 OpenAI 兼容地址（如 vLLM、LM Studio）；
* 流式回复在后台线程读取，增量文本实时追加到聊天区，不阻塞界面；思考型模型的
  ``reasoning_content`` 思考过程以灰色单独展示，与最终回答区分；
* 支持停止生成、清空历史、系统提示词与采样参数（max_tokens / temperature / top_p）。

示例
----
    fcmd llmchatw
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.llm import llmenv
from fcmd.tools.llm.llmbase import (
    DEFAULT_ALIAS,
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    Endpoint,
    ServeError,
    ServeState,
    http_base_url,
)
from fcmd.tools.llm.llmchat import ChatSession
from fcmd.tools.llm.llmserve import process_alive

__all__ = [
    "ChatSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
]

_DESCRIPTION = (
    "llmchat 的 Tk 窗口版（多轮流式对话；可选用 llmenv 登记的服务或自定义地址；"
    "窗口位置与大小记忆到 ~/.fcmd/ui/llmchatw.json）"
)
_DEFAULT_URL = http_base_url(DEFAULT_HOST, DEFAULT_PORT)
_CUSTOM = "自定义"
_UNAVAILABLE = "不可用"

MIN_WIDTH = 860
MIN_HEIGHT = 620
DEFAULT_WIDTH = 1040
DEFAULT_HEIGHT = 780


@dataclass
class ChatSettings:
    """窗口持久化设置（当前只有 geometry）。"""

    geometry: str | None = None


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/llmchatw.json``。"""
    return _guiconfig.config_path("llmchatw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> ChatSettings:
    """读取设置；文件缺失、JSON 损坏或 geometry 非法时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = ChatSettings()
    if raw is not None:
        geometry = raw.get("geometry")
        if isinstance(geometry, str) and valid_geometry(geometry):
            settings.geometry = geometry
    return settings


def save_settings(settings: ChatSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings({"geometry": settings.geometry}, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到窗口原默认尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def _state_available(state: ServeState) -> bool:
    """服务状态对应的进程仍存活即视为可用。"""
    return process_alive(state.pid, state.create_time)


def _first_available(states: Sequence[ServeState]) -> ServeState | None:
    """按登记顺序返回第一个可用服务。"""
    return next((state for state in states if _state_available(state)), None)


class ChatWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 聊天窗口：服务选择、参数配置、流式对话与历史管理。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self.root, self._theme = _guiwindow.create_root(
            title="LLM 聊天客户端",
            saved_geometry=self._settings.geometry,
            default_geometry=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._close,
        )

        self._session: ChatSession | None = None
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._running = False
        self._manual_choice = False
        self._saw_reasoning = False
        self._answer_started = False
        self._states: list[ServeState] = []

        self._service_var = tk.StringVar(value=_CUSTOM)
        self._url_var = tk.StringVar(value=_DEFAULT_URL)
        self._model_var = tk.StringVar(value=DEFAULT_ALIAS)
        self._api_key_var = tk.StringVar(value=DEFAULT_API_KEY)
        self._system_var = tk.StringVar()
        self._max_tokens_var = tk.StringVar(value="2048")
        self._temperature_var = tk.StringVar(value="0.7")
        self._top_p_var = tk.StringVar(value="0.9")
        self._status_var = tk.StringVar(value=_UNAVAILABLE)

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_services()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=(self._theme.spacing.md, self._theme.spacing.md))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        self._build_connection(outer)
        self._build_params(outer)
        self._build_chat(outer)
        self._build_input(outer)

        status = ttk.Label(
            outer,
            textvariable=self._status_var,
            anchor="w",
            relief="sunken",
            padding=(self._theme.spacing.sm, self._theme.spacing.xs),
        )
        status.grid(row=4, column=0, sticky="ew", pady=(self._theme.spacing.sm, 0))

    def _build_connection(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="连接", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=0, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        box.columnconfigure(1, weight=3)
        box.columnconfigure(4, weight=2)

        ttk.Label(box, text="服务").grid(
            row=0, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        self._service_box = ttk.Combobox(box, textvariable=self._service_var, state="readonly", width=22)
        self._service_box.grid(
            row=0, column=1, sticky="ew", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        self._service_box.bind("<<ComboboxSelected>>", self._on_service_selected)
        self._refresh_btn = ttk.Button(box, text="刷新", command=self._refresh_services, width=8)
        self._refresh_btn.grid(
            row=0,
            column=2,
            sticky="w",
            padx=(0, self._theme.spacing.lg + self._theme.spacing.xxs),
            pady=self._theme.spacing.xxs,
        )

        ttk.Label(box, text="模型").grid(
            row=0, column=3, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._model_var).grid(row=0, column=4, sticky="ew", pady=self._theme.spacing.xxs)

        ttk.Label(box, text="地址").grid(
            row=1, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._url_var).grid(
            row=1, column=1, columnspan=4, sticky="ew", pady=self._theme.spacing.xxs
        )

        ttk.Label(box, text="API Key").grid(
            row=2, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._api_key_var, show="*").grid(
            row=2,
            column=1,
            sticky="ew",
            padx=(0, self._theme.spacing.lg + self._theme.spacing.xxs),
            pady=self._theme.spacing.xxs,
        )
        ttk.Label(box, text="系统提示").grid(
            row=2, column=3, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Entry(box, textvariable=self._system_var).grid(row=2, column=4, sticky="ew", pady=self._theme.spacing.xxs)

    def _build_params(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="生成参数", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=1, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        box.columnconfigure(6, weight=1)

        ttk.Label(box, text="max_tokens").grid(row=0, column=0, sticky="e", padx=(0, self._theme.spacing.sm))
        ttk.Spinbox(box, from_=1, to=131072, increment=64, textvariable=self._max_tokens_var, width=9).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(box, text="temperature").grid(
            row=0, column=2, sticky="e", padx=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.sm)
        )
        ttk.Spinbox(box, from_=0.0, to=2.0, increment=0.1, textvariable=self._temperature_var, width=9).grid(
            row=0, column=3, sticky="w"
        )
        ttk.Label(box, text="top_p").grid(
            row=0, column=4, sticky="e", padx=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.sm)
        )
        ttk.Spinbox(box, from_=0.0, to=1.0, increment=0.05, textvariable=self._top_p_var, width=9).grid(
            row=0, column=5, sticky="w"
        )

        self._clear_btn = ttk.Button(box, text="清空对话", command=self._clear, width=10)
        self._clear_btn.grid(row=0, column=7, sticky="e")

    def _build_chat(self, parent: Any) -> None:
        box = self._ttk.LabelFrame(parent, text="对话", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=2, column=0, sticky="nsew", pady=(0, self._theme.spacing.sm))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self._chat = self._tk.Text(
            box,
            wrap="word",
            state="disabled",
            height=18,
            padx=self._theme.spacing.md,
            pady=self._theme.spacing.sm,
            font=_guitheme.BODY_FONT,
        )
        scrollbar = self._ttk.Scrollbar(box, orient="vertical", command=self._chat.yview)
        self._chat.configure(yscrollcommand=scrollbar.set)
        self._chat.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(self._theme.spacing.sm, 0))
        palette = self._theme.palette
        self._chat.tag_configure("user", foreground=palette.accent, spacing1=10, spacing3=2)
        self._chat.tag_configure("ai", foreground=palette.success, spacing1=2, spacing3=10)
        self._chat.tag_configure("reason", foreground=palette.muted, spacing3=4)
        self._chat.tag_configure("info", foreground=palette.muted, spacing1=4, spacing3=4)
        self._chat.tag_configure("error", foreground=palette.danger, spacing3=8)

    def _build_input(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="输入", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=3, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)

        self._input_text = self._tk.Text(
            box,
            height=4,
            wrap="word",
            padx=self._theme.spacing.sm,
            pady=self._theme.spacing.sm,
            undo=True,
            font=_guitheme.BODY_FONT,
        )
        self._input_text.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self._input_text.bind("<Control-Return>", self._on_send_shortcut)

        ttk.Label(box, text="Ctrl+Enter 发送 · Enter 换行", foreground=self._theme.palette.muted).grid(
            row=1, column=0, sticky="w", pady=(self._theme.spacing.sm, 0)
        )
        self._stop_btn = ttk.Button(
            box, text="停止", command=self._stop, state="disabled", width=10, style="Danger.TButton"
        )
        self._stop_btn.grid(
            row=1, column=1, sticky="e", padx=(self._theme.spacing.sm, 0), pady=(self._theme.spacing.sm, 0)
        )
        self._send_btn = ttk.Button(box, text="发送", command=self._send, width=10, style="Accent.TButton")
        self._send_btn.grid(
            row=1, column=2, sticky="e", padx=(self._theme.spacing.sm, 0), pady=(self._theme.spacing.sm, 0)
        )
        self._input_text.focus_set()

    def _on_send_shortcut(self, _event: Any) -> str:
        """Ctrl+Enter 发送并阻止插入换行。"""
        self._send()
        return "break"

    # ------------------------------------------------------------------ #
    # 服务
    # ------------------------------------------------------------------ #
    def _refresh_services(self) -> None:
        """重新读取 llmenv 登记的服务并刷新下拉框，默认选中第一个可用服务。"""
        try:
            self._states = llmenv.list_states()
        except OSError as exc:
            self._states = []
            self._append("error", f"读取服务状态失败: {exc}\n")
        names = [state.name for state in self._states]
        self._service_box.configure(values=[_CUSTOM, *names])

        current = self._service_var.get()
        if current not in [_CUSTOM, *names]:
            current = _CUSTOM
            self._service_var.set(_CUSTOM)
        if current == _CUSTOM and not self._manual_choice:
            first = _first_available(self._states)
            if first is not None:
                current = first.name
                self._service_var.set(current)

        state = next((item for item in self._states if item.name == current), None)
        if state is None:
            self._status_var.set(_UNAVAILABLE)
            return
        self._apply_state(state)
        if _state_available(state):
            self._status_var.set(f"已选择服务 {state.name}")
        else:
            self._status_var.set(_UNAVAILABLE)

    def _on_service_selected(self, _event: Any = None) -> None:
        """选中已登记服务后回填地址 / 模型 / API Key，并提示是否可用。"""
        self._manual_choice = True
        name = self._service_var.get()
        state = next((item for item in self._states if item.name == name), None)
        if state is None:
            self._status_var.set(_CUSTOM)
            return
        self._apply_state(state)
        if _state_available(state):
            self._append("info", f"已切换到服务 {state.name}：{state.endpoint.base_url}\n")
            self._status_var.set(f"已选择服务 {state.name}")
        else:
            self._append("error", f"服务 {state.name} 不可用：{state.endpoint.base_url}\n")
            self._status_var.set(_UNAVAILABLE)

    def _apply_state(self, state: ServeState) -> None:
        """把服务状态的地址 / 模型 / API Key 回填到表单。"""
        endpoint = state.endpoint
        self._url_var.set(endpoint.base_url)
        self._model_var.set(endpoint.model)
        self._api_key_var.set(endpoint.api_key)

    def _endpoint(self) -> Endpoint:
        """把表单值转换为接入点。"""
        url = self._url_var.get().strip().rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        if not url.startswith(("http://", "https://")):
            raise ServeError("服务地址需以 http:// 或 https:// 开头")
        model = self._model_var.get().strip() or DEFAULT_ALIAS
        return Endpoint(url, self._api_key_var.get().strip(), model)

    # ------------------------------------------------------------------ #
    # 对话
    # ------------------------------------------------------------------ #
    def _make_session(self) -> ChatSession:
        """按当前表单构造 / 复用会话（地址或系统提示变化时重建历史）。"""
        endpoint = self._endpoint()
        system = self._system_var.get().strip() or None
        session = self._session
        if session is None or session.endpoint != endpoint or session.system != system:
            session = ChatSession(endpoint, system=system)
            self._session = session
        session.max_tokens = self._int_value(self._max_tokens_var, "max_tokens")
        session.temperature = self._float_value(self._temperature_var, "temperature")
        session.top_p = self._float_value(self._top_p_var, "top_p", upper=1.0)
        return session

    def _send(self) -> None:
        """把输入框内容发送给模型，并在后台线程读取流式回复。"""
        if self._running:
            return
        text = self._input_text.get("1.0", "end").strip()
        if not text:
            self._status_var.set("请输入内容")
            return
        try:
            session = self._make_session()
        except ServeError as exc:
            self._append("error", f"错误: {exc}\n")
            self._status_var.set(str(exc))
            return

        self._append("user", f"你: {text}\n")
        self._input_text.delete("1.0", "end")
        self._saw_reasoning = False
        self._answer_started = False
        self._running = True
        self._set_running(True)
        self._status_var.set("思考中…")
        self._messages = queue.Queue()
        self._thread = threading.Thread(target=self._work, args=(session, text), daemon=True)
        self._thread.start()
        self.root.after(60, self._poll)

    def _work(self, session: ChatSession, prompt: str) -> None:
        """后台线程：执行一轮对话，把思考 / 回答增量与结果放入队列。"""
        try:
            reply = session.ask(
                prompt,
                on_delta=lambda delta: self._messages.put(("delta", delta)),
                on_reasoning=lambda text: self._messages.put(("reasoning", text)),
            )
        except (ServeError, OSError, ValueError) as exc:
            self._messages.put(("error", str(exc)))
        except Exception as exc:  # 后台线程兜底，避免界面失去反馈
            self._messages.put(("error", f"{type(exc).__name__}: {exc}"))
        else:
            self._messages.put(("done", reply))

    def _poll(self) -> None:
        """主线程轮询队列，实时渲染思考与回答增量。"""
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "reasoning":
                    self._render_reasoning(str(payload))
                elif kind == "delta":
                    self._render_answer(str(payload))
                elif kind == "error":
                    self._finish(str(payload), ok=False)
                else:
                    self._finish(str(payload), ok=True)
        except queue.Empty:
            pass
        if self._running:
            self.root.after(60, self._poll)

    def _render_reasoning(self, text: str) -> None:
        """思考增量以灰色显示，并区分思考 / 回答阶段。"""
        if not self._saw_reasoning:
            self._saw_reasoning = True
            self._append("info", "助手 · 思考中…\n")
            self._status_var.set("思考中…")
        self._append("reason", text)

    def _render_answer(self, text: str) -> None:
        """首个回答增量前补「助手: 」标签，思考块与回答之间空一行。"""
        if not self._answer_started:
            self._answer_started = True
            if self._saw_reasoning:
                self._append("ai", "\n")
            self._append("ai", "助手: ")
            self._status_var.set("生成中…")
        self._append("ai", text)

    def _finish(self, reply: str, *, ok: bool) -> None:
        """收尾：恢复按钮状态并补全换行 / 错误提示。"""
        self._running = False
        self._thread = None
        self._set_running(False)
        if not ok:
            self._append("error", f"\n错误: {reply}\n")
            self._status_var.set("失败")
        elif reply:
            if not self._answer_started:
                self._append("ai", "助手: ")
            self._append("ai", "\n")
            self._status_var.set("完成")
        elif self._saw_reasoning:
            self._append("ai", "助手: （仅生成思考内容，未在 max_tokens 内产出回答，请调大 max_tokens）\n")
            self._status_var.set("完成（未产出回答）")
        else:
            self._append("ai", "助手: （无输出）\n")
            self._status_var.set("完成（无输出）")

    def _stop(self) -> None:
        """请求停止当前生成。"""
        if not self._running:
            return
        if self._session is not None:
            self._session.cancel()
        self._status_var.set("正在停止…")

    def _clear(self) -> None:
        """清空聊天区与对话历史。"""
        if self._running:
            self._status_var.set("生成中，请先停止再清空")
            return
        if self._session is not None:
            self._session.reset()
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")
        self._status_var.set("已清空")

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self._send_btn.configure(state=state)
        self._clear_btn.configure(state=state)
        self._stop_btn.configure(state="normal" if running else "disabled")

    def _append(self, tag: str, text: str) -> None:
        """向聊天区追加文本并滚动到底部。"""
        self._chat.configure(state="normal")
        self._chat.insert("end", text, tag)
        self._chat.see("end")
        self._chat.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 参数校验
    # ------------------------------------------------------------------ #
    def _int_value(self, var: Any, label: str) -> int:
        try:
            value = int(var.get().strip())
        except ValueError as exc:
            raise ServeError(f"{label} 必须是整数") from exc
        if value < 1:
            raise ServeError(f"{label} 必须大于 0")
        return value

    def _float_value(self, var: Any, label: str, *, upper: float | None = None) -> float:
        try:
            value = float(var.get().strip())
        except ValueError as exc:
            raise ServeError(f"{label} 必须是数字") from exc
        if value < 0 or (upper is not None and value > upper):
            raise ServeError(f"{label} 超出范围")
        return value

    def _close(self) -> None:
        """关闭窗口；生成中先确认，退出前保存窗口位置与大小。"""
        if self._running and not self._messagebox.askyesno("生成进行中", "回复仍在生成，确实退出？"):
            return
        save_settings(ChatSettings(geometry=self.root.winfo_geometry()), self._settings_path)
        self.root.destroy()


def run_gui() -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(ChatWindow, "窗口版")


@fcmd.tool("llmchatw", help="打开 LLM 聊天窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 LLM 聊天窗口。"""
    run_gui()


@fcmd.main("llmchatw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
