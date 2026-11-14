"""locllmw「模型」页的转换与量化子页：整合原独立的转换与量化窗口。

两者在模型页中是同级标签页（「转换」之后是「量化」），职责严格分开，不出现重复
或交叉的控件：

* 转换：只负责 HF safetensors → GGUF，扫描模型目录中的可转换目录，配置精度 /
  解释器 / 源码 / 引擎 tag，输出默认 ``<models_dir>/converted/<源目录名>/<源目录名>.gguf``；
* 量化：完整承载 GGUF 量化，选择输入模型、量化类型与输出目录（默认
  ``<models_dir>/converted/``）、覆盖策略，展示量化器环境、进度与日志。

两块都复用 :mod:`llmconvert` / :mod:`llmqnt` 的核心逻辑，不在 GUI 里重写命令。
原独立转换 / 量化窗口已移除。
"""

from __future__ import annotations

import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.locllm import _llmconfig, llmbase, llmconvert, llmengine, llmenv, llmmodel, llmqnt
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision, quantize_input_error
from fcmd.apps.locllm.llmbase import ModelEntry, ServeError, human_size
from fcmd.gui import window as _guiwindow

__all__ = [
    "default_output_path",
    "output_path_from_form",
]

_SCRIPT_PLACEHOLDER = "留空自动获取与引擎 tag 匹配的 llama.cpp 源码"
_PERCENT_RE = re.compile(r"(\d{1,3})%\|")


# ---------------------------------------------------------------------- #
# 纯逻辑：可独立测试，不创建 Tk
# ---------------------------------------------------------------------- #
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


def default_output_path(source: Path, root: Path | None = None) -> Path:
    """默认输出路径：``<模型目录>/converted/<源目录名>/<源目录名>.gguf``。"""
    return _llmconfig.products_dir(root) / source.name / f"{source.name}.gguf"


def output_path_from_form(source_text: str, output_text: str, root: Path | None = None) -> Path | None:
    """按表单文本解析输出路径：显式输出优先，留空时落到模型目录的 converted 子目录。"""
    text = source_text.strip()
    if not text:
        return None
    output = output_text.strip()
    if output:
        return Path(output).expanduser()
    return default_output_path(Path(text).expanduser(), root)


def _find_convertible(root: Path) -> list[tuple[Path, int]]:
    """在模型目录中查找可转换的 HF safetensors 目录（含根目录及向下两层）。

    兼容 ``<root>/<模型>`` 与 ``<root>/<组织>/<模型>`` 两种常见布局；命中 HF 目录后
    不再继续向下扫描，避免把模型内部文件误判为可转换目录。
    """
    found: list[tuple[Path, int]] = []
    if not root.is_dir():
        return found
    try:
        if llmconvert.is_hf_safetensors_dir(root):
            return [(root, _hf_size(root))]
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if llmconvert.is_hf_safetensors_dir(child):
                found.append((child, _hf_size(child)))
                continue
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and llmconvert.is_hf_safetensors_dir(grandchild):
                    found.append((grandchild, _hf_size(grandchild)))
    except OSError:
        return found
    return found


@dataclass(frozen=True, slots=True)
class _ConvertForm:
    """主线程快照的表单值；后台线程不得直接读取 Tk 变量。"""

    source: str
    output: str
    outtype: str
    script: str
    python: str
    tag: str


class _ConvertPage:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """locllmw 的转换与量化子页：两个容器由宿主放入同级标签页，共用任务与队列。"""

    def __init__(  # noqa: PLR0913 - 宿主窗口依赖与页面参数显式传入，避免隐式全局
        self,
        convert_parent: Any,
        quant_parent: Any,
        *,
        tk: Any,
        ttk: Any,
        theme: Any,
        messagebox: Any,
        filedialog: Any,
    ) -> None:
        self._tk = tk
        self._ttk = ttk
        self._theme = theme
        self._messagebox = messagebox
        self._filedialog = filedialog
        self.root = convert_parent.winfo_toplevel()

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._jobs = 0
        self._poll_scheduled = False
        self._closed = False
        self._running = False
        self._task_thread: threading.Thread | None = None

        # 转换
        self._convertible: list[tuple[Path, int]] = []
        self._source_var = tk.StringVar(value="")
        self._output_var = tk.StringVar(value="")
        self._outtype_var = tk.StringVar(value="f16")
        self._script_var = tk.StringVar(value="")
        self._python_var = tk.StringVar(value="")
        self._tag_var = tk.StringVar(value="")
        self._convert_status_var = tk.StringVar(value="状态：就绪")
        self._output_hint_var = tk.StringVar(value="")
        self._existing_var = tk.StringVar(value="")
        self._convert_overwrite_var = tk.BooleanVar(value=False)

        # 量化
        self._quant_input: Path | None = None
        self._quant_models: list[ModelEntry] = []
        self._quant_precisions: dict[str, GgufPrecision] = {}
        self._file_var = tk.StringVar(value="尚未选择输入文件")
        self._model_var = tk.StringVar(value="")
        self._quant_info_vars: dict[str, Any] = {}
        self._quant_overwrite_var = tk.BooleanVar(value=False)
        self._quant_out_dir_var = tk.StringVar(value=str(_llmconfig.products_dir()))
        self._quant_out_dir_auto = True
        self._syncing_out_dir = False
        self._quant_vars: dict[str, Any] = {
            quant: tk.BooleanVar(value=quant in llmqnt.DEFAULT_QUANTS) for quant in llmqnt.QUANT_TYPES
        }
        self._quant_checks: dict[str, Any] = {}

        self._build_widgets(convert_parent, quant_parent)
        self._source_var.trace_add("write", lambda *_: self._refresh_output_state())
        self._output_var.trace_add("write", lambda *_: self._refresh_output_state())
        self._quant_out_dir_var.trace_add("write", lambda *_: self._on_quant_out_dir_edited())
        self._refresh_output_state()
        self.refresh_env()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self, convert_parent: Any, quant_parent: Any) -> None:
        """转换与量化各自占据宿主提供的一个标签页容器，不再内嵌 Notebook。"""
        self._build_convert(convert_parent)
        self._build_quant(quant_parent)

    # -------------------------- 转换页 -------------------------------- #
    def _build_convert(self, parent: Any) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        self._build_convertible(parent)
        self._build_io(parent)
        self._build_params(parent)
        self._build_convert_actions(parent)
        self._build_convert_log(parent)

    def _build_convertible(self, parent: Any) -> None:
        """可转换模型列表：自动识别模型目录下的 HF safetensors 目录。"""
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="可转换模型（HF safetensors 目录）", padding=(space.sm, space.xs))
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)

        bar = ttk.Frame(box)
        bar.grid(row=1, column=0, sticky="ew", pady=(space.xs, 0))
        self._convert_models_btn = ttk.Button(bar, text="刷新模型列表", command=self.refresh_env, width=12)
        self._convert_models_btn.pack(side="left")
        self._convert_models_hint_var = self._tk.StringVar(value="正在扫描模型目录…")
        ttk.Label(bar, textvariable=self._convert_models_hint_var, style="Hint.TLabel").pack(
            side="left", padx=(space.sm, 0)
        )

        table = ttk.Frame(box)
        table.grid(row=0, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        self._convert_tree = ttk.Treeview(
            table,
            columns=("model", "size", "existing", "path"),
            show="headings",
            selectmode="browse",
            height=3,
        )
        for key, label, width, stretch in (
            ("model", "模型", 180, False),
            ("size", "大小", 80, False),
            ("existing", "输出", 100, False),
            ("path", "位置", 340, True),
        ):
            self._convert_tree.heading(key, text=label)
            self._convert_tree.column(key, width=width, anchor="w", stretch=stretch)
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self._convert_tree.yview)
        self._convert_tree.configure(yscrollcommand=scrollbar.set)
        self._convert_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._convert_tree.bind("<<TreeviewSelect>>", self._on_convertible_selected)

    def _build_io(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="输入 / 输出", padding=(space.sm, space.xs))
        box.grid(row=1, column=0, sticky="ew", pady=(space.sm, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="源目录").grid(row=0, column=0, sticky="e", padx=(0, space.sm), pady=space.xxs)
        ttk.Entry(box, textvariable=self._source_var).grid(row=0, column=1, sticky="ew", pady=space.xxs)
        self._source_btn = ttk.Button(box, text="浏览…", command=self._pick_source, width=8)
        self._source_btn.grid(row=0, column=2, sticky="w", padx=(space.xs, 0), pady=space.xxs)

        ttk.Label(box, text="输出 GGUF").grid(row=1, column=0, sticky="e", padx=(0, space.sm), pady=space.xxs)
        ttk.Entry(box, textvariable=self._output_var).grid(row=1, column=1, sticky="ew", pady=space.xxs)
        self._output_btn = ttk.Button(box, text="另存为…", command=self._pick_output, width=8)
        self._output_btn.grid(row=1, column=2, sticky="w", padx=(space.xs, 0), pady=space.xxs)
        ttk.Button(box, text="打开目录", command=self._open_convert_dir, width=9).grid(
            row=1, column=3, sticky="w", padx=(space.xs, 0), pady=space.xxs
        )

        hint_label = ttk.Label(box, textvariable=self._output_hint_var, justify="left", style="Hint.TLabel")
        hint_label.grid(row=2, column=0, columnspan=3, sticky="we", pady=(space.xxs, 0))
        hint_label.bind(
            "<Configure>",
            lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
        )

    def _build_params(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="转换参数", padding=(space.sm, space.xs))
        box.grid(row=2, column=0, sticky="ew", pady=(space.sm, 0))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="转换精度").grid(row=0, column=0, sticky="e", padx=(0, space.sm), pady=space.xxs)
        ttk.Combobox(
            box,
            textvariable=self._outtype_var,
            values=llmconvert.CONVERT_OUTTYPES,
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky="w", pady=space.xxs)
        ttk.Label(box, text="引擎 tag").grid(row=0, column=2, sticky="e", padx=(space.lg, space.sm), pady=space.xxs)
        ttk.Entry(box, textvariable=self._tag_var).grid(row=0, column=3, sticky="ew", pady=space.xxs)

        ttk.Label(box, text="解释器").grid(row=1, column=0, sticky="e", padx=(0, space.sm), pady=space.xxs)
        ttk.Entry(box, textvariable=self._python_var).grid(row=1, column=1, columnspan=3, sticky="ew", pady=space.xxs)

        ttk.Label(box, text="转换脚本").grid(row=2, column=0, sticky="e", padx=(0, space.sm), pady=space.xxs)
        ttk.Entry(box, textvariable=self._script_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=space.xxs)
        self._script_btn = ttk.Button(box, text="浏览…", command=self._pick_script, width=8)
        self._script_btn.grid(row=2, column=3, sticky="w", padx=(space.xs, 0), pady=space.xxs)

        self._convert_overwrite_check = ttk.Checkbutton(
            box,
            text="覆盖已存在的输出文件",
            variable=self._convert_overwrite_var,
            command=self._refresh_existing,
        )
        self._convert_overwrite_check.grid(row=3, column=0, columnspan=2, sticky="w", pady=(space.xs, 0))

        self._existing_label = ttk.Label(box, textvariable=self._existing_var, justify="left", style="Hint.TLabel")
        self._existing_label.grid(row=4, column=0, columnspan=4, sticky="we", pady=(space.xxs, 0))
        self._existing_label.bind(
            "<Configure>",
            lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
        )

        hint = ttk.Label(
            box,
            text=(
                f"精度可选：{' / '.join(llmconvert.CONVERT_OUTTYPES)}；"
                "解释器留空自动探测已安装 torch / safetensors / numpy；"
                f"脚本可填 convert_hf_to_gguf.py 或源码 zip，{_SCRIPT_PLACEHOLDER}"
            ),
            justify="left",
            style="Hint.TLabel",
        )
        hint.grid(row=5, column=0, columnspan=4, sticky="we", pady=(space.xxs, 0))
        hint.bind(
            "<Configure>",
            lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
        )

    def _build_convert_actions(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        actions = ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="ew", pady=(space.sm, 0))
        self._preview_btn = ttk.Button(actions, text="预览计划", command=self._preview, width=10)
        self._preview_btn.pack(side="left")
        self._convert_start_btn = ttk.Button(
            actions, text="开始转换", command=self._start_convert, style="Accent.TButton", width=10
        )
        self._convert_start_btn.pack(side="left", padx=(space.sm, 0))
        self._clear_convert_log_btn = ttk.Button(actions, text="清空日志", command=self._clear_convert_log, width=10)
        self._clear_convert_log_btn.pack(side="left", padx=(space.sm, 0))
        self._convert_status_label = ttk.Label(actions, textvariable=self._convert_status_var, style="Muted.TLabel")
        self._convert_status_label.pack(side="left", padx=(space.sm, 0))
        self._convert_progress = ttk.Progressbar(actions, mode="determinate", maximum=100, length=180)
        self._convert_progress.pack(side="right")

    def _build_convert_log(self, parent: Any) -> None:
        box = self._ttk.LabelFrame(
            parent, text="转换输出日志", padding=(self._theme.spacing.sm, self._theme.spacing.xs)
        )
        box.grid(row=4, column=0, sticky="nsew", pady=(self._theme.spacing.sm, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self._convert_log_view = _guiwindow.create_log(box, height=6, grid=True, toolbar=True, theme=self._theme)

    # -------------------------- 量化页 -------------------------------- #
    def _build_quant(self, parent: Any) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(6, weight=1)

        ttk = self._ttk
        space = self._theme.spacing

        file_row = ttk.Frame(parent)
        file_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(file_row, text="输入文件").pack(side="left")
        ttk.Entry(file_row, textvariable=self._file_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=space.sm
        )
        self._quant_file_btn = ttk.Button(file_row, text="选择…", command=self._select_file, width=8)
        self._quant_file_btn.pack(side="left")

        model_row = ttk.Frame(parent)
        model_row.grid(row=1, column=0, sticky="ew", pady=(space.sm, 0))
        ttk.Label(model_row, text="未量化模型（GGUF）").pack(side="left")
        self._quant_model_box = ttk.Combobox(model_row, textvariable=self._model_var, state="disabled")
        self._quant_model_box.pack(side="left", fill="x", expand=True, padx=space.sm)
        self._quant_model_box.bind("<<ComboboxSelected>>", self._on_quant_model_selected)
        self._quant_model_btn = ttk.Button(model_row, text="刷新模型列表", command=self.refresh_env, width=12)
        self._quant_model_btn.pack(side="left")

        info_box = ttk.LabelFrame(parent, text="环境信息", padding=(space.sm, space.xs))
        info_box.grid(row=2, column=0, sticky="ew", pady=(space.sm, 0))
        info_box.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("engine", "引擎"), ("quantizer", "量化器"), ("version", "版本"))):
            ttk.Label(info_box, text=label).grid(row=row, column=0, sticky="nw", padx=(0, space.sm))
            var = self._tk.StringVar(value="检测中…")
            value_label = ttk.Label(info_box, textvariable=var, justify="left")
            value_label.grid(row=row, column=1, sticky="we")
            value_label.bind(
                "<Configure>",
                lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
            )
            self._quant_info_vars[key] = var

        quant_box = ttk.LabelFrame(parent, text="量化类型", padding=(space.sm, space.xs))
        quant_box.grid(row=3, column=0, sticky="ew", pady=(space.sm, 0))
        for index, (quant, desc) in enumerate(llmqnt.QUANT_TYPES.items()):
            check = ttk.Checkbutton(quant_box, text=f"{quant}  {desc}", variable=self._quant_vars[quant])
            check.grid(row=index // 3, column=index % 3, sticky="w", padx=space.sm, pady=space.xxs)
            self._quant_checks[quant] = check

        output_row = ttk.Frame(parent)
        output_row.grid(row=4, column=0, sticky="ew", pady=(space.sm, 0))
        output_row.columnconfigure(1, weight=1)
        ttk.Label(output_row, text="输出目录").grid(row=0, column=0, sticky="e", padx=(0, space.sm))
        ttk.Entry(output_row, textvariable=self._quant_out_dir_var).grid(row=0, column=1, sticky="ew")
        self._quant_out_btn = ttk.Button(output_row, text="浏览…", command=self._pick_quant_out_dir, width=8)
        self._quant_out_btn.grid(row=0, column=2, sticky="w", padx=(space.xs, 0))
        self._quant_default_btn = ttk.Button(output_row, text="默认目录", command=self._reset_quant_out_dir, width=9)
        self._quant_default_btn.grid(row=0, column=3, sticky="w", padx=(space.xs, 0))
        ttk.Button(output_row, text="打开目录", command=self._open_quant_dir, width=9).grid(
            row=0, column=4, sticky="w", padx=(space.xs, 0)
        )

        options = ttk.Frame(parent)
        options.grid(row=5, column=0, sticky="ew", pady=(space.xs, 0))
        self._quant_overwrite_check = ttk.Checkbutton(
            options,
            text="覆盖已存在的量化文件",
            variable=self._quant_overwrite_var,
            command=self._mark_quant_existing,
        )
        self._quant_overwrite_check.pack(side="left")
        self._quant_start_btn = ttk.Button(
            options, text="开始量化", command=self._start_quant, style="Accent.TButton", width=10
        )
        self._quant_start_btn.pack(side="right")
        self._quant_progress = ttk.Progressbar(options, mode="determinate", maximum=100, length=180)
        self._quant_progress.pack(side="right", padx=space.sm)

        log_box = ttk.LabelFrame(parent, text="量化输出日志", padding=(space.sm, space.xs))
        log_box.grid(row=6, column=0, sticky="nsew", pady=(space.sm, 0))
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)
        self._quant_log_view = _guiwindow.create_log(log_box, height=6, grid=True, toolbar=True, theme=self._theme)

    # ------------------------------------------------------------------ #
    # 文件选择
    # ------------------------------------------------------------------ #
    def _pick_source(self) -> None:
        path = self._filedialog.askdirectory(title="选择 HF safetensors 模型目录")
        if not path:
            return
        self._source_var.set(path)
        self._sync_convertible_selection(Path(path).resolve())
        self._convert_status_var.set(f"状态：已选择源目录 {Path(path).name}")

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

    def _select_file(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择 F16 格式的 GGUF 文件",
            filetypes=[("GGUF 文件", "*.gguf"), ("所有文件", "*.*")],
        )
        if path:
            self._choose_quant_input(Path(path))

    def _pick_quant_out_dir(self) -> None:
        path = self._filedialog.askdirectory(title="选择量化输出目录", initialdir=self._quant_out_dir())
        if path:
            self._quant_out_dir_var.set(path)

    def _reset_quant_out_dir(self) -> None:
        """恢复默认输出目录（随模型目录变化的 ``converted`` 子目录）。"""
        self._quant_out_dir_auto = True
        self._write_quant_out_dir(_llmconfig.products_dir())

    # ------------------------------------------------------------------ #
    # 环境扫描
    # ------------------------------------------------------------------ #
    def refresh_env(self) -> None:
        """后台探测引擎 / GPU / 模型目录，并扫描可转换与可量化模型。"""
        self._convert_models_btn.configure(state="disabled")
        self._quant_model_btn.configure(state="disabled")
        self._convert_models_hint_var.set("正在扫描模型目录…")
        for var in self._quant_info_vars.values():
            var.set("检测中…")
        self._spawn(self._scan_env)

    def _scan_env(self) -> None:
        """后台线程：收集引擎信息、模型目录、可转换 HF 目录与可量化 GGUF。"""
        info: dict[str, str] = {"engine": "", "version": "", "gpu": "", "models": "", "quantizer": ""}
        tag: str | None = None
        convertible: list[tuple[Path, int]] = []
        entries: list[ModelEntry] = []
        precisions: dict[str, GgufPrecision] = {}
        try:
            try:
                engine = llmengine.resolve_engine()
            except ServeError as exc:
                info["engine"] = f"未找到（{exc}）"
                info["version"] = "-"
            else:
                tag = engine.tag
                info["engine"] = f"{engine.tag} · {engine.exe}"
                info["version"] = llmengine.probe_version(engine)
            try:
                gpu = llmengine.detect_gpu()
            except Exception as exc:  # 探测失败只降级提示，不阻断页面
                info["gpu"] = f"检测失败（{exc}）"
            else:
                info["gpu"] = f"{gpu.name}（{gpu.memory_mib} MiB）" if gpu else "未检测到 NVIDIA GPU"
            try:
                info["quantizer"] = str(llmqnt.resolve_quantize_exe())
            except llmqnt.QuantizeError as exc:
                info["quantizer"] = f"未找到（{exc}）"

            root = _llmconfig.models_dir()
            info["models"] = str(root)
            convertible = _find_convertible(root)
            try:
                if root.is_dir():
                    entries = llmmodel.scan_models(root)
            except OSError as exc:
                self._queue.put(("qlog", f"扫描模型目录失败: {root}: {exc}"))
            for entry in entries:
                try:
                    precisions[llmenv.path_key(entry.path)] = detect_precision(entry.path)
                except OSError:
                    continue
        finally:
            # 任何一步异常都要让主线程拿到结果，避免按钮永久停在“检测中”
            self._queue.put(("env", (info, tag, convertible, entries, precisions)))

    def _apply_env(
        self,
        info: dict[str, str],
        tag: str | None,
        convertible: list[tuple[Path, int]],
        entries: list[ModelEntry],
        precisions: dict[str, GgufPrecision],
    ) -> None:
        for key, var in self._quant_info_vars.items():
            var.set(info.get(key) or "-")
        if tag and not self._tag_var.get().strip():
            self._tag_var.set(tag)
        if self._quant_out_dir_auto:
            self._write_quant_out_dir(_llmconfig.products_dir())
        self._apply_convertible(convertible)
        self._apply_quant_models(entries, precisions)
        self._refresh_existing()
        self._mark_quant_existing()
        self._convert_models_btn.configure(state="normal")
        self._quant_model_btn.configure(state="normal")

    # ------------------------------------------------------------------ #
    # 转换：模型列表与表单
    # ------------------------------------------------------------------ #
    def _on_convertible_selected(self, _event: Any = None) -> None:
        selection = self._convert_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not 0 <= index < len(self._convertible):
            return
        path = self._convertible[index][0]
        self._source_var.set(str(path))
        base = default_output_path(path)
        detail = "；默认输出已存在（可勾选覆盖后重新生成）" if base.is_file() else "；默认输出尚未生成"
        self._convert_status_var.set(f"状态：已选择 {path.name}（{_dir_stats(path)}{detail}）")
        self._log_convert(f"已选择可转换模型: {path}")

    def _sync_convertible_selection(self, path: Path) -> None:
        """手动选择源目录后，若命中已配置模型则同步列表选中。"""
        key = llmenv.path_key(path)
        for index, (candidate, _) in enumerate(self._convertible):
            if llmenv.path_key(candidate) == key:
                self._convert_tree.selection_set(str(index))
                self._convert_tree.see(str(index))
                return
        self._convert_tree.selection_remove(*self._convert_tree.selection())

    def _apply_convertible(self, convertible: list[tuple[Path, int]]) -> None:
        """填充可转换模型列表，并恢复当前已选源目录的选中状态。"""
        self._convertible = convertible
        self._convert_tree.delete(*self._convert_tree.get_children())
        root = _llmconfig.models_dir()
        for index, (path, size) in enumerate(convertible):
            existing = default_output_path(path, root).is_file()
            self._convert_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(path.name, human_size(size), "已生成" if existing else "未转换", str(path)),
            )
        if convertible:
            self._convert_models_hint_var.set(
                f"发现 {len(convertible)} 个可转换模型；选中后自动填入源目录，“输出”列显示默认输出是否已生成"
            )
        else:
            self._convert_models_hint_var.set(
                "未发现可转换模型（模型目录中需含 config.json 与 *.safetensors）；可在设置页调整模型目录"
            )
        source_text = self._source_var.get().strip()
        if source_text:
            self._sync_convertible_selection(Path(source_text).expanduser())

    def _open_convert_dir(self) -> None:
        """打开转换输出所在目录；尚未选择源目录时打开默认的 converted 目录。"""
        from fcmd.apps.locllm._locllmw_models import open_path

        base = self._base_output()
        target = base.parent if base is not None else _llmconfig.products_dir()
        target.mkdir(parents=True, exist_ok=True)
        open_path(target)

    def _open_quant_dir(self) -> None:
        """打开当前量化输出目录（不存在时先创建）。"""
        from fcmd.apps.locllm._locllmw_models import open_path

        target = self._quant_out_dir()
        target.mkdir(parents=True, exist_ok=True)
        open_path(target)

    def _base_output(self) -> Path | None:
        """当前表单对应的基础 GGUF 输出路径（未选择源目录时返回 None）。"""
        return output_path_from_form(self._source_var.get(), self._output_var.get())

    def _output_hint_text(self) -> str:
        """输出位置提示：显示实际默认目录，避免用户不清楚文件落在哪里。"""
        base = self._base_output()
        if base is None:
            root = _llmconfig.products_dir().expanduser()
            return f"输出留空时写入模型目录的 converted 子目录：{root}/<源目录名>/<源目录名>.gguf"
        if self._output_var.get().strip():
            return f"输出文件：{base}"
        return f"默认输出目录：{base.parent}（文件名 {base.name}）"

    def _refresh_output_state(self) -> None:
        """刷新输出位置提示与已存在输出标记。"""
        self._output_hint_var.set(self._output_hint_text())
        self._refresh_existing()

    def _refresh_existing(self) -> None:
        """标记默认/指定输出是否已存在，避免无意覆盖。"""
        if self._running or self._closed:
            return
        base = self._base_output()
        if base is not None and base.is_file():
            self._existing_var.set(f"输出已存在：{base}（勾选“覆盖已存在的输出文件”后才会重新生成）")
        else:
            self._existing_var.set("")

    # ------------------------------------------------------------------ #
    # 转换：计划与执行
    # ------------------------------------------------------------------ #
    def _preview(self) -> None:
        if self._running:
            self._log_convert("已有任务正在进行，请等待完成")
            return
        self._begin_task(self._preview_work, self._snapshot_form())

    def _start_convert(self) -> None:
        if self._running:
            self._log_convert("已有任务正在进行，请等待完成")
            return
        base = self._base_output()
        if base is None:
            self._messagebox.showwarning("缺少源目录", "请选择 HF safetensors 源目录")
            return
        if base.is_file() and not self._convert_overwrite_var.get():
            self._messagebox.showwarning(
                "输出已存在",
                f"{base}\n\n如要重新生成，请勾选“覆盖已存在的输出文件”。",
            )
            return
        self._begin_task(self._convert_work, self._snapshot_form())

    def _snapshot_form(self) -> _ConvertForm:
        """在主线程读取表单，后台线程只使用这份快照。"""
        return _ConvertForm(
            source=self._source_var.get().strip(),
            output=self._output_var.get().strip(),
            outtype=self._outtype_var.get().strip() or "f16",
            script=self._script_var.get().strip(),
            python=self._python_var.get().strip(),
            tag=self._tag_var.get().strip(),
        )

    def _begin_task(self, target: Any, *args: Any) -> None:
        """启动后台线程执行目标函数，结果通过队列回传主线程。"""
        self._set_running(True)
        self._task_thread = threading.Thread(target=self._task_runner, args=(target, args), daemon=True)
        self._task_thread.start()
        self._schedule_poll()

    def _task_runner(self, target: Any, args: tuple[Any, ...]) -> None:
        """任务线程兜底：无论成功失败都投递 ``task_done`` 并减少作业计数。"""
        try:
            target(*args)
        finally:
            self._queue.put(("task_done", None))
            self._queue.put(("job", None))

    def _prepare_plan(self, form: _ConvertForm) -> llmconvert.ConvertPlan:
        """按表单快照构造转换计划（可能探测解释器、准备源码，耗时操作）。"""
        if not form.source:
            raise ServeError("请选择 HF safetensors 源目录")
        source = Path(form.source).expanduser()
        if not llmconvert.is_hf_safetensors_dir(source):
            raise ServeError(f"不是 HF safetensors 目录（需要 config.json 与 *.safetensors）: {source}")
        self._queue.put(("clog", f"源目录: {source}（{_dir_stats(source)}）"))

        python = form.python
        if not python:
            self._queue.put(("clog", "正在探测已安装 torch / safetensors / numpy 的解释器…"))
            python = llmconvert.find_convert_python()
        self._queue.put(("clog", f"解释器: {python}"))
        self._queue.put(("clog", "正在准备转换脚本（首次使用会下载 llama.cpp 源码）…"))

        base = output_path_from_form(form.source, form.output)
        assert base is not None  # 源目录已校验
        return llmconvert.plan_convert(
            source,
            output=base,
            outtype=form.outtype,
            script=Path(form.script).expanduser() if form.script else None,
            python=python,
            tag=form.tag or None,
        )

    def _log_plan(self, plan: llmconvert.ConvertPlan) -> None:
        """把转换计划写入日志队列。"""
        lines = [
            "转换计划:",
            f"  源目录: {plan.source}（{_dir_stats(plan.source)}）",
            f"  精度: {plan.outtype}",
            f"  输出: {plan.output}",
            f"  脚本: {plan.script}",
            f"  解释器: {plan.python}",
            "  转换命令: " + llmbase.format_command(plan.convert_argv()),
        ]
        for line in lines:
            self._queue.put(("clog", line))

    def _preview_work(self, form: _ConvertForm) -> None:
        try:
            plan = self._prepare_plan(form)
        except ServeError as exc:
            self._queue.put(("cerror", f"构造转换计划失败: {exc}"))
        else:
            self._log_plan(plan)
            self._queue.put(("clog", "计划预览完成，可点击“开始转换”执行"))

    def _convert_work(self, form: _ConvertForm) -> None:
        try:
            plan = self._prepare_plan(form)
            self._log_plan(plan)
            last = -1

            def relay(line: str) -> None:
                nonlocal last
                percent = _progress_percent(line)
                if percent is None:
                    self._queue.put(("clog", line))
                elif percent >= last:
                    last = percent
                    self._queue.put(("cprogress", percent))

            self._queue.put(("clog", f"开始转换 {plan.source.name}…"))
            produced = [str(path) for path in llmconvert.convert_model(plan, on_output=relay)]
            self._queue.put(("clog", f"已生成: {plan.output}"))
            self._queue.put(("cprogress", 100))
        except ServeError as exc:
            self._queue.put(("cerror", f"转换失败: {exc}"))
        else:
            self._queue.put(("csuccess", produced))

    # ------------------------------------------------------------------ #
    # 量化：模型与选择
    # ------------------------------------------------------------------ #
    def _on_quant_model_selected(self, _event: Any = None) -> None:
        index = self._quant_model_box.current()
        if 0 <= index < len(self._quant_models):
            self._choose_quant_input(self._quant_models[index].path)

    def _choose_quant_input(self, path: Path) -> None:
        precision = self._precision_for(path, refresh=True)
        reason = quantize_input_error(precision)
        if reason:
            self._messagebox.showwarning("无法作为量化输入", f"{path.name}：{reason}")
            return
        changed = self._quant_input is None or llmenv.path_key(path) != llmenv.path_key(self._quant_input)
        self._quant_input = path
        self._file_var.set(str(path))
        if changed:
            self._reset_quants()
        self._sync_quant_model_selection(path)
        self._mark_quant_existing()
        _guiwindow.clear_log(self._quant_log_view)
        self._log_quant(f"精度: {precision.detail}")
        self._quant_progress.configure(value=0)

    def _reset_quants(self) -> None:
        """切换输入文件时恢复默认勾选，避免沿用上一个模型的勾选状态。"""
        for quant, var in self._quant_vars.items():
            var.set(quant in llmqnt.DEFAULT_QUANTS)

    def _precision_for(self, path: Path, *, refresh: bool = False) -> GgufPrecision:
        """取模型精度识别结果；``refresh`` 为真时重新读取 GGUF 头部。"""
        key = llmenv.path_key(path)
        if refresh or key not in self._quant_precisions:
            self._quant_precisions[key] = detect_precision(path)
        return self._quant_precisions[key]

    def _sync_quant_model_selection(self, path: Path) -> None:
        """手动选文件后，若命中也同步下拉框显示。"""
        key = llmenv.path_key(path)
        for index, entry in enumerate(self._quant_models):
            if llmenv.path_key(entry.path) == key:
                self._quant_model_box.current(index)
                return
        self._model_var.set("")

    def _apply_quant_models(self, entries: list[ModelEntry], precisions: dict[str, GgufPrecision]) -> None:
        """只把全量精度（未量化）的 GGUF 列入下拉框。"""
        self._quant_precisions = precisions
        usable = [entry for entry in entries if self._precision_for(entry.path).quantized is not True]
        self._quant_models = usable
        labels = [
            f"{entry.relative}（{self._precision_for(entry.path).note}）  ({human_size(entry.size)})"
            for entry in usable
        ]
        self._quant_model_box.configure(values=labels)
        if labels:
            self._model_var.set("")
            self._quant_model_box.configure(state="disabled" if self._running else "readonly")
        else:
            if entries:
                self._model_var.set(f"已配置的 {len(entries)} 个模型均不是全量精度 GGUF，请选择 F16 / BF16 原始文件")
            else:
                self._model_var.set("未发现已配置的 GGUF（请先在设置页配置模型目录）")
            self._quant_model_box.configure(state="disabled")

    def _on_quant_out_dir_edited(self) -> None:
        """输出目录被手动修改时停止跟随模型目录，并刷新已存在标记。"""
        if self._syncing_out_dir or self._closed:
            return
        text = self._quant_out_dir_var.get().strip()
        self._quant_out_dir_auto = not text or (
            llmenv.path_key(Path(text).expanduser()) == llmenv.path_key(_llmconfig.products_dir())
        )
        self._mark_quant_existing()

    def _write_quant_out_dir(self, directory: Path) -> None:
        """程序化写入输出目录，不触发“停止跟随默认目录”的判断。"""
        self._syncing_out_dir = True
        try:
            self._quant_out_dir_var.set(str(directory))
        finally:
            self._syncing_out_dir = False

    def _quant_out_dir(self) -> Path:
        text = self._quant_out_dir_var.get().strip()
        return Path(text).expanduser() if text else _llmconfig.products_dir()

    def _mark_quant_existing(self) -> None:
        """按当前输入文件与输出目录标记已存在的量化输出（只置灰，不自动勾选）。"""
        if self._closed:
            return
        overwrite = self._quant_overwrite_var.get()
        out_dir = self._quant_out_dir()
        for quant, check in self._quant_checks.items():
            desc = llmqnt.QUANT_TYPES[quant]
            exists = self._quant_input is not None and llmqnt.output_path(self._quant_input, quant, out_dir).is_file()
            check.configure(
                text=f"{quant}  {desc}" + ("（已存在）" if exists else ""),
                state="normal" if overwrite or not exists else "disabled",
            )

    def _selected_quants(self) -> list[str]:
        overwrite = self._quant_overwrite_var.get()
        out_dir = self._quant_out_dir()
        selected: list[str] = []
        for quant, var in self._quant_vars.items():
            if not var.get():
                continue
            if (
                not overwrite
                and self._quant_input is not None
                and llmqnt.output_path(self._quant_input, quant, out_dir).is_file()
            ):
                continue
            selected.append(quant)
        return selected

    def _start_quant(self) -> None:
        if self._running:
            self._log_quant("已有任务正在进行，请等待完成")
            return
        if self._quant_input is None or not self._quant_input.is_file():
            self._messagebox.showwarning("缺少输入", "请先选择 F16 GGUF 文件")
            return
        precision = self._precision_for(self._quant_input, refresh=True)
        reason = quantize_input_error(precision)
        if reason:
            self._messagebox.showerror("无法开始量化", f"{self._quant_input.name}：{reason}")
            return
        quants = self._selected_quants()
        if not quants:
            self._messagebox.showwarning("缺少量化类型", "请至少勾选一种尚未生成的量化类型")
            return
        try:
            exe = llmqnt.resolve_quantize_exe()
            jobs = llmqnt.plan_jobs(self._quant_input, quants, self._quant_out_dir())
            pending, skipped = llmqnt.pending_jobs(jobs, self._quant_overwrite_var.get())
        except llmqnt.QuantizeError as exc:
            self._messagebox.showerror("无法开始量化", str(exc))
            return

        _guiwindow.clear_log(self._quant_log_view)
        self._log_quant(f"输入: {self._quant_input}")
        self._log_quant(f"精度: {precision.detail}")
        self._log_quant(f"量化器: {exe}")
        self._log_quant(f"输出目录: {self._quant_out_dir()}")
        self._log_quant(f"量化类型: {', '.join(quants)}（共 {len(pending)} 个任务）")
        if skipped:
            self._log_quant(f"已存在并跳过: {', '.join(job.quant for job in skipped)}")
        if not pending:
            self._log_quant("全部量化文件已存在，无需转换")
            return

        self._set_running(True)
        self._task_thread = threading.Thread(target=self._task_runner, args=(self._quant_work, ()), daemon=True)
        self._quant_work_args = (pending, exe)
        self._task_thread.start()
        self._schedule_poll()

    def _quant_work(self) -> None:
        """后台线程：逐任务执行并把消息投递回主线程。"""
        pending, exe = self._quant_work_args
        success = True
        total = len(pending)
        for index, job in enumerate(pending, 1):
            self._queue.put(("qlog", f"正在转换到 {job.quant} 格式…"))
            try:
                llmqnt.run_job(job, exe, on_output=lambda line: self._queue.put(("qlog", line)))
            except llmqnt.QuantizeError as exc:
                success = False
                self._queue.put(("qlog", f"转换 {job.quant} 失败: {exc}"))
            else:
                self._queue.put(("qlog", f"成功生成: {job.output}"))
            self._queue.put(("qprogress", index * 100 // total))
        self._queue.put(("qdone", success))

    # ------------------------------------------------------------------ #
    # 运行状态与事件循环
    # ------------------------------------------------------------------ #
    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        for button in (
            self._preview_btn,
            self._convert_start_btn,
            self._clear_convert_log_btn,
            self._convert_models_btn,
            self._source_btn,
            self._output_btn,
            self._script_btn,
            self._convert_overwrite_check,
            self._quant_start_btn,
            self._quant_file_btn,
            self._quant_model_btn,
            self._quant_overwrite_check,
            self._quant_out_btn,
            self._quant_default_btn,
        ):
            button.configure(state=state)
        for check in self._quant_checks.values():
            check.configure(state=state)
        self._quant_model_box.configure(state="disabled" if running or not self._quant_models else "readonly")
        if running:
            self._convert_progress.configure(mode="determinate", maximum=100, value=0)
            self._quant_progress.configure(mode="determinate", maximum=100, value=0)
            self._convert_status_var.set("状态：转换中…")
        else:
            if self._convert_status_var.get() == "状态：转换中…":
                self._convert_status_var.set("状态：就绪")
            self._refresh_existing()
            self._mark_quant_existing()

    def _spawn(self, target: Any, *args: Any) -> None:
        """启动后台线程，并在结束时投递 ``job`` 事件以停止轮询。"""

        def runner() -> None:
            try:
                target(*args)
            finally:
                self._queue.put(("job", None))

        self._jobs += 1
        threading.Thread(target=runner, daemon=True).start()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if not self._poll_scheduled and not self._closed:
            self._poll_scheduled = True
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll)

    def _poll(self) -> None:
        """主线程消费后台事件。"""
        self._poll_scheduled = False
        if self._closed:
            return
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self._dispatch(kind, payload)
        except queue.Empty:
            pass
        if self._jobs or self._running:
            self._schedule_poll()

    def _dispatch(self, kind: str, payload: Any) -> None:
        if kind == "job":
            self._jobs = max(0, self._jobs - 1)
        elif kind == "clog":
            self._log_convert(str(payload))
        elif kind == "qlog":
            self._log_quant(str(payload))
        elif kind == "cprogress":
            self._convert_progress.configure(value=int(payload))
        elif kind == "qprogress":
            self._quant_progress.configure(value=int(payload))
        elif kind == "cerror":
            self._log_convert(str(payload))
            self._messagebox.showerror("错误", str(payload))
        elif kind == "csuccess":
            paths = [str(item) for item in payload]
            self._apply_convertible(self._convertible)
            self._convert_status_var.set("状态：转换完成")
            self._messagebox.showinfo("转换完成", "已生成:\n" + "\n".join(paths))
        elif kind == "qdone":
            self._finish_quant(bool(payload))
        elif kind == "task_done":
            self._set_running(False)
        elif kind == "env":
            self._apply_env(*payload)

    def _finish_quant(self, success: bool) -> None:
        if success:
            self._quant_progress.configure(value=100)
            self._log_quant("所有量化转换完成！")
            self._messagebox.showinfo("完成", "所有量化转换已完成")
        else:
            self._log_quant("量化转换过程中出现错误，详见日志")
            self._messagebox.showwarning("失败", "部分量化任务失败，详见日志")

    def close(self) -> bool:
        """确认并停止进行中的任务；返回 ``False`` 表示用户取消关闭。"""
        if self._running and not self._messagebox.askyesno(
            "任务进行中",
            "转换 / 量化任务仍在执行，关闭窗口将中断任务。确实关闭？",
        ):
            return False
        self._closed = True
        return True

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log_convert(self, message: str) -> None:
        _guiwindow.append_log(self._convert_log_view, message)

    def _clear_convert_log(self) -> None:
        """清空转换日志区域。"""
        _guiwindow.clear_log(self._convert_log_view)

    def _log_quant(self, message: str) -> None:
        _guiwindow.append_log(self._quant_log_view, message)
