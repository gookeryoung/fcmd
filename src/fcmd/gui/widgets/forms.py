"""表单与路径输入：两列表单网格、路径输入行与带提示的路径字段。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from fcmd.gui.widgets.buttons import button
from fcmd.gui.widgets.text import ElidedLabel

__all__ = ["FormGrid", "PathField", "PathRow"]


class FormGrid(QWidget):  # pragma: no cover - Qt 控件
    """两列表单网格：每行「标签 + 控件」或「标签 + 控件 + 标签 + 控件」。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)
        self._row = 0

    def add_row(self, label: str, widget: QWidget) -> None:
        """整行：标签在 0 列，控件跨 1–3 列。"""
        self.grid.addWidget(QLabel(label), self._row, 0, Qt.AlignmentFlag.AlignRight)
        self.grid.addWidget(widget, self._row, 1, 1, 3)
        self._row += 1

    def add_pair(
        self,
        label: str,
        left: QWidget,
        right_label: str = "",
        right: QWidget | None = None,
    ) -> None:
        """一行放两对「标签 + 控件」；``right`` 省略时左控件跨 1–3 列。"""
        self.grid.addWidget(QLabel(label), self._row, 0, Qt.AlignmentFlag.AlignRight)
        self.grid.addWidget(left, self._row, 1)
        if right is not None:
            self.grid.addWidget(QLabel(right_label), self._row, 2, Qt.AlignmentFlag.AlignRight)
            self.grid.addWidget(right, self._row, 3)
        else:
            self.grid.addWidget(left, self._row, 1, 1, 3)
        self._row += 1

    def row_count(self) -> int:
        """已占用的行数（供测试与调试）。"""
        return self._row


class PathRow(QWidget):  # pragma: no cover - Qt 控件
    """路径输入行：输入框 + 选择按钮（默认「浏览…」）+「恢复默认」按钮。"""

    changed = Signal(str)
    committed = Signal(str)

    def __init__(  # noqa: PLR0913
        self,
        *,
        value: str = "",
        placeholder: str = "",
        directory: bool = True,
        show_reset: bool = False,
        browse_text: str = "浏览…",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        self.edit.editingFinished.connect(lambda: self.committed.emit(self.value()))
        layout.addWidget(self.edit, 1)
        self.browse = button(browse_text)
        self.browse.clicked.connect(self._pick)
        layout.addWidget(self.browse)
        self.reset = button("恢复默认", kind="ghost") if show_reset else None
        if self.reset is not None:
            self.reset.setVisible(False)
            layout.addWidget(self.reset)

    def value(self) -> str:
        """当前输入文本（已去除首尾空白）。"""
        return self.edit.text().strip()

    def set_value(self, text: str) -> None:
        """写入输入文本（不触发额外副作用）。"""
        self.edit.setText(text)

    def _pick(self) -> None:  # pragma: no cover - 需要真实对话框
        if self._directory:
            path = QFileDialog.getExistingDirectory(self, "选择目录", self.value())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", self.value())
        if path:
            self.edit.setText(path)
            self.committed.emit(self.value())


class PathField(QWidget):  # pragma: no cover - Qt 控件
    """带路径提示的输入行：输入框 + 浏览按钮 + 第二行省略路径。

    与 :class:`PathRow` 的区别是下方多一行 ``ElidedLabel``，用于表单里显示
    完整路径或「未选择 / 留空为 …」说明。需要保存对话框、扩展名过滤或自定义
    提示文案时，继承本类覆写 :meth:`_pick` / :meth:`_refresh_hint`。
    """

    def __init__(  # noqa: PLR0913 - 控件构造参数逐项显式，便于按行替换
        self,
        *,
        value: str = "",
        placeholder: str = "",
        directory: bool = True,
        browse_text: str = "浏览…",
        title: str = "选择文件",
        pattern: str = "",
        hint_object_name: str = "Mono",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = directory
        self._title = title
        self._pattern = pattern
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.edit = QLineEdit(value, self)
        self.edit.setPlaceholderText(placeholder)
        layout.addWidget(self.edit, 0, 0)
        self.browse = button(browse_text)
        self.browse.clicked.connect(self._pick)
        layout.addWidget(self.browse, 0, 1)
        self.hint = ElidedLabel("", parent=self)
        self.hint.setObjectName(hint_object_name)
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

    def _pick(self) -> None:  # pragma: no cover - 需要真实对话框
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(self, self._title, self.value())
        else:
            filters = f"{self._pattern};;所有文件 (*.*)" if self._pattern else "所有文件 (*.*)"
            chosen, _ = QFileDialog.getOpenFileName(self, self._title, self.value(), filters)
        if chosen:
            self.edit.setText(chosen)
