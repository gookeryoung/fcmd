"""parameshw - 二维轴对称网格预览（Tk）。

界面只负责取数与绘制，网格生成、质量统计与线框变换全部位于
:mod:`fcmd.apps.paramesh._mesh` / ``_quality`` / ``_preview``，因此没有显示器
时也能单测纯逻辑部分。窗口复用 ``fcmd.gui`` 的主题与设置持久化。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.paramesh import paramesh as _cli
from fcmd.apps.paramesh._config import LEVELS, MeshConfig, load_config, parse_config
from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._mesh import Mesh, build_mesh
from fcmd.apps.paramesh._model import MeshModel, format_model_summary, load_models, parse_models
from fcmd.apps.paramesh._package import mesh_id, write_package
from fcmd.apps.paramesh._preview import ViewMapper, Wireframe, fit_mapper, pixel_segments, wireframe, zoom_at
from fcmd.apps.paramesh._quality import QualityReport, format_quality, quality_report
from fcmd.apps.paramesh._scale import format_plan
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = ["Settings", "load_settings", "run_gui", "save_settings"]

__version__ = "0.1.0"

_DESCRIPTION = "二维轴对称网格预览（Tk）：按区域着色线框、局部放大、质量摘要与三档网格包导出"

DEFAULT_GEOMETRY = "1180x720"
MIN_WIDTH = 900
MIN_HEIGHT = 600
SETTINGS_NAME = "paramesh"
MIN_ZOOM = 0.1
MAX_ZOOM = 80.0


@dataclass(frozen=True, slots=True)
class Settings:
    """窗口设置：几何尺寸、上次模型 / 配置路径与当前档位。"""

    geometry: str | None = None
    model_path: str = ""
    config_path: str = ""
    level: str = "coarse"


def settings_path() -> Path:
    """配置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/paramesh.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> Settings:
    """读取设置；文件缺失或损坏时回落默认值。"""
    raw = _guiconfig.read_settings(path or settings_path())
    if raw is None:
        return Settings()
    model_path = raw.get("model_path")
    config_path = raw.get("config_path")
    level = raw.get("level")
    return Settings(
        geometry=_guiconfig.valid_geometry(raw.get("geometry")),
        model_path=model_path if isinstance(model_path, str) else "",
        config_path=config_path if isinstance(config_path, str) else "",
        level=level if isinstance(level, str) and level in LEVELS else "coarse",
    )


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """写入设置；失败静默（关闭窗口不应因落盘失败中断）。"""
    _guiconfig.write_settings(
        {
            "geometry": settings.geometry,
            "model_path": settings.model_path,
            "config_path": settings.config_path,
            "level": settings.level,
        },
        path or settings_path(),
    )


def region_color(region: str, palette: Any) -> str:
    """区域着色：炸药 / 间隙 / 管壁各一色；未知区域回落次要前景色。"""
    return {
        "propellant": palette.warning,
        "gap": palette.accent,
        "wall": palette.border_strong,
    }.get(region, palette.muted)


class ParameshWindow:  # pragma: no cover - 需要真实 Tk 窗口
    """主窗口：左侧线框画布，右侧档位、尺度与质量摘要。"""

    def __init__(self, settings_path: Path | None = None, model_file: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._level = self._settings.level
        self._model: MeshModel | None = None
        self._config: MeshConfig = _cli_default_config()
        self._mesh: Mesh | None = None
        self._report: QualityReport | None = None
        self._frame: Wireframe | None = None
        self._zoom = 1.0
        self._focus: tuple[float, float] | None = None
        self._model_path = Path(self._settings.model_path) if self._settings.model_path else None
        self._config_path = Path(self._settings.config_path) if self._settings.config_path else None
        self.root, self._theme = _guiwindow.create_root(
            title=f"网格预览 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )
        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        initial = model_file or (self._model_path if self._model_path and self._model_path.is_file() else None)
        if initial is not None:
            self.root.after(60, lambda: self._load_path(initial))

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        tk = self._tk
        ttk = self._ttk
        palette = self._theme.palette
        spacing = self._theme.spacing
        self._build_menu()

        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill="x", padx=spacing.md, pady=(spacing.md, spacing.xs))
        ttk.Button(toolbar, text="打开模型…", command=self._choose_model, style="Accent.TButton").pack(side="left")
        ttk.Button(toolbar, text="打开配置…", command=self._choose_config).pack(side="left", padx=(spacing.sm, 0))
        ttk.Button(toolbar, text="载入示例", command=self._load_example).pack(side="left", padx=(spacing.sm, 0))
        ttk.Button(toolbar, text="导出三档网格…", command=self._export).pack(side="left", padx=(spacing.sm, 0))
        ttk.Button(toolbar, text="适应窗口", command=self._fit).pack(side="left", padx=(spacing.sm, 0))
        ttk.Button(toolbar, text="放大", command=lambda: self._zoom_by(1.25)).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(toolbar, text="缩小", command=lambda: self._zoom_by(1 / 1.25)).pack(
            side="left", padx=(spacing.xs, 0)
        )
        self._status = ttk.Label(toolbar, text="载入模型或示例后预览", style="Muted.TLabel")
        self._status.pack(side="right")

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=spacing.md, pady=(spacing.xs, spacing.md))

        left = ttk.Frame(body)
        right = ttk.Frame(body, width=380)
        body.add(left, weight=4)
        body.add(right, weight=1)

        self._canvas = tk.Canvas(left, background=palette.surface, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda _event: self._redraw())
        self._canvas.bind("<MouseWheel>", self._on_wheel)
        self._canvas.bind("<Button-4>", lambda event: self._on_wheel(event, direction=1))
        self._canvas.bind("<Button-5>", lambda event: self._on_wheel(event, direction=-1))

        level_row = ttk.Frame(right)
        level_row.pack(fill="x", pady=(0, spacing.sm))
        ttk.Label(level_row, text="网格档位").pack(side="left")
        self._level_var = tk.StringVar(value=self._level)
        level_box = ttk.Combobox(
            level_row, textvariable=self._level_var, values=list(LEVELS), state="readonly", width=10
        )
        level_box.pack(side="left", padx=(spacing.sm, 0))
        level_box.bind("<<ComboboxSelected>>", lambda _event: self._on_level_change())

        self._summary = _guiwindow.create_log(right, height=24)

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开模型…", command=self._choose_model)
        file_menu.add_command(label="打开配置…", command=self._choose_config)
        file_menu.add_command(label="载入示例", command=self._load_example)
        file_menu.add_command(label="导出三档网格…", command=self._export)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)
        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="适应窗口", command=self._fit)
        view_menu.add_command(label="放大", command=lambda: self._zoom_by(1.25))
        view_menu.add_command(label="缩小", command=lambda: self._zoom_by(1 / 1.25))
        menubar.add_cascade(label="视图", menu=view_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def _choose_model(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            title="选择 dynacalc 模型文件",
            filetypes=[("模型 YAML", "*.yaml *.yml"), ("全部文件", "*.*")],
        )
        if selected:
            self._load_path(Path(selected))

    def _choose_config(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            title="选择 paramesh 网格配置",
            filetypes=[("网格配置 YAML", "*.yaml *.yml"), ("全部文件", "*.*")],
        )
        if not selected:
            return
        try:
            config, warnings = load_config(Path(selected))
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        self._config = config
        self._config_path = Path(selected)
        for warning in warnings:
            print(f"配置警告：{warning}")
        self._rebuild()

    def _load_example(self) -> None:
        try:
            models = parse_models(_cli._EXAMPLE_MODEL, "<example>")
            config, _warnings = parse_config(_cli._DEMO_CONFIG, "<example>")
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        self._model_path = None
        self._config_path = None
        self._config = config
        self._set_model(models[0])

    def _load_path(self, path: Path) -> None:
        try:
            models = load_models(path)
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        self._model_path = path
        if self._config_path is not None and self._config_path.is_file():
            with contextlib.suppress(ParameshError):
                self._config, _warnings = load_config(self._config_path)
        self._set_model(models[0])

    def _set_model(self, model: MeshModel) -> None:
        self._model = model
        self._zoom = 1.0
        self._focus = None
        self._rebuild()
        location = str(self._model_path) if self._model_path is not None else "示例（内存）"
        self._status.configure(text=f"{model.model_id} · {location}")

    def _on_level_change(self) -> None:
        level = self._level_var.get()
        if level in LEVELS:
            self._level = level
            self._rebuild()

    def _rebuild(self) -> None:
        if self._model is None:
            return
        try:
            mesh = build_mesh(self._model, self._config, self._level)
        except ParameshError as exc:
            self._mesh = None
            self._report = None
            self._frame = None
            self._render_summary_error(str(exc))
            self._redraw()
            return
        self._mesh = mesh
        self._report = quality_report(mesh)
        self._frame = wireframe(mesh)
        self._zoom = 1.0
        self._focus = None
        self._render_summary()
        self._redraw()
        self._status.configure(
            text=f"{mesh.model.model_id} · {mesh.level} · 节点 {mesh.node_count} · 单元 {mesh.element_count}"
        )

    def _render_summary(self) -> None:
        if self._mesh is None or self._report is None:
            return
        _guiwindow.clear_log(self._summary)
        lines = [format_model_summary(self._mesh.model), "", "— 尺度计划 —", format_plan(self._mesh.plan), ""]
        lines.extend(["— 质量报告 —", format_quality(self._report)])
        for line in lines:
            _guiwindow.append_log(self._summary, line)

    def _render_summary_error(self, message: str) -> None:
        _guiwindow.clear_log(self._summary)
        _guiwindow.append_log(self._summary, f"网格生成失败：{message}")

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _size(self) -> tuple[float, float]:
        return float(max(self._canvas.winfo_width(), 2)), float(max(self._canvas.winfo_height(), 2))

    def _mapper(self) -> ViewMapper | None:
        if self._frame is None:
            return None
        width, height = self._size()
        return fit_mapper(self._frame, width, height, zoom=self._zoom, focus=self._focus)

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        if self._frame is None:
            self._center_text("载入模型或示例后显示网格线框")
            return
        mapper = self._mapper()
        if mapper is None:
            return
        palette = self._theme.palette
        for x1, y1, x2, y2, region in pixel_segments(self._frame, mapper):
            canvas.create_line(x1, y1, x2, y2, fill=region_color(region, palette), width=1)
        self._draw_legend(palette)
        canvas.create_text(
            12,
            12,
            anchor="nw",
            fill=palette.text,
            font=_guitheme.HEADING_FONT,
            text=f"二维轴对称网格 · {self._level} · 缩放 {mapper.zoom:.2f}×",
        )
        if self._mesh is not None:
            canvas.create_text(
                12,
                30,
                anchor="nw",
                fill=palette.muted,
                font=_guitheme.SMALL_FONT,
                text=f"r 0–{self._mesh.model.outer_radius:g} mm · z 0–{self._mesh.model.length:g} mm",
            )

    def _draw_legend(self, palette: Any) -> None:
        if self._mesh is None:
            return
        width, _height = self._size()
        entries = [(region.name, region.element_count) for region in self._mesh.regions]
        for index, (name, count) in enumerate(entries):
            x = width - 12
            y = 12 + index * 18
            self._canvas.create_text(
                x,
                y,
                anchor="ne",
                fill=region_color(name, palette),
                font=_guitheme.SMALL_FONT,
                text=f"■ {name}（{count} 单元）",
            )

    def _center_text(self, text: str) -> None:
        width, height = self._size()
        self._canvas.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=self._theme.palette.muted,
            font=_guitheme.HEADING_FONT,
        )

    # ------------------------------------------------------------------ #
    # 视图
    # ------------------------------------------------------------------ #
    def _fit(self) -> None:
        self._zoom = 1.0
        self._focus = None
        self._redraw()

    def _zoom_by(self, factor: float) -> None:
        width, height = self._size()
        self._zoom_at(factor, width / 2.0, height / 2.0)

    def _on_wheel(self, event: Any, direction: int | None = None) -> None:
        if direction is None:
            delta = getattr(event, "delta", 0)
            direction = 1 if delta > 0 else -1
        self._zoom_at(1.15 if direction > 0 else 1 / 1.15, float(event.x), float(event.y))

    def _zoom_at(self, factor: float, x: float, y: float) -> None:
        mapper = self._mapper()
        if mapper is None:
            return
        zoomed = zoom_at(mapper, factor, x, y)
        self._zoom = min(max(zoomed.zoom, MIN_ZOOM), MAX_ZOOM)
        self._focus = (zoomed.focus_r, zoomed.focus_z)
        self._redraw()

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def _export(self) -> None:
        if self._model is None:
            self._show_error("请先载入模型或示例")
            return
        from tkinter import filedialog, messagebox

        selected = filedialog.askdirectory(title="选择网格包输出目录")
        if not selected:
            return
        target = Path(selected)
        paths: list[Path] = []
        try:
            meshes = [build_mesh(self._model, self._config, level) for level in LEVELS]
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        existing = [target / mesh_id(mesh) for mesh in meshes]
        overwrite = False
        if any(path.exists() for path in existing):
            overwrite = messagebox.askyesno("覆盖确认", "目标目录下已有同名网格包，是否覆盖？")
            if not overwrite:
                return
        try:
            for mesh in meshes:
                paths.append(write_package(mesh, quality_report(mesh), target, overwrite=overwrite))
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        self._status.configure(text=f"已导出 {len(paths)} 档网格到 {target}")
        messagebox.showinfo("导出完成", "已写入：\n" + "\n".join(str(path) for path in paths))

    # ------------------------------------------------------------------ #
    # 其它
    # ------------------------------------------------------------------ #
    def _show_error(self, message: str) -> None:
        from tkinter import messagebox

        messagebox.showerror("paramesh", message)

    def _show_about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "关于",
            "paramesh —— 参数化模型到仿真网格的中间层\n"
            "按 dynacalc-model/1 生成二维轴对称三档网格、LS-DYNA 网格片段与质量报告。\n"
            "不求解、不生成材料参数、不写 dynacalc 的 deck 目录。",
        )

    def close(self) -> None:
        try:
            geometry = self.root.winfo_geometry()
        except Exception:
            geometry = None
        save_settings(
            Settings(
                geometry=_guiconfig.valid_geometry(geometry),
                model_path=str(self._model_path) if self._model_path is not None else "",
                config_path=str(self._config_path) if self._config_path is not None else "",
                level=self._level,
            ),
            self._settings_path,
        )
        self.root.destroy()


def _cli_default_config() -> MeshConfig:
    """默认配置；示例注入前作为占位（缺少反应区尺度，生成时会明确报错）。"""
    return MeshConfig()


def run_gui(model: Path | None = None, config: Path | None = None, settings_path: Path | None = None) -> None:
    """启动 Tk 窗口；tkinter 缺失时由 :func:`fcmd.gui.window.run_window` 提示。"""

    def factory() -> ParameshWindow:
        window = ParameshWindow(settings_path, model)
        if config is not None:
            window._config_path = config
            with contextlib.suppress(ParameshError):
                window._config, _warnings = load_config(config)
            window._rebuild()
        return window

    _guiwindow.run_window(factory, "网格预览")


@fcmd.tool("parameshw", help="打开二维轴对称网格预览窗口（Tk）", description=_DESCRIPTION)
def launch_cmd(model_file: Path | None = None, config_file: Path | None = None) -> None:
    """打开预览窗口，可传入要直接加载的模型与网格配置。

    Parameters
    ----------
    model_file:
        启动时加载的 ``dynacalc-model/1`` YAML；省略时恢复上次文件或打开空窗口
    config_file:
        ``paramesh/1`` 网格配置；省略时使用上次配置或默认值
    """
    run_gui(model=model_file, config=config_file)


@fcmd.main("parameshw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体


if __name__ == "__main__":  # pragma: no cover
    main()
