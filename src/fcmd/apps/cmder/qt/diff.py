"""文本比较与目录比较对话框。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
)

from fcmd.apps.cmder import _diff as diffmod
from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._ops import (
    COMPARE_MODE_TEXT,
    COMPARE_MODES,
    COMPARE_STATUS_TEXT,
    CompareOptions,
    compare_trees,
)
from fcmd.gui.widgets import button, hint_label, make_table, make_table_item
from fcmd.gui.workers import submit

if TYPE_CHECKING:
    pass

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


__all__ = ["CompareDialog", "TextDiffDialog"]


class TextDiffDialog(QDialog):
    """两个文本文件并排只读比较：增删改着色、同步滚动。"""

    def __init__(self, owner: Any, left: Path, right: Path) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"比较 {left.name} ↔ {right.name}")
        self.setMinimumSize(1000, 640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        self.status = hint_label("")
        outer.addWidget(self.status)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.left = self._build_view(row)
        self.right = self._build_view(row)
        outer.addLayout(row, 1)
        self.left.verticalScrollBar().valueChanged.connect(self.right.verticalScrollBar().setValue)
        self.right.verticalScrollBar().valueChanged.connect(self.left.verticalScrollBar().setValue)
        self._render(left, right)
        close = button("关闭")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        outer.addLayout(bottom)

    def _build_view(self, row: QHBoxLayout) -> QTextEdit:
        view = QTextEdit(self)
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        view.setFont(QFont("Consolas", 9))
        row.addWidget(view, 1)
        return view

    def _render(self, left: Path, right: Path) -> None:
        try:
            left_lines, left_truncated = diffmod.load_lines(left)
            right_lines, right_truncated = diffmod.load_lines(right)
        except (OSError, ValueError) as exc:
            self.status.setText(f"读取失败：{exc}")
            return
        result = diffmod.diff_lines(left_lines, right_lines)
        palette = self.palette()
        removed = QColor(palette.text().color())
        added = QColor("#2e7d32")
        changed = QColor("#b26a00")
        left_html: list[str] = []
        right_html: list[str] = []
        for row in result.rows:
            if row.kind == "left":
                left_html.append(f'<span style="background:#ffcdd2">{row.left_text}</span>')
                right_html.append("")
            elif row.kind == "right":
                left_html.append("")
                right_html.append(f'<span style="background:#c8e6c9">{row.right_text}</span>')
            elif row.kind == "both":
                left_html.append(row.left_text)
                right_html.append(row.right_text)
            else:
                left_html.append(f'<span style="background:#ffe0b2">{row.left_text}</span>')
                right_html.append(f'<span style="background:#ffe0b2">{row.right_text}</span>')
        self.left.setHtml("<pre>" + "\n".join(left_html) + "</pre>")
        self.right.setHtml("<pre>" + "\n".join(right_html) + "</pre>")
        del removed, added, changed
        note = diffmod.summary_text(result)
        if left_truncated or right_truncated:
            note += "（大文件仅比较前若干行）"
        self.status.setText(note)


class CompareDialog(QDialog):
    """只读目录比较：口径 / 掩码 / 容差 + 结果表，可转入同步预演。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.report: Any = None
        self.setWindowTitle("目录比较")
        self.setMinimumSize(1040, 680)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.mode_combo = QComboBox(self)
        for mode in COMPARE_MODES:
            self.mode_combo.addItem(COMPARE_MODE_TEXT[mode], mode)
        self.mode_combo.setCurrentIndex(COMPARE_MODES.index("time"))
        grid.addWidget(QLabel("比较口径"), 0, 0)
        grid.addWidget(self.mode_combo, 0, 1)
        self.tolerance = QLineEdit("2", self)
        grid.addWidget(QLabel("时间容差(秒)"), 0, 2)
        grid.addWidget(self.tolerance, 0, 3)
        self.mask_edit = QLineEdit(self)
        self.exclude_edit = QLineEdit(self)
        grid.addWidget(QLabel("包含掩码"), 1, 0)
        grid.addWidget(self.mask_edit, 1, 1)
        grid.addWidget(QLabel("排除掩码"), 1, 2)
        grid.addWidget(self.exclude_edit, 1, 3)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.recursive = QCheckBox("递归", self)
        self.recursive.setChecked(True)
        self.include_hidden = QCheckBox("包含隐藏项", self)
        self.files_only = QCheckBox("仅比较文件", self)
        for widget in (self.recursive, self.include_hidden, self.files_only):
            flags.addWidget(widget)
        flags.addStretch(1)
        grid.addLayout(flags, 2, 0, 1, 4)
        outer.addLayout(grid)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.compare_button = button("开始比较", kind="primary")
        self.compare_button.clicked.connect(self._start)
        toolbar.addWidget(self.compare_button)
        toolbar.addWidget(QLabel("筛选"))
        self.filter_combo = QComboBox(self)
        self.filter_combo.addItem("全部", "all")
        for status, label in COMPARE_STATUS_TEXT.items():
            self.filter_combo.addItem(label, status)
        self.filter_combo.currentIndexChanged.connect(lambda _index: self._render())
        toolbar.addWidget(self.filter_combo)
        toolbar.addStretch(1)
        self.sync_button = button("→ 同步预演")
        self.sync_button.setEnabled(False)
        self.sync_button.clicked.connect(self._open_sync)
        toolbar.addWidget(self.sync_button)
        outer.addLayout(toolbar)

        self.table = make_table(
            ("相对路径", "状态", "左大小", "右大小", "左时间", "右时间", "说明"),
            widths=(360, 100, 100, 100, 150, 150, 220),
            stretch_columns=(6,),
            parent=self,
        )
        self.table.setMinimumHeight(420)
        outer.addWidget(self.table, 1)
        self.status = hint_label("点「开始比较」比较左右面板目录")
        outer.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        outer.addLayout(bottom)

    def _options(self) -> CompareOptions:
        try:
            tolerance = max(0.0, float(self.tolerance.text() or "0"))
        except ValueError:
            tolerance = 2.0
        return CompareOptions(
            recursive=self.recursive.isChecked(),
            include_hidden=self.include_hidden.isChecked(),
            mtime_tolerance=tolerance,
            mode=self.mode_combo.currentData(),
            mask=self.mask_edit.text().strip(),
            exclude_mask=self.exclude_edit.text().strip(),
            files_only=self.files_only.isChecked(),
        )

    def _start(self) -> None:
        left = self.owner.left_panel.state.path
        right = self.owner.right_panel.state.path
        self.compare_button.setEnabled(False)
        self.status.setText("正在比较…")
        submit(
            compare_trees,
            Path(left),
            Path(right),
            self._options(),
            on_done=self._done,
            on_error=self._failed,
            on_finished=lambda: self.compare_button.setEnabled(True),
        )

    def _failed(self, message: str) -> None:
        self.status.setText(f"比较失败：{message}")

    def _done(self, report: Any) -> None:
        self.report = report
        counts = report.counts()
        self.status.setText(
            f"共 {counts.get('total', 0)} 项：仅左侧 {counts.get('left_only', 0)}，"
            f"仅右侧 {counts.get('right_only', 0)}，不同 {counts.get('different', 0)}，相同 {counts.get('same', 0)}"
            + (f"；{len(report.errors)} 项读取失败" if report.errors else "")
        )
        self.sync_button.setEnabled(bool(report.entries))
        self._render()

    def _render(self) -> None:
        status = self.filter_combo.currentData()
        entries = self.report.filtered(status) if self.report is not None else []
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.table.setItem(row, 0, make_table_item(entry.relative))
            self.table.setItem(row, 1, make_table_item(COMPARE_STATUS_TEXT.get(entry.status, entry.status)))
            self.table.setItem(row, 2, make_table_item(core.format_size(entry.left_size or 0)))
            self.table.setItem(row, 3, make_table_item(core.format_size(entry.right_size or 0)))
            self.table.setItem(row, 4, make_table_item(core.format_mtime(entry.left_mtime or 0)))
            self.table.setItem(row, 5, make_table_item(core.format_mtime(entry.right_mtime or 0)))
            self.table.setItem(row, 6, make_table_item(entry.reason))

    def _open_sync(self) -> None:
        if self.report is not None:
            self.owner.open_sync(self.report, self._options().mtime_tolerance)
