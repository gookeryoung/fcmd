"""pptmakew —— PPT 模板设计器（Tk 窗口）。

左侧选页与布局，中间实时预览，右侧编辑当前页内容与六个语义色；顶部可切换
风格 / 画幅 / 装饰，菜单支持模板 JSON 导入导出与直接导出 ``.pptx``。窗口
设置（风格、画幅、装饰、最近目录、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/pptmakew.json``。

示例
----
    fcmd pptmakew        # 打开模板设计器
    pptmakew             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import fcmd
from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake._design import Line, Oval, Rect, Text, build_draw_list
from fcmd.apps.pptmake._pptx import export_pptx, import_pptx
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fcmd.gui.theme import Theme

__all__ = [
    "DEFAULT_GEOMETRY",
    "HELP_TEXT",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "PptmakeSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "safe_filename",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "slide_label",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PPT 模板设计器（Tk）：九种布局、四套风格、六个语义色实时预览，"
    "模板 JSON 与 PPTX 双向导入导出；设置持久化到 ~/.fcmd/ui/pptmakew.json"
)

DEFAULT_GEOMETRY = "1320x840"
MIN_WIDTH = 1100
MIN_HEIGHT = 700

STYLE_OPTIONS: tuple[tuple[str, str], ...] = (
    *((core.STYLE_TEXT[style], style) for style in core.STYLES),
    (core.STYLE_TEXT[core.STYLE_CUSTOM], core.STYLE_CUSTOM),
)
ASPECT_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.ASPECT_TEXT[item], item) for item in core.ASPECTS)
DECOR_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.DECOR_TEXT[item], item) for item in core.DECORS)
LAYOUT_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.LAYOUT_TEXT[item], item) for item in core.LAYOUTS)

_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
_CanvasAnchor = Literal["center", "e", "n", "ne", "nw", "s", "se", "sw", "w"]
_CanvasJustify = Literal["center", "left", "right"]

HELP_TEXT = """1. 左侧「页面」选择要编辑的页；「布局库」双击即可新增一页。
2. 右侧修改标题、副标题、要点与备注；要点按行拆分。
   - 数据页写成「98%|交付准时率」；
   - 时间线写成「01|调研」；
   - 双栏页副标题写成「左栏标题|右栏标题」。
3. 顶部切换风格 / 画幅 / 装饰；右侧六个色块可逐项改色（自动转为自定义风格）。
4. 「文件 → 导出模板」保存 .pptmtpl.json，「文件 → 导出 PPTX」生成演示文稿。
5. 「文件 → 导入」既支持模板 JSON，也支持从现有 .pptx 提取配色与文字。"""


@dataclass(slots=True)
class PptmakeSettings:
    """窗口设置。"""

    style: str = core.STYLE_AURORA
    aspect: str = core.ASPECT_16_9
    decor: str = core.DECOR_ORBS
    last_dir: str = ""
    geometry: str | None = None


def config_path() -> Path:
    """设置文件路径。"""
    return _guiconfig.config_path("pptmakew")


def settings_to_mapping(settings: PptmakeSettings) -> dict[str, Any]:
    """设置转字典。"""
    return {
        "style": settings.style,
        "aspect": settings.aspect,
        "decor": settings.decor,
        "last_dir": settings.last_dir,
        "geometry": settings.geometry,
    }


def settings_from_mapping(raw: Mapping[str, Any], base: PptmakeSettings | None = None) -> PptmakeSettings:
    """从字典解析设置；非法字段保留 ``base`` 或默认值。"""
    settings = replace(base) if base is not None else PptmakeSettings()
    style = raw.get("style")
    if isinstance(style, str):
        with contextlib.suppress(core.TemplateError):
            settings.style = core.normalize_style(style)
    aspect = raw.get("aspect")
    if isinstance(aspect, str):
        with contextlib.suppress(core.TemplateError):
            settings.aspect = core.normalize_aspect(aspect)
    decor = raw.get("decor")
    if isinstance(decor, str):
        settings.decor = core.normalize_decor(decor)
    last_dir = raw.get("last_dir")
    if isinstance(last_dir, str):
        settings.last_dir = last_dir
    geometry = raw.get("geometry")
    settings.geometry = geometry if isinstance(geometry, str) and _guiconfig.valid_geometry(geometry) else None
    return settings


def load_settings(path: Path | None = None) -> PptmakeSettings:
    """读取设置；缺失或损坏时返回默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PptmakeSettings()
    return settings_from_mapping(raw)


def save_settings(settings: PptmakeSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略。"""
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """恢复窗口位置；越界时夹回屏幕并保证最小尺寸。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def safe_filename(name: str, fallback: str = "template") -> str:
    """把模板名转成可用的文件名（去掉 Windows 非法字符）。"""
    cleaned = _FILENAME_RE.sub("_", name).strip().strip(".")
    return cleaned or fallback


def slide_label(index: int, slide: core.SlideSpec) -> str:
    """页面列表显示文案：序号 + 布局 + 截断标题。"""
    title = slide.title.strip() or "（未命名）"
    if len(title) > 18:
        title = title[:17] + "…"
    return f"{index:02d}  {core.layout_label(slide.layout)}  {title}"


def _option_value(options: tuple[tuple[str, str], ...], label: str, default: str) -> str:
    """界面标签转枚举值；无法识别时返回默认值。"""
    for text, value in options:
        if label in (text, value):
            return value
    return default


def _option_label(options: tuple[tuple[str, str], ...], value: str, default: str) -> str:
    """枚举值转界面标签。"""
    for text, item in options:
        if item == value:
            return text
    return default


class PptmakeWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 模板设计器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import colorchooser, filedialog, messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._colorchooser = colorchooser
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._template = core.new_template(
            name="未命名模板",
            style=self._settings.style,
            aspect=self._settings.aspect,
            decor=self._settings.decor,
        )
        self._selected = 0
        self._loading = False
        self._apply_job: str | None = None
        self._preview_job: str | None = None
        self._color_buttons: dict[str, Any] = {}

        self.root, self._theme = _guiwindow.create_root(
            title=f"pptmake - PPT 模板设计器 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )
        self._init_vars()
        self._build_menu()
        self._build_widgets()
        self._refresh_all()
        _guitheme.install_theme_switcher(self.root, on_apply=self._on_gui_theme)

    # ------------------------------------------------------------------ #
    # 变量与构建
    # ------------------------------------------------------------------ #
    def _init_vars(self) -> None:
        tk = self._tk
        self._name_var = tk.StringVar(value=self._template.name)
        self._style_var = tk.StringVar(
            value=_option_label(STYLE_OPTIONS, self._template.theme.style, core.STYLE_TEXT[core.STYLE_AURORA])
        )
        self._aspect_var = tk.StringVar(
            value=_option_label(ASPECT_OPTIONS, self._template.aspect, core.ASPECT_TEXT[core.ASPECT_16_9])
        )
        self._decor_var = tk.StringVar(
            value=_option_label(DECOR_OPTIONS, self._template.theme.decor, core.DECOR_TEXT[core.DECOR_ORBS])
        )
        self._layout_var = tk.StringVar(value=core.LAYOUT_TEXT[core.LAYOUT_BULLETS])
        self._title_var = tk.StringVar()
        self._subtitle_var = tk.StringVar()
        self._status_var = tk.StringVar(value="就绪")
        self._name_var.trace_add("write", lambda *_: self._on_name_changed())

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="新建模板", command=self._new_template)
        file_menu.add_command(label="导入模板 / PPTX…", command=self._import_file)
        file_menu.add_separator()
        file_menu.add_command(label="导出模板…", command=self._export_template)
        file_menu.add_command(label="导出 PPTX…", command=self._export_pptx)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="添加页面", command=self._add_slide)
        edit_menu.add_command(label="复制页面", command=self._duplicate_slide)
        edit_menu.add_command(label="删除页面", command=self._delete_slide)
        edit_menu.add_separator()
        edit_menu.add_command(label="上移", command=lambda: self._move_slide(-1))
        edit_menu.add_command(label="下移", command=lambda: self._move_slide(1))
        menubar.add_cascade(label="编辑", menu=edit_menu)

        style_menu = tk.Menu(menubar, tearoff=False)
        for text, value in STYLE_OPTIONS[: len(core.STYLES)]:
            style_menu.add_radiobutton(
                label=text,
                variable=self._style_var,
                value=text,
                command=lambda item=value: self._apply_style(item),
            )
        style_menu.add_separator()
        decor_menu = tk.Menu(style_menu, tearoff=False)
        for text, value in DECOR_OPTIONS:
            decor_menu.add_radiobutton(
                label=text,
                variable=self._decor_var,
                value=text,
                command=lambda item=value: self._apply_decor(item),
            )
        style_menu.add_cascade(label="装饰样式", menu=decor_menu)
        menubar.add_cascade(label="样式", menu=style_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="使用说明", command=self._show_help)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    def _build_widgets(self) -> None:
        tk = self._tk
        ttk = self._ttk
        palette = self._theme.palette
        space = self._theme.spacing
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root, padding=(space.md, space.sm))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="模板名称").grid(row=0, column=0, padx=(0, space.sm))
        ttk.Entry(top, textvariable=self._name_var).grid(row=0, column=1, sticky="ew", padx=(0, space.md))
        ttk.Button(top, text="新建", command=self._new_template).grid(row=0, column=2, padx=(0, space.xs))
        ttk.Button(top, text="导入", command=self._import_file).grid(row=0, column=3, padx=(0, space.xs))
        ttk.Button(top, text="导出模板", command=self._export_template).grid(row=0, column=4, padx=(0, space.xs))
        ttk.Button(top, text="导出 PPTX", command=self._export_pptx).grid(row=0, column=5)

        controls = ttk.Frame(top)
        controls.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(space.sm, 0))
        ttk.Label(controls, text="风格").pack(side="left", padx=(0, space.xs))
        style_box = ttk.Combobox(
            controls,
            textvariable=self._style_var,
            values=[text for text, _ in STYLE_OPTIONS],
            state="readonly",
            width=10,
        )
        style_box.pack(side="left", padx=(0, space.lg))
        style_box.bind("<<ComboboxSelected>>", lambda _event: self._on_style_selected())
        ttk.Label(controls, text="画幅").pack(side="left", padx=(0, space.xs))
        aspect_box = ttk.Combobox(
            controls,
            textvariable=self._aspect_var,
            values=[text for text, _ in ASPECT_OPTIONS],
            state="readonly",
            width=12,
        )
        aspect_box.pack(side="left", padx=(0, space.lg))
        aspect_box.bind("<<ComboboxSelected>>", lambda _event: self._on_aspect_selected())
        ttk.Label(controls, text="装饰").pack(side="left", padx=(0, space.xs))
        decor_box = ttk.Combobox(
            controls,
            textvariable=self._decor_var,
            values=[text for text, _ in DECOR_OPTIONS],
            state="readonly",
            width=8,
        )
        decor_box.pack(side="left")
        decor_box.bind("<<ComboboxSelected>>", lambda _event: self._on_decor_selected())

        paned = ttk.Panedwindow(root, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", padx=space.md, pady=(0, space.sm))

        left = ttk.Frame(paned)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=3)
        left.rowconfigure(4, weight=2)
        ttk.Label(left, text="页面").grid(row=0, column=0, sticky="w", pady=(0, space.xs))
        self._slides_list = tk.Listbox(
            left,
            exportselection=False,
            activestyle="none",
            bd=0,
            highlightthickness=0,
            bg=palette.surface,
            fg=palette.text,
            selectbackground=palette.accent,
            selectforeground=palette.background,
        )
        self._slides_list.grid(row=1, column=0, sticky="nsew")
        self._slides_list.bind("<<ListboxSelect>>", lambda _event: self._on_slide_selected())
        slide_buttons = ttk.Frame(left)
        slide_buttons.grid(row=2, column=0, sticky="ew", pady=(space.xs, space.md))
        for text, command in (
            ("添加", self._add_slide),
            ("复制", self._duplicate_slide),
            ("删除", self._delete_slide),
            ("上移", lambda: self._move_slide(-1)),
            ("下移", lambda: self._move_slide(1)),
        ):
            ttk.Button(slide_buttons, text=text, width=5, command=command).pack(side="left", padx=(0, 2))
        ttk.Label(left, text="布局库（双击添加）").grid(row=3, column=0, sticky="w", pady=(0, space.xs))
        self._layout_list = tk.Listbox(
            left,
            exportselection=False,
            activestyle="none",
            bd=0,
            highlightthickness=0,
            height=7,
            bg=palette.surface,
            fg=palette.text,
            selectbackground=palette.accent,
            selectforeground=palette.background,
        )
        self._layout_list.grid(row=4, column=0, sticky="nsew")
        for text, _value in LAYOUT_OPTIONS:
            self._layout_list.insert("end", text)
        self._layout_list.selection_set(0)
        self._layout_list.bind("<Double-Button-1>", lambda _event: self._add_slide())
        paned.add(left, weight=0)

        center = ttk.Frame(paned)
        center.columnconfigure(0, weight=1)
        center.rowconfigure(0, weight=1)
        self._canvas = tk.Canvas(center, bd=0, highlightthickness=0, bg=palette.background)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._canvas.bind("<Configure>", lambda _event: self._schedule_preview())
        paned.add(center, weight=1)

        right = ttk.Frame(paned, width=320)
        right.columnconfigure(1, weight=1)
        paned.add(right, weight=0)
        form = ttk.LabelFrame(right, text="页面设置", padding=space.sm)
        form.grid(row=0, column=0, columnspan=2, sticky="nsew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="布局").grid(row=0, column=0, sticky="w", pady=(0, space.xs))
        layout_box = ttk.Combobox(
            form, textvariable=self._layout_var, values=[text for text, _ in LAYOUT_OPTIONS], state="readonly"
        )
        layout_box.grid(row=0, column=1, sticky="ew", pady=(0, space.xs))
        layout_box.bind("<<ComboboxSelected>>", lambda _event: self._schedule_apply())
        ttk.Label(form, text="标题").grid(row=1, column=0, sticky="w", pady=(0, space.xs))
        title_entry = ttk.Entry(form, textvariable=self._title_var)
        title_entry.grid(row=1, column=1, sticky="ew", pady=(0, space.xs))
        title_entry.bind("<KeyRelease>", lambda _event: self._schedule_apply())
        ttk.Label(form, text="副标题").grid(row=2, column=0, sticky="w", pady=(0, space.xs))
        subtitle_entry = ttk.Entry(form, textvariable=self._subtitle_var)
        subtitle_entry.grid(row=2, column=1, sticky="ew", pady=(0, space.xs))
        subtitle_entry.bind("<KeyRelease>", lambda _event: self._schedule_apply())
        ttk.Label(form, text="要点").grid(row=3, column=0, sticky="nw", pady=(0, space.xs))
        self._bullets_text = self._make_text(form, height=9)
        self._bullets_text.grid(row=3, column=1, sticky="nsew", pady=(0, space.xs))
        ttk.Label(form, text="备注").grid(row=4, column=0, sticky="nw")
        self._note_text = self._make_text(form, height=3)
        self._note_text.grid(row=4, column=1, sticky="nsew")
        form.rowconfigure(3, weight=1)

        colors = ttk.LabelFrame(right, text="配色（点击修改）", padding=space.sm)
        colors.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(space.sm, 0))
        for index, (name, label) in enumerate(core.PALETTE_FIELDS):
            button = tk.Button(
                colors, text=label, relief="flat", bd=0, width=9, command=lambda field=name: self._pick_color(field)
            )
            button.grid(row=index // 2, column=index % 2, sticky="ew", padx=2, pady=2)
            self._color_buttons[name] = button
        colors.columnconfigure(0, weight=1)
        colors.columnconfigure(1, weight=1)

        ttk.Label(root, textvariable=self._status_var, anchor="w", padding=(space.md, space.xs)).grid(
            row=2, column=0, sticky="ew"
        )

    def _make_text(self, parent: Any, *, height: int) -> Any:
        tk = self._tk
        palette = self._theme.palette
        return tk.Text(
            parent,
            height=height,
            wrap="word",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette.border,
            highlightcolor=palette.accent,
            bg=palette.surface,
            fg=palette.text,
            insertbackground=palette.text,
        )

    # ------------------------------------------------------------------ #
    # 状态同步
    # ------------------------------------------------------------------ #
    def _refresh_all(self) -> None:
        self._refresh_slide_list()
        self._refresh_form()
        self._refresh_color_buttons()
        self._schedule_preview()

    def _refresh_slide_list(self) -> None:
        self._slides_list.delete(0, "end")
        for index, slide in enumerate(self._template.slides, start=1):
            self._slides_list.insert("end", slide_label(index, slide))
        if self._template.slides:
            self._selected = min(self._selected, len(self._template.slides) - 1)
            self._slides_list.selection_set(self._selected)
            self._slides_list.see(self._selected)
        else:
            self._selected = 0

    def _refresh_form(self) -> None:
        self._loading = True
        try:
            if not self._template.slides:
                self._layout_var.set(core.LAYOUT_TEXT[core.LAYOUT_BULLETS])
                self._title_var.set("")
                self._subtitle_var.set("")
                self._bullets_text.delete("1.0", "end")
                self._note_text.delete("1.0", "end")
                return
            slide = self._template.slides[self._selected]
            self._layout_var.set(_option_label(LAYOUT_OPTIONS, slide.layout, core.LAYOUT_TEXT[core.LAYOUT_BULLETS]))
            self._title_var.set(slide.title)
            self._subtitle_var.set(slide.subtitle)
            self._bullets_text.delete("1.0", "end")
            self._bullets_text.insert("1.0", "\n".join(slide.bullets))
            self._note_text.delete("1.0", "end")
            self._note_text.insert("1.0", slide.note)
        finally:
            self._loading = False

    def _refresh_color_buttons(self) -> None:
        palette = self._template.theme.palette
        for name, button in self._color_buttons.items():
            value = getattr(palette, name)
            foreground = "#111111" if core.relative_luminance(value) > 0.5 else "#FFFFFF"
            button.configure(bg=value, fg=foreground, activebackground=value, activeforeground=foreground)

    def _refresh_style_controls(self) -> None:
        self._loading = True
        try:
            self._name_var.set(self._template.name)
            self._style_var.set(
                _option_label(STYLE_OPTIONS, self._template.theme.style, core.STYLE_TEXT[core.STYLE_AURORA])
            )
            self._aspect_var.set(
                _option_label(ASPECT_OPTIONS, self._template.aspect, core.ASPECT_TEXT[core.ASPECT_16_9])
            )
            self._decor_var.set(
                _option_label(DECOR_OPTIONS, self._template.theme.decor, core.DECOR_TEXT[core.DECOR_ORBS])
            )
        finally:
            self._loading = False

    def _set_template(self, template: core.TemplateSpec) -> None:
        self._template = template
        self._selected = 0
        self._refresh_style_controls()
        self._refresh_all()
        self._status_var.set(f"已载入：{template.name}（{len(template.slides)} 页）")

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)

    # ------------------------------------------------------------------ #
    # 编辑动作
    # ------------------------------------------------------------------ #
    def _on_name_changed(self) -> None:
        if self._loading:
            return
        self._template = replace(self._template, name=self._name_var.get())
        self._set_status("模板名称已更新")

    def _on_style_selected(self) -> None:
        if self._loading:
            return
        self._apply_style(_option_value(STYLE_OPTIONS, self._style_var.get(), core.STYLE_AURORA))

    def _apply_style(self, style: str) -> None:
        if style == core.STYLE_CUSTOM:
            return
        theme = replace(self._template.theme, style=style, palette=core.palette_for_style(style))
        self._template = replace(self._template, theme=theme)
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._schedule_preview()
        self._set_status(f"已切换风格：{core.style_label(style)}")

    def _on_aspect_selected(self) -> None:
        if self._loading:
            return
        aspect = _option_value(ASPECT_OPTIONS, self._aspect_var.get(), core.ASPECT_16_9)
        self._template = replace(self._template, aspect=aspect)
        self._schedule_preview()
        self._set_status(f"已切换画幅：{core.ASPECT_TEXT[aspect]}")

    def _on_decor_selected(self) -> None:
        if self._loading:
            return
        self._apply_decor(_option_value(DECOR_OPTIONS, self._decor_var.get(), core.DECOR_ORBS))

    def _apply_decor(self, decor: str) -> None:
        self._template = replace(self._template, theme=replace(self._template.theme, decor=core.normalize_decor(decor)))
        self._refresh_style_controls()
        self._schedule_preview()
        self._set_status(f"已切换装饰：{core.DECOR_TEXT.get(decor, decor)}")

    def _on_slide_selected(self) -> None:
        selection = self._slides_list.curselection()
        if not selection:
            return
        self._selected = int(selection[0])
        self._refresh_form()
        self._schedule_preview()

    def _schedule_apply(self) -> None:
        if self._loading:
            return
        if self._apply_job is not None:
            self.root.after_cancel(self._apply_job)
        self._apply_job = self.root.after(150, self._apply_form)

    def _apply_form(self) -> None:
        self._apply_job = None
        if not self._template.slides:
            return
        slide = self._template.slides[self._selected]
        bullets = tuple(line.strip() for line in self._bullets_text.get("1.0", "end-1c").splitlines() if line.strip())
        updated = replace(
            slide,
            layout=_option_value(LAYOUT_OPTIONS, self._layout_var.get(), core.LAYOUT_BULLETS),
            title=self._title_var.get(),
            subtitle=self._subtitle_var.get(),
            bullets=bullets,
            note=self._note_text.get("1.0", "end-1c"),
        )
        self._template = core.replace_slide(self._template, self._selected, updated)
        self._slides_list.delete(self._selected)
        self._slides_list.insert(self._selected, slide_label(self._selected + 1, updated))
        self._slides_list.selection_set(self._selected)
        self._schedule_preview()
        self._set_status(f"第 {self._selected + 1} 页已更新")

    def _add_slide(self) -> None:
        selection = self._layout_list.curselection()
        layout = core.LAYOUT_BULLETS if not selection else LAYOUT_OPTIONS[int(selection[0])][1]
        slide = core.new_slide(layout)
        self._template = replace(self._template, slides=(*self._template.slides, slide))
        self._selected = len(self._template.slides) - 1
        self._refresh_slide_list()
        self._refresh_form()
        self._schedule_preview()
        self._set_status(f"已添加「{core.layout_label(layout)}」页面")

    def _duplicate_slide(self) -> None:
        if not self._template.slides:
            return
        slide = replace(
            self._template.slides[self._selected], title=f"{self._template.slides[self._selected].title}（副本）"
        )
        slides = list(self._template.slides)
        slides.insert(self._selected + 1, slide)
        self._template = replace(self._template, slides=tuple(slides))
        self._selected += 1
        self._refresh_slide_list()
        self._refresh_form()
        self._schedule_preview()
        self._set_status("已复制当前页面")

    def _delete_slide(self) -> None:
        if not self._template.slides:
            return
        slides = list(self._template.slides)
        slides.pop(self._selected)
        self._template = replace(self._template, slides=tuple(slides))
        self._refresh_slide_list()
        self._refresh_form()
        self._schedule_preview()
        self._set_status("已删除当前页面")

    def _move_slide(self, delta: int) -> None:
        slides = list(self._template.slides)
        target = self._selected + delta
        if not slides or target < 0 or target >= len(slides):
            return
        slides[self._selected], slides[target] = slides[target], slides[self._selected]
        self._template = replace(self._template, slides=tuple(slides))
        self._selected = target
        self._refresh_slide_list()
        self._refresh_form()
        self._schedule_preview()
        self._set_status("页面顺序已调整")

    def _pick_color(self, field: str) -> None:
        current = getattr(self._template.theme.palette, field)
        label = dict(core.PALETTE_FIELDS)[field]
        _rgb, value = self._colorchooser.askcolor(color=current, parent=self.root, title=f"选择{label}颜色")
        if not value:
            return
        try:
            normalized = core.normalize_hex(value)
        except core.TemplateError as exc:
            self._messagebox.showerror("颜色无效", str(exc), parent=self.root)
            return
        palette = replace(self._template.theme.palette, **{field: normalized})
        self._template = replace(
            self._template,
            theme=replace(self._template.theme, style=core.STYLE_CUSTOM, palette=palette),
        )
        self._refresh_style_controls()
        self._refresh_color_buttons()
        self._schedule_preview()
        self._set_status(f"已更新{label}颜色：{normalized}")

    # ------------------------------------------------------------------ #
    # 设计器动作
    # ------------------------------------------------------------------ #
    def _new_template(self) -> None:
        if not self._messagebox.askyesno("新建模板", "新建模板会丢弃当前未导出的编辑，继续？", parent=self.root):
            return
        self._template = core.new_template(
            name="未命名模板",
            style=self._settings.style,
            aspect=self._settings.aspect,
            decor=self._settings.decor,
        )
        self._selected = 0
        self._refresh_style_controls()
        self._refresh_all()
        self._set_status("已新建模板")

    def _import_file(self) -> None:
        path = self._filedialog.askopenfilename(
            parent=self.root,
            title="导入模板或 PPTX",
            initialdir=self._settings.last_dir or None,
            filetypes=(
                ("PPT 模板", "*.pptmtpl.json"),
                ("PowerPoint", "*.pptx"),
                ("JSON", "*.json"),
                ("所有文件", "*.*"),
            ),
        )
        if not path:
            return
        source = Path(path)
        try:
            template = import_pptx(source) if source.suffix.lower() == ".pptx" else core.load_template(source)
        except core.TemplateError as exc:
            self._messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        self._settings.last_dir = str(source.parent)
        self._set_template(template)

    def _export_template(self) -> None:
        default_name = f"{safe_filename(self._template.name)}{core.TEMPLATE_SUFFIX}"
        path = self._filedialog.asksaveasfilename(
            parent=self.root,
            title="导出模板",
            initialdir=self._settings.last_dir or None,
            initialfile=default_name,
            defaultextension=core.TEMPLATE_SUFFIX,
            filetypes=(("PPT 模板", f"*{core.TEMPLATE_SUFFIX}"), ("JSON", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        target = Path(path)
        try:
            core.save_template(self._template, target)
        except core.TemplateError as exc:
            self._messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self._settings.last_dir = str(target.parent)
        self._set_status(f"模板已导出：{target}")

    def _export_pptx(self) -> None:
        default_name = f"{safe_filename(self._template.name)}.pptx"
        path = self._filedialog.asksaveasfilename(
            parent=self.root,
            title="导出 PPTX",
            initialdir=self._settings.last_dir or None,
            initialfile=default_name,
            defaultextension=".pptx",
            filetypes=(("PowerPoint", "*.pptx"), ("所有文件", "*.*")),
        )
        if not path:
            return
        target = Path(path)
        try:
            count = export_pptx(self._template, target)
        except core.TemplateError as exc:
            self._messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self._settings.last_dir = str(target.parent)
        self._set_status(f"PPTX 已导出：{target}（{count} 页）")
        self._messagebox.showinfo("导出完成", f"已生成 {count} 页：\n{target}", parent=self.root)

    def _show_help(self) -> None:
        self._messagebox.showinfo("使用说明", HELP_TEXT, parent=self.root)

    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于",
            f"pptmake v{__version__}\nPPT 模板设计器：九种布局 + 四套风格 + 语义配色，\n"
            "模板 JSON 与 PPTX 双向导入导出。",
            parent=self.root,
        )

    # ------------------------------------------------------------------ #
    # 预览
    # ------------------------------------------------------------------ #
    def _schedule_preview(self) -> None:
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(30, self._draw_preview)

    def _draw_preview(self) -> None:
        self._preview_job = None
        canvas = self._canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 2 or height <= 2:
            return
        if not self._template.slides:
            canvas.create_text(
                width / 2,
                height / 2,
                text="尚无页面\n请从左侧「布局库」双击添加",
                fill=self._theme.palette.muted,
                font=(_guitheme.ui_font_family(self.root), -14),
                justify="center",
            )
            return
        slide = self._template.slides[self._selected]
        design_width, design_height = core.ASPECT_DESIGN[self._template.aspect]
        scale = min((width - 32) / design_width, (height - 32) / design_height)
        offset_x = (width - design_width * scale) / 2
        offset_y = (height - design_height * scale) / 2
        canvas.create_rectangle(
            offset_x - 1,
            offset_y - 1,
            offset_x + design_width * scale + 1,
            offset_y + design_height * scale + 1,
            outline=self._theme.palette.border,
        )
        for primitive in build_draw_list(slide, self._template):
            self._draw_primitive(primitive, offset_x, offset_y, scale)

    def _draw_primitive(self, primitive: Any, offset_x: float, offset_y: float, scale: float) -> None:
        canvas = self._canvas
        if isinstance(primitive, Rect):
            x = offset_x + primitive.x * scale
            y = offset_y + primitive.y * scale
            w = primitive.w * scale
            h = primitive.h * scale
            if w <= 0 or h <= 0:
                return
            line_width = max(1, round(primitive.line_width * scale)) if primitive.line else 0
            if primitive.radius > 0:
                canvas.create_polygon(
                    self._rounded_points(x, y, w, h, min(primitive.radius * scale, w / 2, h / 2)),
                    smooth=True,
                    splinesteps=12,
                    fill=primitive.fill,
                    outline=primitive.line,
                    width=line_width,
                )
            else:
                canvas.create_rectangle(
                    x, y, x + w, y + h, fill=primitive.fill, outline=primitive.line, width=line_width
                )
            return
        if isinstance(primitive, Oval):
            x = offset_x + primitive.x * scale
            y = offset_y + primitive.y * scale
            w = primitive.w * scale
            h = primitive.h * scale
            if w <= 0 or h <= 0:
                return
            canvas.create_oval(
                x,
                y,
                x + w,
                y + h,
                fill=primitive.fill,
                outline=primitive.line,
                width=max(1, round(primitive.line_width * scale)) if primitive.line else 0,
            )
            return
        if isinstance(primitive, Line):
            canvas.create_line(
                offset_x + primitive.x1 * scale,
                offset_y + primitive.y1 * scale,
                offset_x + primitive.x2 * scale,
                offset_y + primitive.y2 * scale,
                fill=primitive.color,
                width=max(1, round(primitive.width * scale)),
            )
            return
        if isinstance(primitive, Text):
            self._draw_text(primitive, offset_x, offset_y, scale)

    def _draw_text(self, primitive: Text, offset_x: float, offset_y: float, scale: float) -> None:
        x = offset_x + primitive.x * scale
        y = offset_y + primitive.y * scale
        w = primitive.w * scale
        h = primitive.h * scale
        horizontal = {"left": ("w", x), "center": ("", x + w / 2), "right": ("e", x + w)}
        vertical = {"top": ("n", y), "middle": ("", y + h / 2), "bottom": ("s", y + h)}
        anchor = (vertical[primitive.anchor][0] + horizontal[primitive.align][0]) or "center"
        size = max(round(primitive.size * scale), 1)
        family = primitive.font or _guitheme.ui_font_family(self.root)
        font = (family, -size, "bold" if primitive.bold else "normal")
        self._canvas.create_text(
            horizontal[primitive.align][1],
            vertical[primitive.anchor][1],
            text="\n".join(primitive.paragraphs),
            anchor=cast(_CanvasAnchor, anchor),
            width=max(round(w), 1),
            justify=cast(_CanvasJustify, primitive.align),
            fill=primitive.color,
            font=font,
        )

    @staticmethod
    def _rounded_points(x: float, y: float, w: float, h: float, radius: float) -> list[float]:
        """生成圆角矩形的多边形顶点，供 Canvas 平滑绘制。"""
        r = max(radius, 0.0)
        return [
            x + r,
            y,
            x + w - r,
            y,
            x + w,
            y,
            x + w,
            y + r,
            x + w,
            y + h - r,
            x + w,
            y + h,
            x + w - r,
            y + h,
            x + r,
            y + h,
            x,
            y + h,
            x,
            y + h - r,
            x,
            y + r,
            x,
            y,
        ]

    def _on_gui_theme(self, theme: Theme) -> None:
        palette = theme.palette
        self._canvas.configure(bg=palette.background)
        for widget in (self._slides_list, self._layout_list):
            widget.configure(
                bg=palette.surface,
                fg=palette.text,
                selectbackground=palette.accent,
                selectforeground=palette.background,
            )
        for text_widget in (self._bullets_text, self._note_text):
            text_widget.configure(
                bg=palette.surface,
                fg=palette.text,
                insertbackground=palette.text,
                highlightbackground=palette.border,
                highlightcolor=palette.accent,
            )
        self._schedule_preview()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置与窗口位置后退出。"""
        settings = PptmakeSettings(
            style=self._template.theme.style,
            aspect=self._template.aspect,
            decor=self._template.theme.decor,
            last_dir=self._settings.last_dir,
            geometry=self.root.winfo_geometry(),
        )
        save_settings(settings, self._settings_path)
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口。"""
    _guiwindow.run_window(lambda: PptmakeWindow(settings_path), "PPT 模板设计器")


@fcmd.tool("pptmakew", help="打开 PPT 模板设计器（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PPT 模板设计器。"""
    run_gui()


@fcmd.main("pptmakew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
