"""docdiffw - 文档对比工具（Tk GUI）。

交互参考 ``bitool_office.docdiff``：选择旧 / 新 Word 文档，设置结果标题、
对比模式与是否显示修订，一键调用本机 Word 生成对比文档。对比在后台线程
执行，日志实时回传界面；设置（上次文件、输出、标题、模式与窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/docdiffw.json``，下次启动自动恢复。

依赖 Windows + Microsoft Word + ``pywin32``（``pip install fcmd[win32]``）。
与原工具一致，对比结束后会强制结束 ``WINWORD.EXE``，运行前请保存其他
正在编辑的 Word 文档。

示例
----
    fcmd docdiffw        # 打开文档对比窗口
    docdiffw             # 全局命令等价入口
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
from fcmd.tools.office.docdiff import (
    COMPARE_MODES,
    DEFAULT_TITLE,
    CompareMode,
    DocDiffCommand,
    DocDiffError,
    normalize_compare_mode,
    output_filename,
    resolve_output_path,
    validate_documents,
)

__all__ = [
    "DEFAULT_GEOMETRY",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "DocDiffSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "文档对比工具窗口版（Tk）：选择旧/新 doc/docx 文档，设置标题、original/revised 对比模式与"
    "是否显示修订，调用本机 Word 生成对比文档；设置持久化到 ~/.fcmd/ui/docdiffw.json，"
    "依赖 Windows + Word + fcmd[win32]"
)

DEFAULT_GEOMETRY = "900x620+240+160"
MIN_WIDTH = 780
MIN_HEIGHT = 560


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/docdiffw.json``。"""
    return _guiconfig.config_path("docdiffw")


@dataclass
class DocDiffSettings:
    """文档对比窗口的持久化设置。"""

    old_doc: str = ""
    new_doc: str = ""
    output: str = ""
    title: str = DEFAULT_TITLE
    show_changes: bool = True
    compare_mode: str = "original"
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def load_settings(path: Path | None = None) -> DocDiffSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return DocDiffSettings()

    settings = DocDiffSettings()
    old_doc = raw.get("old_doc")
    new_doc = raw.get("new_doc")
    output = raw.get("output")
    title = raw.get("title")
    compare_mode = raw.get("compare_mode")
    geometry = raw.get("geometry")
    if isinstance(old_doc, str):
        settings.old_doc = old_doc
    if isinstance(new_doc, str):
        settings.new_doc = new_doc
    if isinstance(output, str):
        settings.output = output
    if isinstance(title, str) and title:
        settings.title = title
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(compare_mode, str):
        with contextlib.suppress(ValueError):
            settings.compare_mode = normalize_compare_mode(compare_mode)
    settings.show_changes = raw.get("show_changes") is not False
    return settings


def save_settings(settings: DocDiffSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "old_doc": settings.old_doc,
        "new_doc": settings.new_doc,
        "output": settings.output,
        "title": settings.title,
        "show_changes": settings.show_changes,
        "compare_mode": settings.compare_mode,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class DocDiffWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 文档对比工具主窗口。"""

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
        self._browse_buttons: list[Any] = []

        self.root, self._theme = _guiwindow.create_root(
            title=f"文档对比工具 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._old_var = tk.StringVar(value=self._settings.old_doc)
        self._new_var = tk.StringVar(value=self._settings.new_doc)
        self._output_var = tk.StringVar(value=self._settings.output)
        self._title_var = tk.StringVar(value=self._settings.title)
        self._show_changes_var = tk.BooleanVar(value=self._settings.show_changes)
        self._compare_mode_var = tk.StringVar(value=self._settings.compare_mode)
        self._status_var = tk.StringVar(value="就绪")

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)

    def _build_widgets(self) -> None:
        ttk = self._ttk
        palette = self._theme.palette
        self._build_menu()

        container = ttk.Frame(self.root, padding=self._theme.spacing.sm)
        container.pack(fill="both", expand=True)

        files_frame = ttk.LabelFrame(container, text="文档", padding=self._theme.spacing.sm)
        files_frame.pack(fill="x")
        files_frame.columnconfigure(1, weight=1)
        self._add_file_row(files_frame, 0, "旧文档：", self._old_var, self._browse_old)
        self._add_file_row(files_frame, 1, "新文档：", self._new_var, self._browse_new)
        self._add_file_row(files_frame, 2, "输出：", self._output_var, self._browse_output)
        ttk.Label(
            files_frame,
            text="输出留空时保存到新文档同目录：<标题>@<时间戳>.docx",
            foreground=palette.muted,
            anchor="w",
        ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(self._theme.spacing.sm, 0))

        options_frame = ttk.LabelFrame(container, text="选项", padding=self._theme.spacing.sm)
        options_frame.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        options_frame.columnconfigure(1, weight=1)
        ttk.Label(options_frame, text="标题：", width=10, anchor="w").grid(row=0, column=0, sticky="w")
        ttk.Entry(options_frame, textvariable=self._title_var).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=(0, self._theme.spacing.sm)
        )
        ttk.Label(options_frame, text="对比模式：", width=10, anchor="w").grid(
            row=1, column=0, sticky="w", pady=(self._theme.spacing.xs, 0)
        )
        self._compare_mode_combo = ttk.Combobox(
            options_frame,
            textvariable=self._compare_mode_var,
            values=list(COMPARE_MODES),
            state="readonly",
            width=12,
        )
        self._compare_mode_combo.grid(
            row=1, column=1, sticky="w", padx=(0, self._theme.spacing.lg), pady=(self._theme.spacing.xs, 0)
        )
        self._show_changes_check = ttk.Checkbutton(
            options_frame,
            text="显示修订标记",
            variable=self._show_changes_var,
        )
        self._show_changes_check.grid(row=1, column=2, columnspan=2, sticky="w", pady=(self._theme.spacing.xs, 0))
        ttk.Label(
            options_frame,
            text="original 以原文档为基础；revised 以修订文档为基础。",
            foreground=palette.muted,
            anchor="w",
        ).grid(row=2, column=0, columnspan=4, sticky="ew", pady=(self._theme.spacing.sm, 0))

        action_frame = ttk.Frame(container)
        action_frame.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        self._start_button = ttk.Button(
            action_frame,
            text="开始对比",
            command=self._start_comparison,
            style="Accent.TButton",
            padding=(self._theme.spacing.lg + self._theme.spacing.xxs, self._theme.spacing.xs),
        )
        self._start_button.pack(side="right")
        ttk.Label(action_frame, textvariable=self._status_var, anchor="w").pack(side="left", fill="x", expand=True)

        log_frame = ttk.LabelFrame(container, text="日志", padding=self._theme.spacing.sm)
        log_frame.pack(fill="both", expand=True, pady=(self._theme.spacing.sm, 0))
        self._log_text = _guiwindow.create_log(log_frame, height=10)
        self._append_log("选择旧文档与新文档后点击「开始对比」。")

    def _add_file_row(
        self,
        parent: Any,
        row: int,
        label: str,
        variable: Any,
        command: Any,
    ) -> None:
        """添加一行「标签 + 输入框 + 浏览按钮」。"""
        ttk = self._ttk
        ttk.Label(parent, text=label, width=10, anchor="w").grid(row=row, column=0, sticky="w")
        ttk.Entry(parent, textvariable=variable).grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(0, self._theme.spacing.sm),
            pady=self._theme.spacing.hairline,
        )
        button = ttk.Button(parent, text="浏览…", command=command)
        button.grid(row=row, column=2, sticky="e", pady=self._theme.spacing.hairline)
        self._browse_buttons.append(button)

    def _build_menu(self) -> None:
        _guiwindow.build_menu(self.root, on_close=self.close, on_about=self._show_about)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"文档对比工具 v{__version__}\n\n"
            "调用本机 Microsoft Word 生成对比文档\n"
            "支持 .doc / .docx，original / revised 对比模式\n"
            "对比结束后会强制结束 WINWORD.EXE",
        )

    def _ask_doc(self, title: str) -> str:
        """选择 Word 文档的通用文件对话框。"""
        return self._filedialog.askopenfilename(
            title=title,
            filetypes=[("Word 文档", "*.doc *.docx"), ("所有文件", "*.*")],
        )

    def _browse_old(self) -> None:
        """选择旧文档。"""
        filename = self._ask_doc("选择旧文档")
        if filename:
            self._old_var.set(filename)

    def _browse_new(self) -> None:
        """选择新文档。"""
        filename = self._ask_doc("选择新文档")
        if filename:
            self._new_var.set(filename)

    def _browse_output(self) -> None:
        """选择对比结果文件。"""
        raw_new = self._new_var.get().strip()
        new_path = Path(raw_new).expanduser() if raw_new else None
        initial_dir = str(new_path.parent) if new_path is not None and new_path.parent.exists() else None
        title = self._title_var.get().strip() or DEFAULT_TITLE
        filename = self._filedialog.asksaveasfilename(
            title="选择对比结果",
            defaultextension=".docx",
            initialfile=output_filename(title),
            initialdir=initial_dir,
            filetypes=[("Word 文档", "*.docx"), ("所有文件", "*.*")],
        )
        if filename:
            self._output_var.set(filename)

    def _append_log(self, message: str) -> None:
        """向只读日志区追加带时间戳的一行并滚动到底部。"""
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _clear_log(self) -> None:
        """清空日志区。"""
        _guiwindow.clear_log(self._log_text)

    def _set_busy(self, busy: bool) -> None:
        """对比期间禁用按钮，避免重复提交。"""
        state = "disabled" if busy else "normal"
        self._start_button.configure(state=state)
        for button in self._browse_buttons:
            button.configure(state=state)

    # ------------------------------------------------------------------ #
    # 对比（后台线程 + 队列回传，避免阻塞 Tk 主循环）
    # ------------------------------------------------------------------ #
    def _collect_inputs(self) -> tuple[Path, Path] | None:
        """读取并校验旧 / 新文档路径；失败时写日志并返回 ``None``。"""
        raw_old = self._old_var.get().strip()
        raw_new = self._new_var.get().strip()
        if not raw_old or not raw_new:
            self._append_log("错误：请选择旧文档与新文档")
            return None
        old_doc = Path(raw_old).expanduser()
        new_doc = Path(raw_new).expanduser()
        try:
            validate_documents(old_doc, new_doc)
        except DocDiffError as exc:
            self._append_log(f"错误：{exc}")
            return None
        return old_doc, new_doc

    def _start_comparison(self) -> None:
        """校验输入并启动后台对比线程。"""
        if self._worker is not None and self._worker.is_alive():
            return
        inputs = self._collect_inputs()
        if inputs is None:
            return
        old_doc, new_doc = inputs

        try:
            compare_mode = normalize_compare_mode(self._compare_mode_var.get())
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return

        title = self._title_var.get().strip() or DEFAULT_TITLE
        raw_output = self._output_var.get().strip()
        output = Path(raw_output).expanduser() if raw_output else None
        preview = resolve_output_path(new_doc, output, title=title)

        self._clear_log()
        self._set_busy(True)
        self._status_var.set("正在对比…")
        self._append_log(f"旧文档：{old_doc}")
        self._append_log(f"新文档：{new_doc}")
        self._append_log(f"输出：{preview}")
        self._append_log(f"对比模式：{compare_mode}；显示修订：{'是' if self._show_changes_var.get() else '否'}")

        self._worker = _guiwindow.start_worker(
            self.root,
            self._run_comparison,
            (old_doc, new_doc, output, title, bool(self._show_changes_var.get()), compare_mode),
            self._poll_queue,
        )

    def _run_comparison(  # noqa: PLR0913, PLR0917 - 后台线程参数与 CLI 一一对应
        self,
        old_doc: Path,
        new_doc: Path,
        output: Path | None,
        title: str,
        show_changes: bool,
        compare_mode: CompareMode,
    ) -> None:
        """后台线程：执行对比并把结果写入队列。"""
        try:
            target = DocDiffCommand(
                old_doc,
                new_doc,
                output,
                title=title,
                show_changes=show_changes,
                compare_mode=compare_mode,
            ).run()
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("done", str(target)))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "done":
                _, target = event
                self._append_log(f"对比完成：{target}")
                self._status_var.set("对比完成")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showinfo("对比完成", f"已生成对比文档：\n{target}")
                return
            if kind == "error":
                _, message = event
                self._append_log(f"错误：{message}")
                self._status_var.set("对比失败")
                self._set_busy(False)
                self._worker = None
                self._messagebox.showerror("对比失败", message)
                return

        if self._worker is not None:
            self.root.after(80, self._poll_queue)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口（后台线程为 daemon，随进程结束）。"""
        try:
            compare_mode = normalize_compare_mode(self._compare_mode_var.get())
        except ValueError:
            compare_mode = "original"
        save_settings(
            DocDiffSettings(
                old_doc=self._old_var.get(),
                new_doc=self._new_var.get(),
                output=self._output_var.get(),
                title=self._title_var.get(),
                show_changes=bool(self._show_changes_var.get()),
                compare_mode=compare_mode,
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: DocDiffWindow(settings_path), "文档对比工具")


@fcmd.tool("docdiffw", help="打开文档对比窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开文档对比窗口。"""
    run_gui()


@fcmd.main("docdiffw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
