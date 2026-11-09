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
import time
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
    "预览启动计划，后台启动与停止 llama.cpp 服务，查看状态与日志，"
    "并在服务被调用时实时采样 /metrics 展示 token/s"
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
        self.root.geometry("880x640")
        self.root.minsize(780, 520)

        self._models: list[ModelEntry] = []
        self._selected_model: Path | None = None
        self._gpu_mib: int | None = None
        self._busy = False
        self._closing = False

        self._scan_thread: threading.Thread | None = None
        self._scan_results: queue.Queue[tuple[list[ModelEntry], dict[str, str], int | None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None

        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop: threading.Event | None = None
        self._monitor_state: ServeState | None = None
        self._monitor_results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._monitor_seq = 0

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
        self._metric_vars: dict[str, tk.StringVar] = {
            "gen_rate": tk.StringVar(value="0.0 tok/s"),
            "prompt_rate": tk.StringVar(value="0.0 tok/s"),
            "processing": tk.StringVar(value="0"),
            "deferred": tk.StringVar(value="0"),
            "kv": tk.StringVar(value="0.0 %"),
            "predicted": tk.StringVar(value="0"),
            "prompt": tk.StringVar(value="0"),
            "monitor": tk.StringVar(value="未监控"),
        }

        self._build_widgets()
        self._refresh_loaded()
        self._refresh_models()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self._status_var).pack(side="bottom", fill="x", pady=(4, 0))
        self._build_actions(outer)
        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill="both", expand=True)
        start_page = ttk.Frame(self._notebook, padding=10)
        run_page = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(start_page, text="启动配置")
        self._notebook.add(run_page, text="运行监控")
        self._build_params(start_page)
        self._build_models(start_page)
        self._build_env(start_page)
        self._build_loaded(run_page)
        self._build_metrics(run_page)
        self._build_log(run_page)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_params(self, parent: Any) -> None:
        ttk = self._ttk
        params = ttk.LabelFrame(parent, text="启动参数", padding=8)
        params.pack(fill="x")
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)
        self._pair_row(
            params,
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
        self._pair_row(
            params,
            1,
            "监听地址",
            ttk.Entry(params, textvariable=self._host_var),
            "端口",
            ttk.Entry(params, textvariable=self._port_var),
        )
        self._pair_row(
            params,
            2,
            "线程数",
            ttk.Entry(params, textvariable=self._threads_var),
            "",
            ttk.Label(params, text=""),
        )

        advanced, body = self._collapsible(parent, "高级参数（留空回落档位默认）", open_state=False)
        advanced.pack(fill="x", pady=(8, 0))
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)
        self._pair_row(
            body,
            0,
            "上下文",
            ttk.Entry(body, textvariable=self._ctx_var),
            "KV",
            ttk.Combobox(body, textvariable=self._kv_var, values=("", *KV_SCALE), state="readonly"),
        )
        self._pair_row(
            body,
            1,
            "ubatch",
            ttk.Entry(body, textvariable=self._ubatch_var),
            "模型别名",
            ttk.Entry(body, textvariable=self._alias_var),
        )
        self._pair_row(
            body,
            2,
            "API Key",
            ttk.Entry(body, textvariable=self._api_key_var),
            "等待就绪（秒）",
            ttk.Entry(body, textvariable=self._wait_var),
        )

    def _pair_row(  # noqa: PLR0913, PLR0917
        self,
        target: Any,
        row: int,
        label: str,
        widget: Any,
        right_label: str,
        right_widget: Any,
    ) -> None:
        """在目标 Frame 的第 ``row`` 行放置两组“标签 + 输入控件”。"""
        ttk = self._ttk
        ttk.Label(target, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
        widget.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=3)
        if right_label:
            ttk.Label(target, text=right_label).grid(row=row, column=2, sticky="w", padx=(0, 6), pady=3)
        right_widget.grid(row=row, column=3, sticky="ew", pady=3)

    def _collapsible(self, parent: Any, title: str, *, open_state: bool = True) -> Any:
        """创建可折叠分区，返回（外层容器, 内容 Frame）。"""
        ttk = self._ttk
        container = ttk.Frame(parent)
        header = ttk.Frame(container)
        header.pack(fill="x")
        body = ttk.Frame(container, padding=(6, 4, 0, 0))
        state = {"open": open_state}

        def render() -> None:
            if state["open"]:
                body.pack(fill="x")
            else:
                body.pack_forget()
            button.configure(text=("▼ " if state["open"] else "▶ ") + title)

        def flip() -> None:
            state["open"] = not state["open"]
            render()

        button = ttk.Button(header, text="", style="Toolbutton", command=flip)
        button.pack(fill="x", anchor="w")
        render()
        return container, body

    def _build_models(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已配置模型（llmenv 登记的模型目录）", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))

        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("model", "size", "profiles"),
            show="headings",
            selectmode="browse",
            height=7,
        )
        self._tree.heading("model", text="模型")
        self._tree.heading("size", text="大小")
        self._tree.heading("profiles", text="匹配档位")
        self._tree.column("model", width=320, anchor="w")
        self._tree.column("size", width=90, anchor="e", stretch=False)
        self._tree.column("profiles", width=140, anchor="w", stretch=False)
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

    def _build_env(self, parent: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        box, body = self._collapsible(parent, "环境信息（引擎 / 版本 / GPU）", open_state=False)
        box.pack(fill="x", pady=(8, 0))
        body.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("engine", "引擎"), ("version", "版本"), ("gpu", "GPU"))):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8))
            var = tk.StringVar(value="检测中…")
            ttk.Label(body, textvariable=var, wraplength=760, justify="left").grid(row=row, column=1, sticky="w")
            self._info_vars[key] = var

    def _build_loaded(self, parent: Any) -> None:
        """已加载模型：列出运行中的服务及其模型、档位与地址。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已加载模型（运行中的服务）", padding=6)
        box.pack(fill="x")
        self._loaded_tree = ttk.Treeview(
            box,
            columns=("name", "model", "profile", "address"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        self._loaded_tree.heading("name", text="服务")
        self._loaded_tree.heading("model", text="已加载模型")
        self._loaded_tree.heading("profile", text="档位 / ctx")
        self._loaded_tree.heading("address", text="地址")
        self._loaded_tree.column("name", width=90, stretch=False)
        self._loaded_tree.column("model", width=360, anchor="w")
        self._loaded_tree.column("profile", width=140, anchor="w", stretch=False)
        self._loaded_tree.column("address", width=170, anchor="w", stretch=False)
        self._loaded_tree.pack(fill="x")
        self._loaded_var = self._tk.StringVar(value="无运行中的服务")
        ttk.Label(box, textvariable=self._loaded_var).pack(fill="x", pady=(4, 0))

    def _refresh_loaded(self) -> None:
        """刷新“已加载模型”列表（只统计进程仍存活的服务）。"""
        for item in self._loaded_tree.get_children():
            self._loaded_tree.delete(item)
        alive = [state for state in llmenv.list_states() if llmserve.process_alive(state.pid, state.create_time)]
        for state in alive:
            self._loaded_tree.insert(
                "",
                "end",
                values=(
                    state.name,
                    Path(state.model).name,
                    f"{state.profile} / ctx {state.context}",
                    state.base_url,
                ),
            )
        self._loaded_var.set("" if alive else "无运行中的服务")

    def _build_metrics(self, parent: Any) -> None:
        """实时性能面板：显示 /metrics 计算的 token/s 与请求队列状态。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="实时性能（服务运行中每 1s 采样 /metrics）", padding=8)
        box.pack(fill="x")
        for column in range(4):
            box.columnconfigure(column, weight=1)
        items = (
            ("生成速率", "gen_rate"),
            ("提示速率", "prompt_rate"),
            ("处理中请求", "processing"),
            ("排队请求", "deferred"),
            ("KV 使用率", "kv"),
            ("累计生成 tokens", "predicted"),
            ("累计提示 tokens", "prompt"),
            ("采样状态", "monitor"),
        )
        for index, (label, key) in enumerate(items):
            row, column = divmod(index, 4)
            cell = ttk.Frame(box)
            cell.grid(row=row, column=column, sticky="w", padx=(0, 16), pady=2)
            ttk.Label(cell, text=label + ":").pack(side="left")
            ttk.Label(cell, textvariable=self._metric_vars[key], font=("", 9, "bold")).pack(side="left", padx=(4, 0))

    def _build_actions(self, outer: Any) -> None:
        ttk = self._ttk
        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
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

    def _build_log(self, parent: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        log_frame = ttk.LabelFrame(parent, text="输出日志", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self._log_text = tk.Text(log_frame, wrap="none", state="disabled", yscrollcommand=scrollbar.set, height=10)
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

    @staticmethod
    def _gpu_usage() -> tuple[int, int] | None:
        """查询 GPU 已用 / 总显存（MiB）；nvidia-smi 不可用时返回 ``None``。"""
        result = llmbase.run_command(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            used, total = (int(float(item)) for item in result.stdout.strip().splitlines()[0].split(","))
        except ValueError:
            return None
        return used, total

    def _memory_guard(self, plan: llmbase.LaunchPlan) -> None:
        """启动前检查显存 / 内存余量，不足时拒绝启动，避免系统过载。"""
        estimate = llmmodel.plan_vram(plan)
        usage = self._gpu_usage()
        if usage is not None:
            used, total = usage
            label = "显存"
            available = max(total - used, 0) * 1024 * 1024
            self._log(f"显存占用: {used} / {total} MiB")
        else:
            import psutil

            label = "可用内存"
            available = int(psutil.virtual_memory().available)
        if estimate > available:
            raise ServeError(
                f"{label}不足，启动可能过载：预计需要 {human_size(estimate)}，当前可用 {human_size(available)}。"
                "请改用更小档位、减小上下文，或先停止其他服务"
            )
        self._log(f"资源检查通过：预计占用 {human_size(estimate)} / {label}可用 {human_size(available)}")

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
        try:
            self._memory_guard(plan)
        except ServeError as exc:
            self._log("已取消启动：" + str(exc))
            self._messagebox.showerror("资源不足", str(exc))
            return
        self._stop_monitor("启动中…")
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
        self._stop_monitor("停止中…")
        self._begin_task(self._stop_work, (name,))

    def _begin_task(self, target: Any, args: tuple[Any, ...]) -> None:
        """启动后台线程执行目标函数，结果通过队列回传主线程。"""
        self._notebook.select(1)
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
        self._start_monitor(state)
        self._refresh_loaded()
        self._messagebox.showinfo("启动成功", f"服务 {state.name} 已就绪\n{state.base_url}")

    def _show_stopped(self, result: StopResult) -> None:
        self._stop_monitor("已停止")
        self._refresh_loaded()
        if result.was_running:
            self._status_var.set(f"{result.state.name} · 已停止")
            self._log(f"已停止服务 {result.state.name}（pid={result.state.pid}）")
        else:
            self._status_var.set(f"{result.state.name} · 未运行（已清理状态）")
            self._log(f"服务 {result.state.name} 未在运行，已清理残留状态")

    def _refresh_status(self) -> None:
        self._refresh_loaded()
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
        if alive:
            self._start_monitor(state)
        else:
            self._stop_monitor("服务未运行")

    def _show_logs(self) -> None:
        self._notebook.select(1)
        try:
            name = self._model_name()
            content = llmserve.read_log(name, 50)
        except ServeError as exc:
            self._messagebox.showerror("日志不可用", str(exc))
            return
        self._log(f"日志尾部（{name}）:")
        self._log(content)

    # ------------------------------------------------------------------ #
    # 实时性能监控（/metrics）
    # ------------------------------------------------------------------ #
    def _start_monitor(self, state: ServeState) -> None:
        """启动 ``/metrics`` 采样线程；同一服务已在监控时忽略。"""
        current = self._monitor_state
        if (
            self._monitor_thread is not None
            and self._monitor_thread.is_alive()
            and current is not None
            and (current.host, current.port) == (state.host, state.port)
        ):
            self._metric_vars["monitor"].set("监控中")
            return
        self._stop_monitor("重新连接监控…")
        stop = threading.Event()
        self._monitor_stop = stop
        self._monitor_state = state
        self._monitor_seq += 1
        seq = self._monitor_seq
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(state, stop), daemon=True)
        self._monitor_thread.start()
        self._metric_vars["monitor"].set("监控中")
        self._log(f"已开始实时性能采样: {state.base_url}/metrics")
        self.root.after(300, lambda: self._poll_metrics(seq))

    def _stop_monitor(self, reason: str = "未监控") -> None:
        """停止采样线程并更新状态（重复调用安全）。"""
        if self._monitor_stop is not None:
            self._monitor_stop.set()
        thread = self._monitor_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._monitor_stop = None
        self._monitor_thread = None
        self._monitor_state = None
        self._monitor_seq += 1
        self._metric_vars["monitor"].set(reason)

    def _monitor_loop(self, state: ServeState, stop: threading.Event) -> None:
        """后台线程：每秒请求 ``/metrics``，按两次采样的差值计算 token/s。"""
        previous: tuple[float, llmserve.ServeMetrics] | None = None
        while not stop.is_set():
            started = time.monotonic()
            try:
                metrics = llmserve.fetch_metrics(state.host, state.port, state.api_key)
            except ServeError as exc:
                self._monitor_results.put(("error", str(exc)))
            else:
                gen_rate = prompt_rate = 0.0
                if previous is not None:
                    elapsed = started - previous[0]
                    if elapsed > 0:
                        gen_rate = max(metrics.predicted_tokens - previous[1].predicted_tokens, 0.0) / elapsed
                        prompt_rate = max(metrics.prompt_tokens - previous[1].prompt_tokens, 0.0) / elapsed
                previous = (started, metrics)
                self._monitor_results.put(("metrics", (metrics, gen_rate, prompt_rate)))
            stop.wait(1.0)

    def _poll_metrics(self, seq: int) -> None:
        """主线程刷新性能面板（按序号丢弃已停止监控的旧回调）。"""
        if self._closing or seq != self._monitor_seq:
            return
        try:
            while True:
                kind, payload = self._monitor_results.get_nowait()
                if kind == "metrics":
                    metrics, gen_rate, prompt_rate = cast(tuple[llmserve.ServeMetrics, float, float], payload)
                    self._apply_metrics(metrics, gen_rate, prompt_rate)
                else:
                    self._metric_vars["monitor"].set("采样失败: " + str(payload)[:60])
        except queue.Empty:
            pass
        self.root.after(500, lambda: self._poll_metrics(seq))

    def _apply_metrics(self, metrics: llmserve.ServeMetrics, gen_rate: float, prompt_rate: float) -> None:
        self._metric_vars["gen_rate"].set(f"{gen_rate:.1f} tok/s")
        self._metric_vars["prompt_rate"].set(f"{prompt_rate:.1f} tok/s")
        self._metric_vars["processing"].set(f"{metrics.processing:.0f}")
        self._metric_vars["deferred"].set(f"{metrics.deferred:.0f}")
        self._metric_vars["kv"].set(f"{metrics.kv_usage * 100:.1f} %")
        self._metric_vars["predicted"].set(f"{metrics.predicted_tokens:.0f}")
        self._metric_vars["prompt"].set(f"{metrics.prompt_tokens:.0f}")
        self._metric_vars["monitor"].set("监控中")

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
        self._closing = True
        self._stop_monitor()
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
