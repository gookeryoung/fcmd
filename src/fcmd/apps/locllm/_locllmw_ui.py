"""locllmw 的界面构建：控件布局与模型列表渲染（从 locllmw.py 拆出）。

``LocllmWindow`` 继承 ``_LocllmWindowUI``；``locllmw`` 仍 re-export
``_MODEL_PLACEHOLDER`` / ``_HINT_PLACEHOLDER`` / ``_QUANT_SUFFIX_RE`` 供其自身与测试使用。
"""

from __future__ import annotations

import queue
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.locllm import _llmtune, llmengine, llmenv, llmmodel, llmserve
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision
from fcmd.apps.locllm._locllmw_bench import BENCH_CHECKED, RESULT_COLUMNS, SCENARIOS
from fcmd.apps.locllm._locllmw_models import _ModelBrowserPage, settings_from_dict
from fcmd.apps.locllm._locllmw_monitor import (
    _FIT_BUSY,
    _FIT_RUNNING,
    _FIT_UNSUPPORTED,
    RATE_WINDOW_SECONDS,
    _fit_state,
)
from fcmd.apps.locllm._locllmw_settings import SettingsPage
from fcmd.apps.locllm.llmbase import KV_SCALE, PROFILES, ModelEntry, ServeError, human_size
from fcmd.apps.locllm.llmserve import DEFAULT_NAME
from fcmd.gui import sidebar as _guisidebar
from fcmd.gui import window as _guiwindow

_MODEL_PLACEHOLDER = "未选择（按档位自动在模型目录中查找）"
_HINT_PLACEHOLDER = "选择模型后显示能力标签（代码/推理/对话/视觉/数学/重排/嵌入/通用）、硬件适配与最佳实践建议"
_QUANT_SUFFIX_RE = re.compile(r"[-_.](?:I?Q\d+(?:_[A-Za-z0-9]+)*|BF16|F16|F32)$", re.IGNORECASE)


class _LocllmWindowUI:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """LocllmWindow 的界面构建与模型列表渲染（mixin）。"""

    if TYPE_CHECKING:
        # 宿主 LocllmWindow 提供的成员：仅用于类型检查，运行时由宿主类实现。
        root: Any
        _theme: Any
        _tk: Any
        _ttk: Any
        _font: Any
        _busy: bool
        _selected_model: Path | None
        _scan_thread: threading.Thread | None
        _scan_results: Any
        _fits: Any
        _gpu_usage: Any
        _precisions: dict[str, GgufPrecision]
        _metric_vars: Any
        _load_vars: Any
        _gpu_vars: Any
        _status_var: Any
        _model_var: Any
        _profile_var: Any
        _ctx_var: Any
        _kv_var: Any
        _threads_var: Any
        _wait_var: Any
        _ubatch_var: Any
        _parallel_var: Any
        _threads_http_var: Any
        _tune_var: Any
        _name_var: Any
        _host_var: Any
        _port_var: Any
        _alias_var: Any
        _api_key_var: Any
        _preview: Any
        _profile_context: Any
        _log: Any
        _clear_log: Any
        _clear_selection: Any
        _load_state_safe: Any
        _on_model_selected: Any
        _on_profile_changed: Any
        _on_tune_changed: Any
        _on_service_selected: Any
        _open_browser: Any
        _refresh_status: Any
        _select_file: Any
        _select_model: Any
        _show_logs: Any
        _nav: Any
        _start: Any
        _stop: Any
        _sync_tree_selection: Any
        _bench_repeat_var: Any
        _bench_timeout_var: Any
        _bench_tokens_var: Any
        _bench_status_var: Any
        _start_bench: Any
        _stop_bench: Any
        _clear_bench: Any
        _toggle_bench_scenario: Any
        _refresh_hardware_view: Any
        _refresh_recommendations: Any
        _build_overview: Any
        _build_chat_page: Any
        _on_page_changed: Any
        _settings: Any
        _model_page: Any
        _messagebox: Any

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self._status_var).pack(side="bottom", fill="x", pady=(self._theme.spacing.xs, 0))
        self._nav = _guisidebar.SidebarNav(
            outer,
            theme=self._theme,
            page_padding=self._theme.spacing.md,
            title="locllm",
            on_change=self._on_page_changed,
        )
        self._nav.container.pack(fill="both", expand=True)
        overview_page = self._nav.add("overview", "概览", hint="服务状态与快捷入口")
        models_page = self._nav.add("models", "模型", hint="GGUF 模型与显存适配")
        run_page = self._nav.add("run", "运行", hint="档位、上下文与启动参数")
        chat_page = self._nav.add("chat", "对话", hint="单轮 / 多轮流式对话")
        agent_page = self._nav.add("agent", "代理", hint="工具调用与多步任务")
        image_page = self._nav.add("image", "画图", hint="文生图 / 图生图")
        video_page = self._nav.add("video", "视频", hint="文生视频 / 视频理解")
        audio_page = self._nav.add("audio", "音频", hint="语音识别 / 合成")
        monitor_page = self._nav.add("monitor", "监测", hint="指标、日志与性能测试")
        settings_page = self._nav.add("settings", "设置", hint="环境、配置与窗口")
        self._nav.enable_shortcuts()
        self._build_overview(overview_page)
        self._build_model_browser(models_page)
        self._build_actions(run_page)
        self._build_models(run_page)
        self._build_params(run_page)
        self._build_chat_page(chat_page)
        self._build_planned(
            agent_page,
            title="代理",
            summary="代理页规划中：工具调用、文件编辑与多步任务编排将统一在此配置与查看运行轨迹。",
        )
        self._build_planned(
            image_page,
            title="画图",
            summary="画图页规划中：接入本地图片生成模型后，在此配置提示词、尺寸、步数与批量任务。",
        )
        self._build_planned(
            video_page,
            title="视频",
            summary="视频页规划中：视频生成与视频理解将复用同一套本地模型与任务队列；当前可用 videoconvw 做格式转换。",
            commands=("fcmd videoconvw",),
        )
        self._build_planned(
            audio_page,
            title="音频",
            summary="音频页规划中：语音识别与语音合成将共用本页的模型选择、设备与批处理设置。",
        )
        self._build_monitor(monitor_page)
        self._build_settings(settings_page)

    def _build_planned(
        self,
        parent: Any,
        *,
        title: str,
        summary: str,
        commands: tuple[str, ...] = (),
    ) -> None:
        """规划中分页：说明定位与当前可用命令，避免空白页。"""
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text=title, padding=space.sm)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text="规划中", style="Title.TLabel").pack(anchor="w", pady=(0, space.xs))
        hint = ttk.Label(box, text=summary, justify="left")
        hint.pack(anchor="w", fill="x")
        hint.bind(
            "<Configure>",
            lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
        )
        if commands:
            ttk.Label(box, text="当前可用命令：", style="Hint.TLabel").pack(anchor="w", pady=(space.sm, 0))
            for command in commands:
                ttk.Label(box, text=command, font="TkFixedFont").pack(anchor="w")

    def _build_monitor(self, parent: Any) -> None:
        """监测页：实时性能、系统负载、GPU、性能测试与日志。"""
        space = self._theme.spacing
        self._build_metrics(parent)
        self._build_load(parent)
        self._build_gpu(parent)
        bench_box, bench_body = self._collapsible(
            parent,
            "性能测试（短对话 / 长输出 / 代码补全 / 长上下文 / 并发）",
            open_state=False,
        )
        bench_box.pack(fill="x", pady=(space.sm, 0))
        self._build_bench(bench_body)
        self._build_log(parent)

    def _build_settings(self, parent: Any) -> None:
        """设置页：目录 / 引擎 / 界面三组配置。"""
        self._settings_page = SettingsPage(
            parent,
            tk=self._tk,
            ttk=self._ttk,
            theme=self._theme,
            on_config_changed=self._on_config_changed,
        )

    def _on_config_changed(self) -> None:
        """权威配置变更后同步模型页的工作目录并重扫。"""
        if self._model_page is not None:
            self._model_page.refresh_from_config()

    @staticmethod
    def _service_names() -> list[str]:
        """已登记的服务名列表（供服务名下拉框展示）。"""
        try:
            return [state.name for state in llmenv.list_states()]
        except ServeError:
            return []

    @classmethod
    def _default_service_name(cls) -> str:
        """唯一运行中的服务优先作为默认服务名，避免与实际服务不一致。"""
        try:
            states = llmenv.list_states()
        except ServeError:
            return DEFAULT_NAME
        alive = [state.name for state in states if llmserve.process_alive(state.pid, state.create_time)]
        return alive[0] if len(alive) == 1 else DEFAULT_NAME

    def _refresh_service_names(self) -> None:
        """按最新状态文件刷新服务名下拉选项。"""
        self._name_box.configure(values=self._service_names())

    def _build_params(self, parent: Any) -> None:
        ttk = self._ttk
        params = ttk.LabelFrame(parent, text="启动参数", padding=self._theme.spacing.sm)
        params.pack(fill="x")
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)
        self._name_box = ttk.Combobox(
            params,
            textvariable=self._name_var,
            values=self._service_names(),
        )
        self._name_box.bind("<<ComboboxSelected>>", self._on_service_selected)
        self._profile_box = ttk.Combobox(
            params,
            textvariable=self._profile_var,
            values=tuple(PROFILES),
            state="readonly",
        )
        self._profile_box.bind("<<ComboboxSelected>>", self._on_profile_changed)
        _guiwindow.pair_row(
            params,
            0,
            "服务名",
            self._name_box,
            "档位",
            self._profile_box,
            theme=self._theme,
        )
        _guiwindow.pair_row(
            params,
            1,
            "监听地址",
            ttk.Entry(params, textvariable=self._host_var),
            "端口",
            ttk.Entry(params, textvariable=self._port_var),
            theme=self._theme,
        )
        self._tune_box = ttk.Combobox(
            params,
            textvariable=self._tune_var,
            values=tuple(_llmtune.TUNE_LABELS.values()),
            state="readonly",
        )
        self._tune_box.bind("<<ComboboxSelected>>", self._on_tune_changed)
        _guiwindow.pair_row(
            params,
            2,
            "线程数",
            ttk.Entry(params, textvariable=self._threads_var),
            "调优策略",
            self._tune_box,
            theme=self._theme,
        )

        advanced, body = self._collapsible(parent, "高级参数（留空回落档位默认）", open_state=False)
        advanced.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)
        _guiwindow.pair_row(
            body,
            0,
            "上下文",
            ttk.Entry(body, textvariable=self._ctx_var),
            "KV",
            ttk.Combobox(body, textvariable=self._kv_var, values=("", *KV_SCALE), state="readonly"),
            theme=self._theme,
        )
        _guiwindow.pair_row(
            body,
            1,
            "ubatch",
            ttk.Entry(body, textvariable=self._ubatch_var),
            "模型别名（留空自动）",
            ttk.Entry(body, textvariable=self._alias_var),
            theme=self._theme,
        )
        _guiwindow.pair_row(
            body,
            2,
            "并发槽（-np）",
            ttk.Entry(body, textvariable=self._parallel_var),
            "HTTP 线程",
            ttk.Entry(body, textvariable=self._threads_http_var),
            theme=self._theme,
        )
        _guiwindow.pair_row(
            body,
            3,
            "API Key",
            ttk.Entry(body, textvariable=self._api_key_var),
            "等待就绪（秒）",
            ttk.Entry(body, textvariable=self._wait_var),
            theme=self._theme,
        )

    def _collapsible(self, parent: Any, title: str, *, open_state: bool = True) -> Any:
        """创建可折叠分区，返回（外层容器, 内容 Frame）。"""
        return _guiwindow.collapsible(
            parent,
            title,
            padding=(self._theme.spacing.sm, self._theme.spacing.xs, 0, 0),
            open_state=open_state,
        )

    def _build_model_browser(self, parent: Any) -> None:
        """模型页：浏览 / 已安装 / 下载三个子页（整合原 llmdownw）。"""
        self._model_page = _ModelBrowserPage(
            parent,
            tk=self._tk,
            ttk=self._ttk,
            theme=self._theme,
            messagebox=self._messagebox,
            settings=settings_from_dict(getattr(self._settings, "download", None)),
        )

    def _build_models(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已配置模型（llmenv 登记的模型目录）", padding=self._theme.spacing.sm)
        box.pack(fill="both", expand=True, pady=(self._theme.spacing.sm, 0))

        row = ttk.Frame(box)
        row.pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))
        ttk.Entry(row, textvariable=self._model_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, self._theme.spacing.sm)
        )
        self._model_btn = ttk.Button(row, text="刷新", command=self._refresh_models)
        self._model_btn.pack(side="left")
        self._browse_btn = ttk.Button(row, text="浏览…", command=self._select_file)
        self._browse_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._clear_btn = ttk.Button(row, text="清除选择", command=self._clear_selection)
        self._clear_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))

        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("model", "size", "profiles", "tags", "fit"),
            show="headings",
            selectmode="browse",
            height=4,
        )
        self._tree.heading("model", text="模型")
        self._tree.heading("size", text="大小")
        self._tree.heading("profiles", text="匹配档位")
        self._tree.heading("tags", text="标签")
        self._tree.heading("fit", text="硬件")
        self._tree.column("model", width=380, anchor="w")
        self._tree.column("size", width=90, anchor="e", stretch=False)
        self._tree.column("profiles", width=130, anchor="w", stretch=False)
        self._tree.column("tags", width=100, anchor="w", stretch=False)
        self._tree.column("fit", width=90, anchor="w", stretch=False)
        self._strike_font = self._font.nametofont("TkDefaultFont").copy()
        self._strike_font.configure(overstrike=True)
        self._tree.tag_configure("unfit", foreground=self._theme.palette.danger, font=self._strike_font)
        self._tree.tag_configure("busy", foreground=self._theme.palette.warning)
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=yscroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._on_model_selected)

        self._hint_var = self._tk.StringVar(value=_HINT_PLACEHOLDER)
        hint_label = ttk.Label(box, textvariable=self._hint_var, justify="left")
        hint_label.pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))
        hint_label.bind(
            "<Configure>",
            lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
        )

    def _build_loaded(self, parent: Any) -> None:
        """已加载模型：列出运行中的服务及其模型、档位与地址。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已加载模型（运行中的服务）", padding=self._theme.spacing.sm)
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
        ttk.Label(box, textvariable=self._loaded_var).pack(fill="x", pady=(self._theme.spacing.xs, 0))

    def _refresh_loaded(self) -> None:
        """刷新“已加载模型”列表（只统计进程仍存活的服务）。"""
        for item in self._loaded_tree.get_children():
            self._loaded_tree.delete(item)
        alive = [state for state in llmenv.list_states() if llmserve.process_alive(state.pid, state.create_time)]
        self._running_paths = {llmenv.path_key(Path(state.model)) for state in alive}
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
        box = ttk.LabelFrame(
            parent,
            text=f"实时性能（服务运行中每 1s 采样 /metrics，速率按 {RATE_WINDOW_SECONDS:.0f}s 窗口平滑）",
            padding=self._theme.spacing.sm,
        )
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
            cell.grid(
                row=row, column=column, sticky="w", padx=(0, self._theme.spacing.lg), pady=self._theme.spacing.xxs
            )
            ttk.Label(cell, text=label + ":").pack(side="left")
            ttk.Label(cell, textvariable=self._metric_vars[key], font=("", 9, "bold")).pack(
                side="left", padx=(self._theme.spacing.xs, 0)
            )

    def _build_load(self, parent: Any) -> None:
        """系统负载面板：CPU / 频率 / 内存 / 交换与服务进程关键指标。"""
        ttk = self._ttk
        box = ttk.LabelFrame(
            parent, text="系统负载（每 1s 采样；服务运行时额外显示服务进程指标）", padding=self._theme.spacing.sm
        )
        box.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        items = (
            ("CPU", "cpu"),
            ("频率", "cpu_freq"),
            ("内存", "memory"),
            ("交换", "swap"),
            ("服务 CPU", "proc_cpu"),
            ("服务内存", "proc_mem"),
            ("线程", "proc_threads"),
            ("运行时长", "uptime"),
        )
        self._metric_grid(box, items, self._load_vars)

    def _build_gpu(self, parent: Any) -> None:
        """GPU 负载面板：利用率 / 显存 / 温度 / 功耗 / 风扇 / 频率。"""
        ttk = self._ttk
        box = ttk.LabelFrame(
            parent,
            text="GPU 负载（nvidia-smi，每 1s 采样；服务运行时额外显示服务显存）",
            padding=self._theme.spacing.sm,
        )
        box.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        for column in range(4):
            box.columnconfigure(column, weight=1)
        ttk.Label(box, text="设备:").grid(
            row=0, column=0, sticky="w", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        gpu_name_label = ttk.Label(
            box,
            textvariable=self._gpu_vars["gpu_name"],
            font=("", 9, "bold"),
            justify="left",
        )
        gpu_name_label.grid(row=0, column=1, columnspan=3, sticky="we", pady=self._theme.spacing.xxs)
        gpu_name_label.bind(
            "<Configure>",
            lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
        )
        items = (
            ("使用率", "gpu_util"),
            ("显存", "gpu_vram"),
            ("服务显存", "gpu_proc_vram"),
            ("温度", "gpu_temp"),
            ("功耗", "gpu_power"),
            ("风扇", "gpu_fan"),
            ("核心频率", "gpu_clock"),
        )
        self._metric_grid(box, items, self._gpu_vars, start_row=1)

    def _metric_grid(
        self,
        box: Any,
        items: tuple[tuple[str, str], ...],
        variables: dict[str, Any],
        *,
        columns: int = 4,
        start_row: int = 0,
    ) -> None:
        """把“标签 + 数值”按网格排入面板，供系统负载与 GPU 面板复用。"""
        ttk = self._ttk
        for column in range(columns):
            box.columnconfigure(column, weight=1)
        for index, (label, key) in enumerate(items):
            row, column = divmod(index, columns)
            cell = ttk.Frame(box)
            cell.grid(
                row=start_row + row,
                column=column,
                sticky="w",
                padx=(0, self._theme.spacing.lg),
                pady=self._theme.spacing.xxs,
            )
            ttk.Label(cell, text=label + ":").pack(side="left")
            ttk.Label(cell, textvariable=variables[key], font=("", 9, "bold")).pack(
                side="left", padx=(self._theme.spacing.xs, 0)
            )

    def _build_actions(self, outer: Any) -> None:
        ttk = self._ttk
        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))
        self._preview_btn = ttk.Button(actions, text="预览计划", command=self._preview)
        self._preview_btn.pack(side="left")
        self._start_btn = ttk.Button(actions, text="启动服务", command=self._start, style="Accent.TButton")
        self._start_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._stop_btn = ttk.Button(actions, text="停止全部服务", command=self._stop, style="Danger.TButton")
        self._stop_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._status_btn = ttk.Button(actions, text="刷新状态", command=self._refresh_status)
        self._status_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._logs_btn = ttk.Button(actions, text="查看日志", command=self._show_logs)
        self._logs_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._browser_btn = ttk.Button(actions, text="打开浏览器", command=self._open_browser)
        self._browser_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._clear_log_btn = ttk.Button(actions, text="清空日志", command=self._clear_log)
        self._clear_log_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._progress = ttk.Progressbar(actions, mode="indeterminate", length=200)
        self._progress.pack(side="right")

    def _build_log(self, parent: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        log_frame = ttk.LabelFrame(parent, text="输出日志", padding=self._theme.spacing.sm)
        log_frame.pack(fill="both", expand=True, pady=(self._theme.spacing.sm, 0))
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self._log_text = tk.Text(log_frame, wrap="word", state="disabled", yscrollcommand=scrollbar.set, height=10)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    def _build_bench(self, parent: Any) -> None:
        """性能测试页：典型场景多选、测试参数、开始 / 停止与结果表。"""
        ttk = self._ttk
        space = self._theme.spacing
        status = ttk.Label(parent, textvariable=self._bench_status_var)
        status.pack(side="bottom", fill="x", pady=(space.xs, 0))

        scenarios_box = ttk.LabelFrame(parent, text="测试场景（点击“选”列勾选，默认全选）", padding=space.sm)
        scenarios_box.pack(fill="x")
        self._bench_tree = ttk.Treeview(
            scenarios_box,
            columns=("check", "label", "input", "output", "detail"),
            show="headings",
            selectmode="browse",
            height=len(SCENARIOS),
        )
        self._bench_tree.heading("check", text="选")
        self._bench_tree.heading("label", text="场景")
        self._bench_tree.heading("input", text="输入")
        self._bench_tree.heading("output", text="输出上限")
        self._bench_tree.heading("detail", text="说明")
        self._bench_tree.column("check", width=40, anchor="center", stretch=False)
        self._bench_tree.column("label", width=130, anchor="w", stretch=False)
        self._bench_tree.column("input", width=110, anchor="e", stretch=False)
        self._bench_tree.column("output", width=90, anchor="e", stretch=False)
        self._bench_tree.column("detail", width=420, anchor="w")
        for index, scenario in enumerate(SCENARIOS):
            self._bench_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    BENCH_CHECKED,
                    scenario.label,
                    "默认短提示" if not scenario.prompt_tokens else f"约 {scenario.prompt_tokens} tok",
                    f"{scenario.max_tokens} tok",
                    scenario.detail,
                ),
            )
        self._bench_tree.bind("<Button-1>", self._toggle_bench_scenario)
        self._bench_tree.pack(fill="x")

        params = ttk.LabelFrame(parent, text="测试参数", padding=space.sm)
        params.pack(fill="x", pady=(space.sm, 0))
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)
        _guiwindow.pair_row(
            params,
            0,
            "重复次数",
            ttk.Spinbox(params, from_=1, to=20, textvariable=self._bench_repeat_var, width=8),
            "请求超时（秒）",
            ttk.Entry(params, textvariable=self._bench_timeout_var),
            theme=self._theme,
        )
        _guiwindow.pair_row(
            params,
            1,
            "输出上限（留空按场景）",
            ttk.Entry(params, textvariable=self._bench_tokens_var),
            "",
            ttk.Label(params, text=""),
            theme=self._theme,
        )

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(space.sm, 0))
        self._bench_start_btn = ttk.Button(actions, text="开始测试", command=self._start_bench, style="Accent.TButton")
        self._bench_start_btn.pack(side="left")
        self._bench_stop_btn = ttk.Button(
            actions, text="停止", command=self._stop_bench, style="Danger.TButton", state="disabled"
        )
        self._bench_stop_btn.pack(side="left", padx=(space.sm, 0))
        self._bench_clear_btn = ttk.Button(actions, text="清空结果", command=self._clear_bench)
        self._bench_clear_btn.pack(side="left", padx=(space.sm, 0))
        self._bench_progress = ttk.Progressbar(actions, mode="indeterminate", length=180)
        self._bench_progress.pack(side="right")

        result_box = ttk.LabelFrame(parent, text="测试结果", padding=space.sm)
        result_box.pack(fill="both", expand=True, pady=(space.sm, 0))
        self._bench_result_tree = ttk.Treeview(
            result_box,
            columns=RESULT_COLUMNS,
            show="headings",
            height=6,
        )
        widths = (130, 60, 90, 90, 90, 110, 120, 220)
        for column, width in zip(RESULT_COLUMNS, widths, strict=True):
            self._bench_result_tree.heading(column, text=column)
            self._bench_result_tree.column(
                column,
                width=width,
                anchor="e" if column not in ("场景", "状态") else "w",
                stretch=column in ("场景", "状态"),
            )
        self._bench_result_tree.tag_configure("error", foreground=self._theme.palette.danger)
        self._bench_result_tree.tag_configure("cancelled", foreground=self._theme.palette.warning)
        ttk.Label(
            result_box,
            text="聚合 tok/s = 输出 tokens ÷ 墙钟（并发场景体现总吞吐）；单请求 tok/s 取服务端 decode 速率（不含 prefill）",
            justify="left",
        ).pack(side="bottom", fill="x", pady=(space.xs, 0))
        yscroll = ttk.Scrollbar(result_box, orient="vertical", command=self._bench_result_tree.yview)
        self._bench_result_tree.configure(yscrollcommand=yscroll.set)
        self._bench_result_tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")

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
        self._scan_thread = threading.Thread(target=self._scan_models, daemon=True)
        self._scan_thread.start()
        self.root.after(200, self._poll_scan)

    def _scan_models(self) -> None:
        """后台线程：扫描模型目录并收集引擎 / GPU 信息。"""
        entries: list[ModelEntry] = []
        try:
            root = llmenv.models_dir()
            if root.is_dir():
                entries = llmmodel.scan_models(root)
            entries = self._with_running_models(entries)
        except OSError:
            entries = []
        gpu = llmengine.detect_gpu()
        gpu_mib = gpu.memory_mib if gpu is not None else None
        usage = self._gpu_usage()
        free_mib = max(usage[1] - usage[0], 0) if usage is not None else None
        precisions = {llmenv.path_key(entry.path): detect_precision(entry.path) for entry in entries}
        self._scan_results.put((entries, gpu_mib, free_mib, precisions))

    @staticmethod
    def _with_running_models(entries: list[ModelEntry]) -> list[ModelEntry]:
        """把运行中服务的模型并入清单，便于停止已加载模型时在列表中找到。"""
        seen = {llmenv.path_key(entry.path) for entry in entries}
        for state in llmenv.list_states():
            path = Path(state.model)
            key = llmenv.path_key(path)
            if key in seen or not llmserve.process_alive(state.pid, state.create_time):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            seen.add(key)
            entries.append(
                ModelEntry(
                    root=path.parent,
                    path=path,
                    size=size,
                    profiles=tuple(name for name, prof in PROFILES.items() if prof.quant.lower() in path.name.lower()),
                    tags=llmmodel.detect_model_tags(path.name),
                )
            )
        entries.sort(key=lambda entry: (-entry.size, str(entry.path)))
        return entries

    def _poll_scan(self) -> None:
        try:
            entries, gpu_mib, free_mib, precisions = self._scan_results.get_nowait()
        except queue.Empty:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                self.root.after(200, self._poll_scan)
            return
        self._apply_models(entries, gpu_mib, free_mib, precisions)

    def _apply_models(
        self,
        entries: list[ModelEntry],
        gpu_mib: int | None,
        free_mib: int | None,
        precisions: dict[str, GgufPrecision],
    ) -> None:
        self._models = entries
        self._precisions = precisions
        self._gpu_mib = gpu_mib
        self._gpu_free_mib = free_mib
        if self._selected_model is None:
            state = self._load_state_safe(self._name_var.get().strip())
            if state is not None and llmserve.process_alive(state.pid, state.create_time):
                self._select_model(Path(state.model), update_name=False)
        self._render_models()
        self._model_var.set(str(self._selected_model) if self._selected_model else _MODEL_PLACEHOLDER)
        if self._selected_model is not None:
            self._sync_tree_selection(self._selected_model)
        self._model_btn.configure(state="normal")
        self._refresh_hardware_view()
        self._refresh_recommendations()
        if not entries:
            self._log("未发现已配置的 GGUF：用 fcmd llmenv add 登记模型目录，或直接“浏览…”选择模型文件")

    def _row_state(self, entry: ModelEntry) -> str:
        """单个模型的硬件状态：运行中 > 硬件不足 > 需释放显存 > 可用。"""
        if llmenv.path_key(entry.path) in self._running_paths:
            return _FIT_RUNNING
        return _fit_state(*self._fits(entry.size))

    @staticmethod
    def _state_tag(state: str) -> tuple[str, ...]:
        """状态对应的 Treeview tag（用于行着色）。"""
        return {_FIT_UNSUPPORTED: ("unfit",), _FIT_BUSY: ("busy",)}.get(state, ())

    def _render_models(self) -> None:
        """重建模型列表：硬件不足标红，仅当前余量不足标橙，并显示对应状态。"""
        self._tree.delete(*self._tree.get_children())
        for index, entry in enumerate(self._models):
            state = self._row_state(entry)
            name = entry.relative
            precision = self._precisions.get(llmenv.path_key(entry.path))
            if precision is not None:
                name = f"{name}（{precision.note}）"
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    name,
                    human_size(entry.size),
                    ", ".join(entry.profiles) or "-",
                    " · ".join(entry.tags) or "-",
                    state,
                ),
                tags=self._state_tag(state),
            )
        self._show_basis_hint()

    def _update_fit_states(self) -> None:
        """按最新可用显存原地更新各行状态（不重建列表，保留选中项）。"""
        for index, entry in enumerate(self._models):
            iid = str(index)
            if not self._tree.exists(iid):
                continue
            state = self._row_state(entry)
            values = self._tree.item(iid, "values")
            if values and values[-1] != state:
                updated = (*values[:-1], state)
                self._tree.item(iid, values=updated, tags=self._state_tag(state))
        self._show_basis_hint()

    def _show_basis_hint(self) -> None:
        """未选中模型时在提示区展示硬件评估口径。"""
        if self._selected_model is not None:
            return
        available = self._gpu_free_mib if self._gpu_free_mib is not None else self._gpu_mib
        if available is None:
            self._hint_var.set("未检测到可用 NVIDIA GPU，无法评估显存适配（CPU 推理会很慢）")
            return
        _, context, kv_type = self._profile_context()
        tune = _llmtune.LABEL_TO_TUNE.get(self._tune_var.get().strip(), "auto")
        tuning = _llmtune.resolve(tune, context=context, model_bytes=0, gpu_mib=self._gpu_mib)
        kv_type = tuning.kv_type or kv_type
        total = f"{self._gpu_mib} MiB" if self._gpu_mib is not None else "-"
        self._hint_var.set(
            f"硬件评估口径：整卡 {total}，当前可用 {available} MiB，上下文 {context}，KV {kv_type}，"
            f"调优 {tuning.label}；红色=硬件不足（清理显存也放不下），橙色=需释放显存（整卡够用），"
            "选择模型可查看具体建议"
        )
