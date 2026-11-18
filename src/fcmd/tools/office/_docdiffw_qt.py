"""docdiffw 的 Qt 主窗口与入口。

窗口负责收集旧 / 新文档、输出、标题与对比模式，实际对比仍由
:class:`fcmd.tools.office.docdiff.DocDiffCommand` 执行，放进 ``QThreadPool``
避免阻塞界面。

布局口径：文档三行（旧 / 新 / 输出）各带「输入 + 浏览」，输入框下方用
:class:`ElidedLabel` 显示完整路径（中间省略 + tooltip）；日志占满剩余空间，
窗口变矮时整页进 :class:`PageScroll`。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QWidget,
)

from fcmd import __version__
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, ElidedLabel, FormGrid, button, hint_label
from fcmd.gui.qt.workers import submit
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
from fcmd.tools.office.docdiffw import (
    MIN_HEIGHT,
    MIN_WIDTH,
    DocDiffSettings,
    load_settings,
    save_settings,
)

__all__ = ["DocDiffWindow", "run_qt"]

DEFAULT_SIZE = (900, 620)

_WORD_FILTER = "Word 文档 (*.doc *.docx);;所有文件 (*.*)"


class _DocDiffBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台对比线程 → GUI 的事件桥。"""

    event = Signal(object)


class _FileRow(QWidget):  # pragma: no cover - Qt 控件
    """一行文件输入：输入框 + 浏览按钮 + 省略路径提示。"""

    def __init__(
        self,
        *,
        value: str = "",
        save: bool = False,
        title_provider: Callable[[], str] | None = None,
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._save = save
        self._title_provider = title_provider
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.edit = QLineEdit(value, self)
        self.edit.setPlaceholderText(placeholder)
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

    def _refresh_hint(self, text: str) -> None:
        self.hint.setText(str(Path(text).expanduser()) if text.strip() else "未选择")

    def _pick(self) -> None:
        """浏览：保存模式选目标文件，其余选已有文档。"""
        if self._save:
            title = self._title_provider() if self._title_provider is not None else DEFAULT_TITLE
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "选择对比结果",
                output_filename(title),
                "Word 文档 (*.docx);;所有文件 (*.*)",
            )
        else:
            filename, _ = QFileDialog.getOpenFileName(self, "选择文档", self.value(), _WORD_FILTER)
        if filename:
            self.edit.setText(filename)


def _run_comparison(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应
    old_doc: Path,
    new_doc: Path,
    output: Path | None,
    title: str,
    show_changes: bool,
    compare_mode: CompareMode,
    bridge: _DocDiffBridge,
) -> None:
    """线程池里执行对比并把结果投回界面。"""
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
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("done", str(target)))


class DocDiffWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """文档对比工具主窗口。"""

    def __init__(self) -> None:
        self._settings = load_settings()
        self._busy = False
        self._bridge: _DocDiffBridge | None = None
        super().__init__(
            title=f"文档对比工具 v{__version__}",
            config_name="docdiffw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
            scroll=True,
        )
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """文档 / 选项 / 操作行 / 日志。"""
        self.add(self._build_documents_card())
        self.add(self._build_options_card())

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        self._start_button = button("开始对比", kind="primary")
        self._start_button.clicked.connect(self._start_comparison)
        actions.addWidget(self._start_button)
        self.add_layout(actions)

        self._log = self.add_log(height=200)
        self._append_log("选择旧文档与新文档后点击「开始对比」。")

    def _build_documents_card(self) -> Card:
        """旧 / 新 / 输出三行，输出留空时按标题与时间戳自动命名。"""
        card = Card("文档", self)
        grid = FormGrid(card)
        self._old_row = _FileRow(value=self._settings.old_doc, placeholder="旧文档（.doc / .docx）", parent=card)
        grid.add_row("旧文档", self._old_row)
        self._new_row = _FileRow(value=self._settings.new_doc, placeholder="新文档（.doc / .docx）", parent=card)
        grid.add_row("新文档", self._new_row)
        self._output_row = _FileRow(
            value=self._settings.output,
            save=True,
            title_provider=self._current_title,
            placeholder="对比结果（留空自动命名）",
            parent=card,
        )
        grid.add_row("输出", self._output_row)
        card.body.addWidget(grid)
        card.body.addWidget(hint_label("输出留空时保存到新文档同目录：<标题>@<时间戳>.docx"))
        return card

    def _build_options_card(self) -> Card:
        """标题、对比模式与是否显示修订。"""
        card = Card("选项", self)
        grid = FormGrid(card)
        self._title = QLineEdit(self._settings.title, card)
        grid.add_row("标题", self._title)
        self._compare_mode = QComboBox(card)
        self._compare_mode.addItems(list(COMPARE_MODES))
        self._compare_mode.setCurrentText(self._settings.compare_mode)
        self._show_changes = QCheckBox("显示修订标记", card)
        self._show_changes.setChecked(self._settings.show_changes)
        grid.add_pair("对比模式", self._compare_mode, "", self._show_changes)
        card.body.addWidget(grid)
        card.body.addWidget(hint_label("original 以原文档为基础；revised 以修订文档为基础。"))
        return card

    def _current_title(self) -> str:
        """当前标题（空时回退默认标题）。"""
        return self._title.text().strip() or DEFAULT_TITLE

    def _append_log(self, message: str) -> None:
        """追加一行带时间戳的日志。"""
        from datetime import datetime

        self._log.append(f"{datetime.now():%H:%M:%S} {message}")

    # ------------------------------------------------------------------ #
    # 对比
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool) -> None:
        """对比期间禁用按钮与输入，避免重复提交。"""
        self._busy = busy
        for widget in (
            self._start_button,
            self._old_row.browse,
            self._new_row.browse,
            self._output_row.browse,
            self._old_row.edit,
            self._new_row.edit,
            self._output_row.edit,
            self._title,
            self._compare_mode,
            self._show_changes,
        ):
            widget.setEnabled(not busy)

    def _collect_inputs(self) -> tuple[Path, Path] | None:
        """读取并校验旧 / 新文档；失败时写日志并返回 ``None``。"""
        raw_old = self._old_row.value()
        raw_new = self._new_row.value()
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
        """校验输入后在后台启动对比。"""
        if self._busy:
            return
        inputs = self._collect_inputs()
        if inputs is None:
            return
        old_doc, new_doc = inputs

        try:
            compare_mode = normalize_compare_mode(self._compare_mode.currentText())
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return

        title = self._current_title()
        raw_output = self._output_row.value()
        output = Path(raw_output).expanduser() if raw_output else None
        preview = resolve_output_path(new_doc, output, title=title)

        self._log.clear()
        self._set_busy(True)
        self.set_status("正在对比…")
        self._append_log(f"旧文档：{old_doc}")
        self._append_log(f"新文档：{new_doc}")
        self._append_log(f"输出：{preview}")
        self._append_log(f"对比模式：{compare_mode}；显示修订：{'是' if self._show_changes.isChecked() else '否'}")

        bridge = _DocDiffBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(
            _run_comparison,
            old_doc,
            new_doc,
            output,
            title,
            self._show_changes.isChecked(),
            compare_mode,
            bridge,
        )

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：完成或失败。"""
        kind = event[0]
        if kind == "done":
            target = event[1]
            self._append_log(f"对比完成：{target}")
            self.set_status("对比完成")
            self._finish()
            QMessageBox.information(self, "对比完成", f"已生成对比文档：\n{target}")
        elif kind == "error":
            self._append_log(f"错误：{event[1]}")
            self.set_status("对比失败")
            self._finish()
            QMessageBox.critical(self, "对比失败", event[1])

    def _finish(self) -> None:
        """任务结束：恢复控件并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回文档路径、标题、模式与窗口 geometry。"""
        try:
            compare_mode = normalize_compare_mode(self._compare_mode.currentText())
        except ValueError:
            compare_mode = "original"
        save_settings(
            DocDiffSettings(
                old_doc=self._old_row.edit.text(),
                new_doc=self._new_row.edit.text(),
                output=self._output_row.edit.text(),
                title=self._title.text(),
                show_changes=self._show_changes.isChecked(),
                compare_mode=compare_mode,
                geometry=geometry,
            )
        )


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(DocDiffWindow, app_name="docdiffw")
