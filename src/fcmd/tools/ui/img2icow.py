"""img2icow - 图像转 ICO 转换器（Tk GUI）。

交互参考 ``bitool_gui.img2ico``：选择单张图像或整个目录，填写逗号分隔的
图标尺寸与质量档位，一键生成多尺寸 Windows ICO。输入同时支持 SVG 与常见
位图（PNG/JPG/GIF/BMP/WebP/TIFF），图像按原始宽高比缩放并居中放入透明
方形画布；目录输入默认输出到 ``ico_output/``，单文件缺省输出到同目录。

设置（上次输入/输出路径、尺寸、质量、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/img2icow.json``，下次启动自动恢复。

示例
----
    fcmd img2icow         # 打开图像转 ICO 转换器窗口
    img2icow              # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import fcmd
from fcmd.console import print_error

__all__ = [
    "DEFAULT_SIZES_TEXT",
    "QUALITY_CHOICES",
    "Img2IcoSettings",
    "config_path",
    "load_settings",
    "normalize_quality",
    "parse_sizes_text",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "图像转 ICO 转换器窗口版（Tk）：支持 SVG/PNG/JPG/GIF/BMP/WebP/TIFF 单文件或目录批量转换，"
    "自定义多尺寸与质量档位，自动保持宽高比并居中透明填充；"
    "设置持久化到 ~/.fcmd/ui/img2icow.json"
)

DEFAULT_SIZES_TEXT = "16,24,32,48,64,128,256"
QUALITY_CHOICES: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_QUALITY = "high"
DEFAULT_GEOMETRY = "880x640+260+160"
MIN_WIDTH = 760
MIN_HEIGHT = 560

_GEOMETRY_RE = re.compile(r"^\d+x\d+([+-]\d+[+-]\d+)?$")


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/img2icow.json``。"""
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "ui" / "img2icow.json"


@dataclass
class Img2IcoSettings:
    """图像转 ICO 转换器的持久化设置。"""

    input_path: str = ""
    output_path: str = ""
    sizes: str = DEFAULT_SIZES_TEXT
    quality: str = DEFAULT_QUALITY
    geometry: str | None = None


def valid_geometry(text: str | None) -> str | None:
    """校验 Tk geometry 字符串（``WxH`` 或 ``WxH+X+Y``），非法时返回 ``None``。"""
    if text is not None and _GEOMETRY_RE.fullmatch(text):
        return text
    return None


def parse_sizes_text(text: str) -> list[int]:
    """解析逗号/空格分隔的尺寸文本（复用 CLI 的尺寸解析规则）。

    Raises
    ------
    ValueError
        未填写尺寸、包含非整数或非正数。
    """
    from fcmd.dsl.actions.media import parse_size_tokens

    if not text.strip():
        raise ValueError("请输入图标尺寸，例如 16,32,48,128,256")
    sizes = parse_size_tokens([text])
    if not sizes:
        raise ValueError("请输入图标尺寸，例如 16,32,48,128,256")
    return sizes


def normalize_quality(name: str) -> str:
    """把质量档位规范为 ``low`` / ``medium`` / ``high``（忽略大小写与空白）。

    Raises
    ------
    ValueError
        档位不在支持列表中。
    """
    key = name.strip().lower()
    if key not in QUALITY_CHOICES:
        raise ValueError(f"不支持的质量: {name}，支持: {', '.join(QUALITY_CHOICES)}")
    return key


def load_settings(path: Path | None = None) -> Img2IcoSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    file = path or config_path()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Img2IcoSettings()
    if not isinstance(raw, dict):
        return Img2IcoSettings()

    settings = Img2IcoSettings()
    input_path = raw.get("input_path")
    output_path = raw.get("output_path")
    sizes = raw.get("sizes")
    quality = raw.get("quality")
    geometry = raw.get("geometry")
    if isinstance(input_path, str):
        settings.input_path = input_path
    if isinstance(output_path, str):
        settings.output_path = output_path
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(sizes, str) and sizes.strip():
        # 校验通过才保留，非法尺寸文本回退默认值
        with contextlib.suppress(ValueError):
            parse_sizes_text(sizes)
            settings.sizes = sizes
    if isinstance(quality, str):
        with contextlib.suppress(ValueError):
            settings.quality = normalize_quality(quality)
    return settings


def save_settings(settings: Img2IcoSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    file = path or config_path()
    payload = {
        "input_path": settings.input_path,
        "output_path": settings.output_path,
        "sizes": settings.sizes,
        "quality": settings.quality,
        "geometry": settings.geometry,
    }
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class Img2IcoWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 图像转 ICO 转换器主窗口。"""

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
        self.root.title(f"图像转 ICO 转换器 v{__version__}")
        self.root.geometry(valid_geometry(self._settings.geometry) or DEFAULT_GEOMETRY)
        self.root.minsize(MIN_WIDTH, MIN_HEIGHT)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._input_var = tk.StringVar(value=self._settings.input_path)
        self._output_var = tk.StringVar(value=self._settings.output_path)
        self._sizes_var = tk.StringVar(value=self._settings.sizes)
        self._quality_var = tk.StringVar(value=self._settings.quality)
        self._status_var = tk.StringVar(value="就绪")

        self._build_widgets()

    def _build_widgets(self) -> None:
        tk = self._tk
        self._build_menu()

        input_frame = tk.LabelFrame(self.root, text="输入", padx=8, pady=8)
        input_frame.pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(input_frame, text="图像/目录：", width=10, anchor="w").pack(side="left")
        tk.Entry(input_frame, textvariable=self._input_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._browse_input_dir_button = tk.Button(input_frame, text="目录…", command=self._browse_input_dir)
        self._browse_input_dir_button.pack(side="right")
        self._browse_input_file_button = tk.Button(input_frame, text="文件…", command=self._browse_input_file)
        self._browse_input_file_button.pack(side="right", padx=(0, 4))

        output_frame = tk.LabelFrame(self.root, text="输出", padx=8, pady=8)
        output_frame.pack(fill="x", padx=12, pady=4)
        tk.Label(output_frame, text="输出路径：", width=10, anchor="w").pack(side="left")
        tk.Entry(output_frame, textvariable=self._output_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._browse_output_button = tk.Button(output_frame, text="浏览…", command=self._browse_output)
        self._browse_output_button.pack(side="right")
        tk.Label(
            output_frame,
            text="留空时：单文件输出到同目录，目录输入输出到 ico_output/",
            fg="#666666",
            anchor="w",
        ).pack(side="bottom", fill="x", pady=(6, 0))

        options_frame = tk.LabelFrame(self.root, text="选项", padx=8, pady=8)
        options_frame.pack(fill="x", padx=12, pady=4)
        tk.Label(options_frame, text="图标尺寸：", width=10, anchor="w").pack(side="left")
        tk.Entry(options_frame, textvariable=self._sizes_var, width=36).pack(side="left", padx=(0, 16))
        tk.Label(options_frame, text="质量：", anchor="w").pack(side="left")
        quality_combo = self._ttk.Combobox(
            options_frame,
            textvariable=self._quality_var,
            values=QUALITY_CHOICES,
            state="readonly",
            width=10,
        )
        quality_combo.pack(side="left", padx=(4, 0))

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
        self._append_log("选择输入图像或目录，填写尺寸后点击「开始转换」。")

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
            f"图像转 ICO 转换器 v{__version__}\n\n"
            "支持 SVG/PNG/JPG/GIF/BMP/WebP/TIFF\n"
            "按原始宽高比缩放并居中透明填充\n"
            "尺寸与质量可自定义，目录可批量转换",
        )

    def _browse_input_file(self) -> None:
        """选择输入图像文件。"""
        filename = self._filedialog.askopenfilename(
            title="选择输入图像",
            filetypes=[
                ("图像文件", "*.svg *.png *.jpg *.jpeg *.gif *.bmp *.webp *.tif *.tiff"),
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
        """选择输出文件或目录（根据输入类型决定）。"""
        raw_input = self._input_var.get().strip()
        input_path = Path(raw_input).expanduser() if raw_input else None
        if input_path is not None and input_path.is_dir():
            directory = self._filedialog.askdirectory(title="选择输出目录")
            if directory:
                self._output_var.set(directory)
            return

        initial_dir = str(input_path.parent) if input_path is not None and input_path.parent.exists() else None
        initial_file = f"{input_path.stem}.ico" if input_path is not None else "icon.ico"
        filename = self._filedialog.asksaveasfilename(
            title="选择输出图标",
            defaultextension=".ico",
            initialfile=initial_file,
            initialdir=initial_dir,
            filetypes=[("ICO 图标", "*.ico"), ("所有文件", "*.*")],
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
            self._append_log("错误：请选择输入图像或目录")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.exists():
            self._append_log(f"错误：输入路径不存在 {input_path}")
            return

        try:
            sizes = parse_sizes_text(self._sizes_var.get())
            quality = normalize_quality(self._quality_var.get())
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return

        raw_output = self._output_var.get().strip()
        output_path = Path(raw_output).expanduser() if raw_output else None

        self._clear_log()
        self._set_busy(True)
        self._progress.configure(maximum=1, value=0)
        self._status_var.set("正在转换…")
        self._append_log(f"输入：{input_path}")
        self._append_log(f"尺寸：{', '.join(str(size) for size in sizes)}；质量：{quality}")

        self._worker = threading.Thread(
            target=self._run_conversion,
            args=(input_path, output_path, sizes, quality),
            daemon=True,
        )
        self._worker.start()
        self.root.after(80, self._poll_queue)

    def _run_conversion(
        self,
        input_path: Path,
        output_path: Path | None,
        sizes: list[int],
        quality: str,
    ) -> None:
        """后台线程：执行转换并把进度 / 结果写入队列。"""
        from fcmd.dsl.actions.media import convert_images

        def progress(done: int, total: int, message: str) -> None:
            self._queue.put(("progress", done, total, message))

        try:
            canonical, outputs, used_sizes = convert_images(
                input_path,
                output_path,
                fmt="win",
                sizes=sizes,
                quality=quality,
                progress=progress,
            )
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("done", canonical, outputs, used_sizes))

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
                _, _canonical, outputs, used_sizes = event
                self._progress.configure(maximum=max(len(outputs), 1), value=len(outputs))
                for output in outputs:
                    self._append_log(f"已生成 {output}")
                self._append_log(f"完成：{len(outputs)} 个图标，{len(used_sizes)} 种尺寸")
                self._status_var.set(f"完成：{len(outputs)} 个图标")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showinfo("转换完成", f"已生成 {len(outputs)} 个图标文件。")
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
        save_settings(
            Img2IcoSettings(
                input_path=self._input_var.get(),
                output_path=self._output_var.get(),
                sizes=self._sizes_var.get(),
                quality=self._quality_var.get(),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口。"""
    try:
        window = Img2IcoWindow(settings_path)
    except ImportError as exc:
        print_error(f"tkinter 不可用，无法启动图像转 ICO 转换器: {exc}")
        return
    window.root.mainloop()


@fcmd.tool("img2icow", help="打开图像转 ICO 转换器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开图像转 ICO 转换器窗口。"""
    run_gui()


@fcmd.main("img2icow")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
