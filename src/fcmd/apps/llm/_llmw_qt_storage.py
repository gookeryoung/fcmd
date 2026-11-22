"""llmw 设置页的「存储」专用 UI：参考 Steam 的多磁盘卡片。

每个模型目录一块卡片，并排展示盘符 / 挂载点、文件系统、路径与剩余空间；
末尾的虚线卡片用于添加目录。卡片只发信号，增删与主目录切换由设置页写配置。
"""

from __future__ import annotations

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

from fcmd.apps.llm._llmw_storage import StorageLocation, display_path
from fcmd.apps.llm.llmbase import human_size
from fcmd.gui import icons as _icons
from fcmd.gui.widgets import ElidedLabel, StatusTag, button

__all__ = ["StorageGrid", "StorageTile"]

_ICON_SIZE = 26
_MAX_COLUMNS = 3


class StorageTile(QFrame):  # pragma: no cover - Qt 控件
    """单个模型目录的磁盘卡片：硬盘标识 + 路径 + 容量 + 动作。"""

    openRequested = Signal(str)
    removeRequested = Signal(str)
    primaryRequested = Signal(str)

    def __init__(self, location: StorageLocation, *, can_remove: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.location = location
        self.setObjectName("StorageCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(self._build_header(location))
        path = ElidedLabel(display_path(location.path), parent=self)
        path.setObjectName("StoragePath")
        path.setToolTip(str(location.path))
        layout.addWidget(path)
        layout.addWidget(self._build_usage(location))
        layout.addLayout(self._build_actions(location, can_remove=can_remove))
        layout.addStretch(1)

    def _build_header(self, location: StorageLocation) -> QHBoxLayout:
        """标题行：硬盘图标 + 盘符 + 文件系统 + 主目录标记。"""
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
        drive = QLabel(location.disk_label, self)
        drive.setObjectName("StorageDrive")
        row.addWidget(drive)
        meta = QLabel(location.fstype or "本地磁盘", self)
        meta.setObjectName("StorageMeta")
        row.addWidget(meta)
        row.addStretch(1)
        if location.primary:
            row.addWidget(StatusTag("下载目标", tone="accent", parent=self))
        return row

    def _build_usage(self, location: StorageLocation) -> QWidget:
        """容量行：进度条 + 剩余 / 总量；目录不存在或读不到容量时给文字提示。"""
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        bar = QProgressBar(box)
        bar.setObjectName("StorageBar")
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setRange(0, 100)
        bar.setValue(location.percent if location.total else 0)
        layout.addWidget(bar)
        usage = QLabel(self._usage_text(location), self)
        usage.setObjectName("StorageMeta")
        layout.addWidget(usage)
        return box

    @staticmethod
    def _usage_text(location: StorageLocation) -> str:
        if not location.exists:
            return "目录不存在，可打开父目录或移除"
        if not location.total:
            return "无法读取磁盘容量"
        return f"剩余 {human_size(location.free)} / 共 {human_size(location.total)}"

    def _build_actions(self, location: StorageLocation, *, can_remove: bool) -> QHBoxLayout:
        """动作行：打开 / 设为主目录 / 移除。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        open_button = button("打开", kind="ghost", icon="folder")
        open_button.clicked.connect(lambda: self.openRequested.emit(str(location.path)))
        row.addWidget(open_button)
        if not location.primary:
            primary_button = button("设为主目录", kind="ghost", icon="check")
            primary_button.setToolTip("下载与转换产物写入这个目录")
            primary_button.clicked.connect(lambda: self.primaryRequested.emit(str(location.path)))
            row.addWidget(primary_button)
        row.addStretch(1)
        if can_remove:
            remove_button = button("移除", kind="ghost", icon="trash")
            remove_button.setToolTip("只从列表移除，不删除磁盘文件")
            remove_button.clicked.connect(lambda: self.removeRequested.emit(str(location.path)))
            row.addWidget(remove_button)
        return row


class StorageAddTile(QPushButton):  # pragma: no cover - Qt 控件
    """虚线「添加模型目录」卡片：与磁盘卡片同高并排。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("添加模型目录", parent)
        self.setObjectName("StorageAdd")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(150)
        _icons.set_icon(self, "plus", size=18, tone="muted")


class StorageGrid(QWidget):  # pragma: no cover - Qt 控件
    """模型目录卡片网格：最多三列并排，超出后换行；末尾总是「添加」卡片。"""

    addRequested = Signal()
    openRequested = Signal(str)
    removeRequested = Signal(str)
    primaryRequested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)

    def set_locations(self, locations: list[StorageLocation]) -> None:
        """按当前目录列表重建卡片；只剩一个目录时不允许移除。"""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        can_remove = len(locations) > 1
        tiles: list[QWidget] = []
        for location in locations:
            tile = StorageTile(location, can_remove=can_remove, parent=self)
            tile.openRequested.connect(self.openRequested.emit)
            tile.removeRequested.connect(self.removeRequested.emit)
            tile.primaryRequested.connect(self.primaryRequested.emit)
            tiles.append(tile)
        add_tile = StorageAddTile(self)
        add_tile.clicked.connect(self.addRequested.emit)
        tiles.append(add_tile)
        columns = min(_MAX_COLUMNS, len(tiles))
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        for index, tile in enumerate(tiles):
            self._grid.addWidget(tile, index // columns, index % columns)
        # 末行不足列时补占位，避免「添加」卡独占整行把列宽拉满。
        for index in range(len(tiles), ((len(tiles) + columns - 1) // columns) * columns):
            spacer = QWidget(self)
            spacer.setMinimumHeight(1)
            self._grid.addWidget(spacer, index // columns, index % columns)
