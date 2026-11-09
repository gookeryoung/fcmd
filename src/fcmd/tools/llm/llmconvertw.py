"""llmconvertw - HF safetensors 转 GGUF 的 Tk 窗口版。

基于 :mod:`fcmd.tools.llm.llmconvert` 的核心逻辑，提供源目录 / 输出路径选择，
转换精度 / 量化类型 / 解释器 / 源码脚本 / 引擎 tag 配置，可预览转换计划，
后台执行转换并显示实时日志与进度；启动时后台探测引擎、版本、GPU 与模型目录。
转换与计划准备均在后台线程执行，界面不阻塞。转换脚本与量化器输出按行实时
回显，并解析 tqdm 的百分比刷新整体进度条（``\\r`` 覆盖式更新也能即时反映）。

示例
----
    fcmd llmconvertw
"""

from __future__ import annotations

import contextlib
import queue
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import print_error
from fcmd.tools.llm import llmbase, llmconvert, llmengine, llmenv, llmqnt
from fcmd.tools.llm.llmbase import EngineInfo, ServeError, human_size

__all__ = ["main"]

_DESCRIPTION = (
    "HF safetensors 转 GGUF 窗口版（Tk）：启动时后台扫描 llmenv 已配置的模型目录，"
    "识别并列出可转换的 HF safetensors 目录；配置精度、解释器、源码脚本与引擎 tag，"
    "勾选多种输出格式（原始 / 量化），已生成的格式自动标记并置为只读避免覆盖；"
    "可预览转换计划，后台先转换一次再批量量化，实时显示日志与进度"
)

_SCRIPT_PLACEHOLDER = "留空自动获取与引擎 tag 匹配的 llama.cpp 源码"
_PERCENT_RE = re.compile(r"(\d{1,3})%\|")


def _progress_percent(line: str) -> int | None:
    """从 tqdm 进度行（如 `` 45%|████▌ |``）提取百分比，非进度行返回 ``None``。"""
    match = _PERCENT_RE.search(line)
    if match is None:
        return None
    return min(100, int(match.group(1)))


def _hf_size(path: Path) -> int:
    """统计目录下 safetensors 文件总字节数（不可读文件按 0 计）。"""
    total = 0
    for item in path.glob("*.safetensors"):
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def _dir_stats(path: Path) -> str:
    """统计目录下 safetensors 文件数量与总大小（用于转换前的规模预估）。"""
    count = len(list(path.glob("*.safetensors")))
    return f"{count} 个 safetensors，共 {human_size(_hf_size(path))}"


def _find_convertible(roots: list[Path]) -> list[tuple[Path, int]]:
    """在模型目录中查找可转换的 HF safetensors 目录（含根目录及向下两层）。

    兼容 ``<root>/<模型>`` 与 ``<root>/<组织>/<模型>`` 两种常见布局；命中 HF 目录后
    不再继续向下扫描，避免把模型内部文件误判为可转换目录。
    """
    found: list[tuple[Path, int]] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = llmenv.path_key(path)
        if key in seen:
            return
        seen.add(key)
        found.append((path, _hf_size(path)))

    for root in roots:
        if not root.is_dir():
            continue
        try:
            if llmconvert.is_hf_safetensors_dir(root):
                add(root)
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if llmconvert.is_hf_safetensors_dir(child):
                    add(child)
                    continue
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir() and llmconvert.is_hf_safetensors_dir(grandchild):
                        add(grandchild)
        except OSError:
            continue
    return found


class ConvertWindow:
    """Tk 转换窗口：参数表单 + 计划预览 + 后台转换。"""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self.root = tk.Tk()
        self.root.title("HF 转 GGUF 转换")
        self.root.geometry("960x760")
        self.root.minsize(880, 640)

        self._running = False
        self._closing = False
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None
        self._scan_thread: threading.Thread | None = None
        self._scan_results: queue.Queue[tuple[dict[str, str], str | None, list[tuple[Path, int]]]] = queue.Queue()
        self._convertible: list[tuple[Path, int]] = []

        self._source_var = tk.StringVar(value="")
        self._output_var = tk.StringVar(value="")
        self._outtype_var = tk.StringVar(value="f16")
        self._script_var = tk.StringVar(value="")
        self._python_var = tk.StringVar(value="")
        self._tag_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="状态：就绪")
        self._info_vars: dict[str, tk.StringVar] = {}
        self._format_vars: dict[str, tk.BooleanVar] = {
            "raw": tk.BooleanVar(value=True),
            **{quant: tk.BooleanVar(value=False) for quant in llmqnt.QUANT_TYPES},
        }
        self._format_checks: dict[str, Any] = {}
        self._overwrite_var = tk.BooleanVar(value=False)

        self._build_widgets()
        self._source_var.trace_add("write", lambda *_: self._refresh_existing())
        self._output_var.trace_add("write", lambda *_: self._refresh_existing())
        self._refresh_env()

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
        models_page = ttk.Frame(self._notebook, padding=10)
        config_page = ttk.Frame(self._notebook, padding=10)
        log_page = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(models_page, text="模型列表")
        self._notebook.add(config_page, text="转换配置")
        self._notebook.add(log_page, text="输出日志")
        self._log_page = log_page
        self._build_io(config_page)
        self._build_params(config_page)
        self._build_formats(config_page)
        self._build_env(models_page)
        self._build_models(models_page)
        self._build_log(log_page)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_io(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="输入 / 输出", padding=8)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="源目录").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(box, textvariable=self._source_var).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=3)
        self._source_btn = ttk.Button(box, text="浏览…", command=self._pick_source)
        self._source_btn.grid(row=0, column=2, pady=3)

        ttk.Label(box, text="输出 GGUF").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(box, textvariable=self._output_var).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=3)
        self._output_btn = ttk.Button(box, text="另存为…", command=self._pick_output)
        self._output_btn.grid(row=1, column=2, pady=3)
        ttk.Label(box, text="输出留空时写入默认模型目录：<模型目录>/<源目录名>/<源目录名>[-量化].gguf").grid(
            row=2, column=1, sticky="w", pady=(2, 0)
        )

    def _build_models(self, parent: Any) -> None:
        """已配置模型列表：自动识别模型目录下可转换的 HF safetensors 目录。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已配置模型（可转换的 HF safetensors 目录）", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))

        row = ttk.Frame(box)
        row.pack(side="bottom", fill="x", pady=(6, 0))
        self._models_hint_var = self._tk.StringVar(value="正在扫描模型目录…")
        ttk.Label(box, textvariable=self._models_hint_var, wraplength=780, justify="left").pack(
            side="bottom", fill="x", pady=(4, 0)
        )
        self._models_btn = ttk.Button(row, text="刷新", command=self._refresh_env)
        self._models_btn.pack(side="left")

        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("model", "size", "path"),
            show="headings",
            selectmode="browse",
            height=4,
        )
        self._tree.heading("model", text="模型")
        self._tree.heading("size", text="大小")
        self._tree.heading("path", text="位置")
        self._tree.column("model", width=220, anchor="w")
        self._tree.column("size", width=90, anchor="e", stretch=False)
        self._tree.column("path", width=430, anchor="w")
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=yscroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._on_model_selected)

    def _on_model_selected(self, _event: object | None = None) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._convertible):
            path = self._convertible[index][0]
            self._source_var.set(str(path))
            self._status_var.set(f"状态：已选择 {path.name}（{_dir_stats(path)}）")
            self._log(f"已选择可转换模型: {path}")

    def _sync_selection(self, path: Path) -> None:
        """手动选择源目录后，若命中已配置模型则同步列表选中。"""
        key = llmenv.path_key(path)
        for index, (candidate, _) in enumerate(self._convertible):
            if llmenv.path_key(candidate) == key:
                self._tree.selection_set(str(index))
                self._tree.see(str(index))
                return
        self._tree.selection_remove(*self._tree.selection())

    def _build_params(self, parent: Any) -> None:
        ttk = self._ttk
        params = ttk.LabelFrame(parent, text="转换参数", padding=8)
        params.pack(fill="x", pady=(8, 0))
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)

        self._pair_row(
            params,
            0,
            "转换精度",
            ttk.Combobox(
                params,
                textvariable=self._outtype_var,
                values=llmconvert.CONVERT_OUTTYPES,
                state="readonly",
            ),
            "引擎 tag",
            ttk.Entry(params, textvariable=self._tag_var),
        )

        ttk.Label(params, text="解释器").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(params, textvariable=self._python_var).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=3
        )

        ttk.Label(params, text="转换脚本").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(params, textvariable=self._script_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=3
        )
        self._script_btn = ttk.Button(params, text="浏览…", command=self._pick_script)
        self._script_btn.grid(row=2, column=3, sticky="w", pady=3)

        ttk.Label(
            params,
            text=(
                f"精度可选：{' / '.join(llmconvert.CONVERT_OUTTYPES)}；"
                "解释器留空自动探测已安装 torch / safetensors / numpy；"
                f"脚本可填 convert_hf_to_gguf.py 或源码 zip，{_SCRIPT_PLACEHOLDER}"
            ),
            wraplength=860,
            justify="left",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

    def _build_formats(self, parent: Any) -> None:
        """输出格式勾选区：多选量化类型，已存在的输出置为只读避免覆盖。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="输出格式（勾选要生成的版本，已存在的格式只读）", padding=8)
        box.pack(fill="x", pady=(8, 0))
        for column in range(4):
            box.columnconfigure(column, weight=1)

        raw_check = ttk.Checkbutton(box, text="原始 GGUF（不量化）", variable=self._format_vars["raw"])
        raw_check.grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self._format_checks["raw"] = raw_check
        for index, (quant, desc) in enumerate(llmqnt.QUANT_TYPES.items()):
            row, column = divmod(index, 4)
            check = ttk.Checkbutton(box, text=f"{quant}  {desc}", variable=self._format_vars[quant])
            check.grid(row=row + 1, column=column, sticky="w", padx=6, pady=3)
            self._format_checks[quant] = check

        self._overwrite_check = ttk.Checkbutton(
            box,
            text="覆盖已存在的输出文件",
            variable=self._overwrite_var,
            command=self._refresh_existing,
        )
        self._overwrite_check.grid(row=0, column=2, columnspan=2, sticky="w", padx=6, pady=3)

    def _base_output(self) -> Path | None:
        """当前表单对应的基础 GGUF 输出路径（未选择源目录时返回 None）。"""
        source_text = self._source_var.get().strip()
        if not source_text:
            return None
        output_text = self._output_var.get().strip()
        if output_text:
            return Path(output_text).expanduser()
        source = Path(source_text).expanduser()
        root = llmenv.models_roots()[0].expanduser()
        return root / source.name / f"{source.name}.gguf"

    @staticmethod
    def _quant_target(base: Path, quant: str) -> Path:
        """由基础输出路径推导量化输出路径（与 llmqnt 命名口径一致）。"""
        return base.with_name(f"{llmqnt.strip_f16_suffix(base.stem)}-{quant}.gguf")

    def _selected_formats(self) -> tuple[bool, list[str]]:
        """返回已勾选且未被只读过滤的（原始输出, 量化类型列表）。"""
        overwrite = self._overwrite_var.get()
        base = self._base_output()
        raw = bool(self._format_vars["raw"].get())
        if raw and not overwrite and base is not None and base.is_file():
            raw = False
        quants: list[str] = []
        for quant in llmqnt.QUANT_TYPES:
            if not self._format_vars[quant].get():
                continue
            if not overwrite and base is not None and self._quant_target(base, quant).is_file():
                continue
            quants.append(quant)
        return raw, quants

    def _refresh_existing(self) -> None:
        """识别已存在的输出格式，未勾选覆盖时勾选并置为只读。"""
        if self._running or self._closing:
            return
        base = self._base_output()
        overwrite = self._overwrite_var.get()
        for key, check in self._format_checks.items():
            if key == "raw":
                label = "原始 GGUF（不量化）"
                exists = base is not None and base.is_file()
            else:
                label = f"{key}  {llmqnt.QUANT_TYPES[key]}"
                exists = base is not None and self._quant_target(base, key).is_file()
            check.configure(
                text=label + ("（已存在）" if exists else ""),
                state="normal" if overwrite or not exists else "disabled",
            )
            if exists and not overwrite:
                self._format_vars[key].set(True)

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

    def _build_env(self, parent: Any) -> None:
        ttk = self._ttk
        box, body = self._collapsible(parent, "环境信息（引擎 / 版本 / GPU / 模型目录）", open_state=False)
        box.pack(side="bottom", fill="x", pady=(8, 0))
        body.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate(
            (("engine", "引擎"), ("version", "版本"), ("gpu", "GPU"), ("models", "模型目录"))
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8))
            var = self._tk.StringVar(value="检测中…")
            ttk.Label(body, textvariable=var, wraplength=760, justify="left").grid(row=row, column=1, sticky="w")
            self._info_vars[key] = var

    def _build_actions(self, outer: Any) -> None:
        ttk = self._ttk
        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        self._preview_btn = ttk.Button(actions, text="预览计划", command=self._preview)
        self._preview_btn.pack(side="left")
        self._start_btn = ttk.Button(actions, text="开始转换", command=self._start)
        self._start_btn.pack(side="left", padx=(6, 0))
        self._clear_log_btn = ttk.Button(actions, text="清空日志", command=self._clear_log)
        self._clear_log_btn.pack(side="left", padx=(6, 0))
        self._progress = ttk.Progressbar(actions, mode="determinate", maximum=100, length=200)
        self._progress.pack(side="right")

    def _build_log(self, parent: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        log_frame = ttk.LabelFrame(parent, text="输出日志", padding=6)
        log_frame.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self._log_text = tk.Text(log_frame, wrap="none", state="disabled", yscrollcommand=scrollbar.set, height=10)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    # ------------------------------------------------------------------ #
    # 文件选择
    # ------------------------------------------------------------------ #
    def _pick_source(self) -> None:
        path = self._filedialog.askdirectory(title="选择 HF safetensors 模型目录")
        if not path:
            return
        self._source_var.set(path)
        self._sync_selection(Path(path).resolve())
        self._status_var.set(f"状态：已选择源目录 {Path(path).name}")

    def _pick_output(self) -> None:
        path = self._filedialog.asksaveasfilename(
            title="选择输出 GGUF 文件",
            defaultextension=".gguf",
            filetypes=[("GGUF 文件", "*.gguf"), ("所有文件", "*.*")],
        )
        if path:
            self._output_var.set(path)

    def _pick_script(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择转换脚本或 llama.cpp 源码包",
            filetypes=[
                ("转换脚本 / 源码包", "*.py *.zip"),
                ("Python 脚本", "*.py"),
                ("源码压缩包", "*.zip"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._script_var.set(path)

    # ------------------------------------------------------------------ #
    # 环境信息
    # ------------------------------------------------------------------ #
    def _refresh_env(self) -> None:
        """后台探测引擎 / GPU / 模型目录，避免阻塞窗口。"""
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._models_btn.configure(state="disabled")
        self._models_hint_var.set("正在扫描模型目录…")
        for var in self._info_vars.values():
            var.set("检测中…")
        self._scan_thread = threading.Thread(target=self._scan_env, daemon=True)
        self._scan_thread.start()
        self.root.after(200, self._poll_env)

    def _scan_env(self) -> None:
        """后台线程：收集当前引擎 tag / 版本、GPU 与模型目录。"""
        info: dict[str, str] = {"engine": "", "version": "", "gpu": "", "models": ""}
        tag: str | None = None
        try:
            engine = llmengine.resolve_engine()
        except ServeError as exc:
            info["engine"] = f"未找到（{exc}）"
            info["version"] = "-"
        else:
            tag = engine.tag
            info["engine"] = f"{engine.tag} · {engine.exe}"
            info["version"] = self._probe_version(engine)
        gpu = llmengine.detect_gpu()
        info["gpu"] = f"{gpu.name}（{gpu.memory_mib} MiB）" if gpu else "未检测到 NVIDIA GPU"
        roots = llmenv.models_roots()
        info["models"] = " · ".join(str(path) for path in roots)
        self._scan_results.put((info, tag, _find_convertible(roots)))

    @staticmethod
    def _probe_version(engine: EngineInfo) -> str:
        """执行 ``llama-server --version`` 取首行版本信息。"""
        result = llmengine.run_with_engine(llmbase.run_command, [str(engine.exe), "--version"], engine)
        output = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            detail = output.splitlines()[0] if output else f"退出码 {llmbase.format_returncode(result.returncode)}"
            return f"获取失败（{detail}）"
        return output.splitlines()[0] if output else "未知"

    def _poll_env(self) -> None:
        try:
            info, tag, convertible = self._scan_results.get_nowait()
        except queue.Empty:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                self.root.after(200, self._poll_env)
            return
        for key, var in self._info_vars.items():
            var.set(info.get(key) or "-")
        if tag and not self._tag_var.get().strip():
            self._tag_var.set(tag)
        self._apply_models(convertible)

    def _apply_models(self, convertible: list[tuple[Path, int]]) -> None:
        """填充可转换模型列表，并恢复当前已选源目录的选中状态。"""
        self._convertible = convertible
        self._tree.delete(*self._tree.get_children())
        for index, (path, size) in enumerate(convertible):
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=(path.name, human_size(size), str(path)),
            )
        if convertible:
            self._models_hint_var.set(f"发现 {len(convertible)} 个可转换模型；选中后自动填入源目录")
        else:
            self._models_hint_var.set(
                "未发现可转换模型（模型目录中需含 config.json 与 *.safetensors）；可用 fcmd llmenv add 登记模型目录"
            )
        self._models_btn.configure(state="normal")
        source_text = self._source_var.get().strip()
        if source_text:
            self._sync_selection(Path(source_text).expanduser())

    # ------------------------------------------------------------------ #
    # 计划与转换
    # ------------------------------------------------------------------ #
    def _preview(self) -> None:
        if self._running:
            self._log("已有任务正在进行，请等待完成")
            return
        self._begin_task(self._preview_work)

    def _start(self) -> None:
        if self._running:
            self._log("已有任务正在进行，请等待完成")
            return
        raw, quants = self._selected_formats()
        if not raw and not quants:
            self._messagebox.showwarning(
                "缺少输出格式",
                "请至少勾选一种尚未生成的输出格式（已存在的格式默认只读，可勾选“覆盖已存在的输出文件”后重选）",
            )
            return
        self._begin_task(self._convert_work)

    def _begin_task(self, target: Any) -> None:
        """启动后台线程执行目标函数，结果通过队列回传主线程。"""
        self._notebook.select(self._log_page)
        self._messages = queue.Queue()
        self._thread = threading.Thread(target=target, daemon=True)
        self._set_running(True)
        self._thread.start()
        self.root.after(100, self._poll_task)

    def _prepare_plan(
        self, status: queue.Queue[tuple[str, Any]]
    ) -> tuple[llmconvert.ConvertPlan, Path, list[str], bool]:
        """按表单内容构造基础转换计划（可能探测解释器、准备源码，耗时操作）。

        返回（基础转换计划, 基础输出路径, 待量化类型列表, 是否保留基础文件）。
        多处量化时先转换一次基础 GGUF，再逐个量化，避免重复执行转换脚本。
        """
        source_text = self._source_var.get().strip()
        if not source_text:
            raise ServeError("请选择 HF safetensors 源目录")
        source = Path(source_text).expanduser()
        if not llmconvert.is_hf_safetensors_dir(source):
            raise ServeError(f"不是 HF safetensors 目录（需要 config.json 与 *.safetensors）: {source}")
        status.put(("log", f"源目录: {source}（{_dir_stats(source)}）"))

        python = self._python_var.get().strip()
        if not python:
            status.put(("log", "正在探测已安装 torch / safetensors / numpy 的解释器…"))
            python = llmconvert.find_convert_python()
        status.put(("log", f"解释器: {python}"))
        status.put(("log", "正在准备转换脚本（首次使用会下载 llama.cpp 源码）…"))

        outtype = self._outtype_var.get().strip() or "f16"
        script_text = self._script_var.get().strip()
        raw, quants = self._selected_formats()
        base = self._base_output()
        assert base is not None  # 源目录已校验
        convert_target = base
        if quants and not raw:
            convert_target = base.with_name(f"{base.stem}-{outtype}.gguf")
        plan = llmconvert.plan_convert(
            source,
            output=convert_target,
            outtype=outtype,
            quant=None,
            script=Path(script_text).expanduser() if script_text else None,
            python=python,
            tag=self._tag_var.get().strip() or None,
        )
        return plan, base, quants, raw

    @staticmethod
    def _log_plan(
        plan: llmconvert.ConvertPlan,
        base: Path,
        quants: list[str],
        keep_base: bool,
        status: queue.Queue[tuple[str, Any]],
    ) -> None:
        """把转换计划写入日志队列。"""
        status.put(("log", "转换计划:"))
        status.put(("log", f"  源目录: {plan.source}（{_dir_stats(plan.source)}）"))
        status.put(("log", f"  精度: {plan.outtype}"))
        if keep_base:
            status.put(("log", f"  原始 GGUF: {base}"))
        if quants and not keep_base:
            status.put(("log", f"  中间文件: {plan.output}（量化完成后删除）"))
        for quant in quants:
            status.put(("log", f"  {quant}: {ConvertWindow._quant_target(base, quant)}"))
        status.put(("log", f"  脚本: {plan.script}"))
        status.put(("log", f"  解释器: {plan.python}"))
        status.put(("log", "  转换命令: " + llmbase.format_command(plan.convert_argv())))

    @staticmethod
    def _resolve_quant_exe(tag: str | None) -> Path:
        """优先使用指定 tag 引擎同目录的 llama-quantize，其次走通用查找。"""
        try:
            engine = llmengine.resolve_engine(tag)
        except ServeError:
            return llmqnt.resolve_quantize_exe()
        candidate = llmengine.quantize_exe(engine)
        return candidate if candidate is not None else llmqnt.resolve_quantize_exe()

    def _preview_work(self) -> None:
        assert self._messages is not None
        status = self._messages
        try:
            plan, base, quants, keep_base = self._prepare_plan(status)
        except (ServeError, llmqnt.QuantizeError) as exc:
            status.put(("error", f"构造转换计划失败: {exc}"))
        else:
            self._log_plan(plan, base, quants, keep_base, status)
            status.put(("log", "计划预览完成，可点击“开始转换”执行"))
        finally:
            status.put(("done", None))

    def _convert_work(self) -> None:
        assert self._messages is not None
        status = self._messages
        try:
            plan, base, quants, keep_base = self._prepare_plan(status)
            self._log_plan(plan, base, quants, keep_base, status)
            quant_exe: Path | None = None
            if quants:
                quant_exe = self._resolve_quant_exe(self._tag_var.get().strip() or None)
            total = 1 + len(quants)

            def relay(step: int) -> Callable[[str], None]:
                """把实时输出转为日志或整体进度（``step`` 为 0 起的步骤序号）。"""
                last = -1

                def handle(line: str) -> None:
                    nonlocal last
                    percent = _progress_percent(line)
                    if percent is None:
                        status.put(("log", line))
                        return
                    value = min(99, (step * 100 + percent) // total)
                    if value >= last:
                        last = value
                        status.put(("progress", value))

                return handle

            status.put(("log", f"开始转换 {plan.source.name}（共 {total} 步，可能需要较长时间）…"))
            produced = [str(path) for path in llmconvert.convert_model(plan, on_output=relay(0))]
            status.put(("log", f"已生成: {plan.output}"))
            status.put(("progress", 100 // total))
            for index, quant in enumerate(quants, start=1):
                assert quant_exe is not None
                target = self._quant_target(base, quant)
                status.put(("log", f"正在量化到 {quant}: {target.name}…"))
                llmqnt.run_job(
                    llmqnt.QuantizeJob(plan.output, target, quant),
                    quant_exe,
                    on_output=relay(index),
                )
                produced.append(str(target))
                status.put(("log", f"已生成: {target}"))
                status.put(("progress", (index + 1) * 100 // total))
            if quants and not keep_base:
                with contextlib.suppress(OSError):
                    plan.output.unlink()
                status.put(("log", f"已清理中间文件: {plan.output}"))
        except (ServeError, llmqnt.QuantizeError) as exc:
            status.put(("error", f"转换失败: {exc}"))
        else:
            status.put(("success", produced))
        finally:
            status.put(("done", None))

    def _poll_task(self) -> None:
        """主线程轮询队列，刷新日志与状态。"""
        if self._closing:
            return
        assert self._messages is not None
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "progress":
                    self._progress["value"] = int(payload)
                elif kind == "error":
                    self._log(str(payload))
                    self._messagebox.showerror("错误", str(payload))
                elif kind == "success":
                    paths = [str(item) for item in payload]
                    self._status_var.set("状态：转换完成")
                    self._messagebox.showinfo("转换完成", "已生成:\n" + "\n".join(paths))
                elif kind == "done":
                    self._set_running(False)
        except queue.Empty:
            pass
        if self._thread is not None and self._thread.is_alive():
            self.root.after(100, self._poll_task)

    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        for button in (self._preview_btn, self._start_btn, self._clear_log_btn, self._models_btn):
            button.configure(state=state)
        for button in (self._source_btn, self._output_btn, self._script_btn, self._overwrite_check):
            button.configure(state=state)
        for check in self._format_checks.values():
            check.configure(state=state)
        if running:
            self._progress.configure(mode="determinate", maximum=100, value=0)
            self._status_var.set("状态：转换中…")
        else:
            self._progress["value"] = 0
            if self._status_var.get() == "状态：转换中…":
                self._status_var.set("状态：就绪")
            self._refresh_existing()

    def _close(self) -> None:
        running = self._thread is not None and self._thread.is_alive()
        if running and not self._messagebox.askyesno("任务进行中", "转换任务仍在执行，确实退出？"):
            return
        self._closing = True
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
        window = ConvertWindow()
    except ImportError as exc:
        print_error(f"tkinter 不可用，无法启动窗口版: {exc}")
        return
    window.root.mainloop()


@fcmd.tool("llmconvertw", help="打开 HF 转 GGUF 转换窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 HF 转 GGUF 转换窗口。"""
    run_gui()


@fcmd.main("llmconvertw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
