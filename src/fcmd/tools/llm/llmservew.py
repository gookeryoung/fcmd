"""llmservew - 推理服务生命周期管理的 Tk 窗口版。

基于 :mod:`fcmd.tools.llm.llmserve` 的核心逻辑，提供启动参数的图形化配置、
已配置模型列表（后台扫描 ``llmenv`` 登记的模型目录）、启动计划预览、后台
启动 / 停止、状态与日志查看。启动与停止均在后台线程执行，界面不阻塞。

示例
----
    fcmd llmservew
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any, cast

import fcmd
from fcmd.console import print_error
from fcmd.tools.llm import llmbase, llmengine, llmenv, llmmodel, llmserve
from fcmd.tools.llm.llmbase import (
    DEFAULT_ALIAS,
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    EngineInfo,
    ModelEntry,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    http_base_url,
    human_size,
)

__all__ = ["main"]

_DESCRIPTION = (
    "推理服务窗口版（Tk）：列出已配置的可选模型，配置档位/端口/上下文等参数，"
    "预览启动计划，后台启动与停止 llama.cpp 服务并查看状态与日志"
)

_MODEL_PLACEHOLDER = "未选择（按档位自动在模型目录中查找）"


class ServeWindow:
    """Tk 服务窗口：参数表单 + 模型列表 + 计划预览 + 后台启停。"""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self.root = tk.Tk()
        self.root.title("推理服务管理")
        self.root.geometry("860x760")
        self.root.minsize(720, 640)

        self._models: list[ModelEntry] = []
        self._selected_model: Path | None = None
        self._gpu_mib: int | None = None
        self._busy = False

        self._scan_thread: threading.Thread | None = None
        self._scan_results: queue.Queue[tuple[list[ModelEntry], dict[str, str], int | None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None

        self._name_var = tk.StringVar(value=DEFAULT_NAME)
        self._profile_var = tk.StringVar(value=DEFAULT_PROFILE)
        self._host_var = tk.StringVar(value=DEFAULT_HOST)
        self._port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self._ctx_var = tk.StringVar(value="")
        self._kv_var = tk.StringVar(value="")
        self._ubatch_var = tk.StringVar(value="")
        self._alias_var = tk.StringVar(value=DEFAULT_ALIAS)
        self._api_key_var = tk.StringVar(value=DEFAULT_API_KEY)
        self._threads_var = tk.StringVar(value=str(DEFAULT_THREADS))
        self._wait_var = tk.StringVar(value="180")
        self._model_var = tk.StringVar(value=_MODEL_PLACEHOLDER)
        self._status_var = tk.StringVar(value="状态：未查询")
        self._info_vars: dict[str, tk.StringVar] = {}

        self._build_widgets()
        self._refresh_models()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        self._build_params(outer)
        self._build_models(outer)
        self._build_env(outer)
        self._build_actions(outer)
        self._build_log(outer)
        ttk.Label(outer, textvariable=self._status_var).pack(fill="x", pady=(6, 0))
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_params(self, outer: Any) -> None:
        ttk = self._ttk
        params = ttk.LabelFrame(outer, text="启动参数", padding=8)
        params.pack(fill="x")
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)

        def add_row(row: int, label: str, widget: Any, right_label: str, right_widget: Any) -> None:
            ttk.Label(params, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
            widget.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=3)
            ttk.Label(params, text=right_label).grid(row=row, column=2, sticky="w", padx=(0, 6), pady=3)
            right_widget.grid(row=row, column=3, sticky="ew", pady=3)

        add_row(
            0,
            "服务名",
            ttk.Entry(params, textvariable=self._name_var),
            "档位",
            ttk.Combobox(
                params,
                textvariable=self._profile_var,
                values=tuple(PROFILES),
                state="readonly",
            ),
        )
        add_row(
            1,
            "监听地址",
            ttk.Entry(params, textvariable=self._host_var),
            "端口",
            ttk.Entry(params, textvariable=self._port_var),
        )
        add_row(
            2,
            "上下文（留空=档位）",
            ttk.Entry(params, textvariable=self._ctx_var),
            "KV（留空=档位）",
            ttk.Combobox(params, textvariable=self._kv_var, values=("", *KV_SCALE), state="readonly"),
        )
        add_row(
            3,
            "ubatch（留空=档位）",
            ttk.Entry(params, textvariable=self._ubatch_var),
            "线程数",
            ttk.Entry(params, textvariable=self._threads_var),
        )
        add_row(
            4,
            "模型别名",
            ttk.Entry(params, textvariable=self._alias_var),
            "API Key",
            ttk.Entry(params, textvariable=self._api_key_var),
        )
        add_row(
            5,
            "等待就绪（秒）",
            ttk.Entry(params, textvariable=self._wait_var),
            "",
            ttk.Label(params, text=""),
        )

    def _build_models(self, outer: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(outer, text="已配置模型（llmenv 登记的模型目录）", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))

        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("model", "size", "profiles"),
            show="headings",
            selectmode="browse",
            height=6,
        )
        self._tree.heading("model", text="模型")
        self._tree.heading("size", text="大小")
        self._tree.heading("profiles", text="匹配档位")
        self._tree.column("model", width=460, anchor="w")
        self._tree.column("size", width=100, anchor="e", stretch=False)
        self._tree.column("profiles", width=160, anchor="w", stretch=False)
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=yscroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._on_model_selected)

        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Entry(row, textvariable=self._model_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        self._model_btn = ttk.Button(row, text="刷新", command=self._refresh_models)
        self._model_btn.pack(side="left")
        self._browse_btn = ttk.Button(row, text="浏览…", command=self._select_file)
        self._browse_btn.pack(side="left", padx=(6, 0))
        self._clear_btn = ttk.Button(row, text="清除选择", command=self._clear_selection)
        self._clear_btn.pack(side="left", padx=(6, 0))

    def _build_env(self, outer: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        box = ttk.LabelFrame(outer, text="环境信息", padding=8)
        box.pack(fill="x", pady=(8, 0))
        box.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("engine", "引擎"), ("version", "版本"), ("gpu", "GPU"))):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8))
            var = tk.StringVar(value="检测中…")
            ttk.Label(box, textvariable=var, wraplength=700, justify="left").grid(row=row, column=1, sticky="w")
            self._info_vars[key] = var

    def _build_actions(self, outer: Any) -> None:
        ttk = self._ttk
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(8, 0))
        self._preview_btn = ttk.Button(actions, text="预览计划", command=self._preview)
        self._preview_btn.pack(side="left")
        self._start_btn = ttk.Button(actions, text="启动服务", command=self._start)
        self._start_btn.pack(side="left", padx=(6, 0))
        self._stop_btn = ttk.Button(actions, text="停止服务", command=self._stop)
        self._stop_btn.pack(side="left", padx=(6, 0))
        self._status_btn = ttk.Button(actions, text="刷新状态", command=self._refresh_status)
        self._status_btn.pack(side="left", padx=(6, 0))
        self._logs_btn = ttk.Button(actions, text="查看日志", command=self._show_logs)
        self._logs_btn.pack(side="left", padx=(6, 0))
        self._clear_log_btn = ttk.Button(actions, text="清空日志", command=self._clear_log)
        self._clear_log_btn.pack(side="left", padx=(6, 0))
        self._progress = ttk.Progressbar(actions, mode="indeterminate", length=200)
        self._progress.pack(side="right")

    def _build_log(self, outer: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        log_frame = ttk.LabelFrame(outer, text="输出日志", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self._log_text = tk.Text(log_frame, wrap="none", state="disabled", yscrollcommand=scrollbar.set, height=12)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    # ------------------------------------------------------------------ #
    # 模型选择
    # ------------------------------------------------------------------ #
    def _on_model_selected(self, _event: object | None = None) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._models):
            self._select_model(self._models[index].path)

    def _select_file(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择 GGUF 模型",
            filetypes=[("GGUF 文件", "*.gguf"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._tree.selection_remove(*self._tree.selection())
        self._select_model(Path(path).resolve())

    def _select_model(self, path: Path) -> None:
        self._selected_model = path
        self._model_var.set(str(path))
        self._log(f"已选择模型: {path}")

    def _clear_selection(self) -> None:
        self._tree.selection_remove(*self._tree.selection())
        self._selected_model = None
        self._model_var.set(_MODEL_PLACEHOLDER)

    # ------------------------------------------------------------------ #
    # 扫描模型与引擎信息
    # ------------------------------------------------------------------ #
    def _refresh_models(self) -> None:
        """后台扫描已配置模型目录并探测引擎 / GPU 信息，避免阻塞窗口。"""
        if self._busy:
            return
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._model_btn.configure(state="disabled")
        self._model_var.set("正在扫描模型目录…")
        for var in self._info_vars.values():
            var.set("检测中…")
        self._scan_thread = threading.Thread(target=self._scan_models, daemon=True)
        self._scan_thread.start()
        self.root.after(200, self._poll_scan)

    def _scan_models(self) -> None:
        """后台线程：扫描模型目录并收集引擎 / GPU 信息。"""
        entries: list[ModelEntry] = []
        try:
            roots = [root for root in llmenv.models_roots() if root.is_dir()]
            if roots:
                entries = llmmodel.scan_models(roots)
        except OSError:
            entries = []
        info, gpu_mib = self._collect_env_info()
        self._scan_results.put((entries, info, gpu_mib))

    def _collect_env_info(self) -> tuple[dict[str, str], int | None]:
        """收集当前引擎 tag / 可执行文件、版本与显存（MiB）。"""
        info: dict[str, str] = {"engine": "", "version": "", "gpu": ""}
        try:
            engine = llmengine.resolve_engine()
        except llmbase.ServeError as exc:
            info["engine"] = f"未找到（{exc}）"
        else:
            info["engine"] = f"{engine.tag} · {engine.exe}"
            info["version"] = self._probe_version(engine)
        gpu = llmengine.detect_gpu()
        info["gpu"] = f"{gpu.name}（{gpu.memory_mib} MiB）" if gpu else "未检测到 NVIDIA GPU"
        return info, gpu.memory_mib if gpu is not None else None

    @staticmethod
    def _probe_version(engine: EngineInfo) -> str:
        """执行 ``llama-server --version`` 取首行版本信息。"""
        result = llmengine.run_with_engine(llmbase.run_command, [str(engine.exe), "--version"], engine)
        output = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            detail = output.splitlines()[0] if output else f"退出码 {llmbase.format_returncode(result.returncode)}"
            return f"获取失败（{detail}）"
        return output.splitlines()[0] if output else "未知"

    def _poll_scan(self) -> None:
        try:
            entries, info, gpu_mib = self._scan_results.get_nowait()
        except queue.Empty:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                self.root.after(200, self._poll_scan)
            return
        self._apply_models(entries, info, gpu_mib)

    def _apply_models(self, entries: list[ModelEntry], info: dict[str, str], gpu_mib: int | None) -> None:
        self._models = entries
        self._gpu_mib = gpu_mib
        self._tree.delete(*self._tree.get_children())
        for index, entry in enumerate(entries):
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=(entry.relative, human_size(entry.size), ", ".join(entry.profiles) or "-"),
            )
        self._model_var.set(str(self._selected_model) if self._selected_model else _MODEL_PLACEHOLDER)
        if self._selected_model is not None:
            key = llmenv.path_key(self._selected_model)
            for index, entry in enumerate(entries):
                if llmenv.path_key(entry.path) == key:
                    self._tree.selection_set(str(index))
                    self._tree.see(str(index))
                    break
        for key, var in self._info_vars.items():
            var.set(info.get(key) or "-")
        self._model_btn.configure(state="normal")
        if not entries:
            self._log("未发现已配置的 GGUF：用 fcmd llmenv add 登记模型目录，或直接“浏览…”选择模型文件")

    # ------------------------------------------------------------------ #
    # 参数解析
    # ------------------------------------------------------------------ #
    @staticmethod
    def _int_field(var: Any, label: str) -> int:
        text = var.get().strip()
        try:
            return int(text)
        except ValueError as exc:
            raise ServeError(f"{label} 必须是整数: {text!r}") from exc

    @staticmethod
    def _optional_int_field(var: Any, label: str) -> int | None:
        text = var.get().strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ServeError(f"{label} 必须是整数: {text!r}") from exc

    def _model_name(self) -> str:
        name = self._name_var.get().strip()
        if not name:
            raise ServeError("服务名不能为空")
        return llmbase.validate_name(name)

    def _build_plan(self) -> llmbase.LaunchPlan:
        """按表单内容构造启动计划（参数错误时抛 :class:`ServeError`）。"""
        profile = self._profile_var.get().strip()
        return llmserve.make_plan(
            self._model_name(),
            cast(ProfileName, profile),
            model=self._selected_model,
            port=self._int_field(self._port_var, "端口"),
            host=self._host_var.get().strip() or DEFAULT_HOST,
            ctx=self._optional_int_field(self._ctx_var, "上下文"),
            kv_type=self._kv_var.get().strip() or None,
            ubatch=self._optional_int_field(self._ubatch_var, "ubatch"),
            alias=self._alias_var.get().strip() or DEFAULT_ALIAS,
            api_key=self._api_key_var.get().strip() or DEFAULT_API_KEY,
            threads=self._int_field(self._threads_var, "线程数"),
        )

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _preview(self) -> None:
        try:
            plan = self._build_plan()
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        estimate = llmmodel.plan_vram(plan)
        self._log("启动计划:")
        self._log(f"  服务名: {plan.name}")
        self._log(f"  引擎: {plan.engine.tag} · {plan.engine.exe}")
        self._log(f"  模型: {plan.model}")
        self._log(
            f"  档位: {plan.profile.name} / {plan.profile.quant} / kv {plan.effective_kv_type} / "
            f"ctx {plan.context} / ub {plan.effective_ubatch}"
        )
        self._log(f"  监听: {http_base_url(plan.host, plan.port)}")
        self._log(f"  显存估算: {human_size(estimate)}")
        self._log("  命令: " + format_command(plan.argv()))
        if self._gpu_mib is not None and estimate > self._gpu_mib * 1024 * 1024:
            self._log(
                f"  警告: 估算 {human_size(estimate)} 超过显存 {self._gpu_mib} MiB，可能 OOM；"
                "可改用 --profile speed 或减小上下文"
            )

    def _start(self) -> None:
        if self._busy:
            self._log("已有任务正在进行，请等待完成")
            return
        try:
            plan = self._build_plan()
            wait_s = self._int_field(self._wait_var, "等待就绪秒数")
            if wait_s <= 0:
                raise ServeError(f"等待就绪秒数必须为正整数: {wait_s}")
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        self._begin_task(self._start_work, (plan, wait_s))

    def _stop(self) -> None:
        if self._busy:
            self._log("已有任务正在进行，请等待完成")
            return
        try:
            name = self._model_name()
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        self._begin_task(self._stop_work, (name,))

    def _begin_task(self, target: Any, args: tuple[Any, ...]) -> None:
        """启动后台线程执行目标函数，结果通过队列回传主线程。"""
        self._messages = queue.Queue()
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._set_busy(True)
        self._thread.start()
        self.root.after(100, self._poll_task)

    def _start_work(self, plan: llmbase.LaunchPlan, wait_s: int) -> None:
        assert self._messages is not None
        status = self._messages
        status.put(("log", f"正在启动 {plan.name}，等待 /health（最多 {wait_s}s）…"))
        try:
            state = llmserve.start_server(plan, wait_s=float(wait_s))
        except ServeError as exc:
            status.put(("error", f"启动失败: {exc}"))
        else:
            status.put(("state", state))
        finally:
            status.put(("done", None))

    def _stop_work(self, name: str) -> None:
        assert self._messages is not None
        status = self._messages
        status.put(("log", f"正在停止服务 {name}…"))
        try:
            result = llmserve.stop_server(name)
        except ServeError as exc:
            status.put(("error", f"停止失败: {exc}"))
        else:
            status.put(("stopped", result))
        finally:
            status.put(("done", None))

    def _poll_task(self) -> None:
        """主线程轮询队列，刷新日志与状态。"""
        assert self._messages is not None
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "error":
                    self._log(str(payload))
                    self._messagebox.showerror("错误", str(payload))
                elif kind == "state":
                    self._show_state(cast(ServeState, payload))
                elif kind == "stopped":
                    self._show_stopped(cast(StopResult, payload))
                elif kind == "done":
                    self._set_busy(False)
        except queue.Empty:
            if self._thread is not None and self._thread.is_alive():
                self.root.after(100, self._poll_task)

    def _show_state(self, state: ServeState) -> None:
        self._status_var.set(f"{state.name} · 运行中 · {state.base_url} · pid {state.pid}")
        self._log(f"服务已就绪: {state.base_url}（OpenAI: {state.base_url}/v1）")
        self._log(f"  模型: {state.model}")
        self._log(f"  档位: {state.profile} / ctx {state.context}")
        self._log(f"  日志: {state.log}")
        self._messagebox.showinfo("启动成功", f"服务 {state.name} 已就绪\n{state.base_url}")

    def _show_stopped(self, result: StopResult) -> None:
        if result.was_running:
            self._status_var.set(f"{result.state.name} · 已停止")
            self._log(f"已停止服务 {result.state.name}（pid={result.state.pid}）")
        else:
            self._status_var.set(f"{result.state.name} · 未运行（已清理状态）")
            self._log(f"服务 {result.state.name} 未在运行，已清理残留状态")

    def _refresh_status(self) -> None:
        try:
            name = self._model_name()
            state = llmenv.require_state(name)
        except ServeError as exc:
            self._status_var.set("状态：未启动")
            self._log(str(exc))
            return
        alive = llmserve.process_alive(state.pid, state.create_time)
        self._status_var.set(f"{state.name} · {'运行中' if alive else '已退出'} · {state.base_url} · pid {state.pid}")
        self._log(f"服务 {state.name}: {'运行中' if alive else '已退出'}")
        self._log(f"  地址: {state.base_url}")
        self._log(f"  模型: {state.model}")
        self._log(f"  档位: {state.profile} / ctx {state.context}")
        self._log(f"  启动时间: {state.started_at}")
        self._log(f"  日志: {state.log}")

    def _show_logs(self) -> None:
        try:
            name = self._model_name()
            content = llmserve.read_log(name, 50)
        except ServeError as exc:
            self._messagebox.showerror("日志不可用", str(exc))
            return
        self._log(f"日志尾部（{name}）:")
        self._log(content)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in (self._preview_btn, self._start_btn, self._stop_btn, self._status_btn, self._logs_btn):
            button.configure(state=state)
        self._model_btn.configure(state=state)
        self._browse_btn.configure(state=state)
        self._clear_btn.configure(state=state)
        if busy:
            self._progress.start(12)
        else:
            self._progress.stop()

    def _close(self) -> None:
        running = self._thread is not None and self._thread.is_alive()
        if running and not self._messagebox.askyesno("任务进行中", "启动 / 停止任务仍在执行，确实退出？"):
            return
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        """清空输出日志区域。"""
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")


def run_gui() -> None:
    """创建并运行 Tk 主窗口。"""
    try:
        window = ServeWindow()
    except ImportError as exc:
        print_error(f"tkinter 不可用，无法启动窗口版: {exc}")
        return
    window.root.mainloop()


@fcmd.tool("llmservew", help="打开推理服务窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开推理服务窗口。"""
    run_gui()


@fcmd.main("llmservew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
