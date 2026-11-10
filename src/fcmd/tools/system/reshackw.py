"""reshackw - Windows PE 资源查看与修改（Tk GUI）。

交互参考 ``bitool_gui.reshack``：左侧按资源类型分组展示 PE 文件（exe/dll/sys
等）的资源树，右侧提供文本 / 十六进制 / 图像三种预览，支持导出选中资源、
替换资源（写入前自动备份）以及全部导出。解析与写回复用
:mod:`fcmd.tools.system.reshack`，需安装 ``pip install fcmd[pe]``。

设置（上次文件、最近打开、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/reshackw.json``，下次启动自动恢复。

示例
----
    fcmd reshackw         # 打开 PE 资源编辑器窗口
    reshackw              # 全局命令等价入口
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fcmd
from fcmd.tools import _guiconfig, _guitheme, _guiwindow
from fcmd.tools.system._reshackw_core import (
    FIT_MAX_UPSCALE,
    HEX_PREVIEW_LIMIT,
    MAX_ZOOM,
    MIN_ZOOM,
    TEXT_PREVIEW_LIMIT,
    ZOOM_STEP,
    clamp_zoom,
    copy_image_to_clipboard,
    decode_text,
    default_export_name,
    fit_zoom,
    format_hex_preview,
    group_resources,
    image_to_dib,
    is_text_resource,
    load_preview_image,
    zoom_in,
    zoom_out,
)
from fcmd.tools.system.reshack import (
    RT_BITMAP,
    RT_ICON,
    PEError,
    PEFile,
    PEFileInfo,
    ResourceEntry,
    export_payload,
    format_file_size,
    is_pe_file,
    resource_extension,
)

__all__ = [
    "FIT_MAX_UPSCALE",
    "HEX_PREVIEW_LIMIT",
    "MAX_ZOOM",
    "MIN_ZOOM",
    "TEXT_PREVIEW_LIMIT",
    "ZOOM_STEP",
    "ReshackSettings",
    "add_recent_file",
    "clamp_zoom",
    "config_path",
    "copy_image_to_clipboard",
    "decode_text",
    "default_export_name",
    "fit_zoom",
    "format_hex_preview",
    "group_resources",
    "image_to_dib",
    "is_text_resource",
    "load_preview_image",
    "load_settings",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
    "zoom_in",
    "zoom_out",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PE 资源编辑器窗口版（Tk）：按类型展示资源树，支持文本/十六进制/图像预览（图像可缩放/适应窗口，"
    "右键复制到剪贴板或另存为 PNG/ICO/BMP）、单资源导出（Icon/Bitmap 自动补 ICO/BMP 头）与替换（自动备份）、"
    "全部导出；设置持久化到 ~/.fcmd/ui/reshackw.json，依赖 fcmd[pe]"
)

DEFAULT_GEOMETRY = "1080x720+180+120"
MIN_WIDTH = 900
MIN_HEIGHT = 600
RECENT_LIMIT = 10
_ZOOM_STEP_DELAY_MS = 100


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/reshackw.json``。"""
    return _guiconfig.config_path("reshackw")


@dataclass
class ReshackSettings:
    """PE 资源编辑器的持久化设置。"""

    file_path: str = ""
    recent_files: list[str] = field(default_factory=list)
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def add_recent_file(files: list[str], path: Path, limit: int = RECENT_LIMIT) -> list[str]:
    """把路径插入最近列表头部，去重并截断到 ``limit`` 条。"""
    entry = str(path)
    remaining = [item for item in files if item != entry]
    return [entry, *remaining][:limit]


def load_settings(path: Path | None = None) -> ReshackSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return ReshackSettings()

    settings = ReshackSettings()
    file_path = raw.get("file_path")
    recent = raw.get("recent_files")
    geometry = raw.get("geometry")
    if isinstance(file_path, str):
        settings.file_path = file_path
    if isinstance(recent, list):
        settings.recent_files = [item for item in recent if isinstance(item, str)][:RECENT_LIMIT]
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: ReshackSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "file_path": settings.file_path,
        "recent_files": settings.recent_files,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


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


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class ReshackWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk PE 资源编辑器主窗口。"""

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
        self._pe: PEFile | None = None
        self._current_file: Path | None = None
        self._resources: dict[str, ResourceEntry] = {}
        self._preview_photo: Any = None
        self._preview_image: Any = None
        self._shown_resource: ResourceEntry | None = None
        self._zoom = 1.0
        self._fit_mode = False
        self._resize_job: str | None = None

        self.root, self._theme = _guiwindow.create_root(
            title=f"PE 资源编辑器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._status_var = tk.StringVar(value="就绪")
        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_recent_menu()
        if self._settings.file_path:
            path = Path(self._settings.file_path).expanduser()
            if is_pe_file(path):
                self.root.after(50, lambda: self._start_load(path))

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        tk = self._tk
        ttk = self._ttk
        palette = self._theme.palette
        self._build_menu()

        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.md, self._theme.spacing.xs))
        self._open_button = ttk.Button(toolbar, text="打开…", command=self._choose_file, style="Accent.TButton")
        self._open_button.pack(side="left")
        self._export_button = ttk.Button(toolbar, text="导出…", command=self._export_selected)
        self._export_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._replace_button = ttk.Button(toolbar, text="替换…", command=self._replace_selected, style="Danger.TButton")
        self._replace_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._export_all_button = ttk.Button(toolbar, text="全部导出…", command=self._export_all)
        self._export_all_button.pack(side="left", padx=(self._theme.spacing.sm, 0))

        # PanedWindow 保留经典控件（ttk 无对应分隔容器），仅按色板上色
        paned = tk.PanedWindow(
            self.root,
            orient="horizontal",
            sashwidth=6,
            sashrelief="flat",
            borderwidth=0,
            background=palette.surface,
        )
        paned.pack(fill="both", expand=True, padx=self._theme.spacing.md, pady=self._theme.spacing.xs)

        tree_frame = ttk.LabelFrame(paned, text="资源", padding=self._theme.spacing.sm)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("type", "size", "language"),
            show="tree headings",
            selectmode="browse",
        )
        self._tree.heading("#0", text="名称")
        self._tree.column("#0", width=180, anchor="w")
        self._tree.heading("type", text="类型")
        self._tree.column("type", width=110, anchor="w")
        self._tree.heading("size", text="大小")
        self._tree.column("size", width=80, anchor="e")
        self._tree.heading("language", text="语言")
        self._tree.column("language", width=60, anchor="e")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self._tree.pack(side="left", fill="both", expand=True)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(tree_frame, minsize=320)

        preview_frame = ttk.LabelFrame(paned, text="预览", padding=self._theme.spacing.sm)
        self._notebook = ttk.Notebook(preview_frame)
        text_container = ttk.Frame(self._notebook)
        self._text_preview = tk.Text(text_container, wrap="word", state="disabled", font=_guitheme.MONO_FONT)
        self._text_preview.pack(fill="both", expand=True)
        hex_container = ttk.Frame(self._notebook)
        self._hex_preview = tk.Text(hex_container, wrap="none", state="disabled", font=_guitheme.MONO_FONT)
        hex_v_scroll = ttk.Scrollbar(hex_container, orient="vertical", command=self._hex_preview.yview)
        hex_h_scroll = ttk.Scrollbar(hex_container, orient="horizontal", command=self._hex_preview.xview)
        self._hex_preview.configure(yscrollcommand=hex_v_scroll.set, xscrollcommand=hex_h_scroll.set)
        hex_v_scroll.pack(side="right", fill="y")
        hex_h_scroll.pack(side="bottom", fill="x")
        self._hex_preview.pack(side="left", fill="both", expand=True)
        image_container = ttk.Frame(self._notebook)
        zoom_bar = ttk.Frame(image_container)
        zoom_bar.pack(fill="x", pady=(0, self._theme.spacing.xs))
        ttk.Label(zoom_bar, text="缩放").pack(side="left")
        self._zoom_out_button = ttk.Button(zoom_bar, text="－", width=3, command=self._zoom_out_image)
        self._zoom_out_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._zoom_label = ttk.Label(zoom_bar, text="100%", width=7)
        self._zoom_label.pack(side="left")
        self._zoom_in_button = ttk.Button(zoom_bar, text="＋", width=3, command=self._zoom_in_image)
        self._zoom_in_button.pack(side="left")
        self._zoom_reset_button = ttk.Button(zoom_bar, text="1:1", command=self._reset_zoom_image)
        self._zoom_reset_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._zoom_fit_button = ttk.Button(zoom_bar, text="适应", command=self._fit_image)
        self._zoom_fit_button.pack(side="left", padx=(self._theme.spacing.sm, 0))

        # 图像预览 Canvas 不做主题化，只把浅灰底换成深色底，避免影响图像本身判读
        self._image_canvas = tk.Canvas(image_container, background=palette.log_bg, highlightthickness=0)
        image_h_scroll = ttk.Scrollbar(image_container, orient="horizontal", command=self._image_canvas.xview)
        image_v_scroll = ttk.Scrollbar(image_container, orient="vertical", command=self._image_canvas.yview)
        self._image_canvas.configure(xscrollcommand=image_h_scroll.set, yscrollcommand=image_v_scroll.set)
        image_v_scroll.pack(side="right", fill="y")
        image_h_scroll.pack(side="bottom", fill="x")
        self._image_canvas.pack(side="left", fill="both", expand=True)
        self._image_canvas.bind("<Configure>", self._on_image_canvas_resize)
        self._image_canvas.bind("<Control-MouseWheel>", self._on_image_wheel)
        self._image_canvas.bind("<MouseWheel>", self._on_image_scroll)
        self._image_canvas.bind("<Control-Button-4>", lambda _event: self._zoom_in_image())
        self._image_canvas.bind("<Control-Button-5>", lambda _event: self._zoom_out_image())
        self._image_menu = tk.Menu(self.root, tearoff=False)
        self._image_menu.add_command(label="复制图像", command=self._copy_preview_image)
        self._image_menu.add_command(label="保存图像为…", command=self._save_preview_image)
        self._image_canvas.bind("<Button-3>", self._show_image_menu)
        self._notebook.add(text_container, text="文本")
        self._notebook.add(hex_container, text="十六进制")
        self._notebook.add(image_container, text="图像")
        self._notebook.pack(fill="both", expand=True)
        paned.add(preview_frame, minsize=420)

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=self._theme.spacing.sm)
        log_frame.pack(fill="x", padx=self._theme.spacing.md, pady=(self._theme.spacing.xs, self._theme.spacing.xs))
        self._log_text = _guiwindow.create_log(log_frame, height=5)

        status = ttk.Frame(self.root)
        status.pack(fill="x", padx=self._theme.spacing.md, pady=(0, self._theme.spacing.sm))
        self._status_label = ttk.Label(status, textvariable=self._status_var, anchor="w")
        self._status_label.pack(side="left", fill="x", expand=True)

        self._set_busy(False)
        self._append_log("打开 PE 文件（exe/dll/sys）后，在左侧选择资源查看预览。")
        self.root.bind("<Control-plus>", lambda _event: self._zoom_in_image())
        self.root.bind("<Control-equal>", lambda _event: self._zoom_in_image())
        self.root.bind("<Control-minus>", lambda _event: self._zoom_out_image())
        self.root.bind("<Control-0>", lambda _event: self._reset_zoom_image())

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开…", command=self._choose_file)
        self._recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近打开", menu=self._recent_menu)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)

        resource_menu = tk.Menu(menubar, tearoff=False)
        resource_menu.add_command(label="导出…", command=self._export_selected)
        resource_menu.add_command(label="替换…", command=self._replace_selected)
        resource_menu.add_command(label="全部导出…", command=self._export_all)
        resource_menu.add_separator()
        resource_menu.add_command(label="放大预览", accelerator="Ctrl+=", command=self._zoom_in_image)
        resource_menu.add_command(label="缩小预览", accelerator="Ctrl+-", command=self._zoom_out_image)
        resource_menu.add_command(label="实际大小", accelerator="Ctrl+0", command=self._reset_zoom_image)
        resource_menu.add_command(label="适应窗口", command=self._fit_image)
        resource_menu.add_separator()
        resource_menu.add_command(label="属性", command=self._show_properties)
        menubar.add_cascade(label="资源", menu=resource_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    # ------------------------------------------------------------------ #
    # 基础交互
    # ------------------------------------------------------------------ #
    def _append_log(self, message: str) -> None:
        """向只读日志区追加带时间戳的一行。"""
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _set_busy(self, busy: bool) -> None:
        """任务执行期间禁用工具栏按钮，避免重复提交。"""
        state = "disabled" if busy else "normal"
        for button in (
            self._open_button,
            self._export_button,
            self._replace_button,
            self._export_all_button,
        ):
            button.configure(state=state)
        if busy:
            self._status_label.configure(foreground=self._theme.palette.text)

    def _refresh_recent_menu(self) -> None:
        """重建「最近打开」菜单。"""
        self._recent_menu.delete(0, "end")
        if not self._settings.recent_files:
            self._recent_menu.add_command(label="（空）", state="disabled")
            return
        for item in self._settings.recent_files:
            self._recent_menu.add_command(label=item, command=lambda value=item: self._open_recent(value))

    def _open_recent(self, value: str) -> None:
        path = Path(value).expanduser()
        if not is_pe_file(path):
            self._messagebox.showerror("无法打开", f"文件不存在或不是有效的 PE 文件:\n{value}")
            return
        self._start_load(path)

    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于",
            f"PE 资源编辑器 v{__version__}\n\n"
            "查看 / 导出 / 替换 Windows PE 资源\n"
            "替换前自动生成 .backup 备份\n"
            "依赖 pefile（pip install fcmd[pe]）",
        )

    def _show_properties(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            self._messagebox.showinfo("未选择资源", "请先在左侧选择资源。")
            return
        self._messagebox.showinfo(
            "资源属性",
            f"类型: {resource.type_name} ({resource.type_id})\n"
            f"名称: {resource.name_id}\n"
            f"语言: {resource.language_id}\n"
            f"大小: {format_file_size(resource.size)}\n"
            f"RVA: 0x{resource.offset:X}\n\n"
            f"数据前 16 字节:\n{resource.data[:16].hex(' ')}",
        )

    def _selected_resource(self) -> ResourceEntry | None:
        selection = self._tree.selection()
        if not selection:
            return None
        return self._resources.get(selection[0])

    # ------------------------------------------------------------------ #
    # 打开文件（后台线程 + 队列回传）
    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        filename = self._filedialog.askopenfilename(
            title="打开 PE 文件",
            filetypes=[
                ("PE 文件", "*.exe *.dll *.sys *.ocx *.scr *.cpl *.ax *.acm *.drv"),
                ("所有文件", "*.*"),
            ],
        )
        if filename:
            self._start_load(Path(filename))

    def _start_load(self, path: Path) -> None:
        if self._is_busy():
            return
        if not is_pe_file(path):
            self._messagebox.showerror("无法打开", f"不是有效的 PE 文件:\n{path}")
            return
        self._set_busy(True)
        self._status_var.set(f"正在加载 {path.name}…")
        self._append_log(f"打开 {path}")
        self._start_worker(self._load_worker, (path,))

    def _load_worker(self, path: Path) -> None:
        pe = PEFile()
        try:
            pe.open(path)
            info = pe.file_info()
            resources = pe.resources()
        except PEError as exc:
            pe.close()
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("loaded", pe, path, info, resources))

    # ------------------------------------------------------------------ #
    # 导出 / 替换（后台线程 + 队列回传）
    # ------------------------------------------------------------------ #
    def _export_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            self._messagebox.showinfo("未选择资源", "请先在左侧选择资源。")
            return
        extension = resource_extension(resource.type_id)
        filename = self._filedialog.asksaveasfilename(
            title="导出资源",
            initialfile=default_export_name(resource),
            defaultextension=extension,
            filetypes=[(f"资源文件 (*{extension})", f"*{extension}"), ("所有文件", "*.*")],
        )
        if not filename:
            return
        output = Path(filename).expanduser()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(export_payload(resource))
        except OSError as exc:
            self._messagebox.showerror("导出失败", str(exc))
            return
        self._append_log(f"已导出 {resource.type_name} #{resource.name_id} -> {output}")
        self._status_var.set(f"已导出 {output.name}")

    def _replace_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None or self._pe is None:
            self._messagebox.showinfo("未选择资源", "请先在左侧选择资源。")
            return
        filename = self._filedialog.askopenfilename(title="选择新的资源数据")
        if not filename:
            return
        try:
            new_data = Path(filename).read_bytes()
        except OSError as exc:
            self._messagebox.showerror("读取失败", str(exc))
            return
        confirmed = self._messagebox.askyesno(
            "确认替换",
            f"类型: {resource.type_name} ({resource.type_id})\n"
            f"名称: {resource.name_id}，语言: {resource.language_id}\n"
            f"原大小: {format_file_size(resource.size)}，新大小: {format_file_size(len(new_data))}\n\n"
            "将自动生成 .backup 备份后写入，继续？",
        )
        if not confirmed:
            return
        if self._is_busy():
            return
        self._set_busy(True)
        self._status_var.set("正在替换资源…")
        self._start_worker(
            self._replace_worker,
            (resource.type_id, resource.name_id, new_data, resource.language_id),
        )

    def _replace_worker(
        self,
        type_id: int,
        name_id: int,
        new_data: bytes,
        language_id: int,
    ) -> None:
        if self._pe is None:
            self._queue.put(("error", "尚未加载 PE 文件"))
            return
        try:
            old_size, new_size, backup = self._pe.replace_resource(type_id, name_id, new_data, language_id)
            info = self._pe.file_info()
            resources = self._pe.resources()
        except PEError as exc:
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("replaced", old_size, new_size, backup, info, resources))

    def _export_all(self) -> None:
        if self._pe is None or self._current_file is None:
            self._messagebox.showinfo("未打开文件", "请先打开 PE 文件。")
            return
        if self._is_busy():
            return
        directory = self._filedialog.askdirectory(title="选择全部导出目录")
        if not directory:
            return
        target = Path(directory).expanduser() / f"{self._current_file.stem}_resources"
        self._set_busy(True)
        self._status_var.set("正在导出全部资源…")
        self._start_worker(self._export_all_worker, (target,))

    def _export_all_worker(self, target: Path) -> None:
        if self._pe is None:
            self._queue.put(("error", "尚未加载 PE 文件"))
            return
        try:
            exported, total, directory = self._pe.export_all(target)
        except PEError as exc:
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("exported", directory, exported, total))

    # ------------------------------------------------------------------ #
    # 线程与队列
    # ------------------------------------------------------------------ #
    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _start_worker(self, target: Any, args: tuple[Any, ...]) -> None:
        self._worker = _guiwindow.start_worker(self.root, target, args, self._poll_queue)

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新界面状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "loaded":
                self._apply_loaded(event)
            elif kind == "replaced":
                self._apply_replaced(event)
            elif kind == "exported":
                self._apply_exported(event)
            elif kind == "error":
                self._set_busy(False)
                self._worker = None
                self._status_var.set("操作失败")
                self._status_label.configure(foreground=self._theme.palette.danger)
                self._append_log(f"错误：{event[1]}")
                self._messagebox.showerror("操作失败", event[1])
                return

        if self._is_busy():
            self.root.after(80, self._poll_queue)
        else:
            self._worker = None

    def _apply_loaded(self, event: tuple[Any, ...]) -> None:
        _, pe, path, info, resources = event
        if self._pe is not None:
            self._pe.close()
        self._pe = pe
        self._current_file = path
        self._populate_tree(resources)
        self._clear_preview()
        self._update_file_info(info)
        self._settings.file_path = str(path)
        self._settings.recent_files = add_recent_file(self._settings.recent_files, path)
        save_settings(self._settings, self._settings_path)
        self._refresh_recent_menu()
        self._append_log(f"已加载 {len(resources)} 个资源")
        self._status_var.set(f"{path.name}：{len(resources)} 个资源")
        self._worker = None
        self._set_busy(False)

    def _apply_replaced(self, event: tuple[Any, ...]) -> None:
        _, old_size, new_size, backup, info, resources = event
        self._populate_tree(resources)
        self._update_file_info(info)
        self._append_log(f"已替换资源：{format_file_size(old_size)} -> {format_file_size(new_size)}，备份 {backup}")
        self._status_var.set("资源替换完成")
        self._worker = None
        self._set_busy(False)
        self._messagebox.showinfo("替换完成", f"资源已写入，原文件备份在:\n{backup}")

    def _apply_exported(self, event: tuple[Any, ...]) -> None:
        _, directory, exported, total = event
        self._append_log(f"已导出 {exported}/{total} 个资源 -> {directory}")
        self._status_var.set(f"已导出 {exported}/{total} 个资源")
        self._worker = None
        self._set_busy(False)
        self._messagebox.showinfo("导出完成", f"已导出 {exported}/{total} 个资源到:\n{directory}")

    # ------------------------------------------------------------------ #
    # 资源树与预览
    # ------------------------------------------------------------------ #
    def _populate_tree(self, resources: list[ResourceEntry]) -> None:
        self._tree.delete(*self._tree.get_children())
        self._resources = {}
        for type_id, type_name, items in group_resources(resources):
            group_id = f"type:{type_id}"
            self._tree.insert(
                "", "end", iid=group_id, text=f"{type_name} ({len(items)})", open=True, values=("", "", "")
            )
            for resource in items:
                item_id = f"res:{type_id}:{resource.name_id}:{resource.language_id}"
                self._tree.insert(
                    group_id,
                    "end",
                    iid=item_id,
                    text=str(resource.name_id),
                    values=(
                        resource.type_name,
                        format_file_size(resource.size),
                        resource.language_id,
                    ),
                )
                self._resources[item_id] = resource

    def _update_file_info(self, info: PEFileInfo) -> None:
        self.root.title(f"PE 资源编辑器 v{__version__} - {info.path.name}")
        bits = "64 位" if info.is_64bit else "32 位"
        kind = "DLL" if info.is_dll else "EXE"
        self._append_log(f"{kind} / {info.machine} / {bits} / {info.subsystem}")

    def _clear_preview(self) -> None:
        self._set_text(self._text_preview, "")
        self._set_text(self._hex_preview, "")
        self._preview_photo = None
        self._preview_image = None
        self._shown_resource = None
        self._zoom = 1.0
        self._fit_mode = False
        self._update_zoom_label()
        self._show_image_message("选择资源后显示预览")

    def _on_select(self, _event: Any = None) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        self._show_preview(resource)

    def _show_preview(self, resource: ResourceEntry) -> None:
        self._shown_resource = resource
        if is_text_resource(resource.type_id):
            data = resource.data[:TEXT_PREVIEW_LIMIT]
            text = decode_text(data)
            if len(resource.data) > TEXT_PREVIEW_LIMIT:
                text += f"\n\n... 仅显示前 {TEXT_PREVIEW_LIMIT} 字节，共 {len(resource.data)} 字节"
        else:
            text = f"(二进制资源，共 {format_file_size(resource.size)}，请查看「十六进制」或「图像」预览)"
        self._set_text(self._text_preview, text)
        self._set_text(self._hex_preview, format_hex_preview(resource.data))
        self._show_image(resource)
        self._status_var.set(f"{resource.type_name} #{resource.name_id}（{format_file_size(resource.size)}）")

    def _show_image(self, resource: ResourceEntry) -> None:
        self._preview_photo = None
        self._preview_image = None
        self._zoom = 1.0
        self._fit_mode = False
        self._update_zoom_label()
        if resource.type_id not in (RT_BITMAP, RT_ICON):
            self._show_image_message("此资源类型不支持图像预览")
            return
        image = load_preview_image(resource.data, resource.type_id)
        if image is None:
            self._show_image_message("无法解码图像（或未安装 Pillow）")
            return
        self._preview_image = image
        self._fit_image()

    def _show_image_menu(self, event: Any) -> str:
        """在图像画布上弹出右键菜单；无图像时置灰。"""
        state = "normal" if self._preview_image is not None else "disabled"
        self._image_menu.entryconfigure(0, state=state)
        self._image_menu.entryconfigure(1, state=state)
        try:
            self._image_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._image_menu.grab_release()
        return "break"

    def _copy_preview_image(self) -> None:
        """把当前预览图像复制到 Windows 剪贴板。"""
        if self._preview_image is None:
            return
        if copy_image_to_clipboard(self._preview_image):
            self._append_log("已复制预览图像到剪贴板")
            self._status_var.set("图像已复制到剪贴板")
        else:
            self._messagebox.showwarning("复制失败", "无法写入剪贴板（需要 Windows 并安装 Pillow）。")

    def _preview_default_name(self) -> str:
        """预览图像的默认保存文件名（PNG）。"""
        if self._shown_resource is None:
            return "preview.png"
        return f"{Path(default_export_name(self._shown_resource)).stem}.png"

    def _save_preview_image(self) -> None:
        """把当前预览图像另存为 PNG / ICO / BMP。"""
        if self._preview_image is None:
            return
        filename = self._filedialog.asksaveasfilename(
            title="保存图像",
            initialfile=self._preview_default_name(),
            defaultextension=".png",
            filetypes=[("PNG 图像", "*.png"), ("ICO 图标", "*.ico"), ("BMP 图像", "*.bmp")],
        )
        if not filename:
            return
        output = Path(filename).expanduser()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            self._preview_image.save(output)
        except (OSError, ValueError) as exc:
            self._messagebox.showerror("保存失败", str(exc))
            return
        self._append_log(f"已保存预览图像 -> {output}")
        self._status_var.set(f"已保存 {output.name}")

    def _show_image_message(self, message: str) -> None:
        """在图像画布中央显示提示文字（无图像时）。"""
        self._image_canvas.delete("all")
        self._image_canvas.configure(scrollregion=(0, 0, 0, 0))
        width = max(self._image_canvas.winfo_width() - 24, 160)
        self._image_canvas.create_text(
            12,
            12,
            anchor="nw",
            text=message,
            fill=self._theme.palette.muted,
            width=width,
        )

    def _render_preview_image(self) -> None:
        """按当前倍率重绘图像预览（放大用邻近采样，缩小用 LANCZOS）。"""
        if self._preview_image is None:
            return
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self._show_image_message("未安装 Pillow，无法显示图像")
            return
        image = self._preview_image
        width = max(1, round(image.width * self._zoom))
        height = max(1, round(image.height * self._zoom))
        resample = Image.Resampling.NEAREST if self._zoom >= 1 else Image.Resampling.LANCZOS
        try:
            resized = image if (width, height) == image.size else image.resize((width, height), resample)
            photo = ImageTk.PhotoImage(resized)
        except Exception as exc:  # 无显示环境 / 图像模式不支持
            self._show_image_message(f"图像预览失败：{exc}")
            return
        self._preview_photo = photo
        self._image_canvas.delete("all")
        self._image_canvas.create_image(0, 0, anchor="nw", image=photo)
        self._image_canvas.configure(scrollregion=(0, 0, width, height))
        self._update_zoom_label()

    def _zoom_in_image(self) -> None:
        self._apply_zoom(zoom_in(self._zoom))

    def _zoom_out_image(self) -> None:
        self._apply_zoom(zoom_out(self._zoom))

    def _reset_zoom_image(self) -> None:
        self._apply_zoom(1.0)

    def _apply_zoom(self, value: float) -> None:
        if self._preview_image is None:
            return
        self._zoom = clamp_zoom(value)
        self._fit_mode = False
        self._render_preview_image()

    def _fit_image(self) -> None:
        """按画布可用区域计算并应用“适应窗口”倍率。"""
        if self._preview_image is None:
            return
        self._fit_mode = True
        view_width = self._image_canvas.winfo_width()
        view_height = self._image_canvas.winfo_height()
        if view_width > 1 and view_height > 1:
            self._zoom = fit_zoom(
                self._preview_image.width,
                self._preview_image.height,
                view_width,
                view_height,
            )
        else:
            self._zoom = 1.0
        self._render_preview_image()

    def _update_zoom_label(self) -> None:
        self._zoom_label.configure(text=f"{round(self._zoom * 100)}%")

    def _on_image_wheel(self, event: Any) -> str:
        """Ctrl + 滚轮调整图像缩放。"""
        delta = getattr(event, "delta", 0)
        if delta > 0:
            self._zoom_in_image()
        elif delta < 0:
            self._zoom_out_image()
        return "break"

    def _on_image_scroll(self, event: Any) -> str:
        """普通滚轮滚动图像；Ctrl 组合交给缩放绑定处理。"""
        if getattr(event, "state", 0) & 0x0004:
            return "break"
        delta = getattr(event, "delta", 0)
        if delta:
            self._image_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        return "break"

    def _on_image_canvas_resize(self, _event: Any = None) -> None:
        """画布尺寸变化时，在适应模式下延迟重算倍率，避免连续重绘。"""
        if not self._fit_mode or self._preview_image is None:
            return
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(_ZOOM_STEP_DELAY_MS, self._apply_fit)

    def _apply_fit(self) -> None:
        self._resize_job = None
        self._fit_image()

    @staticmethod
    def _set_text(widget: Any, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置、释放 PE 句柄并退出窗口。"""
        save_settings(
            ReshackSettings(
                file_path=self._settings.file_path,
                recent_files=self._settings.recent_files,
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        if self._pe is not None:
            self._pe.close()
            self._pe = None
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
            self._resize_job = None
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: ReshackWindow(settings_path), "PE 资源编辑器")


@fcmd.tool("reshackw", help="打开 PE 资源编辑器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PE 资源编辑器窗口。"""
    run_gui()


@fcmd.main("reshackw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
