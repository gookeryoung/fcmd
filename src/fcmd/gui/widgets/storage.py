"""通用「磁盘 / 存储目录」卡片网格：参考 Steam 的多磁盘管理 UI。

组件只负责展示与发信号（打开 / 移除 / 设为主目录 / 添加），不读写任何应用配置；
调用方把自己的目录模型转换成 :class:`DiskLocation` 即可复用。适合设置页的模型目录、
下载目录、工作目录等多根目录管理。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui import icons as _icons
from fcmd.gui.widgets.buttons import button
from fcmd.gui.widgets.text import ElidedLabel, StatusTag
from fcmd.infras.system import format_bytes

__all__ = ["DiskAddTile", "DiskGrid", "DiskLocation", "DiskTile"]

_ICON_SIZE = 26
_MAX_COLUMNS = 3


@dataclass(frozen=True, slots=True)
class DiskLocation:
    """一条磁盘 / 目录展示信息；``primary`` 表示应用的主目录（下载落点等）。"""

    path: Path
    label: str
    fstype: str = ""
    total: int = 0
    free: int = 0
    exists: bool = True
    primary: bool = False
    path_text: str = ""
    primary_label: str = "主目录"

    @property
    def used(self) -> int:
        """已用空间（字节）。"""
        return max(self.total - self.free, 0)

    @property
    def percent(self) -> int:
        """已用比例（0–100 的整数，供进度条使用）。"""
        return round(self.used / self.total * 100) if self.total else 0

    @property
    def display_text(self) -> str:
        """展示路径：未显式给出时使用 ``os.fspath``。"""
        return self.path_text or os.fspath(self.path)


class DiskTile(QFrame):  # pragma: no cover - Qt 控件
    """单块磁盘卡片：硬盘标识 + 路径 + 容量 + 打开 / 设为主目录 / 移除。"""

    openRequested = Signal(str)
    removeRequested = Signal(str)
    primaryRequested = Signal(str)

    def __init__(self, location: DiskLocation, *, can_remove: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.location = location
        self.setObjectName("DiskCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(self._build_header(location))
        path = ElidedLabel(location.display_text, parent=self)
        path.setObjectName("DiskPath")
        path.setToolTip(str(location.path))
        layout.addWidget(path)
        layout.addWidget(self._build_usage(location))
        layout.addLayout(self._build_actions(location, can_remove=can_remove))
        layout.addStretch(1)

    def _build_header(self, location: DiskLocation) -> QHBoxLayout:
        """标题行：硬盘图标 + 盘符 / 挂载点 + 文件系统 + 主目录标记。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        icon = QLabel(self)
        icon.setPixmap(
            _icons.icon("disc", size=_ICON_SIZE, color=_icons.tone_color("accent")).pixmap(
                QSize(_ICON_SIZE, _ICON_SIZE)
            )
        )
        icon.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        row.addWidget(icon)
        drive = QLabel(location.label, self)
        drive.setObjectName("DiskDrive")
        row.addWidget(drive)
        meta = QLabel(location.fstype or "本地磁盘", self)
        meta.setObjectName("DiskMeta")
        row.addWidget(meta)
        row.addStretch(1)
        if location.primary:
            row.addWidget(StatusTag(location.primary_label, tone="accent", parent=self))
        return row

    def _build_usage(self, location: DiskLocation) -> QWidget:
        """容量行：进度条 + 剩余 / 总量；目录不存在或读不到容量时给文字提示。"""
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        bar = QProgressBar(box)
        bar.setObjectName("DiskBar")
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setRange(0, 100)
        bar.setValue(location.percent if location.total else 0)
        layout.addWidget(bar)
        usage = QLabel(self._usage_text(location), self)
        usage.setObjectName("DiskMeta")
        layout.addWidget(usage)
        return box

    @staticmethod
    def _usage_text(location: DiskLocation) -> str:
        if not location.exists:
            return "目录不存在，可打开父目录或移除"
        if not location.total:
            return "无法读取磁盘容量"
        return f"剩余 {format_bytes(location.free)} / 共 {format_bytes(location.total)}"

    def _build_actions(self, location: DiskLocation, *, can_remove: bool) -> QHBoxLayout:
        """动作行：打开 / 设为主目录 / 移除。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        open_button = button("打开", kind="ghost", icon="folder")
        open_button.clicked.connect(lambda: self.openRequested.emit(str(location.path)))
        row.addWidget(open_button)
        if not location.primary:
            primary_button = button("设为主目录", kind="ghost", icon="check")
            primary_button.setToolTip("写入 / 下载等默认落到这个目录")
            primary_button.clicked.connect(lambda: self.primaryRequested.emit(str(location.path)))
            row.addWidget(primary_button)
        row.addStretch(1)
        if can_remove:
            remove_button = button("移除", kind="ghost", icon="trash")
            remove_button.setToolTip("只从列表移除，不删除磁盘文件")
            remove_button.clicked.connect(lambda: self.removeRequested.emit(str(location.path)))
            row.addWidget(remove_button)
        return row


class DiskAddTile(QPushButton):  # pragma: no cover - Qt 控件
    """虚线「添加目录」卡片：与磁盘卡片同高并排。"""

    def __init__(self, text: str = "添加目录", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("DiskAdd")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(150)
        _icons.set_icon(self, "plus", size=18, tone="muted")


class DiskGrid(QWidget):  # pragma: no cover - Qt 控件
    """目录卡片网格：默认最多三列并排，超出后换行；末尾总是「添加」卡片。"""

    addRequested = Signal()
    openRequested = Signal(str)
    removeRequested = Signal(str)
    primaryRequested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        add_text: str = "添加目录",
        max_columns: int = _MAX_COLUMNS,
    ) -> None:
        super().__init__(parent)
        self._add_text = add_text
        self._max_columns = max(max_columns, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)

    def set_locations(self, locations: list[DiskLocation]) -> None:
        """按当前目录列表重建卡片；只剩一个目录时不允许移除。"""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        can_remove = len(locations) > 1
        tiles: list[QWidget] = []
        for location in locations:
            tile = DiskTile(location, can_remove=can_remove, parent=self)
            tile.openRequested.connect(self.openRequested.emit)
            tile.removeRequested.connect(self.removeRequested.emit)
            tile.primaryRequested.connect(self.primaryRequested.emit)
            tiles.append(tile)
        add_tile = DiskAddTile(self._add_text, self)
        add_tile.clicked.connect(self.addRequested.emit)
        tiles.append(add_tile)
        columns = min(self._max_columns, len(tiles))
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        for index, tile in enumerate(tiles):
            self._grid.addWidget(tile, index // columns, index % columns)
        # 末行不足列时补占位，避免「添加」卡独占整行把列宽拉满。
        for index in range(len(tiles), ((len(tiles) + columns - 1) // columns) * columns):
            spacer = QWidget(self)
            spacer.setMinimumHeight(1)
            self._grid.addWidget(spacer, index // columns, index % columns)
