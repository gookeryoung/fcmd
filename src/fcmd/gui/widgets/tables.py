"""只读表格：统一的表格外观与只读单元格。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["make_table", "make_table_item"]


def make_table_item(text: str) -> QTableWidgetItem:  # pragma: no cover - Qt 控件
    """只读表格单元格。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def make_table(
    headers: Sequence[str],
    *,
    widths: Sequence[int] | None = None,
    stretch_columns: Sequence[int] = (),
    stretch_last: bool = False,
    parent: QWidget | None = None,
) -> QTableWidget:  # pragma: no cover - Qt 控件
    """创建只读、整行选择、交替行色的表格。"""
    table = QTableWidget(0, len(headers), parent)
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    for index in range(len(headers)):
        if index in stretch_columns:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        else:
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(index, widths[index] if widths and index < len(widths) else 120)
    if stretch_last and len(headers) and len(headers) - 1 not in stretch_columns:
        header.setStretchLastSection(True)
    return table
