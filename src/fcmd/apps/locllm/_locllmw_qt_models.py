"""locllmw（Qt）模型页：模型目录扫描结果的分组列表与显存适配标注。

列表按「模型目录里的一级子目录」折叠分组：有运行中实例的组排在最前，
默认只展开运行中与当前选中模型所在的组，避免一次铺开上百个 GGUF。
行内状态沿用 Tk 版口径（:func:`fcmd.apps.locllm._locllmw_monitor._fit_state`）：
红色加删除线 = 硬件不足，橙色 = 需释放显存。

``ModelTree`` 同时被运行页复用（运行页需要就地挑模型），因此把它做成独立
控件，只依赖共享状态与一个「按文件大小求状态」的回调，避免两页互相引用。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from fcmd.apps.locllm import llmenv, llmmodel
from fcmd.apps.locllm._locllmw_monitor import _FIT_BUSY, _FIT_RUNNING, _FIT_UNSUPPORTED, _fit_state
from fcmd.apps.locllm._locllmw_ui import _HINT_PLACEHOLDER, _default_open_groups, _group_models
from fcmd.apps.locllm.llmbase import ModelEntry, human_size
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.widgets import Card, HintLabel, PageScroll, button

if TYPE_CHECKING:
    from collections.abc import Callable

    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = [
    "COLUMNS",
    "ModelTree",
    "ModelsPage",
    "fit_state_for",
    "model_row_values",
    "ordered_groups",
]

COLUMNS = ("模型", "大小", "匹配档位", "标签", "硬件")
"""模型列表列名，顺序与 ``model_row_values`` 一一对应。"""


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
) -> tuple[str, str, str, str, str]:
    """把一个模型条目格式化为列表行（与 :data:`COLUMNS` 对应）。"""
    name = entry.relative
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
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 90), (2, 130), (3, 110), (4, 90)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(column, width)
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
                )
                child = QTreeWidgetItem(group_item, list(values))
                child.setData(0, Qt.ItemDataRole.UserRole, ("model", index))
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


class ModelsPage(QWidget):  # pragma: no cover - Qt 控件
    """模型页：扫描 / 选择模型，展示精度与显存适配；下载与转换见 CLI。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.refresh_button = button("刷新模型列表")
        self.expand_button = button("展开全部", kind="ghost")
        self.collapse_button = button("收起全部", kind="ghost")
        self.browse_button = button("浏览…")
        self.clear_button = button("清除选择")
        self.open_dir_button = button("打开模型目录", kind="ghost")
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.expand_button)
        toolbar.addWidget(self.collapse_button)
        toolbar.addWidget(self.browse_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self.open_dir_button)
        layout.addLayout(toolbar)

        scroll = PageScroll(self)
        layout.addWidget(scroll, 1)
        self.card = Card("运行模型（模型目录中的 GGUF）")
        self.tree = ModelTree(state, self.card)
        self.tree.tree.setMinimumHeight(320)
        self.card.body.addWidget(self.tree)
        scroll.body.addWidget(self.card, 1)

        self.hint = HintLabel(
            "下载与转换请用 `fcmd llmdown` / `fcmd llmconvert`；"
            "需要内嵌下载浏览器与转换向导时用 `fcmd locllmw --ui tk`。"
        )
        scroll.body.addWidget(self.hint)

        self.refresh_button.clicked.connect(self._refresh)
        self.expand_button.clicked.connect(self.tree.expand_all)
        self.collapse_button.clicked.connect(self.tree.collapse_all)
        self.browse_button.clicked.connect(self._browse)
        self.clear_button.clicked.connect(self._clear)
        self.open_dir_button.clicked.connect(self._open_dir)
        state.modelsChanged.connect(self._on_models_changed)
        state.servicesChanged.connect(lambda _states: self.tree.refresh())

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        self.card.setTitle("正在扫描模型目录…")
        self.state.refresh_models()

    def _on_models_changed(self, outcome: object) -> None:
        self.card.setTitle(f"运行模型（模型目录中的 GGUF，共 {len(self.state.models)} 个）")
        self.tree.refresh()
        issue = getattr(outcome, "issue", None)
        if issue:
            self.hint.setText(issue)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 GGUF 模型", "", "GGUF 文件 (*.gguf);;所有文件 (*.*)")
        if not path:
            return
        self.tree.tree.clearSelection()
        self.state.select_model(Path(path).resolve())

    def _clear(self) -> None:
        self.tree.tree.clearSelection()
        self.state.selected_model = None
        self.state.selectionChanged.emit(None, False)
        self.tree.set_hint(_HINT_PLACEHOLDER)

    def _open_dir(self) -> None:
        from fcmd.apps.locllm._locllmw_models import open_path

        open_path(llmenv.models_dir())
