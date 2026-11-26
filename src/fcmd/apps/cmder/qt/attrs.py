"""属性编辑与书签对话框。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QVBoxLayout,
)

from fcmd.apps.cmder._attrs import AttributeChange, apply_attributes, format_timestamp, parse_timestamp
from fcmd.gui.widgets import button, hint_label

if TYPE_CHECKING:
    pass

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


__all__ = ["AttributeDialog", "BookmarkDialog"]


class AttributeDialog(QDialog):
    """批量修改时间戳与 Windows 只读 / 隐藏属性。"""

    def __init__(self, owner: Any, paths: list[Path]) -> None:
        super().__init__(owner)
        self.owner = owner
        self.paths = paths
        self.setWindowTitle("修改属性")
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label(f"将应用到 {len(paths)} 个条目"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("修改时间"), 0, 0)
        self.mtime_edit = QLineEdit(self)
        self.mtime_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS，留空不改")
        if paths:
            with __import__("contextlib").suppress(OSError):
                self.mtime_edit.setText(format_timestamp(paths[0].stat().st_mtime))
        grid.addWidget(self.mtime_edit, 0, 1)
        self.readonly = QCheckBox("只读", self)
        self.hidden = QCheckBox("隐藏", self)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        for widget in (self.readonly, self.hidden):
            flags.addWidget(widget)
        flags.addStretch(1)
        grid.addLayout(flags, 1, 0, 1, 2)
        outer.addLayout(grid)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        apply_button = button("应用", kind="primary")
        apply_button.clicked.connect(self._apply)
        actions.addWidget(apply_button)
        outer.addLayout(actions)

    def _apply(self) -> None:
        timestamp = None
        if self.mtime_edit.text().strip():
            timestamp = parse_timestamp(self.mtime_edit.text())
            if timestamp is None:
                self.owner.show_error("时间无效", "时间格式应为 YYYY-MM-DD HH:MM:SS")
                return
        report = apply_attributes(
            self.paths,
            AttributeChange(
                modified=timestamp,
                readonly=self.readonly.isChecked() if self.readonly.isChecked() else None,
                hidden=self.hidden.isChecked() if self.hidden.isChecked() else None,
            ),
        )
        self.owner.set_status(report.message())
        self.owner.after_operation()
        self.accept()


class BookmarkDialog(QDialog):
    """书签管理：添加当前目录、删除、打开。"""

    def __init__(self, owner: Any, panel: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.panel = panel
        self.setWindowTitle("书签")
        self.setMinimumSize(560, 420)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        self.list = QListWidget(self)
        outer.addWidget(self.list, 1)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("打开", self._open),
            ("添加当前目录", self._add),
            ("删除", self._remove),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        actions.addStretch(1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addLayout(actions)
        self.list.itemDoubleClicked.connect(lambda _item: self._open())
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        self.list.addItems(list(self.owner.settings.bookmarks))

    def _save(self, items: list[str]) -> None:
        self.owner.settings.bookmarks = items
        self.owner.persist_settings()
        self._reload()

    def _add(self) -> None:
        path = self.panel.state.path
        items = list(self.owner.settings.bookmarks)
        if path and path not in items:
            items.append(path)
            self._save(items)

    def _remove(self) -> None:
        row = self.list.currentRow()
        items = list(self.owner.settings.bookmarks)
        if 0 <= row < len(items):
            items.pop(row)
            self._save(items)

    def _open(self) -> None:
        row = self.list.currentRow()
        items = list(self.owner.settings.bookmarks)
        if 0 <= row < len(items):
            self.panel.set_path(Path(items[row]))
            self.accept()
