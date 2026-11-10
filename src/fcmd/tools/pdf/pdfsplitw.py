"""pdfsplitw - PDF 分割 / 页面提取窗口版（Tk）。

选择 PDF 后按三种模式之一分割：按份数平均分割、按每份页数切分、按页码范围
提取。分割在后台线程执行，进度与日志实时回传界面；输入 / 输出路径、模式与
参数保存在 ``${FCMD_HOME:-~/.fcmd}/ui/pdfsplitw.json``。

示例
----
    fcmd pdfsplitw        # 打开 PDF 分割窗口
    pdfsplitw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.tools import _guiconfig, _guitheme, _guiwindow
from fcmd.tools.pdf.pdfsplit import (
    split_by_parts,
    split_by_range,
    split_by_size,
)

__all__ = [
    "DEFAULT_GEOMETRY",
    "MODES",
    "MODE_LABELS",
    "PdfSplitSettings",
    "config_path",
    "default_output_path",
    "load_settings",
    "normalize_mode",
    "parse_positive_int",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PDF 分割窗口版（Tk）：按份数 / 每份页数 / 页码范围三种模式分割或提取页面，"
    "进度与日志实时回传；设置持久化到 ~/.fcmd/ui/pdfsplitw.json"
)

DEFAULT_GEOMETRY = "880x700+240+130"
MIN_WIDTH = 780
MIN_HEIGHT = 580

MODES: tuple[str, ...] = ("parts", "size", "range")
MODE_LABELS: dict[str, str] = {
    "parts": "按份数",
    "size": "每份页数",
    "range": "页码范围",
}


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/pdfsplitw.json``。"""
    return _guiconfig.config_path("pdfsplitw")


@dataclass
class PdfSplitSettings:
    """PDF 分割窗口的持久化设置。"""

    input_path: str = ""
    output_dir: str = ""
    output_path: str = ""
    mode: str = "parts"
    parts: int = 2
    size: int = 1
    pages: str = ""
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def normalize_mode(value: str) -> str:
    """校验模式标识（``parts`` / ``size`` / ``range``）。

    Raises
    ------
    ValueError
        模式不在支持范围内。
    """
    key = value.strip().lower()
    if key not in MODES:
        raise ValueError(f"无效的分割模式: {value!r}（支持 {', '.join(MODES)}）")
    return key


def parse_positive_int(value: int | str, label: str) -> int:
    """把界面输入解析为正整数。

    Raises
    ------
    ValueError
        非整数或小于 1。
    """
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}须为整数: {value!r}") from exc
    if number < 1:
        raise ValueError(f"{label}须为正整数: {number}")
    return number


def default_output_path(input_path: Path) -> Path:
    """页码范围模式的缺省输出：``<原名>_extracted.pdf``。"""
    return input_path.with_name(f"{input_path.stem}_extracted.pdf")


def load_settings(path: Path | None = None) -> PdfSplitSettings:
    """读取设置文件；缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PdfSplitSettings()

    settings = PdfSplitSettings()
    input_path = raw.get("input_path")
    output_dir = raw.get("output_dir")
    output_path = raw.get("output_path")
    mode = raw.get("mode")
    parts = raw.get("parts")
    size = raw.get("size")
    pages = raw.get("pages")
    geometry = raw.get("geometry")
    if isinstance(input_path, str):
        settings.input_path = input_path
    if isinstance(output_dir, str):
        settings.output_dir = output_dir
    if isinstance(output_path, str):
        settings.output_path = output_path
    if isinstance(mode, str):
        with contextlib.suppress(ValueError):
            settings.mode = normalize_mode(mode)
    if isinstance(parts, int) and not isinstance(parts, bool) and parts >= 1:
        settings.parts = parts
    if isinstance(size, int) and not isinstance(size, bool) and size >= 1:
        settings.size = size
    if isinstance(pages, str):
        settings.pages = pages
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: PdfSplitSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "input_path": settings.input_path,
        "output_dir": settings.output_dir,
        "output_path": settings.output_path,
        "mode": settings.mode,
        "parts": settings.parts,
        "size": settings.size,
        "pages": settings.pages,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class PdfSplitWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk PDF 分割主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._worker: threading.Thread | None = None

        self.root, self._theme = _guiwindow.create_root(
            title=f"PDF 分割 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._input_var = tk.StringVar(value=self._settings.input_path)
        self._output_dir_var = tk.StringVar(value=self._settings.output_dir)
        self._output_path_var = tk.StringVar(value=self._settings.output_path)
        self._mode_var = tk.StringVar(value=self._settings.mode)
        self._parts_var = tk.StringVar(value=str(self._settings.parts))
        self._size_var = tk.StringVar(value=str(self._settings.size))
        self._pages_var = tk.StringVar(value=self._settings.pages)
        self._status_var = tk.StringVar(value="就绪")

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)

    def _build_widgets(self) -> None:
        ttk = self._ttk
        palette = self._theme.palette
        self._build_menu()

        input_frame = ttk.LabelFrame(self.root, text="输入 PDF", padding=self._theme.spacing.sm)
        input_frame.pack(fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.md, self._theme.spacing.xs))
        ttk.Label(input_frame, text="PDF 路径：", width=10, anchor="w").pack(side="left")
        ttk.Entry(input_frame, textvariable=self._input_var).pack(
            side="left", fill="x", expand=True, padx=(0, self._theme.spacing.sm)
        )
        self._browse_input_button = ttk.Button(input_frame, text="浏览…", command=self._browse_input)
        self._browse_input_button.pack(side="right")

        self._output_dir_frame = ttk.LabelFrame(self.root, text="输出", padding=self._theme.spacing.sm)
        self._output_dir_frame.pack(fill="x", padx=self._theme.spacing.md, pady=self._theme.spacing.xs)
        ttk.Label(self._output_dir_frame, text="输出目录：", width=10, anchor="w").pack(side="left")
        ttk.Entry(self._output_dir_frame, textvariable=self._output_dir_var).pack(
            side="left", fill="x", expand=True, padx=(0, self._theme.spacing.sm)
        )
        self._browse_output_dir_button = ttk.Button(
            self._output_dir_frame, text="目录…", command=self._browse_output_dir
        )
        self._browse_output_dir_button.pack(side="right")
        ttk.Label(
            self._output_dir_frame,
            text="留空时输出到输入文件所在目录；按份数 / 每份页数生成 <原名>_partN.pdf",
            foreground=palette.muted,
            anchor="w",
        ).pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))

        self._output_file_frame = ttk.Frame(self.root)
        self._output_file_frame.pack(fill="x", padx=self._theme.spacing.md, pady=(0, self._theme.spacing.xs))
        ttk.Label(self._output_file_frame, text="提取文件：", width=10, anchor="w").pack(side="left")
        ttk.Entry(self._output_file_frame, textvariable=self._output_path_var).pack(
            side="left", fill="x", expand=True, padx=(0, self._theme.spacing.sm)
        )
        self._browse_output_file_button = ttk.Button(
            self._output_file_frame, text="浏览…", command=self._browse_output_file
        )
        self._browse_output_file_button.pack(side="right")
        ttk.Label(
            self._output_file_frame,
            text="仅页码范围模式使用，留空为 <原名>_extracted.pdf",
            foreground=palette.muted,
            anchor="w",
        ).pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))

        mode_frame = ttk.LabelFrame(self.root, text="分割模式", padding=self._theme.spacing.sm)
        mode_frame.pack(fill="x", padx=self._theme.spacing.md, pady=self._theme.spacing.xs)

        parts_row = ttk.Frame(mode_frame)
        parts_row.pack(fill="x")
        self._parts_radio = ttk.Radiobutton(
            parts_row, text="按份数", value="parts", variable=self._mode_var, command=self._on_mode_change
        )
        self._parts_radio.pack(side="left")
        self._parts_spinbox = ttk.Spinbox(
            parts_row, from_=1, to=999, increment=1, textvariable=self._parts_var, width=8
        )
        self._parts_spinbox.pack(side="left", padx=(self._theme.spacing.sm, 0))

        size_row = ttk.Frame(mode_frame)
        size_row.pack(fill="x", pady=(self._theme.spacing.xs, 0))
        self._size_radio = ttk.Radiobutton(
            size_row, text="每份页数", value="size", variable=self._mode_var, command=self._on_mode_change
        )
        self._size_radio.pack(side="left")
        self._size_spinbox = ttk.Spinbox(size_row, from_=1, to=999, increment=1, textvariable=self._size_var, width=8)
        self._size_spinbox.pack(side="left", padx=(self._theme.spacing.sm, 0))

        range_row = ttk.Frame(mode_frame)
        range_row.pack(fill="x", pady=(self._theme.spacing.xs, 0))
        self._range_radio = ttk.Radiobutton(
            range_row, text="页码范围", value="range", variable=self._mode_var, command=self._on_mode_change
        )
        self._range_radio.pack(side="left")
        ttk.Entry(range_row, textvariable=self._pages_var).pack(
            side="left", fill="x", expand=True, padx=(self._theme.spacing.sm, 0)
        )
        ttk.Label(
            mode_frame,
            text="页码范围支持 1,3-5,7- 语法",
            foreground=palette.muted,
            anchor="w",
        ).pack(fill="x", pady=(self._theme.spacing.xs, 0))

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.xs, 0))
        self._start_button = ttk.Button(
            action_frame,
            text="开始分割",
            command=self._start,
            style="Accent.TButton",
            padding=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.xs),
        )
        self._start_button.pack(side="right")
        ttk.Label(action_frame, textvariable=self._status_var, anchor="w").pack(side="left", fill="x", expand=True)

        progress_frame = ttk.Frame(self.root)
        progress_frame.pack(
            fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.sm, self._theme.spacing.xs)
        )
        self._progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=1, value=0)
        self._progress.pack(fill="x")

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=self._theme.spacing.sm)
        log_frame.pack(
            fill="both", expand=True, padx=self._theme.spacing.md, pady=(self._theme.spacing.xs, self._theme.spacing.md)
        )
        self._log_text = _guiwindow.create_log(log_frame, height=12)
        self._append_log("选择 PDF、设置输出目录与分割模式后点击「开始分割」。")
        self._on_mode_change()

    def _build_menu(self) -> None:
        _guiwindow.build_menu(self.root, on_close=self.close, on_about=self._show_about)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于",
            f"PDF 分割 v{__version__}\n\n"
            "按份数：平均分成 N 份\n"
            "每份页数：按固定页数切分\n"
            "页码范围：提取 1,3-5,7- 等指定页",
        )

    def _on_mode_change(self) -> None:
        """按当前模式启用对应参数控件。"""
        mode = self._mode_var.get()
        for widget in (self._parts_spinbox,):
            widget.configure(state="normal" if mode == "parts" else "disabled")
        for widget in (self._size_spinbox,):
            widget.configure(state="normal" if mode == "size" else "disabled")
        self._output_file_frame.pack_forget()
        if mode == "range":
            self._output_file_frame.pack(
                fill="x", padx=self._theme.spacing.md, pady=(0, self._theme.spacing.xs), after=self._output_dir_frame
            )

    def _browse_input(self) -> None:
        filename = self._filedialog.askopenfilename(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if filename:
            self._input_var.set(filename)
            self._output_path_var.set(str(default_output_path(Path(filename))))

    def _browse_output_dir(self) -> None:
        directory = self._filedialog.askdirectory(title="选择输出目录")
        if directory:
            self._output_dir_var.set(directory)

    def _browse_output_file(self) -> None:
        filename = self._filedialog.asksaveasfilename(
            title="选择提取输出文件",
            defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if filename:
            self._output_path_var.set(filename)

    def _append_log(self, message: str) -> None:
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _set_busy(self, busy: bool) -> None:
        """分割期间禁用按钮，避免重复提交。"""
        state = "disabled" if busy else "normal"
        for button in (
            self._start_button,
            self._browse_input_button,
            self._browse_output_dir_button,
            self._browse_output_file_button,
        ):
            button.configure(state=state)

    # ------------------------------------------------------------------ #
    # 分割（后台线程 + 队列回传）
    # ------------------------------------------------------------------ #
    def _start(self) -> None:
        """校验输入并启动后台分割线程。"""
        if self._worker is not None and self._worker.is_alive():
            return

        raw_input = self._input_var.get().strip()
        if not raw_input:
            self._append_log("错误：请选择输入 PDF")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.is_file():
            self._append_log(f"错误：输入文件不存在 {input_path}")
            return

        try:
            mode = normalize_mode(self._mode_var.get())
            parts = parse_positive_int(self._parts_var.get(), "分割份数") if mode == "parts" else 1
            size = parse_positive_int(self._size_var.get(), "每份页数") if mode == "size" else 1
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return
        pages = self._pages_var.get().strip()
        if mode == "range" and not pages:
            self._append_log("错误：页码范围模式需要填写页码范围")
            return

        raw_output_dir = self._output_dir_var.get().strip()
        output_dir = Path(raw_output_dir).expanduser() if raw_output_dir else input_path.parent
        raw_output_path = self._output_path_var.get().strip()
        if mode == "range":
            output_path = Path(raw_output_path).expanduser() if raw_output_path else default_output_path(input_path)
            if not output_path.suffix:
                output_path = output_path.with_suffix(".pdf")
        else:
            output_path = None

        self._set_busy(True)
        self._progress.configure(maximum=1, value=0)
        self._status_var.set("正在分割…")
        self._append_log(f"输入：{input_path}")
        self._append_log(f"模式：{MODE_LABELS[mode]}；输出目录：{output_dir}")

        self._worker = _guiwindow.start_worker(
            self.root,
            self._run_split,
            (input_path, output_dir, output_path, mode, parts, size, pages),
            self._poll_queue,
        )

    def _run_split(  # noqa: PLR0913, PLR0917 - 参数由界面校验结果一次性传入，保持显式
        self,
        input_path: Path,
        output_dir: Path,
        output_path: Path | None,
        mode: str,
        parts: int,
        size: int,
        pages: str,
    ) -> None:
        """后台线程：按模式执行分割，把进度事件写入队列。"""

        def progress(done: int, total: int, message: str) -> None:
            self._queue.put(("progress", done, total, message))

        try:
            if mode == "parts":
                outputs = split_by_parts(input_path, output_dir, parts, progress=progress)
                target = output_dir
            elif mode == "size":
                outputs = split_by_size(input_path, output_dir, size, progress=progress)
                target = output_dir
            else:
                if output_path is None:
                    raise ValueError("页码范围模式缺少输出路径")
                outputs = [split_by_range(input_path, output_path, pages, progress=progress)]
                target = output_path
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("done", len(outputs), target))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "progress":
                _, done, total, message = event
                self._progress.configure(maximum=max(total, 1), value=done)
                self._append_log(message)
            elif kind == "done":
                _, count, target = event
                self._progress.configure(value=self._progress.cget("maximum"))
                self._status_var.set(f"完成：{count} 个输出")
                self._append_log(f"完成：{count} 个输出 -> {target}")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showinfo("分割完成", f"已生成 {count} 个输出：\n{target}")
                return
            elif kind == "error":
                self._append_log(f"错误：{event[1]}")
                self._status_var.set("分割失败")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showerror("分割失败", event[1])
                return

        if self._worker is not None:
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll_queue)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口（后台线程为 daemon，随进程结束）。"""
        try:
            mode = normalize_mode(self._mode_var.get())
        except ValueError:
            mode = "parts"
        try:
            parts = parse_positive_int(self._parts_var.get(), "分割份数")
        except ValueError:
            parts = 2
        try:
            size = parse_positive_int(self._size_var.get(), "每份页数")
        except ValueError:
            size = 1
        save_settings(
            PdfSplitSettings(
                input_path=self._input_var.get(),
                output_dir=self._output_dir_var.get(),
                output_path=self._output_path_var.get(),
                mode=mode,
                parts=parts,
                size=size,
                pages=self._pages_var.get(),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: PdfSplitWindow(settings_path), "PDF 分割窗口")


@fcmd.tool("pdfsplitw", help="打开 PDF 分割窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PDF 分割窗口。"""
    run_gui()


@fcmd.main("pdfsplitw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
