"""parameshw 的 Qt 主窗口：左侧线框画布，右侧网格档位与摘要。

网格生成、质量统计与线框变换仍全部位于 :mod:`fcmd.apps.paramesh._mesh` /
``_quality`` / ``_preview``，窗口只负责取数与绘制；画布用 ``QPainter`` 直接
画线段，颜色一律从当前主题的 ``Palette`` 取，切换主题即时重绘。

布局口径：工具栏按钮在一行内靠左，窗口变窄时右侧面板仍保底宽度；
画布与摘要用 ``QSplitter`` 分隔，分栏比例写入 ``ui/paramesh.json`` 并
在下次启动恢复；画布内容始终按可用像素重算 ``fit_mapper``，缩放窗口不裁切。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPaintEvent, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd import __version__
from fcmd.apps.paramesh import paramesh as _cli
from fcmd.apps.paramesh._config import LEVELS, MeshConfig, load_config, parse_config
from fcmd.apps.paramesh._errors import ParameshError
from fcmd.apps.paramesh._mesh import Mesh, build_mesh
from fcmd.apps.paramesh._model import MeshModel, format_model_summary, load_models, parse_models
from fcmd.apps.paramesh._package import mesh_id, write_package
from fcmd.apps.paramesh._preview import ViewMapper, Wireframe, fit_mapper, pixel_segments, wireframe, zoom_at
from fcmd.apps.paramesh._quality import QualityReport, format_quality, quality_report
from fcmd.apps.paramesh._scale import format_plan
from fcmd.apps.paramesh.parameshw import (
    DEFAULT_GEOMETRY,
    MAX_ZOOM,
    MIN_HEIGHT,
    MIN_WIDTH,
    MIN_ZOOM,
    Settings,
    load_settings,
    region_color,
    save_settings,
)
from fcmd.gui.geometry import size_from_geometry
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import (
    LogView,
    apply_splitter_sizes,
    button,
    make_splitter,
    splitter_sizes,
)

__all__ = ["ParameshWindow", "run_qt"]

_DEFAULT_SPLIT = (800, 380)


class _PreviewCanvas(QWidget):  # pragma: no cover - Qt 控件
    """线框画布：绘制委托给窗口，滚轮缩放。"""

    def __init__(self, owner: ParameshWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002 - Qt 回调签名
        self._owner.paint_canvas(self)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        position = event.position()
        self._owner.zoom_at_wheel(position.x(), position.y(), 1.15 if delta > 0 else 1 / 1.15)
        event.accept()


class ParameshWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """网格预览主窗口。"""

    def __init__(self, model_file: Path | None = None, config_file: Path | None = None) -> None:
        self._settings = load_settings()
        self._level = self._settings.level
        self._model: MeshModel | None = None
        self._config: MeshConfig = MeshConfig()
        self._mesh: Mesh | None = None
        self._report: QualityReport | None = None
        self._frame: Wireframe | None = None
        self._zoom = 1.0
        self._focus: tuple[float, float] | None = None
        self._model_path = Path(self._settings.model_path) if self._settings.model_path else None
        self._config_path = Path(self._settings.config_path) if self._settings.config_path else None
        super().__init__(
            title=f"网格预览 v{__version__}",
            config_name="paramesh",
            default_size=size_from_geometry(DEFAULT_GEOMETRY),
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
        )
        self._build()
        self._install_menu_actions()
        if config_file is not None:
            self._apply_config_file(config_file)
        initial = model_file or (self._model_path if self._model_path and self._model_path.is_file() else None)
        if initial is not None:
            QTimer.singleShot(60, lambda: self._load_path(initial))

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """工具栏 + 画布 / 摘要分栏。"""
        toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(6)
        open_model = button("打开模型…", kind="primary")
        open_model.clicked.connect(self._choose_model)
        toolbar_layout.addWidget(open_model)
        open_config = button("打开配置…")
        open_config.clicked.connect(self._choose_config)
        toolbar_layout.addWidget(open_config)
        example = button("载入示例")
        example.clicked.connect(self._load_example)
        toolbar_layout.addWidget(example)
        export = button("导出三档网格…")
        export.clicked.connect(self._export)
        toolbar_layout.addWidget(export)
        fit = button("适应窗口")
        fit.clicked.connect(self._fit)
        toolbar_layout.addWidget(fit)
        zoom_in = button("放大")
        zoom_in.clicked.connect(lambda: self._zoom_by(1.25))
        toolbar_layout.addWidget(zoom_in)
        zoom_out = button("缩小")
        zoom_out.clicked.connect(lambda: self._zoom_by(1 / 1.25))
        toolbar_layout.addWidget(zoom_out)
        toolbar_layout.addStretch(1)
        self.add(toolbar)

        self._splitter = make_splitter(Qt.Orientation.Horizontal, parent=self)
        self._canvas = _PreviewCanvas(self, self._splitter)
        self._splitter.addWidget(self._canvas)
        self._splitter.addWidget(self._build_side_panel())
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 1)
        sizes = list(self._settings.splitter) if self._settings.splitter else list(_DEFAULT_SPLIT)
        apply_splitter_sizes(self._splitter, tuple(sizes))
        self.body.addWidget(self._splitter, 1)
        self.set_status("载入模型或示例后预览")

    def _build_side_panel(self) -> QWidget:
        """右侧：档位选择 + 模型 / 尺度 / 质量摘要。"""
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        level_row = QHBoxLayout()
        level_row.setContentsMargins(0, 0, 0, 0)
        level_row.addWidget(QLabel("网格档位", panel))
        self._level_box = QComboBox(panel)
        self._level_box.addItems(list(LEVELS))
        self._level_box.setCurrentText(self._level)
        self._level_box.currentTextChanged.connect(self._on_level_change)
        level_row.addWidget(self._level_box)
        level_row.addStretch(1)
        layout.addLayout(level_row)
        self._summary = LogView(height=420, parent=panel)
        layout.addWidget(self._summary, 1)
        return panel

    def _install_menu_actions(self) -> None:
        """把文件 / 视图动作插入骨架菜单，位置与迁移前的窗口一致。"""
        from PySide6.QtGui import QAction

        file_menu = self._menu("文件")
        insert_at = file_menu.actions()[0] if file_menu.actions() else None
        for text, slot in (
            ("打开模型…", self._choose_model),
            ("打开配置…", self._choose_config),
            ("载入示例", self._load_example),
            ("导出三档网格…", self._export),
        ):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if insert_at is not None:
                file_menu.insertAction(insert_at, action)
            else:
                file_menu.addAction(action)
        if insert_at is not None:
            file_menu.insertSeparator(insert_at)

        view_menu = self._menu("视图")
        theme_action = view_menu.actions()[0] if view_menu.actions() else None
        for text, slot in (
            ("适应窗口", self._fit),
            ("放大", lambda: self._zoom_by(1.25)),
            ("缩小", lambda: self._zoom_by(1 / 1.25)),
        ):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if theme_action is not None:
                view_menu.insertAction(theme_action, action)
            else:
                view_menu.addAction(action)
        if theme_action is not None:
            view_menu.insertSeparator(theme_action)

    def _menu(self, title: str) -> QMenu:
        """按标题取出骨架菜单。"""
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is not None and action.text() == title:
                return menu
        raise LookupError(f"缺少菜单：{title}")

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def _choose_model(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 dynacalc 模型文件",
            "",
            "模型 YAML (*.yaml *.yml);;全部文件 (*.*)",
        )
        if selected:
            self._load_path(Path(selected))

    def _choose_config(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "选择 paramesh 网格配置",
            "",
            "网格配置 YAML (*.yaml *.yml);;全部文件 (*.*)",
        )
        if selected:
            self._apply_config_file(Path(selected))

    def _apply_config_file(self, path: Path) -> None:
        """读取网格配置并重建；失败时弹窗且保持原配置。"""
        try:
            config, _warnings = load_config(path)
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        self._config = config
        self._config_path = path
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
        self.set_status(f"{model.model_id} · {location}")

    def _on_level_change(self, level: str) -> None:
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
            self._canvas.update()
            return
        self._mesh = mesh
        self._report = quality_report(mesh)
        self._frame = wireframe(mesh)
        self._zoom = 1.0
        self._focus = None
        self._render_summary()
        self._canvas.update()
        self.set_status(f"{mesh.model.model_id} · {mesh.level} · 节点 {mesh.node_count} · 单元 {mesh.element_count}")

    def _render_summary(self) -> None:
        if self._mesh is None or self._report is None:
            return
        self._summary.clear()
        lines = [format_model_summary(self._mesh.model), "", "— 尺度计划 —", format_plan(self._mesh.plan), ""]
        lines.extend(["— 质量报告 —", format_quality(self._report)])
        for line in lines:
            self._summary.append(line)

    def _render_summary_error(self, message: str) -> None:
        self._summary.clear()
        self._summary.append(f"网格生成失败：{message}")

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paint_canvas(self, widget: QWidget) -> None:
        """用 ``QPainter`` 画线框、图例与标题（尺寸取控件逻辑像素）。"""
        painter = QPainter(widget)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.theme().palette
        painter.fillRect(widget.rect(), QColor(palette.surface))
        width = float(max(widget.width(), 2))
        height = float(max(widget.height(), 2))
        if self._frame is None:
            self._paint_centered(painter, widget, "载入模型或示例后显示网格线框", QColor(palette.muted))
            return
        mapper = fit_mapper(self._frame, width, height, zoom=self._zoom, focus=self._focus)
        for x1, y1, x2, y2, region in pixel_segments(self._frame, mapper):
            painter.setPen(QPen(QColor(region_color(region, palette)), 1))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        self._paint_legend(painter, width, QColor(palette.muted))
        title_font = QFont(painter.font())
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() + 2)
        painter.setFont(title_font)
        painter.setPen(QColor(palette.text))
        painter.drawText(QPointF(12, 24), f"二维轴对称网格 · {self._level} · 缩放 {mapper.zoom:.2f}×")
        small_font = QFont(painter.font())
        small_font.setBold(False)
        small_font.setPointSizeF(max(title_font.pointSizeF() - 2, 7))
        painter.setFont(small_font)
        painter.setPen(QColor(palette.muted))
        if self._mesh is not None:
            painter.drawText(
                QPointF(12, 44),
                f"r 0–{self._mesh.model.outer_radius:g} mm · z 0–{self._mesh.model.length:g} mm",
            )

    def _paint_legend(self, painter: QPainter, width: float, color: QColor) -> None:
        """右上角图例：区域名 + 单元数，右对齐。"""
        if self._mesh is None:
            return
        metrics = QFontMetrics(painter.font())
        for index, region in enumerate(self._mesh.regions):
            text = f"■ {region.name}（{region.element_count} 单元）"
            painter.setPen(QColor(region_color(region.name, self.theme().palette)))
            painter.drawText(QPointF(width - 12 - metrics.horizontalAdvance(text), 20 + index * 18), text)
        painter.setPen(color)

    def _paint_centered(self, painter: QPainter, widget: QWidget, text: str, color: QColor) -> None:
        """居中提示文字。"""
        font = QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 2)
        painter.setFont(font)
        painter.setPen(color)
        metrics = QFontMetrics(font)
        rect = QRectF(widget.rect())
        x = rect.center().x() - metrics.horizontalAdvance(text) / 2
        y = rect.center().y() + metrics.ascent() / 2
        painter.drawText(QPointF(x, y), text)

    # ------------------------------------------------------------------ #
    # 视图
    # ------------------------------------------------------------------ #
    def _fit(self) -> None:
        self._zoom = 1.0
        self._focus = None
        self._canvas.update()

    def _zoom_by(self, factor: float) -> None:
        self._zoom_at(factor, self._canvas.width() / 2.0, self._canvas.height() / 2.0)

    def zoom_at_wheel(self, x: float, y: float, factor: float) -> None:
        """滚轮缩放：以光标位置为中心。"""
        self._zoom_at(factor, x, y)

    def _mapper(self) -> ViewMapper | None:
        if self._frame is None:
            return None
        return fit_mapper(
            self._frame,
            float(max(self._canvas.width(), 2)),
            float(max(self._canvas.height(), 2)),
            zoom=self._zoom,
            focus=self._focus,
        )

    def _zoom_at(self, factor: float, x: float, y: float) -> None:
        mapper = self._mapper()
        if mapper is None:
            return
        zoomed = zoom_at(mapper, factor, x, y)
        self._zoom = min(max(zoomed.zoom, MIN_ZOOM), MAX_ZOOM)
        self._focus = (zoomed.focus_r, zoomed.focus_z)
        self._canvas.update()

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def _export(self) -> None:
        if self._model is None:
            self._show_error("请先载入模型或示例")
            return
        selected = QFileDialog.getExistingDirectory(self, "选择网格包输出目录")
        if not selected:
            return
        target = Path(selected)
        try:
            meshes = [build_mesh(self._model, self._config, level) for level in LEVELS]
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        existing = [target / mesh_id(mesh) for mesh in meshes]
        overwrite = False
        if any(path.exists() for path in existing):
            answer = QMessageBox.question(self, "覆盖确认", "目标目录下已有同名网格包，是否覆盖？")
            if answer != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        paths: list[Path] = []
        try:
            for mesh in meshes:
                paths.append(write_package(mesh, quality_report(mesh), target, overwrite=overwrite))
        except ParameshError as exc:
            self._show_error(str(exc))
            return
        self.set_status(f"已导出 {len(paths)} 档网格到 {target}")
        QMessageBox.information(self, "导出完成", "已写入：\n" + "\n".join(str(path) for path in paths))

    # ------------------------------------------------------------------ #
    # 其它
    # ------------------------------------------------------------------ #
    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "paramesh", message)

    def _persist(self, geometry: str) -> None:
        """关闭时写回 geometry、上次模型 / 配置、档位与分栏比例。"""
        save_settings(
            Settings(
                geometry=geometry,
                model_path=str(self._model_path) if self._model_path is not None else "",
                config_path=str(self._config_path) if self._config_path is not None else "",
                level=self._level,
                splitter=splitter_sizes(self._splitter),
            )
        )


def run_qt(model: Path | None = None, config: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: ParameshWindow(model, config), app_name="parameshw")
