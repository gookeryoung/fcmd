"""cmder 对话框共用的小工具：状态文案与勾选单元格。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

__all__ = ["_RENAME_STATUS", "_checkbox_item", "_rename_status"]

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


def _checkbox_item(checked: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem("")
    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item
