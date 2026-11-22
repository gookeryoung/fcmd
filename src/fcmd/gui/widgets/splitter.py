"""分栏容器：创建不可折叠分栏并读写尺寸（尺寸持久化用）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

from fcmd.gui.layout import parse_sizes, serialize_sizes

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["apply_splitter_sizes", "make_splitter", "splitter_sizes"]


def make_splitter(
    orientation: Qt.Orientation = Qt.Orientation.Horizontal,
    *,
    sizes: Sequence[int] = (),
    parent: QWidget | None = None,
) -> QSplitter:  # pragma: no cover - Qt 控件
    """创建不可折叠的分栏容器（可拖动，尺寸可持久化）。"""
    splitter = QSplitter(orientation, parent)
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(6)
    if sizes:
        splitter.setSizes(list(sizes))
    return splitter


def splitter_sizes(splitter: QSplitter) -> list[int]:  # pragma: no cover - Qt 控件
    """读取分栏尺寸（写入设置文件用）。"""
    return serialize_sizes(splitter.sizes())


def apply_splitter_sizes(splitter: QSplitter, sizes: object) -> bool:  # pragma: no cover - Qt 控件
    """按记忆尺寸恢复分栏；非法内容保持当前布局并返回 ``False``。"""
    parsed = parse_sizes(sizes)
    if parsed is None:
        return False
    splitter.setSizes(parsed)
    return True
