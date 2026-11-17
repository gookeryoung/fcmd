"""locllmw（Qt）模型页：模型目录扫描结果的分组列表与显存适配标注。

列表按「模型目录里的一级子目录」折叠分组：有运行中实例的组排在最前，
默认只展开运行中与当前选中模型所在的组，避免一次铺开上百个 GGUF。
行内状态沿用既有口径（:func:`fcmd.apps.locllm._locllmw_monitor._fit_state`）：
红色加删除线 = 硬件不足，橙色 = 需释放显存。

``ModelTree`` 同时被运行页复用（运行页需要就地挑模型），因此把它做成独立
控件，只依赖共享状态与一个「按文件大小求状态」的回调，避免两页互相引用。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QResizeEvent
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from fcmd.apps.locllm import llmenv, llmmodel
from fcmd.apps.locllm._locllmw_monitor import _FIT_BUSY, _FIT_RUNNING, _FIT_UNSUPPORTED, _fit_state
from fcmd.apps.locllm._locllmw_ui import _HINT_PLACEHOLDER, _default_open_groups, _group_models
from fcmd.apps.locllm.llmbase import ModelEntry, human_size
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.widgets import HintLabel

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = [
    "COLUMNS",
    "NAME_COLUMN_WIDTH",
    "ModelTree",
    "display_path_for",
    "fit_state_for",
    "model_row_values",
    "ordered_groups",
]

COLUMNS = ("模型", "大小", "匹配档位", "标签", "硬件")
"""模型列表列名，顺序与 ``model_row_values`` 一一对应。"""

NAME_COLUMN_WIDTH = 460
"""模型名初始列宽：长路径默认也能看清，窗口更宽时继续扩展。"""


def display_path_for(entry: ModelEntry, root: Path | None) -> str:
    """分组列表里的展示路径：省略与一级分组重复的那段前缀。

    只影响展示；定位、复制与 tooltip 仍使用 ``entry.path`` 的完整绝对路径。
    目录外模型（跨盘 / 运行中服务指向别处）没有可省略的公共前缀，退回原始相对路径。
    """
    base = root if root is not None else entry.root
    try:
        relative = entry.path.relative_to(base)
    except ValueError:
        return entry.relative
    if len(relative.parts) <= 1:
        return entry.relative
    return str(Path(*relative.parts[1:]))


def fit_state_for(
    *,
    running: bool,
    total: llmmodel.VramFit | None,
    free: llmmodel.VramFit | None,
) -> str:
    """计算行的硬件状态文本：运行中 > 硬件不足 > 需释放显存 > 可用。"""
    if running:
        return _FIT_RUNNING
    return _fit_state(total, free)


def model_row_values(
    entry: ModelEntry,
    *,
    precision_note: str = "",
    hardware: str = "-",
    display_path: str | None = None,
) -> tuple[str, str, str, str, str]:
    """把一个模型条目格式化为列表行（与 :data:`COLUMNS` 对应）。

    ``display_path`` 仅供展示（默认 :attr:`ModelEntry.relative`）；分组前缀由调用方
    用 :func:`display_path_for` 省略，内部定位仍以 ``entry.path`` 为准。
    """
    name = display_path if display_path is not None else entry.relative
    if precision_note:
        name = f"{name}（{precision_note}）"
    return (
        name,
        human_size(entry.size),
        ", ".join(entry.profiles) or "-",
        " · ".join(entry.tags) or "-",
        hardware,
    )


def ordered_groups(
    entries: list[ModelEntry],
    root: Path | None,
    running_paths: set[str],
) -> list[tuple[str, list[tuple[int, ModelEntry]]]]:
    """按一级子目录分组并把有运行中实例的组排到最前（组内保持原顺序）。"""
    groups = _group_models(entries, root)
    running_groups = {
        label for label, items in groups if any(llmenv.path_key(entry.path) in running_paths for _i, entry in items)
    }
    return sorted(groups, key=lambda item: (item[0] not in running_groups, item[0].casefold()))


class ModelTree(QWidget):  # pragma: no cover - Qt 控件
    """分组模型树：列 = 模型 / 大小 / 匹配档位 / 标签 / 硬件。"""

    modelSelected = Signal(object)

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._fit_resolver: Callable[[ModelEntry], str] | None = None
        self._open_groups: dict[str, bool] = {}
        self._labels: dict[str, str] = {}
        self._path_by_index: dict[int, Path] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, NAME_COLUMN_WIDTH)
        for column, width in ((1, 90), (2, 130), (3, 110), (4, 90)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(column, width)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemExpanded.connect(lambda item: self._remember_group(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._remember_group(item, False))
        layout.addWidget(self.tree)
        self.hint = HintLabel(_HINT_PLACEHOLDER)
        layout.addWidget(self.hint)

    # ------------------------------------------------------------------ #
    # 渲染
    # ------------------------------------------------------------------ #
    def set_fit_resolver(self, resolver: Callable[[ModelEntry], str]) -> None:
        """注入「条目 → 硬件状态」回调（由运行页按当前档位 / 上下文提供）。"""
        self._fit_resolver = resolver
        self.refresh()

    def set_hint(self, text: str) -> None:
        """更新列表下方提示文字。"""
        self.hint.setText(text)

    def refresh(self) -> None:
        """按共享状态重建列表，尽量保持选中项与滚动位置。"""
        theme = _guitheme.load_theme()
        danger = QBrush(QColor(theme.palette.danger))
        warning = QBrush(QColor(theme.palette.warning))
        accent = QBrush(QColor(theme.palette.accent))
        muted = QBrush(QColor(theme.palette.muted))
        strike = QFont(self.font())
        strike.setStrikeOut(True)
        bold = QFont(self.font())
        bold.setBold(True)

        selected = self.state.selected_model
        scroll = self.tree.verticalScrollBar().value()
        self.tree.clear()
        self._labels.clear()
        self._path_by_index.clear()
        entries = self.state.models
        groups = ordered_groups(entries, self.state.models_root, self.state.running_paths)
        default_open = _default_open_groups(groups, self.state.running_paths, selected)

        for label, items in groups:
            group_item = QTreeWidgetItem([f"{label}（{len(items)}）", "", "", "", ""])
            group_item.setFirstColumnSpanned(True)
            group_item.setFont(0, bold)
            group_item.setForeground(0, accent)
            group_item.setData(0, Qt.ItemDataRole.UserRole, ("group", label))
            self.tree.addTopLevelItem(group_item)
            group_item.setExpanded(self._open_groups.get(label, label in default_open))
            self._labels[label] = label
            for index, entry in items:
                precision = self.state.precisions.get(llmenv.path_key(entry.path))
                hardware = self._hardware_text(entry)
                values = model_row_values(
                    entry,
                    precision_note=precision.note if precision is not None else "",
                    hardware=hardware,
                    display_path=display_path_for(entry, self.state.models_root),
                )
                child = QTreeWidgetItem(group_item, list(values))
                child.setData(0, Qt.ItemDataRole.UserRole, ("model", index))
                child.setToolTip(0, str(entry.path))
                for column, value in enumerate(values):
                    if column and value:
                        child.setToolTip(column, value)
                child.setForeground(1, muted)
                child.setForeground(2, muted)
                child.setForeground(3, muted)
                if hardware == _FIT_UNSUPPORTED:
                    for column in range(len(COLUMNS)):
                        child.setForeground(column, danger)
                        child.setFont(column, strike)
                elif hardware == _FIT_BUSY:
                    for column in range(len(COLUMNS)):
                        child.setForeground(column, warning)
                elif hardware == _FIT_RUNNING:
                    child.setForeground(4, accent)
                self._path_by_index[index] = entry.path
        self.tree.verticalScrollBar().setValue(scroll)
        self._select_path(selected, emit=False)

    def expand_all(self) -> None:
        """展开所有分组（模型较多时配合纵向滚动查看）。"""
        self.tree.expandAll()

    def collapse_all(self) -> None:
        """收起所有分组，只保留分组标题与数量。"""
        self.tree.collapseAll()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fill_name_column()

    def _fill_name_column(self) -> None:
        """窗口变宽时把剩余宽度给模型名；变窄时保留列宽并交给横向滚动条。"""
        header = self.tree.header()
        used = sum(header.sectionSize(column) for column in range(len(COLUMNS)))
        extra = self.tree.viewport().width() - used
        if extra > 0:
            header.resizeSection(0, header.sectionSize(0) + extra)

    def _hardware_text(self, entry: ModelEntry) -> str:
        """调用注入的状态回调；未注入时只标注运行中 / 未知。"""
        if self._fit_resolver is not None:
            return self._fit_resolver(entry)
        return _FIT_RUNNING if llmenv.path_key(entry.path) in self.state.running_paths else "-"

    def _on_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] != "model":
            return
        path = self._path_by_index.get(int(data[1]))
        if path is not None:
            self.modelSelected.emit(path)

    def _remember_group(self, item: QTreeWidgetItem, expanded: bool) -> None:
        """记住用户手动展开 / 收起的分组，重建后沿用。"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "group":
            self._open_groups[str(data[1])] = expanded

    # ------------------------------------------------------------------ #
    # 选择
    # ------------------------------------------------------------------ #
    def select_path(self, path: Path) -> None:
        """在树中选中指定路径的模型（展开其所在分组）。"""
        self._select_path(path, emit=False)

    def _select_path(self, path: Path | None, *, emit: bool) -> None:
        if path is None:
            self.tree.clearSelection()
            return
        key = llmenv.path_key(path)
        for index, candidate in self._path_by_index.items():
            if llmenv.path_key(candidate) != key:
                continue
            for top in range(self.tree.topLevelItemCount()):
                group_item = self.tree.topLevelItem(top)
                for child_index in range(group_item.childCount()):
                    child = group_item.child(child_index)
                    data = child.data(0, Qt.ItemDataRole.UserRole)
                    if data and data[0] == "model" and int(data[1]) == index:
                        group_item.setExpanded(True)
                        self.tree.setCurrentItem(child)
                        self.tree.scrollToItem(child)
                        if emit:
                            self.modelSelected.emit(path)
                        return
