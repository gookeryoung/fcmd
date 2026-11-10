"""pdfcryptw - PDF 批量加密 / 解密窗口版（Tk）。

选择目录后一键加密或解密其中的 PDF（可递归子目录），输出与源文件同目录：
加密写 ``<原名>.enc.pdf``，解密写 ``<原名>.dec.pdf``。处理在后台线程执行，
进度与日志实时回传界面；目录、并发线程数与递归选项保存在
``${FCMD_HOME:-~/.fcmd}/ui/pdfcryptw.json``（密码不落盘）。

示例
----
    fcmd pdfcryptw        # 打开 PDF 加密解密窗口
    pdfcryptw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.pdf.pdfcrypt import (
    DEFAULT_WORKERS,
    MIN_PASSWORD_LENGTH,
    collect_pdf_files,
    process_files,
    validate_password,
)

__all__ = [
    "DEFAULT_GEOMETRY",
    "DEFAULT_WORKERS",
    "MAX_WORKERS",
    "MIN_WORKERS",
    "PdfCryptSettings",
    "config_path",
    "load_settings",
    "parse_workers",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PDF 批量加密解密窗口版（Tk）：选择目录、输入密码，一键加密或解密，"
    "支持递归子目录与并发线程；设置持久化到 ~/.fcmd/ui/pdfcryptw.json（不含密码）"
)

DEFAULT_GEOMETRY = "860x680+240+140"
MIN_WIDTH = 760
MIN_HEIGHT = 560
MIN_WORKERS = 1
MAX_WORKERS = 32


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/pdfcryptw.json``。"""
    return _guiconfig.config_path("pdfcryptw")


@dataclass
class PdfCryptSettings:
    """加密解密窗口的持久化设置（不保存密码）。"""

    directory: str = ""
    workers: int = DEFAULT_WORKERS
    recursive: bool = True
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def parse_workers(value: int | str) -> int:
    """把界面输入解析为合法并发线程数（1-32）。

    Raises
    ------
    ValueError
        非整数或超出范围。
    """
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"并发线程数须为整数: {value!r}") from exc
    if not MIN_WORKERS <= workers <= MAX_WORKERS:
        raise ValueError(f"并发线程数须为 {MIN_WORKERS}-{MAX_WORKERS}: {workers}")
    return workers


def load_settings(path: Path | None = None) -> PdfCryptSettings:
    """读取设置文件；缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PdfCryptSettings()

    settings = PdfCryptSettings()
    directory = raw.get("directory")
    workers = raw.get("workers")
    recursive = raw.get("recursive")
    geometry = raw.get("geometry")
    if isinstance(directory, str):
        settings.directory = directory
    if isinstance(workers, int) and not isinstance(workers, bool):
        with contextlib.suppress(ValueError):
            settings.workers = parse_workers(workers)
    if isinstance(recursive, bool):
        settings.recursive = recursive
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: PdfCryptSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "directory": settings.directory,
        "workers": settings.workers,
        "recursive": settings.recursive,
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
class PdfCryptWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk PDF 加密解密主窗口。"""

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
            title=f"PDF 加密解密 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._directory_var = tk.StringVar(value=self._settings.directory or str(Path.cwd()))
        self._password_var = tk.StringVar()
        self._workers_var = tk.StringVar(value=str(self._settings.workers))
        self._recursive_var = tk.BooleanVar(value=self._settings.recursive)
        self._status_var = tk.StringVar(value="就绪")

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)

    def _build_widgets(self) -> None:
        ttk = self._ttk
        palette = self._theme.palette
        self._build_menu()

        directory_frame = ttk.LabelFrame(self.root, text="目录", padding=self._theme.spacing.sm)
        directory_frame.pack(
            fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.md, self._theme.spacing.xs)
        )
        ttk.Label(directory_frame, text="PDF 目录：", width=10, anchor="w").pack(side="left")
        ttk.Entry(directory_frame, textvariable=self._directory_var).pack(
            side="left", fill="x", expand=True, padx=(0, self._theme.spacing.sm)
        )
        self._browse_button = ttk.Button(directory_frame, text="浏览…", command=self._browse_directory)
        self._browse_button.pack(side="right")

        options_frame = ttk.LabelFrame(self.root, text="选项", padding=self._theme.spacing.sm)
        options_frame.pack(fill="x", padx=self._theme.spacing.md, pady=self._theme.spacing.xs)
        ttk.Label(options_frame, text="密码：", width=10, anchor="w").pack(side="left")
        self._password_entry = ttk.Entry(options_frame, textvariable=self._password_var, show="*")
        self._password_entry.pack(side="left", fill="x", expand=True, padx=(0, self._theme.spacing.lg))
        ttk.Label(options_frame, text="线程：", anchor="w").pack(side="left")
        self._workers_spinbox = ttk.Spinbox(
            options_frame,
            from_=MIN_WORKERS,
            to=MAX_WORKERS,
            increment=1,
            textvariable=self._workers_var,
            width=6,
        )
        self._workers_spinbox.pack(side="left", padx=(self._theme.spacing.xs, self._theme.spacing.lg))
        self._recursive_check = ttk.Checkbutton(
            options_frame,
            text="递归子目录",
            variable=self._recursive_var,
        )
        self._recursive_check.pack(side="left")

        hint = f"密码至少 {MIN_PASSWORD_LENGTH} 字符更安全；加密输出 .enc.pdf，解密输出 .dec.pdf"
        ttk.Label(
            self.root,
            text=hint,
            foreground=palette.muted,
            anchor="w",
        ).pack(fill="x", padx=self._theme.spacing.md)

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.xs, 0))
        self._encrypt_button = ttk.Button(
            action_frame,
            text="加密",
            command=lambda: self._start(decrypt=False),
            style="Accent.TButton",
            padding=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.xs),
        )
        self._encrypt_button.pack(side="right")
        self._decrypt_button = ttk.Button(
            action_frame,
            text="解密",
            command=lambda: self._start(decrypt=True),
            padding=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.xs),
        )
        self._decrypt_button.pack(side="right", padx=(0, self._theme.spacing.sm))
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
        self._append_log("选择目录、输入密码后点击「加密」或「解密」。")

    def _build_menu(self) -> None:
        _guiwindow.build_menu(self.root, on_close=self.close, on_about=self._show_about)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于",
            f"PDF 加密解密 v{__version__}\n\n"
            "加密输出 <原名>.enc.pdf，解密输出 <原名>.dec.pdf\n"
            "支持递归子目录与并发处理\n"
            "设置不保存密码",
        )

    def _browse_directory(self) -> None:
        directory = self._filedialog.askdirectory(title="选择 PDF 目录")
        if directory:
            self._directory_var.set(directory)

    def _append_log(self, message: str) -> None:
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _set_busy(self, busy: bool) -> None:
        """处理期间禁用输入，避免重复提交。"""
        state = "disabled" if busy else "normal"
        for widget in (
            self._encrypt_button,
            self._decrypt_button,
            self._browse_button,
            self._password_entry,
            self._workers_spinbox,
        ):
            widget.configure(state=state)
        self._recursive_check.configure(state=state)

    # ------------------------------------------------------------------ #
    # 处理（后台线程 + 队列回传）
    # ------------------------------------------------------------------ #
    def _start(self, *, decrypt: bool) -> None:
        """校验输入并启动后台加解密线程。"""
        if self._worker is not None and self._worker.is_alive():
            return

        directory = Path(self._directory_var.get().strip() or ".").expanduser()
        try:
            password = validate_password(self._password_var.get())
            workers = parse_workers(self._workers_var.get())
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return
        if len(password) < MIN_PASSWORD_LENGTH:
            self._append_log(f"警告：密码长度小于 {MIN_PASSWORD_LENGTH} 字符，建议使用更强的密码")

        action = "解密" if decrypt else "加密"
        self._set_busy(True)
        self._progress.configure(maximum=1, value=0)
        self._status_var.set(f"正在{action}…")
        self._append_log(f"{action}目录：{directory}；线程：{workers}")

        self._worker = _guiwindow.start_worker(
            self.root,
            self._run,
            (directory, password, workers, decrypt, self._recursive_var.get()),
            self._poll_queue,
        )

    def _run(self, directory: Path, password: str, workers: int, decrypt: bool, recursive: bool) -> None:
        """后台线程：筛选文件并批量处理，把事件写入队列。"""
        action = "解密" if decrypt else "加密"
        try:
            files = collect_pdf_files(directory, encrypted=decrypt, recursive=recursive)
            if not files:
                kind = "已加密" if decrypt else "未加密"
                self._queue.put(("empty", f"目录中未找到{kind}的 PDF 文件：{directory}"))
                return
            self._queue.put(("message", f"找到 {len(files)} 个待{action}文件"))

            def progress(done: int, total: int, source: Path, result: Any) -> None:
                self._queue.put(("progress", done, total, source.name, result.ok, result.error))

            report = process_files(files, password, decrypt=decrypt, workers=workers, progress=progress)
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("done", action, len(report.succeeded), len(report.failed)))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "message":
                self._append_log(event[1])
            elif kind == "progress":
                _, done, total, name, ok, error = event
                self._progress.configure(maximum=max(total, 1), value=done)
                if ok:
                    self._append_log(f"[{done}/{total}] 完成：{name}")
                else:
                    self._append_log(f"[{done}/{total}] 失败：{name}（{error}）")
            elif kind == "empty":
                self._append_log(event[1])
                self._status_var.set("没有待处理文件")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showinfo("提示", event[1])
                return
            elif kind == "done":
                _, action, succeeded, failed = event
                self._progress.configure(value=self._progress.cget("maximum"))
                self._status_var.set(f"{action}完成：成功 {succeeded}，失败 {failed}")
                self._append_log(f"{action}汇总：成功 {succeeded} 个，失败 {failed} 个")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showinfo(f"{action}完成", f"成功 {succeeded} 个，失败 {failed} 个")
                return
            elif kind == "error":
                self._append_log(f"错误：{event[1]}")
                self._status_var.set("处理失败")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showerror("处理失败", event[1])
                return

        if self._worker is not None:
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll_queue)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口（后台线程为 daemon，随进程结束）。"""
        try:
            workers = parse_workers(self._workers_var.get())
        except ValueError:
            workers = DEFAULT_WORKERS
        save_settings(
            PdfCryptSettings(
                directory=self._directory_var.get(),
                workers=workers,
                recursive=self._recursive_var.get(),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: PdfCryptWindow(settings_path), "PDF 加密解密窗口")


@fcmd.tool("pdfcryptw", help="打开 PDF 加密解密窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PDF 加密解密窗口。"""
    run_gui()


@fcmd.main("pdfcryptw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
