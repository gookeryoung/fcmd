"""目录同步对话框：方向、冲突策略与执行报告。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._ops import (
    SYNC_BOTH,
    SYNC_CONFLICT_TEXT,
    SYNC_CONFLICTS,
    SYNC_LTR,
    SYNC_RTL,
    SyncOptions,
    SyncRunReport,
    build_sync_plan,
    execute_sync_deletes,
    format_sync_report,
    revalidate_sync_items,
    split_sync_selection,
    sync_invert_selection,
    sync_item_key,
    sync_select_all,
)
from fcmd.gui.widgets import button, hint_label, make_table, make_table_item

if TYPE_CHECKING:
    pass

from fcmd.apps.cmder.qt._common import _checkbox_item

__all__ = ["SyncDialog"]


class SyncDialog(QDialog):
    """预演式目录同步：方向 / 冲突策略 / 删除开关 + 可勾选计划。"""

    def __init__(self, owner: Any, report: Any, tolerance: float = 2.0) -> None:
        super().__init__(owner)
        self.owner = owner
        self.report = report
        self.tolerance = tolerance
        self.plan: Any = None
        self.selection: frozenset[str] = frozenset()
        self.setWindowTitle("目录同步")
        self.setMinimumSize(1080, 700)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.direction_combo = QComboBox(self)
        for direction, label in ((SYNC_LTR, "左 → 右"), (SYNC_RTL, "右 → 左"), (SYNC_BOTH, "双向镜像")):
            self.direction_combo.addItem(label, direction)
        self.direction_combo.currentIndexChanged.connect(lambda _index: self.refresh_plan())
        grid.addWidget(QLabel("方向"), 0, 0)
        grid.addWidget(self.direction_combo, 0, 1)
        self.conflict_combo = QComboBox(self)
        for policy in SYNC_CONFLICTS:
            self.conflict_combo.addItem(SYNC_CONFLICT_TEXT[policy], policy)
        self.conflict_combo.currentIndexChanged.connect(lambda _index: self.refresh_plan())
        grid.addWidget(QLabel("冲突"), 0, 2)
        grid.addWidget(self.conflict_combo, 0, 3)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.delete_extra = QCheckBox("删除目标端多余项（回收站）", self)
        self.delete_extra.toggled.connect(lambda _checked: self.refresh_plan())
        self.delete_dirs = QCheckBox("包含目录", self)
        self.delete_dirs.toggled.connect(lambda _checked: self.refresh_plan())
        for widget in (self.delete_extra, self.delete_dirs):
            flags.addWidget(widget)
        flags.addStretch(1)
        grid.addLayout(flags, 1, 0, 1, 4)
        outer.addLayout(grid)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("全选", lambda: self._set_selection(sync_select_all(self.plan)) if self.plan else None),
            ("全不选", lambda: self._set_selection(frozenset())),
            (
                "反选",
                lambda: self._set_selection(sync_invert_selection(self.plan, self.selection)) if self.plan else None,
            ),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self.execute_button = button("执行勾选项", kind="primary")
        self.execute_button.clicked.connect(self._execute)
        toolbar.addWidget(self.execute_button)
        outer.addLayout(toolbar)

        self.table = make_table(
            ("", "动作", "相对路径", "目标", "说明"),
            widths=(36, 100, 360, 420, 220),
            stretch_columns=(2, 3),
            parent=self,
        )
        self.table.setMinimumHeight(460)
        outer.addWidget(self.table, 1)
        self.status = hint_label("")
        outer.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        outer.addLayout(bottom)
        self.refresh_plan()

    def _options(self) -> SyncOptions:
        return SyncOptions(
            direction=self.direction_combo.currentData(),
            conflict=self.conflict_combo.currentData(),
            delete_target_extra=self.delete_extra.isChecked(),
            delete_dirs=self.delete_dirs.isChecked(),
            mtime_tolerance=self.tolerance,
        )

    def refresh_plan(self) -> None:
        try:
            self.plan = build_sync_plan(self.report, self._options())
        except ValueError as exc:
            self.status.setText(f"预演失败：{exc}")
            return
        self.selection = sync_select_all(self.plan)
        self._render()

    def _render(self) -> None:
        plan = self.plan
        items = [*plan.copies, *plan.deletes] if plan is not None else []
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            cell = _checkbox_item(sync_item_key(item) in self.selection)
            self.table.setItem(row, 0, cell)
            self.table.setItem(row, 1, make_table_item(item.action_text))
            self.table.setItem(row, 2, make_table_item(item.relative))
            self.table.setItem(row, 3, make_table_item(str(item.target)))
            self.table.setItem(row, 4, make_table_item(item.skip_reason))
        if plan is not None:
            counts = plan.counts()
            self.status.setText(
                f"复制 {counts['copy']}（新建 {counts['copy_new']} / 覆盖 {counts['copy_overwrite']}），"
                f"删除 {counts['delete']}，跳过 {counts['skipped']}，相同 {counts['same']}"
            )

    def _set_selection(self, selection: frozenset[str]) -> None:
        self.selection = selection
        self._render()

    def _execute(self) -> None:
        plan = self.plan
        if plan is None or not self.selection:
            self.status.setText("没有勾选任何计划项")
            return
        copies, deletes, unselected = split_sync_selection(plan, self.selection)
        if not self.owner.ask(
            "确认执行同步",
            f"将复制 {len(copies)} 项、删除 {len(deletes)} 项（目标端多余项移入回收站），继续？",
        ):
            return
        ready_copies, skipped_copies = revalidate_sync_items(copies, mtime_tolerance=self.tolerance)
        ready_deletes, skipped_deletes = revalidate_sync_items(deletes, mtime_tolerance=self.tolerance)
        groups: dict[Path, list[Any]] = {}
        for item in ready_copies:
            if item.source is None:
                continue
            transfer = core.TransferItem(source=item.source, target=item.target, overwrite=item.overwrite)
            groups.setdefault(plan.root_for(item.target), []).append(transfer)
        enqueue_ok = True
        for destination, items in groups.items():
            if not self.owner.enqueue_transfer(items, destination, move=False):
                enqueue_ok = False
        delete_report = execute_sync_deletes(ready_deletes) if enqueue_ok else None
        self.owner.refresh_both()
        report = SyncRunReport(
            copied=len(ready_copies) if enqueue_ok else 0,
            copied_new=sum(1 for item in ready_copies if not item.overwrite),
            deleted=len(delete_report.completed) if delete_report else 0,
            delete_not_executed=len(delete_report.not_executed) if delete_report else len(ready_deletes),
            unselected=unselected,
            enqueue_ok=enqueue_ok,
            skipped=tuple((str(item.target), reason) for item, reason in (*skipped_copies, *skipped_deletes)),
            failed=tuple(
                (str(item.target), error) for item, error in (delete_report.failures if delete_report else ())
            ),
        )
        self.owner.show_text("同步结果", "\n".join(format_sync_report(report)))
        self.status.setText("已执行；复制任务在传输队列中继续")
