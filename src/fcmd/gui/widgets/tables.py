"""只读表格与只读树：统一外观、只读单元格与行选择。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["make_table", "make_table_item", "make_tree", "make_tree_item"]


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


def make_tree_item(texts: Sequence[str], *, children: bool = False) -> QTreeWidgetItem:  # pragma: no cover - Qt 控件
    """只读树节点；``children=True`` 时显示展开箭头（内容稍后惰性填充）。"""
    item = QTreeWidgetItem(list(texts))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if children:
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
    return item


def make_tree(
    headers: Sequence[str],
    *,
    widths: Sequence[int] | None = None,
    stretch_columns: Sequence[int] = (),
    stretch_last: bool = True,
    parent: QWidget | None = None,
) -> QTreeWidget:  # pragma: no cover - Qt 控件
    """创建只读、整行选择、交替行色的树（单选，空 headers 时隐藏表头）。"""
    tree = QTreeWidget(parent)
    tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tree.setAlternatingRowColors(True)
    tree.setAllColumnsShowFocus(True)
    tree.setUniformRowHeights(True)
    if headers:
        tree.setHeaderLabels(list(headers))
        header = tree.header()
        for index in range(len(headers)):
            if index in stretch_columns:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
            else:
                header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(index, widths[index] if widths and index < len(widths) else 120)
        if stretch_last and len(headers) - 1 not in stretch_columns:
            header.setStretchLastSection(True)
    else:
        tree.setHeaderHidden(True)
    return tree
