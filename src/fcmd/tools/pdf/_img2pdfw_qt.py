"""img2pdfw 的 Qt 主窗口与入口。

窗口负责收集输入 / 输出、页面尺寸、DPI 与 EXIF 校正，转换仍由
:mod:`fcmd.tools.pdf.img2pdf` 完成；批量转换放进 ``QThreadPool``，进度经
信号桥排队回 GUI 线程。

布局口径：输入 / 输出 / 选项三张卡片，路径输入整行拉伸并在下方用
:class:`ElidedLabel` 显示完整路径；进度条与日志占满剩余宽度，窗口变矮时
整页进 :class:`PageScroll`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)

from fcmd import __version__
from fcmd.gui.geometry import size_from_geometry
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, ElidedLabel, FormGrid, ProgressRow, button, hint_label
from fcmd.gui.qt.workers import EventBridge as _ConvertBridge
from fcmd.gui.qt.workers import submit
from fcmd.tools.pdf.img2pdf import collect_pdf_images, images_to_pdf, resolve_pdf_output
from fcmd.tools.pdf.img2pdfw import (
    DEFAULT_DPI,
    DEFAULT_GEOMETRY,
    MIN_HEIGHT,
    MIN_WIDTH,
    PAGE_SIZE_OPTIONS,
    Img2PdfSettings,
    default_output_path,
    format_page_size,
    load_settings,
    normalize_page,
    parse_dpi,
    save_settings,
)

__all__ = ["Img2PdfWindow", "run_qt"]

_IMAGE_FILTER = "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tif *.tiff *.ico);;所有文件 (*.*)"


def _run_conversion(  # noqa: PLR0913, PLR0917 - 转换参数逐项显式传递，便于对照既有行为
    input_path: Path,
    output_path: Path,
    page_size: str,
    dpi: int,
    normalize: bool,
    bridge: _ConvertBridge,
) -> None:
    """线程池里执行图片合并，把进度与结果投回界面。"""

    def progress(done: int, total: int, message: str) -> None:
        bridge.event.emit(("progress", done, total, message))

    try:
        images = collect_pdf_images([input_path])
        bridge.event.emit(("message", f"找到 {len(images)} 张图片"))
        result = images_to_pdf(
            images,
            output_path,
            page_size=page_size,
            dpi=dpi,
            normalize=normalize,
            progress=progress,
        )
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("done", result, len(images)))


class Img2PdfWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """图片转 PDF 转换器主窗口。"""

    def __init__(self) -> None:
        self._settings = load_settings()
        self._busy = False
        self._bridge: _ConvertBridge | None = None
        super().__init__(
            title=f"图片转 PDF 转换器 v{__version__}",
            config_name="img2pdfw",
            default_size=size_from_geometry(DEFAULT_GEOMETRY),
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
            scroll=True,
        )
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """输入 / 输出 / 选项 / 操作行 / 进度 / 日志。"""
        self.add(self._build_input_card())
        self.add(self._build_output_card())
        self.add(self._build_options_card())

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        self._convert_button = button("开始转换", kind="primary")
        self._convert_button.clicked.connect(self._start_conversion)
        actions.addWidget(self._convert_button)
        self.add_layout(actions)

        self._progress_row = ProgressRow(parent=self)
        self._progress = self._progress_row.bar
        self.add(self._progress_row)

        self._log = self.add_log(height=180)
        self._log.append("选择图片或目录，设置页面尺寸与 DPI 后点击「开始转换」。")

    def _build_input_card(self) -> Card:
        """输入行：可手填，也可分别按「图片…」「目录…」挑选。"""
        card = Card("输入", self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._input = QLineEdit(self._settings.input_path, card)
        self._input.setPlaceholderText("图片文件或目录")
        self._input.textChanged.connect(self._refresh_input_hint)
        row.addWidget(self._input, 1)
        self._browse_file_button = button("图片…")
        self._browse_file_button.clicked.connect(self._browse_input_file)
        row.addWidget(self._browse_file_button)
        self._browse_dir_button = button("目录…")
        self._browse_dir_button.clicked.connect(self._browse_input_dir)
        row.addWidget(self._browse_dir_button)
        card.body.addLayout(row)
        self._input_hint = ElidedLabel("", parent=card)
        self._input_hint.setObjectName("Mono")
        card.body.addWidget(self._input_hint)
        self._refresh_input_hint(self._input.text())
        return card

    def _build_output_card(self) -> Card:
        """输出行：留空时按输入类型自动命名。"""
        card = Card("输出", self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._output = QLineEdit(self._settings.output_path, card)
        self._output.setPlaceholderText("输出 PDF 路径（留空自动决定）")
        self._output.textChanged.connect(self._refresh_output_hint)
        row.addWidget(self._output, 1)
        self._browse_output_button = button("浏览…")
        self._browse_output_button.clicked.connect(self._browse_output)
        row.addWidget(self._browse_output_button)
        card.body.addLayout(row)
        self._output_hint = ElidedLabel("", parent=card)
        self._output_hint.setObjectName("Hint")
        card.body.addWidget(self._output_hint)
        self._refresh_output_hint(self._output.text())
        card.body.addWidget(hint_label("留空时：目录输入输出到 <目录名>.pdf，单文件输出到同名 PDF"))
        return card

    def _build_options_card(self) -> Card:
        """页面尺寸、DPI 与 EXIF 校正。"""
        card = Card("选项", self)
        grid = FormGrid(card)
        self._page_size = QComboBox(card)
        self._page_size.addItems(list(PAGE_SIZE_OPTIONS))
        self._page_size.setCurrentText(format_page_size(self._settings.page_size))
        self._dpi = QSpinBox(card)
        self._dpi.setRange(36, 1200)
        self._dpi.setValue(self._settings.dpi)
        grid.add_pair("页面尺寸", self._page_size, "DPI", self._dpi)
        self._normalize = QCheckBox("校正 EXIF 方向", card)
        self._normalize.setChecked(self._settings.normalize)
        card.body.addWidget(grid)
        card.body.addWidget(self._normalize)
        return card

    def _refresh_input_hint(self, text: str) -> None:
        """显示当前输入路径（长路径中间省略，悬停看全文）。"""
        self._input_hint.setText(str(Path(text).expanduser()) if text.strip() else "未选择输入")

    def _refresh_output_hint(self, text: str) -> None:
        """显示输出路径；留空时提示自动命名规则。"""
        self._output_hint.setText(f"输出到：{Path(text).expanduser()}" if text.strip() else "输出位置：留空时自动命名")

    # ------------------------------------------------------------------ #
    # 选择路径
    # ------------------------------------------------------------------ #
    def _browse_input_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "选择输入图片", self._input.text(), _IMAGE_FILTER)
        if filename:
            self._input.setText(filename)

    def _browse_input_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输入目录", self._input.text())
        if directory:
            self._input.setText(directory)

    def _browse_output(self) -> None:
        """按输入给出建议输出路径后再选目标。"""
        raw_input = self._input.text().strip()
        input_path = Path(raw_input).expanduser() if raw_input else None
        suggested = (
            default_output_path(input_path) if input_path is not None and input_path.exists() else Path("output.pdf")
        )
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "选择输出 PDF",
            str(suggested) if suggested.parent.exists() else suggested.name,
            "PDF 文件 (*.pdf);;所有文件 (*.*)",
        )
        if filename:
            self._output.setText(filename)

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用控件，避免重复提交。"""
        self._busy = busy
        for widget in (
            self._convert_button,
            self._browse_file_button,
            self._browse_dir_button,
            self._browse_output_button,
            self._input,
            self._output,
            self._page_size,
            self._dpi,
            self._normalize,
        ):
            widget.setEnabled(not busy)

    def _start_conversion(self) -> None:
        """校验输入后在后台启动转换。"""
        if self._busy:
            return

        raw_input = self._input.text().strip()
        if not raw_input:
            self._log.append("错误：请选择输入图片或目录")
            return
        input_path = Path(raw_input).expanduser()
        if not input_path.exists():
            self._log.append(f"错误：输入路径不存在 {input_path}")
            return

        try:
            page_size = normalize_page(self._page_size.currentText())
            dpi = parse_dpi(self._dpi.value())
        except ValueError as exc:
            self._log.append(f"错误：{exc}")
            return

        raw_output = self._output.text().strip()
        output_path = Path(raw_output).expanduser() if raw_output else default_output_path(input_path)
        try:
            output_path = resolve_pdf_output(output_path)
        except ValueError as exc:
            self._log.append(f"错误：{exc}")
            return

        self._log.clear()
        self._set_busy(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self.set_status("正在转换…")
        self._log.append(f"输入：{input_path}")
        self._log.append(f"输出：{output_path}")
        self._log.append(f"页面尺寸：{page_size}；DPI：{dpi}")

        bridge = _ConvertBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(
            _run_conversion,
            input_path,
            output_path,
            page_size,
            dpi,
            self._normalize.isChecked(),
            bridge,
        )

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：消息、进度、完成、失败。"""
        kind = event[0]
        if kind == "message":
            self._log.append(event[1])
        elif kind == "progress":
            _, done, total, message = event
            self._progress.setRange(0, max(total, 1))
            self._progress.setValue(done)
            self._log.append(message)
        elif kind == "done":
            _, output, total = event
            self._progress.setRange(0, max(total, 1))
            self._progress.setValue(total)
            self._log.append(f"完成：{total} 页 -> {output}")
            self.set_status(f"完成：{total} 页")
            self._finish()
            QMessageBox.information(self, "转换完成", f"已生成 PDF：\n{output}")
        elif kind == "error":
            self._log.append(f"错误：{event[1]}")
            self.set_status("转换失败")
            self._finish()
            QMessageBox.critical(self, "转换失败", event[1])

    def _finish(self) -> None:
        """任务结束：恢复控件并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回输入 / 输出 / 页面尺寸 / DPI / 校正与窗口 geometry。"""
        try:
            page_size = normalize_page(self._page_size.currentText())
        except ValueError:
            page_size = "auto"
        try:
            dpi = parse_dpi(self._dpi.value())
        except ValueError:
            dpi = DEFAULT_DPI
        save_settings(
            Img2PdfSettings(
                input_path=self._input.text(),
                output_path=self._output.text(),
                page_size=page_size,
                dpi=dpi,
                normalize=self._normalize.isChecked(),
                geometry=geometry,
            )
        )


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(Img2PdfWindow, app_name="img2pdfw")
