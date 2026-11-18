"""pdfsplitw 的 Qt 主窗口与入口。

窗口负责收集输入 / 输出、分割模式与参数，实际切分仍由
:mod:`fcmd.tools.pdf.pdfsplit` 执行；任务放进 ``QThreadPool``，进度事件经
信号桥排队回 GUI 线程，界面不阻塞。

布局口径：内容放进 :class:`PageScroll`（窗口变矮时滚动而不是压扁控件），
输入 / 输出目录 / 提取文件都用路径行并在下方用 :class:`ElidedLabel` 显示
解析后的完整路径（中间省略 + tooltip）；页码范围模式才显示提取文件行。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QRadioButton,
    QSpinBox,
    QWidget,
)

from fcmd import __version__
from fcmd.gui.geometry import size_from_geometry
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, ElidedLabel, PathRow, button, hint_label
from fcmd.gui.qt.workers import submit
from fcmd.tools.pdf.pdfsplit import split_by_parts, split_by_range, split_by_size
from fcmd.tools.pdf.pdfsplitw import (
    DEFAULT_GEOMETRY,
    MIN_HEIGHT,
    MIN_WIDTH,
    MODE_LABELS,
    MODES,
    PdfSplitSettings,
    default_output_path,
    load_settings,
    normalize_mode,
    parse_positive_int,
    save_settings,
)

__all__ = ["PdfSplitWindow", "run_qt"]

DEFAULT_SIZE = (880, 700)
_PDF_FILTER = "PDF 文件 (*.pdf);;所有文件 (*.*)"


class _PdfInputRow(PathRow):  # pragma: no cover - Qt 控件
    """输入 PDF 行：浏览对话框限定 PDF 过滤器。"""

    def _pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 PDF 文件", self.value(), _PDF_FILTER)
        if path:
            self.edit.setText(path)


class _OutputRow(QWidget):  # pragma: no cover - Qt 控件
    """提取文件行：保存对话框 + 完整路径省略提示。"""

    def __init__(self, *, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suggestion = ""
        self._empty_hint = ""
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.edit = QLineEdit(value, self)
        self.edit.setPlaceholderText("提取输出文件（留空按文件名自动命名）")
        layout.addWidget(self.edit, 0, 0)
        self.browse = button("浏览…")
        self.browse.clicked.connect(self._pick)
        layout.addWidget(self.browse, 0, 1)
        self.hint = ElidedLabel("", parent=self)
        self.hint.setObjectName("Mono")
        layout.addWidget(self.hint, 1, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        self.edit.textChanged.connect(self._refresh_hint)
        self._refresh_hint(self.edit.text())

    def value(self) -> str:
        """当前输入文本（去首尾空白）。"""
        return self.edit.text().strip()

    def set_value(self, text: str) -> None:
        """写入输入文本。"""
        self.edit.setText(text)

    def set_suggestion(self, path: Path | None) -> None:
        """设置留空时的建议输出路径（用于提示与保存对话框默认值）。"""
        self._suggestion = str(path) if path is not None else ""
        self._empty_hint = f"留空为 {path}" if path is not None else "仅页码范围模式使用"
        self._refresh_hint(self.edit.text())

    def _refresh_hint(self, text: str) -> None:
        shown = str(Path(text).expanduser()) if text.strip() else (self._suggestion or self._empty_hint)
        self.hint.setText(shown)

    def _pick(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择提取输出文件",
            self.value() or self._suggestion,
            _PDF_FILTER,
        )
        if path:
            self.edit.setText(path)


class _SplitBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台分割线程 → GUI 的事件桥。"""

    event = Signal(object)


def _run_split(  # noqa: PLR0913, PLR0917 - 参数由界面校验结果一次性传入，保持显式
    input_path: Path,
    output_dir: Path,
    output_path: Path | None,
    mode: str,
    parts: int,
    size: int,
    pages: str,
    bridge: _SplitBridge,
) -> None:
    """线程池里执行分割，把进度事件逐条投回界面。"""

    def progress(done: int, total: int, message: str) -> None:
        bridge.event.emit(("progress", done, total, message))

    try:
        if mode == "parts":
            outputs = split_by_parts(input_path, output_dir, parts, progress=progress)
            target = output_dir
        elif mode == "size":
            outputs = split_by_size(input_path, output_dir, size, progress=progress)
            target = output_dir
        else:
            if output_path is None:
                raise ValueError("页码范围模式缺少输出路径")
            outputs = [split_by_range(input_path, output_path, pages, progress=progress)]
            target = output_path
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("done", len(outputs), str(target)))


class PdfSplitWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """PDF 分割主窗口。"""

    def __init__(self) -> None:
        self._settings = load_settings()
        self._busy = False
        self._bridge: _SplitBridge | None = None
        super().__init__(
            title=f"PDF 分割 v{__version__}",
            config_name="pdfsplitw",
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
        """输入 / 输出 / 模式 / 操作行 / 进度 / 日志。"""
        self.add(self._build_input_card())
        self.add(self._build_output_card())
        self.add(self._build_mode_card())

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        self._start_button = button("开始分割", kind="primary")
        self._start_button.clicked.connect(self._start)
        actions.addWidget(self._start_button)
        self.add_layout(actions)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self.add(self._progress)

        self._log = self.add_log(height=200)
        self._append_log("选择 PDF、设置输出目录与分割模式后点击「开始分割」。")
        self._on_mode_change()

    def _build_input_card(self) -> Card:
        """输入 PDF：路径行 + 完整路径省略提示。"""
        card = Card("输入 PDF", self)
        self._input_row = _PdfInputRow(value=self._settings.input_path, placeholder="选择待分割的 PDF", parent=card)
        self._input_row.changed.connect(self._on_input_change)
        card.body.addWidget(self._input_row)
        self._input_hint = ElidedLabel("", parent=card)
        self._input_hint.setObjectName("Mono")
        card.body.addWidget(self._input_hint)
        self._refresh_input_hint(self._input_row.value())
        return card

    def _build_output_card(self) -> Card:
        """输出目录与（页码范围模式才显示的）提取文件行。"""
        card = Card("输出", self)
        self._output_dir_row = PathRow(
            value=self._settings.output_dir,
            placeholder="输出目录（留空使用输入文件所在目录）",
            directory=True,
            parent=card,
        )
        self._output_dir_row.changed.connect(lambda _text: self._refresh_output_hints())
        card.body.addWidget(self._output_dir_row)
        self._output_dir_hint = ElidedLabel("", parent=card)
        self._output_dir_hint.setObjectName("Mono")
        card.body.addWidget(self._output_dir_hint)
        card.body.addWidget(hint_label("按份数 / 每份页数生成 <原名>_partN.pdf"))

        self._output_file_row = _OutputRow(value=self._settings.output_path, parent=card)
        card.body.addWidget(self._output_file_row)
        self._output_file_hint = hint_label("仅页码范围模式使用，留空为 <原名>_extracted.pdf")
        card.body.addWidget(self._output_file_hint)
        self._refresh_output_hints()
        return card

    def _build_mode_card(self) -> Card:
        """三种模式各占一行，只有当前模式的参数可编辑。"""
        card = Card("分割模式", self)
        self._mode_buttons: dict[str, QRadioButton] = {}
        self._parts = QSpinBox(card)
        self._parts.setRange(1, 999)
        self._parts.setValue(self._settings.parts)
        self._size = QSpinBox(card)
        self._size.setRange(1, 999)
        self._size.setValue(self._settings.size)
        self._pages = QLineEdit(self._settings.pages, card)
        self._pages.setPlaceholderText("1,3-5,7-")

        for mode in MODES:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            radio = QRadioButton(MODE_LABELS[mode], card)
            radio.setChecked(self._settings.mode == mode)
            self._mode_buttons[mode] = radio
            row.addWidget(radio)
            if mode == "parts":
                row.addWidget(self._parts)
            elif mode == "size":
                row.addWidget(self._size)
            else:
                row.addWidget(self._pages, 1)
            row.addStretch(1)
            card.body.addLayout(row)
            radio.toggled.connect(self._on_mode_change)
        card.body.addWidget(hint_label("页码范围支持 1,3-5,7- 语法"))
        return card

    def _on_input_change(self, text: str) -> None:
        """输入变化时同步两处路径提示。"""
        self._refresh_input_hint(text)
        self._refresh_output_hints()

    def _refresh_input_hint(self, text: str) -> None:
        """输入路径的完整形式（空输入时提示未选择）。"""
        self._input_hint.setText(str(Path(text).expanduser()) if text.strip() else "未选择")

    def _refresh_output_hints(self) -> None:
        """输出目录与提取文件的解析结果提示。"""
        raw_dir = self._output_dir_row.value()
        raw_input = self._input_row.value()
        if raw_dir:
            self._output_dir_hint.setText(str(Path(raw_dir).expanduser()))
        elif raw_input.strip():
            fallback = Path(raw_input).expanduser().parent
            self._output_dir_hint.setText(f"留空：输出到 {fallback}")
        else:
            self._output_dir_hint.setText("留空：输出到输入文件所在目录")
        suggestion = default_output_path(Path(raw_input).expanduser()) if raw_input.strip() else None
        self._output_file_row.set_suggestion(suggestion)

    def _current_mode(self) -> str:
        for mode, radio in self._mode_buttons.items():
            if radio.isChecked():
                return mode
        return "parts"

    def _on_mode_change(self, *_args: object) -> None:
        """按当前模式启用参数控件，并显隐「提取文件」行。"""
        mode = self._current_mode()
        self._parts.setEnabled(mode == "parts")
        self._size.setEnabled(mode == "size")
        self._pages.setEnabled(mode == "range")
        visible = mode == "range"
        self._output_file_row.setVisible(visible)
        self._output_file_hint.setVisible(visible)

    # ------------------------------------------------------------------ #
    # 分割
    # ------------------------------------------------------------------ #
    def _append_log(self, message: str) -> None:
        """追加一行带时间戳的日志。"""
        from datetime import datetime

        self._log.append(f"{datetime.now():%H:%M:%S} {message}")

    def _set_busy(self, busy: bool) -> None:
        """分割期间禁用按钮与输入，避免重复提交。"""
        self._busy = busy
        for widget in (
            self._start_button,
            self._input_row.browse,
            self._output_dir_row.browse,
            self._output_file_row.browse,
            self._input_row.edit,
            self._output_dir_row.edit,
            self._output_file_row.edit,
        ):
            widget.setEnabled(not busy)

    def _collect(self) -> tuple[Path, Path, Path | None, str, int, int, str] | None:
        """校验输入并返回后台任务参数；失败时写日志并返回 ``None``。"""
        raw_input = self._input_row.value()
        if not raw_input:
            self._append_log("错误：请选择输入 PDF")
            return None
        input_path = Path(raw_input).expanduser()
        if not input_path.is_file():
            self._append_log(f"错误：输入文件不存在 {input_path}")
            return None

        mode = normalize_mode(self._current_mode())
        try:
            parts = parse_positive_int(self._parts.value(), "分割份数") if mode == "parts" else 1
            size = parse_positive_int(self._size.value(), "每份页数") if mode == "size" else 1
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return None
        pages = self._pages.text().strip()
        if mode == "range" and not pages:
            self._append_log("错误：页码范围模式需要填写页码范围")
            return None

        raw_dir = self._output_dir_row.value()
        output_dir = Path(raw_dir).expanduser() if raw_dir else input_path.parent
        raw_output = self._output_file_row.value()
        if mode == "range":
            output_path = Path(raw_output).expanduser() if raw_output else default_output_path(input_path)
            if not output_path.suffix:
                output_path = output_path.with_suffix(".pdf")
        else:
            output_path = None
        return input_path, output_dir, output_path, mode, parts, size, pages

    def _start(self) -> None:
        """校验输入后在后台启动分割任务。"""
        if self._busy:
            return
        collected = self._collect()
        if collected is None:
            return
        input_path, output_dir, output_path, mode, parts, size, pages = collected

        self._set_busy(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self.set_status("正在分割…")
        self._append_log(f"输入：{input_path}")
        self._append_log(f"模式：{MODE_LABELS[mode]}；输出目录：{output_dir}")

        bridge = _SplitBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(_run_split, input_path, output_dir, output_path, mode, parts, size, pages, bridge)

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        kind = event[0]
        if kind == "progress":
            _, done, total, message = event
            self._progress.setRange(0, max(total, 1))
            self._progress.setValue(done)
            self._append_log(message)
        elif kind == "done":
            _, count, target = event
            self._progress.setValue(self._progress.maximum())
            self.set_status(f"完成：{count} 个输出")
            self._append_log(f"完成：{count} 个输出 -> {target}")
            self._finish()
            QMessageBox.information(self, "分割完成", f"已生成 {count} 个输出：\n{target}")
        elif kind == "error":
            self._append_log(f"错误：{event[1]}")
            self.set_status("分割失败")
            self._finish()
            QMessageBox.critical(self, "分割失败", event[1])

    def _finish(self) -> None:
        """任务结束：恢复输入并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回路径、模式与参数（非法数字回退默认值）。"""
        try:
            parts = parse_positive_int(self._parts.value(), "分割份数")
        except ValueError:
            parts = 2
        try:
            size = parse_positive_int(self._size.value(), "每份页数")
        except ValueError:
            size = 1
        save_settings(
            PdfSplitSettings(
                input_path=self._input_row.value(),
                output_dir=self._output_dir_row.value(),
                output_path=self._output_file_row.value(),
                mode=normalize_mode(self._current_mode()),
                parts=parts,
                size=size,
                pages=self._pages.text(),
                geometry=geometry,
            )
        )


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(PdfSplitWindow, app_name="pdfsplitw")
