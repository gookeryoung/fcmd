"""llmw 模型管理对话框：查看已下载 / 未完成模型并批量移除。

模型页的「清理」入口升级为「模型管理」：列出模型目录下的全部本地模型，
显示文件数、大小与是否仍有 ``.part`` 残留，支持多选移除、清理未完成下载、
清理空目录与列表缓存。删除动作复用 :func:`fcmd.apps.llm.llmdown.delete_local_model`。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from fcmd.apps.llm.llmbase import human_size
from fcmd.apps.llm.llmdown import LocalModel, ModelToolError, delete_local_model, scan_local_models
from fcmd.gui.widgets import Card, button, fit_dialog_to_content, hint_label, make_table, make_table_item
from fcmd.gui.workers import submit

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["ModelManagerDialog"]

_COLUMNS = ("仓库", "大小", "文件", "状态", "路径")


class ModelManagerDialog(QDialog):  # pragma: no cover - Qt 控件
    """本地模型管理：扫描、展示、批量移除与残留清理。"""

    def __init__(
        self,
        models_root: Path,
        *,
        on_changed: Callable[[], None] | None = None,
        on_clear_cache: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.models_root = models_root
        self._on_changed = on_changed
        self._on_clear_cache = on_clear_cache
        self._models: tuple[LocalModel, ...] = ()
        self._scanning = False

        self.setWindowTitle("模型管理")
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        card = Card("本地模型", self)
        layout.addWidget(card, 1)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._summary = QLabel("正在扫描…", card)
        self._summary.setObjectName("Hint")
        header.addWidget(self._summary, 1)
        self._refresh_button = button("刷新", kind="ghost")
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button)
        card.body.addLayout(header)

        self._table = make_table(
            _COLUMNS,
            widths=(230, 90, 70, 150, 380),
            stretch_columns=(0, 4),
            parent=card,
        )
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(320)
        card.body.addWidget(self._table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._remove_button = button("移除选中", kind="danger")
        self._remove_button.setToolTip("删除选中的本地模型目录（不可恢复）")
        self._remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(self._remove_button)
        self._partial_button = button("清理未完成下载", kind="ghost")
        self._partial_button.setToolTip("删除选中模型的 .part 残留；未选中时清理全部未完成模型")
        self._partial_button.clicked.connect(self._cleanup_partial)
        actions.addWidget(self._partial_button)
        self._empty_button = button("清理空目录", kind="ghost")
        self._empty_button.clicked.connect(self._cleanup_empty_dirs)
        actions.addWidget(self._empty_button)
        self._cache_button = button("清理列表缓存", kind="ghost")
        self._cache_button.clicked.connect(self._clear_cache)
        actions.addWidget(self._cache_button)
        actions.addStretch(1)
        self._close_button = button("关闭", kind="ghost")
        self._close_button.clicked.connect(self.accept)
        actions.addWidget(self._close_button)
        card.body.addLayout(actions)
        card.body.addWidget(hint_label("多选后点「移除选中」；未完成模型可只清理 .part 后继续下载。"))

        self.refresh()
        fit_dialog_to_content(self, min_width=900)

    # ------------------------------------------------------------------ #
    # 扫描 / 渲染
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """后台扫描模型目录，避免大目录阻塞对话框。"""
        if self._scanning:
            return
        self._scanning = True
        self._summary.setText("正在扫描模型目录…")
        submit(scan_local_models, self.models_root, on_done=self._render, on_error=self._scan_failed)

    def _scan_failed(self, message: str) -> None:
        self._scanning = False
        self._summary.setText(f"扫描失败：{message}")
        self._models = ()
        self._table.setRowCount(0)

    def _render(self, models: list[LocalModel]) -> None:
        self._scanning = False
        self._models = tuple(sorted(models, key=lambda model: (model.complete, model.repo.casefold())))
        self._table.setRowCount(len(self._models))
        for row, model in enumerate(self._models):
            status = "完整" if model.complete else f"未完成（{model.partial} 个 .part）"
            values = (
                model.repo,
                human_size(model.size),
                str(model.files),
                status,
                str(self.models_root.joinpath(*model.repo.split("/"))),
            )
            for column, text in enumerate(values):
                self._table.setItem(row, column, make_table_item(text))
        complete = sum(1 for model in self._models if model.complete)
        incomplete = len(self._models) - complete
        self._summary.setText(
            f"共 {len(self._models)} 个模型目录：完整 {complete} 个，未完成 {incomplete} 个"
            if self._models
            else "模型目录下没有本地模型"
        )
        self._remove_button.setEnabled(bool(self._models))
        self._partial_button.setEnabled(incomplete > 0 or bool(self._models))

    def _selected_models(self) -> list[LocalModel]:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        return [self._models[row] for row in rows if 0 <= row < len(self._models)]

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #
    def _remove_selected(self) -> None:
        selected = self._selected_models()
        if not selected:
            self._summary.setText("请先选择要移除的模型")
            return
        names = "、".join(model.repo for model in selected)
        answer = QMessageBox.question(
            self,
            "移除本地模型",
            f"将删除 {len(selected)} 个本地模型目录（不可恢复）：\n{names}\n\n如果服务正在使用这些模型，请先停止服务。确定继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failures: list[str] = []
        for model in selected:
            try:
                delete_local_model(self.models_root, model.repo)
            except ModelToolError as exc:
                failures.append(f"{model.repo}: {exc}")
        self._changed()
        if failures:
            QMessageBox.critical(self, "部分模型移除失败", "\n".join(failures))
        self.refresh()

    def _cleanup_partial(self) -> None:
        selected = self._selected_models()
        targets = selected or [model for model in self._models if not model.complete]
        if not targets:
            self._summary.setText("没有未完成下载")
            return
        files: list[Path] = []
        for model in targets:
            try:
                files.extend(path for path in model.path.rglob("*.part") if path.is_file())
            except OSError:
                continue
        if not files:
            self._summary.setText("选中模型没有 .part 残留")
            return
        answer = QMessageBox.question(
            self,
            "清理未完成下载",
            f"将删除 {len(files)} 个 .part 文件；模型需要重新下载。确定继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        for path in files:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        self._summary.setText(f"已清理 {removed} 个 .part 文件")
        self._changed()
        self.refresh()

    def _cleanup_empty_dirs(self) -> None:
        if not self.models_root.is_dir():
            return
        removed = 0
        dirs = sorted(
            (path for path in self.models_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for path in dirs:
            try:
                path.rmdir()
                removed += 1
            except OSError:
                continue
        self._summary.setText(f"已清理 {removed} 个空目录")
        self._changed()
        self.refresh()

    def _clear_cache(self) -> None:
        if self._on_clear_cache is not None:
            self._on_clear_cache()
        self._summary.setText("模型列表缓存已清理")

    def _changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()
