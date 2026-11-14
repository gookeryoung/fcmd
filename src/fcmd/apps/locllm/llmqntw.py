"""llmqntw - GGUF 量化工具的 Tk 窗口版。

基于 :mod:`fcmd.apps.locllm.llmqnt` 的核心逻辑，提供文件选择、量化类型勾选、
进度条与实时日志。窗口启动时扫描 ``llmenv`` 已配置的模型目录，按 GGUF 元数据
与张量类型识别精度和完整性，下拉框只列出全量（F16 / BF16 等）且可用的模型，
并在括号中备注精度；已量化或不完整的文件会在选择时明确拦截。也可手动浏览文件；
同时显示当前 llama.cpp 引擎、量化器与版本信息。窗口关闭后命令退出。

示例
----
    fcmd llmqntw
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.locllm import llmbase, llmengine, llmenv, llmmodel, llmqnt
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision, quantize_input_error
from fcmd.apps.locllm.llmbase import ModelEntry, human_size
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "QuantizeSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
]

_DESCRIPTION = (
    "GGUF 量化窗口版（Tk）：从已配置模型下拉选择或手动选文件，按 GGUF 元数据识别全量与量化精度"
    "并检查完整性（备注在括号中，避免误转或漏转），显示引擎/量化器版本，勾选多种量化类型批量转换；"
    "窗口位置与大小记忆到 ~/.fcmd/ui/llmqntw.json"
)

MIN_WIDTH = 560
MIN_HEIGHT = 520


@dataclass
class QuantizeSettings:
    """窗口持久化设置（当前只有 geometry）。"""

    geometry: str | None = None


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/llmqntw.json``。"""
    return _guiconfig.config_path("llmqntw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> QuantizeSettings:
    """读取设置；文件缺失、JSON 损坏或 geometry 非法时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = QuantizeSettings()
    if raw is not None:
        geometry = raw.get("geometry")
        if isinstance(geometry, str) and valid_geometry(geometry):
            settings.geometry = geometry
    return settings


def save_settings(settings: QuantizeSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings({"geometry": settings.geometry}, path or config_path())


def _default_geometry(screen_width: int, screen_height: int) -> str:
    """按屏幕比例计算默认窗口尺寸。"""
    width, height = _guitheme.proportional_geometry(screen_width, screen_height)
    return f"{width}x{height}"


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到按屏幕比例计算的尺寸。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=_default_geometry,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


class QuantizeWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 量化窗口：构建界面并把转换任务放到后台线程执行。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self.root, self._theme = _guiwindow.create_root(
            title="GGUF 量化工具",
            saved_geometry=self._settings.geometry,
            default_geometry=_default_geometry,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._close,
        )

        self.input_file: Path | None = None
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None
        self._models: list[ModelEntry] = []
        self._precisions: dict[str, GgufPrecision] = {}
        self._scan_thread: threading.Thread | None = None
        self._scan_results: queue.Queue[tuple[list[ModelEntry], dict[str, str], dict[str, GgufPrecision]]] = (
            queue.Queue()
        )
        self._running = False

        self._file_var = tk.StringVar(value="尚未选择输入文件")
        self._model_var = tk.StringVar(value="")
        self._info_vars: dict[str, tk.StringVar] = {}
        self._overwrite_var = tk.BooleanVar(value=False)
        self._quant_vars: dict[str, tk.BooleanVar] = {
            quant: tk.BooleanVar(value=quant in llmqnt.DEFAULT_QUANTS) for quant in llmqnt.QUANT_TYPES
        }
        self._quant_checks: dict[str, Any] = {}
        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._mark_existing()
        self._refresh_models()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        tk = self._tk
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=self._theme.spacing.md)
        outer.pack(fill="both", expand=True)

        file_row = ttk.Frame(outer)
        file_row.pack(fill="x")
        ttk.Label(file_row, text="输入文件").pack(side="left")
        ttk.Entry(file_row, textvariable=self._file_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=self._theme.spacing.sm
        )
        self._file_btn = ttk.Button(file_row, text="选择…", command=self._select_file)
        self._file_btn.pack(side="left")

        model_row = ttk.Frame(outer)
        model_row.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        ttk.Label(model_row, text="已配置模型（未量化）").pack(side="left")
        self._model_box = ttk.Combobox(model_row, textvariable=self._model_var, state="disabled")
        self._model_box.pack(side="left", fill="x", expand=True, padx=self._theme.spacing.sm)
        self._model_box.bind("<<ComboboxSelected>>", self._on_model_selected)
        self._model_btn = ttk.Button(model_row, text="刷新", command=self._refresh_models)
        self._model_btn.pack(side="left")

        info_box = ttk.LabelFrame(outer, text="环境信息", padding=self._theme.spacing.sm)
        info_box.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        info_box.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("engine", "引擎"), ("quantizer", "量化器"), ("version", "版本"))):
            ttk.Label(info_box, text=label).grid(row=row, column=0, sticky="nw", padx=(0, self._theme.spacing.sm))
            var = tk.StringVar(value="检测中…")
            value_label = ttk.Label(info_box, textvariable=var, justify="left")
            value_label.grid(row=row, column=1, sticky="we")
            value_label.bind(
                "<Configure>",
                lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
            )
            self._info_vars[key] = var

        quant_box = ttk.LabelFrame(outer, text="量化类型", padding=self._theme.spacing.sm)
        quant_box.pack(fill="x", pady=(self._theme.spacing.md, 0))
        for index, (quant, desc) in enumerate(llmqnt.QUANT_TYPES.items()):
            check = ttk.Checkbutton(
                quant_box,
                text=f"{quant}  {desc}",
                variable=self._quant_vars[quant],
            )
            check.grid(
                row=index // 3, column=index % 3, sticky="w", padx=self._theme.spacing.sm, pady=self._theme.spacing.xxs
            )
            self._quant_checks[quant] = check

        options = ttk.Frame(outer)
        options.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        self._overwrite_check = ttk.Checkbutton(
            options,
            text="覆盖已存在的量化文件",
            variable=self._overwrite_var,
            command=self._mark_existing,
        )
        self._overwrite_check.pack(side="left")
        self._start_btn = ttk.Button(options, text="开始转换", command=self._start, style="Accent.TButton")
        self._start_btn.pack(side="right")
        self._progress = ttk.Progressbar(options, mode="determinate", maximum=100, length=240)
        self._progress.pack(side="right", padx=self._theme.spacing.sm)

        log_frame = ttk.LabelFrame(outer, text="输出日志", padding=self._theme.spacing.sm)
        log_frame.pack(fill="both", expand=True, pady=(self._theme.spacing.md, 0))
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self._log_text = tk.Text(log_frame, wrap="word", state="disabled", yscrollcommand=scrollbar.set)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _select_file(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择 F16 格式的 GGUF 文件",
            filetypes=[("GGUF 文件", "*.gguf"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._choose(Path(path))

    def _on_model_selected(self, _event: object | None = None) -> None:
        index = self._model_box.current()
        if 0 <= index < len(self._models):
            self._choose(self._models[index].path)

    def _choose(self, path: Path) -> None:
        precision = self._precision_for(path, refresh=True)
        reason = quantize_input_error(precision)
        if reason:
            self._messagebox.showwarning("无法作为量化输入", f"{path.name}：{reason}")
            return
        changed = self.input_file is None or llmenv.path_key(path) != llmenv.path_key(self.input_file)
        self.input_file = path
        self._file_var.set(str(path))
        if changed:
            self._reset_quants()
        self._sync_model_selection(path)
        self._mark_existing()
        self._clear_log()
        self._log(f"精度: {precision.detail}")
        self._progress["value"] = 0

    def _reset_quants(self) -> None:
        """切换输入文件时恢复默认勾选，避免沿用上一个模型的勾选状态。"""
        for quant, var in self._quant_vars.items():
            var.set(quant in llmqnt.DEFAULT_QUANTS)

    def _precision_for(self, path: Path, *, refresh: bool = False) -> GgufPrecision:
        """取模型精度识别结果；``refresh`` 为真时重新读取 GGUF 头部。"""
        key = llmenv.path_key(path)
        if refresh or key not in self._precisions:
            self._precisions[key] = detect_precision(path)
        return self._precisions[key]

    def _sync_model_selection(self, path: Path) -> None:
        """手动选文件后，若命中也同步下拉框显示。"""
        key = llmenv.path_key(path)
        for index, entry in enumerate(self._models):
            if llmenv.path_key(entry.path) == key:
                self._model_box.current(index)
                return
        self._model_var.set("")

    # ------------------------------------------------------------------ #
    # 已配置模型 / 引擎信息
    # ------------------------------------------------------------------ #
    def _refresh_models(self) -> None:
        """后台扫描已配置模型目录并探测引擎信息，避免阻塞窗口。"""
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._model_btn.configure(state="disabled")
        self._model_box.configure(state="disabled", values=[])
        self._model_var.set("正在扫描模型目录…")
        for var in self._info_vars.values():
            var.set("检测中…")
        self._scan_thread = threading.Thread(target=self._scan_models, daemon=True)
        self._scan_thread.start()
        self.root.after(200, self._poll_scan)

    def _scan_models(self) -> None:
        """后台线程：扫描模型目录并收集 llama.cpp 引擎 / 量化器信息。"""
        entries: list[ModelEntry] = []
        try:
            root = llmenv.models_dir()
            if root.is_dir():
                entries = llmmodel.scan_models(root)
        except OSError:
            entries = []
        precisions = {llmenv.path_key(entry.path): detect_precision(entry.path) for entry in entries}
        self._scan_results.put((entries, self._collect_env_info(), precisions))

    def _collect_env_info(self) -> dict[str, str]:
        """收集当前引擎 tag / 可执行文件、量化器路径与版本。"""
        info: dict[str, str] = {"engine": "", "quantizer": "", "version": ""}
        try:
            engine = llmengine.resolve_engine()
        except llmbase.ServeError as exc:
            info["engine"] = f"未找到（{exc}）"
        else:
            info["engine"] = f"{engine.tag} · {engine.exe}"
            info["version"] = llmengine.probe_version(engine)
        try:
            info["quantizer"] = str(llmqnt.resolve_quantize_exe())
        except llmqnt.QuantizeError as exc:
            info["quantizer"] = f"未找到（{exc}）"
        return info

    def _poll_scan(self) -> None:
        try:
            entries, info, precisions = self._scan_results.get_nowait()
        except queue.Empty:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                self.root.after(200, self._poll_scan)
            return
        self._apply_models(entries, info, precisions)

    def _apply_models(
        self,
        entries: list[ModelEntry],
        info: dict[str, str],
        precisions: dict[str, GgufPrecision],
    ) -> None:
        self._precisions = precisions
        usable = [entry for entry in entries if self._precision_for(entry.path).quantized is not True]
        self._models = usable
        labels = [
            f"{entry.relative}（{self._precision_for(entry.path).note}）  ({human_size(entry.size)})"
            for entry in usable
        ]
        self._model_box.configure(values=labels)
        if labels:
            self._model_var.set("")
            self._model_box.configure(state="disabled" if self._running else "readonly")
        else:
            if entries:
                self._model_var.set(f"已配置的 {len(entries)} 个模型均不是全量精度 GGUF，请选择 F16 / BF16 原始文件")
            else:
                self._model_var.set("未发现已配置的 GGUF（用 fcmd llmenv add 登记模型目录）")
            self._model_box.configure(state="disabled")
        for key, var in self._info_vars.items():
            var.set(info.get(key) or "-")
        self._model_btn.configure(state="disabled" if self._running else "normal")

    def _mark_existing(self) -> None:
        """按当前输入文件标记已存在的量化输出（只置灰，不自动勾选）。"""
        overwrite = self._overwrite_var.get()
        for quant, check in self._quant_checks.items():
            desc = llmqnt.QUANT_TYPES[quant]
            exists = self.input_file is not None and llmqnt.output_path(self.input_file, quant).is_file()
            check.configure(
                text=f"{quant}  {desc}" + ("（已存在）" if exists else ""),
                state="normal" if overwrite or not exists else "disabled",
            )

    def _selected_quants(self) -> list[str]:
        overwrite = self._overwrite_var.get()
        selected: list[str] = []
        for quant, var in self._quant_vars.items():
            if not var.get():
                continue
            if not overwrite and self.input_file is not None and llmqnt.output_path(self.input_file, quant).is_file():
                continue
            selected.append(quant)
        return selected

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._log("已有转换任务正在进行，请等待完成")
            return
        if self.input_file is None or not self.input_file.is_file():
            self._messagebox.showwarning("缺少输入", "请先选择 F16 GGUF 文件")
            return
        precision = self._precision_for(self.input_file, refresh=True)
        reason = quantize_input_error(precision)
        if reason:
            self._messagebox.showerror("无法开始转换", f"{self.input_file.name}：{reason}")
            return
        quants = self._selected_quants()
        if not quants:
            self._messagebox.showwarning("缺少量化类型", "请至少勾选一种尚未生成的量化类型")
            return
        try:
            exe = llmqnt.resolve_quantize_exe()
            jobs = llmqnt.plan_jobs(self.input_file, quants)
            pending, skipped = llmqnt.pending_jobs(jobs, self._overwrite_var.get())
        except llmqnt.QuantizeError as exc:
            self._messagebox.showerror("无法开始转换", str(exc))
            return

        self._clear_log()
        self._log(f"输入: {self.input_file}")
        self._log(f"精度: {precision.detail}")
        self._log(f"引擎: {exe}")
        self._log(f"量化类型: {', '.join(quants)}（共 {len(pending)} 个任务）")
        if skipped:
            self._log(f"已存在并跳过: {', '.join(job.quant for job in skipped)}")
        if not pending:
            self._log("全部量化文件已存在，无需转换")
            return

        self._set_running(True)
        self._messages = queue.Queue()
        self._thread = threading.Thread(target=self._work, args=(pending, exe), daemon=True)
        self._thread.start()
        self.root.after(100, self._poll)

    def _work(self, jobs: list[llmqnt.QuantizeJob], exe: Path) -> None:
        """后台线程：逐任务执行并把消息投递回主线程。"""
        assert self._messages is not None
        status = self._messages
        success = True
        total = len(jobs)
        for index, job in enumerate(jobs, 1):
            status.put(("log", f"正在转换到 {job.quant} 格式…"))
            try:
                llmqnt.run_job(job, exe, on_output=lambda line: status.put(("log", line)))
            except llmqnt.QuantizeError as exc:
                success = False
                status.put(("log", f"转换 {job.quant} 失败: {exc}"))
            else:
                status.put(("log", f"成功生成: {job.output}"))
            status.put(("progress", index * 100 // total))
        status.put(("done", success))

    def _poll(self) -> None:
        """主线程轮询队列，刷新日志 / 进度。"""
        assert self._messages is not None
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "progress":
                    self._progress["value"] = int(payload)
                elif kind == "done":
                    self._finish(bool(payload))
        except queue.Empty:
            pass
        if self._thread is not None and self._thread.is_alive():
            self.root.after(100, self._poll)

    def _finish(self, success: bool) -> None:
        self._set_running(False)
        self._mark_existing()
        if success:
            self._progress["value"] = 100
            self._log("所有量化转换完成！")
            self._messagebox.showinfo("完成", "所有量化转换已完成")
        else:
            self._log("量化转换过程中出现错误，详见日志")
            self._messagebox.showwarning("失败", "部分量化任务失败，详见日志")

    def _set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        self._start_btn.configure(state=state)
        self._file_btn.configure(state=state)
        self._model_btn.configure(state=state)
        self._model_box.configure(state="disabled" if running or not self._models else "readonly")
        if not running:
            self._overwrite_check.configure(state="normal")

    def _close(self) -> None:
        save_settings(QuantizeSettings(geometry=self.root.winfo_geometry()), self._settings_path)
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        _guiwindow.append_log(self._log_text, message)

    def _clear_log(self) -> None:
        _guiwindow.clear_log(self._log_text)


def run_gui() -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(QuantizeWindow, "窗口版")


@fcmd.tool("llmqntw", help="打开 GGUF 量化窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 GGUF 量化窗口。"""
    run_gui()


@fcmd.main("llmqntw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
