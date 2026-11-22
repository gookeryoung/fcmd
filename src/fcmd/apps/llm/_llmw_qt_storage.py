"""llmw 存储卡片适配层：把模型目录模型转成共享「磁盘卡片」组件。

磁盘卡片的布局 / 样式 / 信号全部在 :mod:`fcmd.gui.widgets.storage`；本模块只负责
``StorageLocation`` → ``DiskLocation`` 的字段映射与 llmw 的默认文案。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fcmd.apps.llm._llmw_storage import StorageLocation, display_path
from fcmd.gui.widgets import DiskAddTile, DiskGrid, DiskLocation, DiskTile

__all__ = ["StorageAddTile", "StorageGrid", "StorageTile"]

StorageTile = DiskTile
StorageAddTile = DiskAddTile


class StorageGrid(DiskGrid):  # pragma: no cover - Qt 控件
    """llmw 模型目录网格：默认文案对齐「添加模型目录 / 下载目标」。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent, add_text="添加模型目录")

    def set_locations(self, locations: list[StorageLocation]) -> None:
        super().set_locations([_disk_location(location) for location in locations])


def _disk_location(location: StorageLocation) -> DiskLocation:
    return DiskLocation(
        path=Path(location.path),
        label=location.disk_label,
        fstype=location.fstype,
        total=location.total,
        free=location.free,
        exists=location.exists,
        primary=location.primary,
        path_text=display_path(location.path),
        primary_label="下载目标",
    )
