"""日志区：只读日志文本 + 复制 / 清空 / 导出工具条。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from fcmd.gui.widgets.buttons import button
from fcmd.gui.widgets.text import mono_font

__all__ = ["LogView"]


class LogView(QWidget):  # pragma: no cover - Qt 控件
    """只读日志区 + 工具条（复制全部 / 清空 / 导出）。"""

    def __init__(self, *, height: int = 180, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch(1)
        self.copy_button = button("复制全部", kind="ghost")
        self.clear_button = button("清空", kind="ghost")
        self.export_button = button("导出…", kind="ghost")
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)
        self.view = QPlainTextEdit(self)
        self.view.setObjectName("Log")
        self.view.setReadOnly(True)
        self.view.setMinimumHeight(height)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.view, 1)
        self.view.setFont(mono_font())
        self.clear_button.clicked.connect(self.clear)
        self.copy_button.clicked.connect(self.copy_all)
        self.export_button.clicked.connect(self._export)

    def append(self, message: str) -> None:
        """追加一行并按需滚动到底部。"""
        self.view.appendPlainText(message)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_timestamped(self, message: str) -> None:
        """追加一行 ``HH:MM:SS`` 前缀的日志（工具窗口的常用格式）。"""
        self.append(f"{datetime.now():%H:%M:%S} {message}")

    def text(self) -> str:
        """全部日志文本。"""
        return self.view.toPlainText()

    def clear(self) -> None:
        """清空日志。"""
        self.view.clear()

    def copy_all(self) -> None:
        """复制全部日志到剪贴板。"""
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.text())

    def _export(self) -> None:  # pragma: no cover - 需要真实对话框
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "llmw.log", "日志 (*.log);;所有文件 (*.*)")
        if not path:
            return
        from pathlib import Path

        Path(path).write_text(self.text(), encoding="utf-8")
