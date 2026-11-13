"""paramcadw - 参数化建模与几何预览（Tk）。

界面只负责取数与绘制，几何采样全部来自 :mod:`fcmd.apps.paramcad._preview`，
因此没有显示器时也能单测纯逻辑部分。窗口复用 ``fcmd.gui`` 的主题与设置持久化。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.apps.paramcad import _export, _preview
from fcmd.apps.paramcad._model import TubeModel, format_summary
from fcmd.apps.paramcad._quantity import ParamcadError
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.paramcad._preview import AxialSection, CrossSection, Curve

__all__ = ["CanvasMap", "Settings", "load_settings", "run_gui", "save_settings"]

__version__ = "0.1.0"

_DESCRIPTION = "参数化建模预览（Tk）：二维截面、轴向剖面与密度 / 壁厚分布曲线；数据可导出为 dynacalc-model/1 模型描述"

DEFAULT_GEOMETRY = "1120x720"
MIN_WIDTH = 900
MIN_HEIGHT = 600
SETTINGS_NAME = "paramcad"


@dataclass(frozen=True, slots=True)
class Settings:
    """窗口设置：几何尺寸与上次打开的模型路径。"""

    geometry: str | None = None
    model_path: str = ""


def settings_path() -> Path:
    """配置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/paramcad.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> Settings:
    """读取设置；文件缺失或损坏时回落默认值。"""
    raw = _guiconfig.read_settings(path or settings_path())
    if raw is None:
        return Settings()
    model_path = raw.get("model_path")
    return Settings(
        geometry=_guiconfig.valid_geometry(raw.get("geometry")),
        model_path=model_path if isinstance(model_path, str) else "",
    )


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """写入设置；失败静默（关闭窗口不应因落盘失败中断）。"""
    _guiconfig.write_settings(
        {"geometry": settings.geometry, "model_path": settings.model_path}, path or settings_path()
    )


@dataclass(frozen=True, slots=True)
class CanvasMap:
    """把毫米坐标映射到画布像素（等比缩放，居中留白）。"""

    width: float
    height: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    padding: float = 18.0

    def to_pixel(self, x: float, y: float) -> tuple[float, float]:
        """毫米坐标 → 画布像素（y 轴向上为正）。"""
        span_x = max(self.max_x - self.min_x, 1e-9)
        span_y = max(self.max_y - self.min_y, 1e-9)
        usable_width = max(self.width - 2 * self.padding, 1.0)
        usable_height = max(self.height - 2 * self.padding, 1.0)
        scale = min(usable_width / span_x, usable_height / span_y)
        center_x = self.width / 2.0
        center_y = self.height / 2.0
        mid_x = (self.min_x + self.max_x) / 2.0
        mid_y = (self.min_y + self.max_y) / 2.0
        return center_x + (x - mid_x) * scale, center_y - (y - mid_y) * scale

    def polyline(self, points: Sequence[tuple[float, float]]) -> list[float]:
        """折线像素坐标扁平列表，供 ``Canvas.create_line`` 使用。"""
        rows: list[float] = []
        for x, y in points:
            pixel_x, pixel_y = self.to_pixel(x, y)
            rows.extend((pixel_x, pixel_y))
        return rows


def _symmetric_map(section: CrossSection, width: float, height: float) -> CanvasMap:
    half = section.half_extent_mm if section.half_extent_mm > 0 else 1.0
    return CanvasMap(width, height, -half, half, -half, half)


class ParamcadWindow:  # pragma: no cover - 需要真实 Tk 窗口
    """主窗口：左侧三个预览页，右侧参数摘要。"""

    def __init__(self, settings_path: Path | None = None, model_file: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._model: TubeModel | None = None
        self._model_path = Path(self._settings.model_path) if self._settings.model_path else None
        self.root, self._theme = _guiwindow.create_root(
            title=f"参数化建模预览 v{__version__}",
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
        ttk.Button(toolbar, text="打开模型…", command=self._choose_file, style="Accent.TButton").pack(side="left")
        ttk.Button(toolbar, text="载入示例", command=self._load_example).pack(side="left", padx=(spacing.sm, 0))
        ttk.Button(toolbar, text="导出模型…", command=self._export).pack(side="left", padx=(spacing.sm, 0))
        self._status = ttk.Label(toolbar, text="载入模型或示例后预览", style="Muted.TLabel")
        self._status.pack(side="right")

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=spacing.md, pady=(spacing.xs, spacing.md))

        left = ttk.Frame(body)
        right = ttk.Frame(body, width=360)
        body.add(left, weight=4)
        body.add(right, weight=1)

        self._notebook = ttk.Notebook(left)
        self._notebook.pack(fill="both", expand=True)
        self._section_canvas = tk.Canvas(self._notebook, background=palette.surface, highlightthickness=0)
        self._axial_canvas = tk.Canvas(self._notebook, background=palette.surface, highlightthickness=0)
        self._curve_canvas = tk.Canvas(self._notebook, background=palette.surface, highlightthickness=0)
        self._notebook.add(self._section_canvas, text="二维截面")
        self._notebook.add(self._axial_canvas, text="轴向剖面")
        self._notebook.add(self._curve_canvas, text="分布曲线")
        for canvas in (self._section_canvas, self._axial_canvas, self._curve_canvas):
            canvas.bind("<Configure>", lambda _event: self._redraw())

        self._summary = _guiwindow.create_log(right, height=20)

    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开模型…", command=self._choose_file)
        file_menu.add_command(label="载入示例", command=self._load_example)
        file_menu.add_command(label="导出模型…", command=self._export)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        from tkinter import filedialog

        selected = filedialog.askopenfilename(
            title="选择 dynacalc 模型文件",
            filetypes=[("模型 YAML", "*.yaml *.yml"), ("全部文件", "*.*")],
        )
        if selected:
            self._load_path(Path(selected))

    def _load_example(self) -> None:
        self._set_model(_export.example_model(), path=None)

    def _load_path(self, path: Path) -> None:
        try:
            models = _export.load_models(path)
        except ParamcadError as exc:
            self._show_error(str(exc))
            return
        self._set_model(models[0], path=path, extra=len(models))

    def _set_model(self, model: TubeModel, *, path: Path | None, extra: int = 1) -> None:
        self._model = model
        self._model_path = path
        self._render_summary()
        self._redraw()
        location = str(path) if path is not None else "示例（内存）"
        suffix = f"，文件含 {extra} 个模型，显示第一个" if extra > 1 else ""
        self._status.configure(text=f"{model.model_id} · {location}{suffix}")

    def _render_summary(self) -> None:
        if self._model is None:
            return
        _guiwindow.clear_log(self._summary)
        for line in format_summary(self._model).splitlines():
            _guiwindow.append_log(self._summary, line)

    def _export(self) -> None:
        if self._model is None:
            self._show_error("请先载入模型或示例")
            return
        from tkinter import filedialog, messagebox

        suggested = f"{self._model.model_id}.yaml"
        selected = filedialog.asksaveasfilename(
            title="导出 dynacalc 模型",
            defaultextension=".yaml",
            initialfile=suggested,
            filetypes=[("模型 YAML", "*.yaml"), ("全部文件", "*.*")],
        )
        if not selected:
            return
        target = Path(selected)
        overwrite = False
        if target.exists():
            overwrite = messagebox.askyesno("覆盖确认", f"{target} 已存在，是否覆盖？")
            if not overwrite:
                return
        try:
            _export.export_model(self._model, target, overwrite=overwrite)
        except ParamcadError as exc:
            self._show_error(str(exc))
            return
        self._status.configure(text=f"已导出：{target}")
        messagebox.showinfo("导出完成", f"已写入\n{target}\n\n指纹：{_export.model_digest(self._model)}")

    def _show_error(self, message: str) -> None:
        from tkinter import messagebox

        messagebox.showerror("paramcad", message)

    def _show_about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "关于",
            "paramcad —— 参数化建模与几何预览\n"
            "只产出 dynacalc-model/1 模型描述，不求解、不生成网格、不输出关键字。\n"
            f"导出契约版本：{_export.SCHEMA}",
        )

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _redraw(self) -> None:
        if self._model is None:
            for canvas in (self._section_canvas, self._axial_canvas, self._curve_canvas):
                self._clear(canvas)
                self._center_text(canvas, "载入模型或示例后显示预览")
            return
        self._draw_section(_preview.section_at(self._model))
        self._draw_axial(_preview.axial_section(self._model))
        self._draw_curves(_preview.distribution_curves(self._model))

    def _clear(self, canvas: Any) -> None:
        canvas.delete("all")

    def _size(self, canvas: Any) -> tuple[float, float]:
        return float(max(canvas.winfo_width(), 2)), float(max(canvas.winfo_height(), 2))

    def _center_text(self, canvas: Any, text: str) -> None:
        width, height = self._size(canvas)
        canvas.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=self._theme.palette.muted,
            font=_guitheme.HEADING_FONT,
        )

    def _draw_section(self, section: CrossSection) -> None:
        canvas = self._section_canvas
        self._clear(canvas)
        palette = self._theme.palette
        if not section.available:
            self._center_text(canvas, section.note)
            return
        width, height = self._size(canvas)
        mapper = _symmetric_map(section, width, height)
        outer = mapper.polyline(section.outer)
        inner = mapper.polyline(section.inner)
        canvas.create_polygon(outer, fill=palette.surface_active, outline=palette.accent, width=2)
        canvas.create_polygon(inner, fill=palette.background, outline=palette.border_strong, width=1)
        if section.propellant:
            canvas.create_polygon(
                mapper.polyline(section.propellant),
                fill=palette.warning,
                stipple="gray25",
                outline=palette.warning,
            )
        center_x, center_y = mapper.to_pixel(0.0, 0.0)
        canvas.create_line(center_x, 0, center_x, height, fill=palette.border, dash=(4, 6))
        canvas.create_line(0, center_y, width, center_y, fill=palette.border, dash=(4, 6))

        for annotation in section.annotations:
            self._draw_section_annotation(canvas, mapper, annotation)

        half = section.half_extent_mm
        canvas.create_text(
            12,
            12,
            anchor="nw",
            fill=palette.text,
            font=_guitheme.HEADING_FONT,
            text=f"{'拉拔后' if section.state_label == 'after' else '拉拔前'}截面 · 外径 {2 * half:.1f} mm",
        )
        if section.note:
            canvas.create_text(
                12,
                height - 12,
                anchor="sw",
                fill=palette.warning,
                font=_guitheme.SMALL_FONT,
                text=section.note,
                width=max(width - 24, 100),
            )

    def _draw_section_annotation(self, canvas: Any, mapper: CanvasMap, annotation: Any) -> None:
        import math

        palette = self._theme.palette
        half = max(mapper.max_x, abs(mapper.min_x))
        radius = half * 1.02
        points: list[float] = []
        span = (annotation.end_deg - annotation.start_deg) % 360.0 or 6.0
        steps = max(int(span / 6.0), 2)
        for index in range(steps + 1):
            degree = annotation.start_deg + span * index / steps
            x = radius * math.cos(math.radians(degree))
            y = radius * math.sin(math.radians(degree))
            points.extend(mapper.to_pixel(x, y))
        canvas.create_line(points, fill=palette.danger, width=4)
        mid = annotation.start_deg + span / 2.0
        label_x, label_y = mapper.to_pixel(
            radius * 1.12 * math.cos(math.radians(mid)),
            radius * 1.12 * math.sin(math.radians(mid)),
        )
        canvas.create_text(label_x, label_y, text=annotation.label, fill=palette.danger, font=_guitheme.SMALL_FONT)

    def _draw_axial(self, section: AxialSection) -> None:
        canvas = self._axial_canvas
        self._clear(canvas)
        palette = self._theme.palette
        if not section.available:
            self._center_text(canvas, section.note)
            return
        width, height = self._size(canvas)
        half = section.half_extent_mm if section.half_extent_mm > 0 else 1.0
        mapper = CanvasMap(width, height, 0.0, max(section.length_mm, 1e-6), -half, half)
        self._polyline(canvas, mapper, section.outer_top, palette.accent, 2)
        self._polyline(canvas, mapper, section.outer_bottom, palette.accent, 2)
        self._polyline(canvas, mapper, section.inner_top, palette.border_strong, 1)
        self._polyline(canvas, mapper, section.inner_bottom, palette.border_strong, 1)
        if section.propellant_top and section.propellant_bottom:
            self._polyline(canvas, mapper, section.propellant_top, palette.warning, 1)
            self._polyline(canvas, mapper, section.propellant_bottom, palette.warning, 1)
        for annotation in section.annotations:
            x, _ = mapper.to_pixel(section.length_mm * annotation.position, 0.0)
            canvas.create_line(x, 8, x, height - 8, fill=palette.danger, dash=(3, 5))
            canvas.create_text(
                x + 4,
                16,
                anchor="nw",
                text=annotation.label,
                fill=palette.danger,
                font=_guitheme.SMALL_FONT,
            )
        canvas.create_text(
            12,
            12,
            anchor="nw",
            fill=palette.text,
            font=_guitheme.HEADING_FONT,
            text=f"轴向剖面 · L={section.length_mm:.1f} mm（径向已放大，非等比）",
        )
        if section.note:
            canvas.create_text(
                12,
                height - 12,
                anchor="sw",
                fill=palette.warning,
                font=_guitheme.SMALL_FONT,
                text=section.note,
                width=max(width - 24, 100),
            )

    def _polyline(
        self, canvas: Any, mapper: CanvasMap, points: Sequence[tuple[float, float]], color: str, width: int
    ) -> None:
        if points:
            canvas.create_line(mapper.polyline(points), fill=color, width=width)

    def _draw_curves(self, curves: Sequence[Curve]) -> None:
        canvas = self._curve_canvas
        self._clear(canvas)
        palette = self._theme.palette
        if not curves:
            self._center_text(canvas, "没有可绘制的分布曲线")
            return
        width, height = self._size(canvas)
        band = height / len(curves)
        for index, curve in enumerate(curves):
            top = index * band
            self._draw_curve_band(canvas, curve, (top, top + band, width), palette)
        if len(curves) > 1:
            for index in range(1, len(curves)):
                canvas.create_line(0, index * band, width, index * band, fill=palette.border)

    def _draw_curve_band(
        self,
        canvas: Any,
        curve: Curve,
        band: tuple[float, float, float],
        palette: Any,
    ) -> None:
        top, bottom, width = band
        if not curve.points:
            return
        min_x = min(point[0] for point in curve.points)
        max_x = max(point[0] for point in curve.points)
        min_y = min(point[1] for point in curve.points)
        max_y = max(point[1] for point in curve.points)
        if max_x == min_x:
            max_x = min_x + 1.0
        if max_y == min_y:
            max_y = min_y + 1.0
        mapper = CanvasMap(width, bottom - top, min_x, max_x, min_y, max_y, padding=14.0)
        rows = mapper.polyline(curve.points)
        shifted = [rows[i] if i % 2 == 0 else rows[i] + top for i in range(len(rows))]
        canvas.create_line(shifted, fill=palette.accent, width=2)
        canvas.create_text(
            10,
            top + 8,
            anchor="nw",
            fill=palette.text,
            font=_guitheme.BODY_FONT,
            text=f"{curve.title}（{curve.x_label} / {curve.y_label}）",
        )
        canvas.create_text(
            10,
            top + 24,
            anchor="nw",
            fill=palette.muted,
            font=_guitheme.SMALL_FONT,
            text=f"来源：{curve.source}" + (f" · {curve.note}" if curve.note else ""),
        )
        canvas.create_text(
            width - 10,
            top + 8,
            anchor="ne",
            fill=palette.muted,
            font=_guitheme.SMALL_FONT,
            text=f"{min_y:.3g} – {max_y:.3g}",
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        try:
            geometry = self.root.winfo_geometry()
        except Exception:
            geometry = None
        save_settings(
            Settings(
                geometry=_guiconfig.valid_geometry(geometry),
                model_path=str(self._model_path) if self._model_path is not None else "",
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(model: Path | None = None, settings_path: Path | None = None) -> None:
    """启动 Tk 窗口；tkinter 缺失时由 :func:`fcmd.gui.window.run_window` 提示。"""
    _guiwindow.run_window(lambda: ParamcadWindow(settings_path, model), "参数化建模预览")


@fcmd.tool("paramcadw", help="打开参数化建模预览窗口（Tk）", description=_DESCRIPTION)
def launch_cmd(model_file: Path | None = None) -> None:
    """打开预览窗口，可传入要直接加载的模型文件。

    Parameters
    ----------
    model_file:
        启动时加载的 ``dynacalc-model/1`` YAML；省略时恢复上次文件或打开空窗口
    """
    run_gui(model=model_file)


@fcmd.main("paramcadw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体


if __name__ == "__main__":  # pragma: no cover
    main()
