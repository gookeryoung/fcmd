"""传输队列对话框：排队任务列表与批量操作。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from fcmd.apps.cmder._ops import (
    item_status_text,
)
from fcmd.gui.widgets import button, make_table, make_table_item

if TYPE_CHECKING:
    from fcmd.apps.cmder._ops import QueueTask

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


__all__ = ["TransferQueueDialog"]


class TransferQueueDialog(QDialog):
    """传输队列：任务 / 条目状态、暂停、并发、取消与重试。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("传输队列")
        self.setMinimumSize(900, 600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self.pause_button = button("暂停队列")
        self.pause_button.clicked.connect(self._toggle_pause)
        controls.addWidget(self.pause_button)
        controls.addWidget(QLabel("并发"))
        self.parallel = QSpinBox(self)
        self.parallel.setRange(1, 8)
        self.parallel.setValue(owner.settings.queue_parallel)
        self.parallel.valueChanged.connect(self._set_parallel)
        controls.addWidget(self.parallel)
        controls.addStretch(1)
        for text, handler in (
            ("取消选中任务", self._cancel),
            ("重试选中任务", self._retry),
            ("重试全部失败", self._retry_all),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            controls.addWidget(widget)
        outer.addLayout(controls)

        self.tasks = make_table(
            ("任务", "动作", "目标", "状态", "完成 / 失败"),
            widths=(70, 80, 380, 110, 120),
            stretch_columns=(2,),
            parent=self,
        )
        self.tasks.setMinimumHeight(220)
        outer.addWidget(self.tasks, 1)
        self.items = make_table(
            ("来源", "目标", "状态", "错误"),
            widths=(360, 360, 100, 260),
            stretch_columns=(0, 1, 3),
            parent=self,
        )
        self.items.setMinimumHeight(200)
        outer.addWidget(self.items, 1)
        self.tasks.itemSelectionChanged.connect(self._refresh_items)
        close = button("关闭")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        outer.addLayout(row)
        self.refresh()

    def _selected_task(self) -> QueueTask | None:
        row = self.tasks.currentRow()
        tasks = self.owner.queue.tasks
        return tasks[row] if 0 <= row < len(tasks) else None

    def refresh(self) -> None:
        tasks = self.owner.queue.tasks
        self.tasks.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            self.tasks.setItem(row, 0, make_table_item(f"#{task.task_id}"))
            self.tasks.setItem(row, 1, make_table_item(task.action))
            self.tasks.setItem(row, 2, make_table_item(str(task.destination)))
            self.tasks.setItem(row, 3, make_table_item(task.status_text))
            self.tasks.setItem(row, 4, make_table_item(f"{task.done_count} / {task.failed_count}"))
        self.pause_button.setText("恢复队列" if self.owner.queue_paused() else "暂停队列")
        self._refresh_items()

    def _refresh_items(self) -> None:
        task = self._selected_task()
        items = task.items if task is not None else []
        self.items.setRowCount(len(items))
        for row, item in enumerate(items):
            self.items.setItem(row, 0, make_table_item(str(item.source)))
            self.items.setItem(row, 1, make_table_item(str(item.target)))
            self.items.setItem(row, 2, make_table_item(item_status_text(item)))
            self.items.setItem(row, 3, make_table_item(item.error))

    def _toggle_pause(self) -> None:
        self.owner.toggle_queue_pause()
        self.refresh()

    def _set_parallel(self, value: int) -> None:
        self.owner.set_queue_parallel(value)

    def _cancel(self) -> None:
        task = self._selected_task()
        if task is not None:
            message = self.owner.queue.cancel(task.task_id)
            self.owner.set_status(message)
            self.refresh()

    def _retry(self) -> None:
        task = self._selected_task()
        if task is not None:
            self.owner.retry_queue_items(task.task_id)
            self.refresh()

    def _retry_all(self) -> None:
        for task in list(self.owner.queue.tasks):
            if task.failed_count:
                self.owner.retry_queue_items(task.task_id)
        self.refresh()
