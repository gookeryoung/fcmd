"""pevieww - PE 文件查看器（Tk GUI）。

左侧按「DOS 头 / NT 头 / 可选头 / 数据目录 / 节表 / 导入 / 导出 / 资源 /
重定位 / 调试 / 签名 / Overlay」分类展示结构，右侧提供详情、概览与十六进制
三种视图；选中节点后十六进制视图自动跳到对应文件偏移。

与 :mod:`fcmd.apps.reshack.reshackw` 一样复用 :mod:`fcmd.gui` 的窗口骨架、
主题与设置持久化，设置保存在 ``${FCMD_HOME:-~/.fcmd}/ui/pevieww.json``。

示例
----
    fcmd pevieww
    fcmd pevieww --file app.exe
    pevieww --file app.exe
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import fcmd
from fcmd.apps.peview import _edit, _report, _view
from fcmd.apps.peview._pe import PEImage, load_pe
from fcmd.apps.peview._reader import PEError
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "DEFAULT_GEOMETRY",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "PeviewSettings",
    "add_recent_file",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "run_gui",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PE 查看器窗口版（Tk）：按分类浏览 DOS/NT/可选头、数据目录、节表、导入导出、资源、"
    "重定位、调试与签名，详情与十六进制联动；设置持久化到 ~/.fcmd/ui/pevieww.json"
)

DEFAULT_GEOMETRY = "1180x760+160+100"
MIN_WIDTH = 960
MIN_HEIGHT = 620
RECENT_LIMIT = 10


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/pevieww.json``。"""
    return _guiconfig.config_path("pevieww")


@dataclass
class PeviewSettings:
    """PE 查看器的持久化设置。"""

    file_path: str = ""
    recent_files: list[str] = field(default_factory=list)
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def add_recent_file(files: list[str], path: Path, limit: int = RECENT_LIMIT) -> list[str]:
    """把路径插入最近列表头部，去重并截断到 ``limit`` 条。"""
    entry = str(path)
    remaining = [item for item in files if item != entry]
    return [entry, *remaining][:limit]


def load_settings(path: Path | None = None) -> PeviewSettings:
    """读取设置文件；缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PeviewSettings()
    settings = PeviewSettings()
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


def save_settings(settings: PeviewSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "file_path": settings.file_path,
        "recent_files": settings.recent_files,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸。"""
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
class PeviewWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk PE 查看器主窗口。"""

    def __init__(self, settings_path: Path | None = None, file: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._pe: PEImage | None = None
        self._current_file: Path | None = None
        self._hex_offset = 0

        self.root, self._theme = _guiwindow.create_root(
            title=f"PE 查看器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )
        self._status_var = tk.StringVar(value="就绪：打开一个 PE 文件开始")
        self._offset_var = tk.StringVar(value="0x0")
        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._refresh_recent_menu()

        initial = file or (Path(self._settings.file_path).expanduser() if self._settings.file_path else None)
        if initial is not None and initial.is_file():
            self.root.after(60, lambda: self._load_file(initial))

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
        self._reload_button = ttk.Button(toolbar, text="重新加载", command=self._reload)
        self._reload_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._edit_button = ttk.Button(toolbar, text="编辑字段…", command=self._open_editor)
        self._edit_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        ttk.Label(toolbar, text="写前自动备份，写后自校验").pack(side="right")

        paned = tk.PanedWindow(
            self.root,
            orient="horizontal",
            sashwidth=6,
            sashrelief="flat",
            borderwidth=0,
            background=palette.surface,
        )
        paned.pack(fill="both", expand=True, padx=self._theme.spacing.md, pady=self._theme.spacing.xs)

        tree_frame = ttk.LabelFrame(paned, text="结构", padding=self._theme.spacing.sm)
        self._tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse")
        self._tree.column("#0", width=300, anchor="w")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self._tree.pack(side="left", fill="both", expand=True)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        paned.add(tree_frame, minsize=320)

        right = ttk.Notebook(paned)
        self._detail_text = self._make_text_tab(right, "详情", wrap="word", both_scroll=True)
        self._summary_text = self._make_text_tab(right, "概览", wrap="word", both_scroll=True)
        hex_tab = ttk.Frame(right)
        hex_bar = ttk.Frame(hex_tab)
        hex_bar.pack(fill="x", pady=(0, self._theme.spacing.xs))
        ttk.Label(hex_bar, text="偏移").pack(side="left")
        self._offset_entry = ttk.Entry(hex_bar, textvariable=self._offset_var, width=14)
        self._offset_entry.pack(side="left", padx=(self._theme.spacing.xs, 0))
        self._offset_entry.bind("<Return>", lambda _event: self._jump())
        self._jump_button = ttk.Button(hex_bar, text="跳转", command=self._jump)
        self._jump_button.pack(side="left", padx=(self._theme.spacing.sm, 0))
        self._hex_text = self._make_text(hex_tab, wrap="none", font=_guitheme.MONO_FONT)
        right.add(hex_tab, text="十六进制")
        paned.add(right, minsize=460)

        self._status = ttk.Label(self.root, textvariable=self._status_var, anchor="w")
        self._status.pack(fill="x", padx=self._theme.spacing.md, pady=(0, self._theme.spacing.sm))

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开…", command=self._choose_file)
        self._recent_menu = tk.Menu(file_menu, tearoff=False)
        file_menu.add_cascade(label="最近打开", menu=self._recent_menu)
        file_menu.add_command(label="重新加载", command=self._reload)
        file_menu.add_command(label="编辑字段…", command=self._open_editor)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    def _make_text(
        self,
        parent: Any,
        *,
        wrap: Literal["none", "char", "word"],
        font: Any = None,
        both_scroll: bool = False,
    ) -> Any:
        """创建只读文本区，``both_scroll`` 时同时提供水平与垂直滚动条。"""
        tk = self._tk
        ttk = self._ttk
        frame = ttk.Frame(parent)
        text = (
            tk.Text(frame, wrap=wrap, state="disabled", font=font)
            if font
            else tk.Text(frame, wrap=wrap, state="disabled")
        )
        v_scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=v_scroll.set)
        v_scroll.pack(side="right", fill="y")
        if both_scroll:
            h_scroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
            text.configure(xscrollcommand=h_scroll.set)
            h_scroll.pack(side="bottom", fill="x")
        text.pack(side="left", fill="both", expand=True)
        frame.pack(fill="both", expand=True)
        return text

    def _make_text_tab(
        self, notebook: Any, title: str, *, wrap: Literal["none", "char", "word"], both_scroll: bool
    ) -> Any:
        """创建页签并把只读文本区放进去。"""
        tab = self._ttk.Frame(notebook)
        text = self._make_text(tab, wrap=wrap, both_scroll=both_scroll)
        notebook.add(tab, text=title)
        return text

    # ------------------------------------------------------------------ #
    # 加载与展示
    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        chosen = self._filedialog.askopenfilename(
            title="选择 PE 文件",
            filetypes=[
                ("PE 文件", "*.exe *.dll *.sys *.ocx *.cpl *.scr *.efi"),
                ("所有文件", "*.*"),
            ],
        )
        if chosen:
            self._load_file(Path(chosen))

    def _reload(self) -> None:
        if self._current_file is not None:
            self._load_file(self._current_file)
        else:
            self._choose_file()

    def _open_editor(self) -> None:
        if self._pe is None or self._current_file is None:
            self._messagebox.showinfo("尚未打开文件", "请先打开一个 PE 文件再编辑。", parent=self.root)
            return
        EditDialog(self, self._pe, self._current_file)

    def _load_file(self, path: Path) -> None:
        try:
            pe = load_pe(path)
        except PEError as exc:
            self._messagebox.showerror("无法打开 PE 文件", str(exc), parent=self.root)
            self._status_var.set(f"打开失败：{exc}")
            return
        self._pe = pe
        self._current_file = path
        self._settings.file_path = str(path)
        self._settings.recent_files = add_recent_file(self._settings.recent_files, path)
        self._refresh_recent_menu()
        self.root.title(f"PE 查看器 v{__version__} - {path.name}")
        self._populate_tree()
        self._set_text(self._summary_text, _report.format_summary(pe))
        self._status_var.set(
            f"{path}  |  {_report.format_size(pe.size)}  |  {len(pe.sections)} 节  |  "
            f"{len(pe.imports)} 个导入库  |  {'有签名' if pe.signature.present else '无签名'}"
        )

    def _populate_tree(self) -> None:
        self._tree.delete(*self._tree.get_children(""))
        if self._pe is None:
            return
        for row in _view.build_tree(self._pe):
            self._tree.insert(row.parent or "", "end", iid=row.node_id, text=row.text)
        for category in self._tree.get_children(""):
            if self._tree.get_children(category):
                self._tree.item(category, open=True)
        self._tree.selection_set("dos")
        self._tree.focus("dos")
        self._on_select()

    def _on_select(self, _event: Any = None) -> None:
        if self._pe is None:
            return
        selection = self._tree.selection()
        if not selection:
            return
        node_id = selection[0]
        rows = _view.details_for(self._pe, node_id)
        body = "\n".join(f"{label}: {value}" for label, value in rows) if rows else "(该节点没有详情)"
        self._set_text(self._detail_text, body)
        offset = _view.node_offset(self._pe, node_id)
        if offset is not None:
            self._hex_offset = offset
            self._offset_var.set(f"0x{offset:X}")
        self._render_hex()

    def _render_hex(self) -> None:
        if self._pe is None:
            return
        self._set_text(self._hex_text, _view.hex_view(self._pe, self._hex_offset))

    def _jump(self) -> None:
        if self._pe is None:
            return
        text = self._offset_var.get().strip()
        try:
            offset = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
        except ValueError:
            self._messagebox.showerror("偏移无效", f"请输入十进制或 0x 开头的十六进制偏移：{text}", parent=self.root)
            return
        if offset < 0 or offset >= self._pe.size:
            self._messagebox.showerror(
                "偏移超出范围",
                f"偏移 0x{offset:X} 不在 0 - 0x{self._pe.size:X} 之间",
                parent=self.root,
            )
            return
        self._hex_offset = offset
        self._offset_var.set(f"0x{offset:X}")
        self._render_hex()

    def _set_text(self, widget: Any, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _refresh_recent_menu(self) -> None:
        self._recent_menu.delete(0, "end")
        if not self._settings.recent_files:
            self._recent_menu.add_command(label="（无）", state="disabled")
            return
        for item in self._settings.recent_files:
            path = Path(item)
            self._recent_menu.add_command(label=path.name or item, command=lambda p=path: self._load_file(p))

    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于 PE 查看器",
            f"pevieww v{__version__}\n\n"
            "只读浏览 PE 结构与十六进制，解析不依赖 pefile。\n"
            "编辑功能写入前会自动备份，并在写入后重新解析校验。",
            parent=self.root,
        )

    def close(self) -> None:
        try:
            self._settings.geometry = self.root.winfo_geometry()
            save_settings(self._settings, self._settings_path)
        finally:
            self.root.destroy()


# ---------------------------------------------------------------------- #
# 编辑对话框
# ---------------------------------------------------------------------- #
class EditDialog:  # pragma: no cover - Tk 对话框不纳入单元覆盖（CI 无显示环境）
    """字段编辑对话框：预演 / 写入，风险与备份说明常驻可见。"""

    def __init__(self, window: PeviewWindow, pe: PEImage, path: Path) -> None:
        tk = window._tk
        ttk = window._ttk
        self._window = window
        self._pe = pe
        self._path = path
        self._values: dict[str, Any] = {}
        self._original = {name: field.value for name, field in pe.header_fields.items()}

        self.top = tk.Toplevel(window.root)
        self.top.title(f"编辑字段 - {path.name}")
        self.top.transient(window.root)
        self.top.geometry("760x620")
        self.top.minsize(600, 440)

        ttk.Label(self.top, text=f"文件: {path}", anchor="w").pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(
            self.top,
            text=(
                "安全边界：写入前自动创建 <文件名>.backup[N] 备份；写入后重新解析并校验结构与未改字段，"
                "校验失败则放弃写入、原文件不变。只允许修改下列白名单字段，结构性字段"
                "（e_lfanew、节地址/大小、数据目录等）一律拒绝。"
            ),
            wraplength=720,
            justify="left",
        ).pack(fill="x", padx=12, pady=(0, 8))

        canvas = tk.Canvas(self.top, borderwidth=0, highlightthickness=0)
        scroll = ttk.Scrollbar(self.top, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True, padx=12)

        for row, (name, field_value) in enumerate(pe.header_fields.items()):
            ttk.Label(inner, text=f"{field_value.label}（{name}）").grid(
                row=row, column=0, sticky="w", padx=(0, 12), pady=2
            )
            variable = tk.StringVar(value=f"0x{field_value.value:X}")
            ttk.Entry(inner, textvariable=variable, width=24).grid(row=row, column=1, sticky="w", pady=2)
            self._values[name] = variable

        if pe.signature.present:
            ttk.Label(
                self.top,
                text="该文件带 Authenticode 签名：写入任何字段都会使签名失效。",
                anchor="w",
            ).pack(fill="x", padx=12, pady=(8, 0))

        buttons = ttk.Frame(self.top)
        buttons.pack(fill="x", padx=12, pady=10)
        self._confirm = tk.BooleanVar(value=False)
        ttk.Checkbutton(buttons, text="我已确认风险：写入前自动备份", variable=self._confirm).pack(side="left")
        self._signature = tk.BooleanVar(value=not pe.signature.present)
        if pe.signature.present:
            ttk.Checkbutton(buttons, text="我已知晓签名将失效", variable=self._signature).pack(
                side="left", padx=(12, 0)
            )
        ttk.Button(buttons, text="取消", command=self.top.destroy).pack(side="right")
        ttk.Button(buttons, text="写入", command=lambda: self._submit(True)).pack(side="right", padx=(0, 8))
        ttk.Button(buttons, text="预演", command=lambda: self._submit(False)).pack(side="right", padx=(0, 8))

    def _submit(self, apply: bool) -> None:
        messagebox = self._window._messagebox
        try:
            edits: dict[str, int] = {}
            for name, variable in self._values.items():
                parsed = _edit.parse_value(variable.get())
                if parsed != self._original[name]:
                    edits[name] = parsed
        except PEError as exc:
            messagebox.showerror("字段值无效", str(exc), parent=self.top)
            return
        if not edits:
            messagebox.showinfo("没有修改", "所有字段都与当前值相同。", parent=self.top)
            return
        if apply and not self._confirm.get():
            messagebox.showwarning("需要确认", "请先勾选「我已确认风险：写入前自动备份」。", parent=self.top)
            return
        if apply and self._pe.signature.present and not self._signature.get():
            messagebox.showwarning(
                "需要确认", "文件带 Authenticode 签名，请勾选「我已知晓签名将失效」。", parent=self.top
            )
            return
        try:
            result = _edit.apply_edits(
                self._path,
                edits,
                apply=apply,
                allow_signature_break=True if not apply else self._signature.get(),
            )
        except PEError as exc:
            messagebox.showerror("编辑失败", str(exc), parent=self.top)
            return
        lines = [f"{change.name}: 0x{change.old:X} → 0x{change.new:X}" for change in result.changes]
        if apply:
            lines.append(f"备份: {result.backup}")
            lines.append("写入后已重新解析校验通过。")
            messagebox.showinfo("写入完成", "\n".join(lines), parent=self.top)
            self.top.destroy()
            self._window._load_file(self._path)
        else:
            lines.append("预演完成：未修改文件。")
            messagebox.showinfo("预演结果", "\n".join(lines), parent=self.top)


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None, file: Path | None = None) -> None:
    """启动 Tk 窗口；tkinter 缺失时由 :func:`fcmd.gui.window.run_window` 提示。"""
    _guiwindow.run_window(lambda: PeviewWindow(settings_path, file), "PE 查看器")


@fcmd.tool("pevieww", help="打开 PE 查看器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd(file: Path | None = None) -> None:
    """打开 PE 查看器窗口，可传入要直接加载的文件。

    Parameters
    ----------
    file:
        启动时打开的 PE 文件路径；省略时打开空窗口或恢复上次文件
    """
    run_gui(file=file)
