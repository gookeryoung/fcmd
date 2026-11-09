"""img2pdfw - 图片转 PDF 转换器（Tk GUI）。

交互参考 ``bitool_gui.img2pdf``：选择图片文件或目录，设置页面尺寸、DPI
与 EXIF 方向校正，一键合并为 PDF。目录输入按文件名顺序排版并默认输出
``<目录名>.pdf``，单文件输入默认输出同名 PDF。转换在后台线程执行，进度
与日志实时回传界面；设置（上次输入/输出、页面尺寸、DPI、窗口位置）保存
在 ``${FCMD_HOME:-~/.fcmd}/ui/img2pdfw.json``，下次启动自动恢复。

示例
----
    fcmd img2pdfw        # 打开图片转 PDF 转换器窗口
    img2pdfw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import print_error
from fcmd.tools import _guiconfig

__all__ = [
    "DEFAULT_DPI",
    "DEFAULT_GEOMETRY",
    "DEFAULT_PAGE_SIZE",
    "PAGE_SIZE_OPTIONS",
    "Img2PdfSettings",
    "config_path",
    "default_output_path",
    "load_settings",
    "parse_dpi",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "图片转 PDF 转换器窗口版（Tk）：支持图片文件或目录批量、页面尺寸（A3/A4/A5/B5/letter/legal）"
    "与 DPI 设置、EXIF 方向校正；设置持久化到 ~/.fcmd/ui/img2pdfw.json"
)

PAGE_SIZE_OPTIONS: tuple[str, ...] = ("auto", "A4", "A3", "A5", "B5", "letter", "legal")
DEFAULT_PAGE_SIZE = "auto"
DEFAULT_DPI = 150
DEFAULT_GEOMETRY = "900x680+240+140"
MIN_WIDTH = 780
MIN_HEIGHT = 580


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/img2pdfw.json``。"""
    return _guiconfig.config_path("img2pdfw")


@dataclass
class Img2PdfSettings:
    """图片转 PDF 转换器的持久化设置。"""

    input_path: str = ""
    output_path: str = ""
    page_size: str = DEFAULT_PAGE_SIZE
    dpi: int = DEFAULT_DPI
    normalize: bool = True
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def normalize_page(value: str) -> str:
    """校验并规范化页面尺寸（复用 CLI 的取值规则）。

    Raises
    ------
    ValueError
        取值不在支持列表中。
    """
    from fcmd.tools.pdf.img2pdf import normalize_page_size

    return normalize_page_size(value)


def format_page_size(value: str) -> str:
    """把规范化页面键转成界面显示值（``A4`` / ``auto`` / ``letter``）。"""
    key = normalize_page(value)
    return key if key in {"auto", "letter", "legal"} else key.upper()


def parse_dpi(value: int | str) -> int:
    """把界面输入解析为合法 DPI（36-1200）。

    Raises
    ------
    ValueError
        非整数或超出范围。
    """
    from fcmd.tools.pdf.img2pdf import normalize_dpi

    try:
        dpi = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"DPI 须为整数: {value!r}") from exc
    return normalize_dpi(dpi)


def default_output_path(input_path: Path) -> Path:
    """按输入类型给出缺省输出：目录 → ``<目录名>.pdf``，文件 → 同名 ``.pdf``。"""
    if input_path.is_dir():
        return input_path / f"{input_path.name}.pdf"
    return input_path.with_suffix(".pdf")


def load_settings(path: Path | None = None) -> Img2PdfSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return Img2PdfSettings()

    settings = Img2PdfSettings()
    input_path = raw.get("input_path")
    output_path = raw.get("output_path")
    page_size = raw.get("page_size")
    dpi = raw.get("dpi")
    normalize = raw.get("normalize")
    geometry = raw.get("geometry")
    if isinstance(input_path, str):
        settings.input_path = input_path
    if isinstance(output_path, str):
        settings.output_path = output_path
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(page_size, str):
        with contextlib.suppress(ValueError):
            settings.page_size = normalize_page(page_size)
    if isinstance(dpi, int) and not isinstance(dpi, bool):
        with contextlib.suppress(ValueError):
            settings.dpi = parse_dpi(dpi)
    if isinstance(normalize, bool):
        settings.normalize = normalize
    return settings


def save_settings(settings: Img2PdfSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "input_path": settings.input_path,
        "output_path": settings.output_path,
        "page_size": settings.page_size,
        "dpi": settings.dpi,
        "normalize": settings.normalize,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class Img2PdfWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 图片转 PDF 转换器主窗口。"""

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

        self.root = tk.Tk()
        self.root.title(f"图片转 PDF 转换器 v{__version__}")
        self.root.geometry(valid_geometry(self._settings.geometry) or DEFAULT_GEOMETRY)
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._input_var = tk.StringVar(value=self._settings.input_path)
        self._output_var = tk.StringVar(value=self._settings.output_path)
        self._page_size_var = tk.StringVar(value=format_page_size(self._settings.page_size))
        self._dpi_var = tk.StringVar(value=str(self._settings.dpi))
        self._normalize_var = tk.BooleanVar(value=self._settings.normalize)
        self._status_var = tk.StringVar(value="就绪")

        self._build_widgets()

    def _build_widgets(self) -> None:
        tk = self._tk
        self._build_menu()

        input_frame = tk.LabelFrame(self.root, text="输入", padx=8, pady=8)
        input_frame.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(input_frame, text="图片/目录：", width=10, anchor="w").pack(side="left")
        tk.Entry(input_frame, textvariable=self._input_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._browse_input_dir_button = tk.Button(input_frame, text="目录…", command=self._browse_input_dir)
        self._browse_input_dir_button.pack(side="right")
        self._browse_input_file_button = tk.Button(input_frame, text="图片…", command=self._browse_input_file)
        self._browse_input_file_button.pack(side="right", padx=(0, 4))

        output_frame = tk.LabelFrame(self.root, text="输出", padx=8, pady=8)
        output_frame.pack(fill="x", padx=12, pady=4)
        tk.Label(output_frame, text="PDF 路径：", width=10, anchor="w").pack(side="left")
        tk.Entry(output_frame, textvariable=self._output_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._browse_output_button = tk.Button(output_frame, text="浏览…", command=self._browse_output)
        self._browse_output_button.pack(side="right")
        tk.Label(
            output_frame,
            text="留空时：目录输入输出到 <目录名>.pdf，单文件输出到同名 PDF",
            fg="#666666",
            anchor="w",
        ).pack(side="bottom", fill="x", pady=(6, 0))

        options_frame = tk.LabelFrame(self.root, text="选项", padx=8, pady=8)
        options_frame.pack(fill="x", padx=12, pady=4)
        tk.Label(options_frame, text="页面尺寸：", width=10, anchor="w").pack(side="left")
        self._page_size_combo = self._ttk.Combobox(
            options_frame,
            textvariable=self._page_size_var,
            values=PAGE_SIZE_OPTIONS,
            state="readonly",
            width=10,
        )
        self._page_size_combo.pack(side="left", padx=(0, 16))
        tk.Label(options_frame, text="DPI：", anchor="w").pack(side="left")
        self._dpi_spinbox = tk.Spinbox(
            options_frame,
            from_=36,
            to=1200,
            increment=1,
            textvariable=self._dpi_var,
            width=8,
        )
        self._dpi_spinbox.pack(side="left", padx=(4, 16))
        self._normalize_check = tk.Checkbutton(
            options_frame,
            text="校正 EXIF 方向",
            variable=self._normalize_var,
        )
        self._normalize_check.pack(side="left")

        action_frame = tk.Frame(self.root)
        action_frame.pack(fill="x", padx=12, pady=(4, 0))
        self._convert_button = tk.Button(
            action_frame,
            text="开始转换",
            command=self._start_conversion,
            bg="#2f6fed",
            fg="white",
            activebackground="#1f5bd0",
            activeforeground="white",
            padx=18,
            pady=4,
        )
        self._convert_button.pack(side="right")
        tk.Label(action_frame, textvariable=self._status_var, anchor="w").pack(side="left", fill="x", expand=True)

        progress_frame = tk.Frame(self.root)
        progress_frame.pack(fill="x", padx=12, pady=(8, 4))
        self._progress = self._ttk.Progressbar(progress_frame, mode="determinate", maximum=1, value=0)
        self._progress.pack(fill="x")

        log_frame = tk.LabelFrame(self.root, text="日志", padx=8, pady=8)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        scrollbar = tk.Scrollbar(log_frame, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._log_text.pack(side="left", fill="both", expand=True)
        self._append_log("选择图片或目录，设置页面尺寸与 DPI 后点击「开始转换」。")

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"图片转 PDF 转换器 v{__version__}\n\n"
            "支持 PNG/JPG/BMP/GIF/WebP/TIFF/ICO\n"
            "页面尺寸：auto/A4/A3/A5/B5/letter/legal\n"
            "目录输入按文件名顺序合并",
        )

    def _browse_input_file(self) -> None:
        """选择输入图片文件。"""
        filename = self._filedialog.askopenfilename(
            title="选择输入图片",
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.ico"),
                ("所有文件", "*.*"),
            ],
        )
        if filename:
            self._input_var.set(filename)

    def _browse_input_dir(self) -> None:
        """选择输入目录。"""
        directory = self._filedialog.askdirectory(title="选择输入目录")
        if directory:
            self._input_var.set(directory)

    def _browse_output(self) -> None:
        """选择输出 PDF 文件。"""
        raw_input = self._input_var.get().strip()
        input_path = Path(raw_input).expanduser() if raw_input else None
        if input_path is not None and input_path.exists():
            suggested = default_output_path(input_path)
        else:
            suggested = Path("output.pdf")
        filename = self._filedialog.asksaveasfilename(
            title="选择输出 PDF",
            defaultextension=".pdf",
            initialfile=suggested.name,
            initialdir=str(suggested.parent) if suggested.parent.exists() else "",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if filename:
            self._output_var.set(filename)

    def _append_log(self, message: str) -> None:
        """向只读日志区追加带时间戳的一行并滚动到底部。"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_text.configure(state="normal")
        self._log_text.insert("end", f"[{timestamp}] {message}\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        """清空日志区。"""
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用按钮，避免重复提交。"""
        state = "disabled" if busy else "normal"
        for button in (
            self._convert_button,
            self._browse_input_file_button,
            self._browse_input_dir_button,
            self._browse_output_button,
        ):
            button.configure(state=state)

    # ------------------------------------------------------------------ #
    # 转换（后台线程 + 队列回传，避免阻塞 Tk 主循环）
    # ------------------------------------------------------------------ #
    def _start_conversion(self) -> None:
        """校验输入并启动后台转换线程。"""
        if self._worker is not None and self._worker.is_alive():
            return

        raw_input = self._input_var.get().strip()
        if not raw_input:
            self._append_log("错误：请选择输入图片或目录")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.exists():
            self._append_log(f"错误：输入路径不存在 {input_path}")
            return

        try:
            page_size = normalize_page(self._page_size_var.get())
            dpi = parse_dpi(self._dpi_var.get())
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return

        raw_output = self._output_var.get().strip()
        output_path = Path(raw_output).expanduser() if raw_output else default_output_path(input_path)
        from fcmd.tools.pdf.img2pdf import resolve_pdf_output

        try:
            output_path = resolve_pdf_output(output_path)
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return

        self._clear_log()
        self._set_busy(True)
        self._progress.configure(maximum=1, value=0)
        self._status_var.set("正在转换…")
        self._append_log(f"输入：{input_path}")
        self._append_log(f"输出：{output_path}")
        self._append_log(f"页面尺寸：{page_size}；DPI：{dpi}")

        self._worker = threading.Thread(
            target=self._run_conversion,
            args=(input_path, output_path, page_size, dpi, self._normalize_var.get()),
            daemon=True,
        )
        self._worker.start()
        self.root.after(80, self._poll_queue)

    def _run_conversion(
        self,
        input_path: Path,
        output_path: Path,
        page_size: str,
        dpi: int,
        normalize: bool,
    ) -> None:
        """后台线程：执行转换并把进度 / 结果写入队列。"""
        from fcmd.tools.pdf.img2pdf import collect_pdf_images, images_to_pdf

        def progress(done: int, total: int, message: str) -> None:
            self._queue.put(("progress", done, total, message))

        try:
            images = collect_pdf_images([input_path])
            self._queue.put(("message", f"找到 {len(images)} 张图片"))
            result = images_to_pdf(
                images,
                output_path,
                page_size=page_size,
                dpi=dpi,
                normalize=normalize,
                progress=progress,
            )
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("done", result, len(images)))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "message":
                _, message = event
                self._append_log(message)
            elif kind == "progress":
                _, done, total, message = event
                self._progress.configure(maximum=max(total, 1), value=done)
                self._append_log(message)
            elif kind == "done":
                _, output, total = event
                self._progress.configure(maximum=max(total, 1), value=total)
                self._append_log(f"完成：{total} 页 -> {output}")
                self._status_var.set(f"完成：{total} 页")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showinfo("转换完成", f"已生成 PDF：\n{output}")
                return
            elif kind == "error":
                _, message = event
                self._append_log(f"错误：{message}")
                self._status_var.set("转换失败")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showerror("转换失败", message)
                return

        if self._worker is not None:
            self.root.after(80, self._poll_queue)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口（后台转换线程为 daemon，随进程结束）。"""
        try:
            page_size = normalize_page(self._page_size_var.get())
        except ValueError:
            page_size = DEFAULT_PAGE_SIZE
        try:
            dpi = parse_dpi(self._dpi_var.get())
        except ValueError:
            dpi = DEFAULT_DPI
        save_settings(
            Img2PdfSettings(
                input_path=self._input_var.get(),
                output_path=self._output_var.get(),
                page_size=page_size,
                dpi=dpi,
                normalize=self._normalize_var.get(),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口。"""
    try:
        window = Img2PdfWindow(settings_path)
    except ImportError as exc:
        print_error(f"tkinter 不可用，无法启动图片转 PDF 转换器: {exc}")
        return
    window.root.mainloop()


@fcmd.tool("img2pdfw", help="打开图片转 PDF 转换器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开图片转 PDF 转换器窗口。"""
    run_gui()


@fcmd.main("img2pdfw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
