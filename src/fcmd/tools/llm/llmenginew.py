"""llmenginew - llama.cpp 引擎管理的 Tk 窗口版。

基于 :mod:`fcmd.tools.llm.llmengine` 的核心逻辑，提供三种操作：

* 在线下载：按 tag / 变体（cuda / cpu）从 GitHub release 安装预编译包；
* 离线安装：检查本地 zip 离线包（引擎包、cudart 运行库包、源码包）并解压安装，
  也支持直接登记已编译的引擎目录；
* 引擎管理：查看已安装引擎、切换默认引擎、执行 ``llama-server --version`` 体检。

下载、检查与安装均在后台线程执行，界面显示实时进度与日志。

示例
----
    fcmd llmenginew
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import sidebar as _guisidebar
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.llm import llmengine, llmenv
from fcmd.tools.llm.llmbase import DEFAULT_ENGINE_TAG, EngineInfo, EngineVariant, ServeError, human_size

__all__ = [
    "EngineSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
]

_DESCRIPTION = (
    "llama.cpp 引擎窗口版（Tk）：在线下载官方预编译包，或检查并安装本地 zip 离线包"
    "（引擎包 / cudart 运行库 / 源码包），查看已安装引擎并切换默认；"
    "窗口位置与大小记忆到 ~/.fcmd/ui/llmenginew.json"
)

_VARIANTS = ("cuda", "cpu")
_OFFLINE_PLACEHOLDER = "尚未选择离线包（zip 文件或包含 zip 的目录）"
_KIND_LABELS = {
    "engine": "引擎",
    "cudart": "CUDA 运行库",
    "source": "源码包",
    "unknown": "未知",
    "invalid": "无效",
}
TaskFunc = Callable[[llmengine.ProgressCallback], tuple[str, str | None]]

MIN_WIDTH = 780
MIN_HEIGHT = 520
DEFAULT_WIDTH = 880
DEFAULT_HEIGHT = 640


@dataclass
class EngineSettings:
    """窗口持久化设置（当前只有 geometry）。"""

    geometry: str | None = None


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/llmenginew.json``。"""
    return _guiconfig.config_path("llmenginew")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> EngineSettings:
    """读取设置；文件缺失、JSON 损坏或 geometry 非法时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = EngineSettings()
    if raw is not None:
        geometry = raw.get("geometry")
        if isinstance(geometry, str) and valid_geometry(geometry):
            settings.geometry = geometry
    return settings


def save_settings(settings: EngineSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings({"geometry": settings.geometry}, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到窗口原默认尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


class EngineWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 引擎管理窗口：在线下载、离线包检查安装、引擎列表与体检。"""

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
            title="llama.cpp 引擎管理",
            saved_geometry=self._settings.geometry,
            default_geometry=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._close,
        )

        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None
        self._running = False
        self._tag_edited = False
        self._engines: list[EngineInfo] = []
        self._buttons: list[Any] = []

        self._tag_var = tk.StringVar(value=DEFAULT_ENGINE_TAG)
        self._variant_var = tk.StringVar(value="cuda")
        self._offline_var = tk.StringVar(value=_OFFLINE_PLACEHOLDER)
        self._deep_var = tk.BooleanVar(value=False)
        self._status_var = tk.StringVar(value="就绪")
        self._info_vars: dict[str, tk.StringVar] = {}

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_all()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=self._theme.spacing.md)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self._status_var).pack(side="bottom", fill="x", pady=(self._theme.spacing.xs, 0))
        self._nav = _guisidebar.SidebarNav(
            outer,
            theme=self._theme,
            page_padding=self._theme.spacing.md,
            title="引擎管理",
        )
        self._nav.container.pack(fill="both", expand=True)
        install_page = self._nav.add("install", "安装配置", hint="在线下载与离线包")
        manage_page = self._nav.add("manage", "引擎列表 / 日志", hint="已安装引擎与体检")
        self._nav.enable_shortcuts()
        self._build_online(install_page)
        self._build_offline(install_page)
        self._build_env(install_page)
        self._build_actions(manage_page)
        self._build_engines(manage_page)
        self._build_log(manage_page)

    def _build_env(self, page: Any) -> None:
        ttk = self._ttk
        box, body = self._collapsible(page, "环境信息（当前引擎 / 根目录 / 已安装）", open_state=False)
        box.pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))
        body.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("current", "当前引擎"), ("root", "引擎根目录"), ("installed", "已安装"))):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, self._theme.spacing.sm))
            var = self._tk.StringVar(value="检测中…")
            value_label = ttk.Label(body, textvariable=var, justify="left")
            value_label.grid(row=row, column=1, sticky="we")
            value_label.bind(
                "<Configure>",
                lambda event: event.widget.configure(wraplength=_guiwindow.fit_wraplength(event.width)),
            )
            self._info_vars[key] = var

    def _build_online(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(page, text="在线下载（GitHub release）", padding=self._theme.spacing.sm)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="TAG").grid(
            row=0, column=0, sticky="w", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        entry = ttk.Entry(box, textvariable=self._tag_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, self._theme.spacing.md), pady=self._theme.spacing.xxs)
        entry.bind("<KeyRelease>", self._mark_tag_edited)
        ttk.Label(box, text="变体").grid(
            row=0, column=2, sticky="w", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xxs
        )
        ttk.Combobox(box, textvariable=self._variant_var, values=list(_VARIANTS), state="readonly", width=8).grid(
            row=0, column=3, sticky="w", pady=self._theme.spacing.xxs
        )
        button = ttk.Button(box, text="在线安装", command=self._online_install, style="Accent.TButton")
        button.grid(row=0, column=4, padx=(self._theme.spacing.md, 0), pady=self._theme.spacing.xxs)
        self._buttons.append(button)
        ttk.Label(box, text="cuda 变体自动下载引擎包 + cudart 运行库").grid(row=1, column=1, columnspan=4, sticky="w")

    def _build_offline(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(page, text="离线包（zip）", padding=self._theme.spacing.sm)
        box.pack(fill="x", pady=(self._theme.spacing.sm, 0))
        box.columnconfigure(0, weight=1)

        row = ttk.Frame(box)
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self._offline_var, state="readonly").grid(row=0, column=0, sticky="ew")
        zip_btn = ttk.Button(row, text="选择 zip…", command=self._select_zip)
        zip_btn.grid(row=0, column=1, padx=(self._theme.spacing.sm, 0))
        dir_btn = ttk.Button(row, text="选择目录…", command=self._select_dir)
        dir_btn.grid(row=0, column=2, padx=(self._theme.spacing.sm, 0))

        actions = ttk.Frame(box)
        actions.grid(row=1, column=0, sticky="ew", pady=(self._theme.spacing.sm, 0))
        ttk.Checkbutton(actions, text="深度 CRC 校验（大包较慢）", variable=self._deep_var).pack(side="left")
        install_btn = ttk.Button(actions, text="安装离线包", command=self._offline_install)
        install_btn.pack(side="right")
        check_btn = ttk.Button(actions, text="检查", command=self._check_offline)
        check_btn.pack(side="right", padx=(0, self._theme.spacing.sm))
        self._buttons.extend([zip_btn, dir_btn, install_btn, check_btn])

    def _build_engines(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(page, text="已安装引擎", padding=self._theme.spacing.sm)
        box.pack(fill="both", expand=True)
        body = ttk.Frame(box)
        body.pack(fill="both", expand=True)
        columns = ("tag", "current", "exe")
        self._tree = ttk.Treeview(body, columns=columns, show="headings", height=6)
        for column, title, width, stretch in (
            ("tag", "TAG", 110, False),
            ("current", "当前", 50, False),
            ("exe", "可执行文件", 620, True),
        ):
            self._tree.heading(column, text=title)
            self._tree.column(column, width=width, anchor="w", stretch=stretch)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_actions(self, page: Any) -> None:
        ttk = self._ttk
        actions = ttk.Frame(page)
        actions.pack(side="bottom", fill="x", pady=(self._theme.spacing.sm, 0))
        for text, command in (
            ("刷新", self._refresh_all),
            ("设为默认", self._use_selected),
            ("检查运行", self._check_selected),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="left", padx=(0, self._theme.spacing.sm))
            self._buttons.append(button)
        ttk.Button(actions, text="清空日志", command=self._clear_log).pack(
            side="left", padx=(0, self._theme.spacing.sm)
        )
        self._progress = ttk.Progressbar(actions, mode="determinate", maximum=100, length=200)
        self._progress.pack(side="right")

    def _build_log(self, page: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(page, text="输出日志", padding=self._theme.spacing.sm)
        box.pack(fill="both", expand=True, pady=(self._theme.spacing.sm, 0))
        scrollbar = ttk.Scrollbar(box)
        scrollbar.pack(side="right", fill="y")
        self._log_text = self._tk.Text(box, wrap="word", state="disabled", yscrollcommand=scrollbar.set, height=10)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    def _collapsible(self, parent: Any, title: str, *, open_state: bool = True) -> Any:
        """创建可折叠分区，返回（外层容器, 内容 Frame）。"""
        return _guiwindow.collapsible(
            parent,
            title,
            padding=(self._theme.spacing.sm, self._theme.spacing.xs, 0, 0),
            open_state=open_state,
        )

    # ------------------------------------------------------------------ #
    # 引擎列表与信息
    # ------------------------------------------------------------------ #
    def _refresh_all(self) -> None:
        self._refresh_engines()
        self._refresh_env()

    def _refresh_engines(self) -> None:
        self._engines = llmengine.list_engines()
        for item in self._tree.get_children():
            self._tree.delete(item)
        for info in self._engines:
            self._tree.insert(
                "",
                "end",
                iid=info.tag,
                values=(info.tag, "*" if info.source == "current" else "", str(info.exe)),
            )

    def _refresh_env(self) -> None:
        self._info_vars["root"].set(str(llmenv.engines_root()))
        try:
            current = llmengine.resolve_engine()
        except ServeError as exc:
            self._info_vars["current"].set(f"未找到（{exc}）")
        else:
            self._info_vars["current"].set(f"{current.tag} · {current.exe}")
        self._info_vars["installed"].set(f"{len(self._engines)} 个")

    def _selected_tag(self) -> str | None:
        selection = self._tree.selection()
        if not selection:
            self._messagebox.showinfo("未选择引擎", "请先在列表中选择一个引擎")
            return None
        return selection[0]

    @staticmethod
    def _ensure_current(tag: str) -> None:
        """没有默认引擎时把刚安装的引擎设为默认。"""
        if not any(item.source == "current" for item in llmengine.list_engines()):
            llmengine.use_engine(tag)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _mark_tag_edited(self, _event: object | None = None) -> None:
        self._tag_edited = True

    def _select_zip(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择 llama.cpp 离线包",
            filetypes=[("zip 压缩包", "*.zip"), ("所有文件", "*.*")],
        )
        if path:
            self._offline_var.set(path)

    def _select_dir(self) -> None:
        path = self._filedialog.askdirectory(title="选择包含 zip 离线包或已编译引擎的目录")
        if path:
            self._offline_var.set(path)

    def _offline_source(self) -> Path | None:
        raw = self._offline_var.get()
        if raw == _OFFLINE_PLACEHOLDER:
            self._messagebox.showwarning("缺少离线包", "请选择 zip 文件或包含 zip 的目录")
            return None
        path = Path(raw).expanduser()
        if not path.exists():
            self._messagebox.showwarning("路径不存在", str(path))
            return None
        return path

    def _online_install(self) -> None:
        tag = self._tag_var.get().strip()
        variant = self._variant_var.get()
        if not tag:
            self._messagebox.showwarning("缺少 TAG", "请填写引擎 TAG（如 b12211）")
            return
        if variant not in _VARIANTS:
            self._messagebox.showwarning("变体无效", f"请选择 {', '.join(_VARIANTS)}")
            return
        typed = cast("EngineVariant", variant)

        def job(progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            info = llmengine.install_engine(tag, variant=typed, on_progress=progress)
            self._ensure_current(tag)
            return f"引擎就绪: {info.tag} -> {info.exe}", None

        self._run_task(f"在线安装 {tag}（{variant}）", job)

    def _offline_install(self) -> None:
        source = self._offline_source()
        if source is None:
            return
        tag = self._tag_var.get().strip()
        if not tag:
            self._messagebox.showwarning("缺少 TAG", "请填写引擎 TAG（如 b12211）")
            return
        infer = not self._tag_edited

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            target = tag
            if infer:
                inferred = next((item.tag for item in llmengine.inspect_zips([source]) if item.tag), None)
                if inferred:
                    target = inferred
            if source.is_dir():
                zips = sorted(item for item in source.glob("*.zip") if item.is_file())
                info = (
                    llmengine.install_engine(target, archives=zips)
                    if zips
                    else llmengine.install_engine(target, path=source)
                )
            else:
                info = llmengine.install_engine(target, archives=[source])
            self._ensure_current(target)
            return f"引擎就绪: {info.tag} -> {info.exe}", target

        self._run_task(f"安装离线包 {source.name}", job)

    def _check_offline(self) -> None:
        source = self._offline_source()
        if source is None:
            return
        deep = self._deep_var.get()

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            inspections = llmengine.inspect_zips([source], deep=deep)
            if not inspections:
                return "没有找到 zip 压缩包", None
            lines: list[str] = []
            inferred: str | None = None
            for item in inspections:
                state = "可用" if item.usable else "不可安装"
                lines.append(
                    f"{item.path.name}: {_KIND_LABELS[item.kind]} / {state} / TAG={item.tag or '-'} / "
                    f"变体={item.variant or '-'} / llama-server={item.server or '-'} / {human_size(item.total_size)}"
                )
                lines.extend(f"  · {note}" for note in item.notes)
                lines.extend(f"  ! {problem}" for problem in item.problems)
                if inferred is None and item.tag:
                    inferred = item.tag
            return "\n".join(lines), inferred

        self._run_task("检查离线包", job, notify=False)

    def _use_selected(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            info = llmengine.use_engine(tag)
            return f"默认引擎: {info.tag} -> {info.exe}", None

        self._run_task(f"切换默认引擎 {tag}", job)

    def _check_selected(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            engine = llmengine.resolve_engine(tag)
            check = llmengine.check_engine(engine)
            return f"{check.status} {check.name}: {check.detail}", None

        self._run_task(f"检查引擎 {tag}", job, notify=False)

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _run_task(self, label: str, func: TaskFunc, notify: bool = True) -> None:
        """在后台线程执行任务，通过队列把日志 / 进度 / 结果送回主线程。"""
        if self._running:
            self._log("已有任务正在进行，请等待完成")
            return
        self._running = True
        self._nav.select("manage")
        self._set_buttons(False)
        self._progress.configure(mode="determinate")
        self._progress["value"] = 0
        self._status_var.set(label)
        self._log(f"== {label} ==")
        messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._messages = messages

        def progress(name: str, done: int, total: int | None) -> None:
            messages.put(("progress", (name, done, total)))

        def work() -> None:
            try:
                summary, tag = func(progress)
            except (ServeError, OSError, ValueError) as exc:
                messages.put(("done", (False, str(exc), None, notify)))
            except Exception as exc:  # 后台线程兜底，避免界面失去反馈
                messages.put(("done", (False, f"{type(exc).__name__}: {exc}", None, notify)))
            else:
                messages.put(("done", (True, summary, tag, notify)))

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        self.root.after(100, self._poll)

    def _poll(self) -> None:
        assert self._messages is not None
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "done":
                    self._finish(*payload)
        except queue.Empty:
            pass
        if self._thread is not None and self._thread.is_alive():
            self.root.after(100, self._poll)

    def _apply_progress(self, name: str, done: int, total: int | None) -> None:
        if total:
            if str(self._progress["mode"]) != "determinate":
                self._progress.stop()
                self._progress.configure(mode="determinate")
            self._progress["value"] = min(100.0, done * 100.0 / total)
            self._status_var.set(f"{name}: {human_size(done)} / {human_size(total)}")
        else:
            if str(self._progress["mode"]) != "indeterminate":
                self._progress.configure(mode="indeterminate")
                self._progress.start(12)
            self._status_var.set(f"{name}: {human_size(done)}")

    def _finish(self, ok: bool, message: str, tag: str | None, notify: bool) -> None:
        self._running = False
        self._progress.stop()
        self._progress.configure(mode="determinate")
        self._progress["value"] = 100 if ok else 0
        self._status_var.set("完成" if ok else "失败")
        self._set_buttons(True)
        if tag:
            self._tag_var.set(tag)
        if message:
            self._log(message)
        self._refresh_all()
        if not notify:
            return
        if ok:
            self._messagebox.showinfo("完成", message or "任务已完成")
        else:
            self._messagebox.showerror("失败", message or "任务失败")

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons:
            button.configure(state=state)

    def _close(self) -> None:
        if self._running and not self._messagebox.askyesno("任务进行中", "安装 / 检查任务仍在执行，确实退出？"):
            return
        save_settings(EngineSettings(geometry=self.root.winfo_geometry()), self._settings_path)
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        _guiwindow.append_log(self._log_text, message)

    def _clear_log(self) -> None:
        """清空输出日志区域。"""
        _guiwindow.clear_log(self._log_text)


def run_gui() -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(EngineWindow, "窗口版")


@fcmd.tool("llmenginew", help="打开 llama.cpp 引擎管理窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 llama.cpp 引擎管理窗口。"""
    run_gui()


@fcmd.main("llmenginew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
