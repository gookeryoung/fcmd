"""通用「磁盘 / 存储目录」卡片网格：参考 Steam 的多磁盘管理 UI。

组件只负责展示与发信号（打开 / 移除 / 设为主目录 / 添加），不读写任何应用配置；
调用方把自己的目录模型转换成 :class:`DiskLocation` 即可复用。适合设置页的模型目录、
下载目录、工作目录等多根目录管理。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui import icons as _icons
from fcmd.gui.widgets.buttons import ButtonStateMixin, button, confirm
from fcmd.gui.widgets.cards import Card
from fcmd.gui.widgets.forms import PathField
from fcmd.gui.widgets.metrics import ProgressBar
from fcmd.gui.widgets.text import ElidedLabel, StatusTag, hint_label
from fcmd.shared.formatting import format_bytes

__all__ = [
    "DiskAddTile",
    "DiskGrid",
    "DiskLocation",
    "DiskTile",
    "PathPairCard",
    "StorageCard",
    "StorageRoots",
    "default_disk_locations",
]

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
    meta: str = ""
    """副标题（文件系统 / 逻辑说明）；空串时回落到 ``fstype`` 或「本地磁盘」。"""

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
    """单块磁盘卡片：硬盘标识 + 路径 + 容量 + 更改 / 打开 / 设为主目录 / 移除。"""

    openRequested = Signal(str)
    changeRequested = Signal(str)
    removeRequested = Signal(str)
    primaryRequested = Signal(str)

    def __init__(
        self,
        location: DiskLocation,
        *,
        can_remove: bool,
        change_text: str = "",
        show_primary: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.location = location
        self.setObjectName("DiskCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        layout.addLayout(self._build_header(location, show_primary=show_primary))
        path = ElidedLabel(location.display_text, parent=self)
        path.setObjectName("DiskPath")
        path.setToolTip(str(location.path))
        layout.addWidget(path)
        layout.addWidget(self._build_usage(location))
        layout.addLayout(
            self._build_actions(
                location,
                can_remove=can_remove,
                change_text=change_text,
                show_primary=show_primary,
            )
        )
        layout.addStretch(1)

    def _build_header(self, location: DiskLocation, *, show_primary: bool = True) -> QHBoxLayout:
        """标题行：硬盘图标 + 盘符 / 挂载点 + 文件系统 + 主目录标记（可关）。"""
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
        meta = QLabel(location.meta or location.fstype or "本地磁盘", self)
        meta.setObjectName("DiskMeta")
        row.addWidget(meta)
        row.addStretch(1)
        if show_primary and location.primary:
            row.addWidget(StatusTag(location.primary_label, tone="accent", parent=self))
        return row

    def _build_usage(self, location: DiskLocation) -> QWidget:
        """容量行：进度条 + 剩余 / 总量；目录不存在或读不到容量时给文字提示。"""
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        bar = ProgressBar(height=6, parent=box)
        bar.setObjectName("DiskBar")
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

    def _build_actions(
        self,
        location: DiskLocation,
        *,
        can_remove: bool,
        change_text: str = "",
        show_primary: bool = True,
    ) -> QHBoxLayout:
        """动作行：更改（可选）/ 打开 / 设为主目录（可关）/ 移除。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(6)
        if change_text:
            change_button = button(change_text, kind="ghost")
            change_button.setToolTip("选择新的目录")
            change_button.clicked.connect(lambda: self.changeRequested.emit(str(location.path)))
            row.addWidget(change_button)
        open_button = button("打开", kind="ghost", icon="folder")
        open_button.clicked.connect(lambda: self.openRequested.emit(str(location.path)))
        row.addWidget(open_button)
        if show_primary and not location.primary:
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


class DiskAddTile(ButtonStateMixin, QPushButton):  # pragma: no cover - Qt 控件
    """虚线「添加目录」卡片：与磁盘卡片同高并排。"""

    def __init__(self, text: str = "添加目录", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("DiskAdd")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(150)
        self.set_icon_name("plus", size=18, tone="muted")


class DiskGrid(QWidget):  # pragma: no cover - Qt 控件
    """目录卡片网格：默认最多三列并排，超出后换行；末尾是「添加」卡片。

    ``add_text=None`` 时不出「添加」卡片（单目录、由卡片自带「更改」按钮的场景用）；
    ``change_text`` 给卡片的动作行加一个「更改」按钮（排在「打开」之前）；
    ``show_primary=False`` 时不显示主目录胶囊与「设为主目录」（目录地位平等的场景，
    如扫描目录）。
    """

    addRequested = Signal()
    openRequested = Signal(str)
    changeRequested = Signal(str)
    removeRequested = Signal(str)
    primaryRequested = Signal(str)

    def __init__(  # noqa: PLR0913 - 复用组件的可选外观参数成组出现
        self,
        parent: QWidget | None = None,
        *,
        add_text: str | None = "添加目录",
        change_text: str = "",
        max_columns: int = _MAX_COLUMNS,
        min_count: int = 1,
        max_count: int = 0,
        show_primary: bool = True,
    ) -> None:
        super().__init__(parent)
        self._add_text = add_text
        self._change_text = change_text
        self._show_primary = show_primary
        self._max_columns = max(max_columns, 1)
        self._min_count = max(min_count, 0)
        self._max_count = max(max_count, 0)
        """允许的最大目录数；``0`` 表示不限。达到上限后不再显示「添加」卡片。"""
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
        can_remove = len(locations) > self._min_count
        tiles: list[QWidget] = []
        for location in locations:
            tile = DiskTile(
                location,
                can_remove=can_remove,
                change_text=self._change_text,
                show_primary=self._show_primary,
                parent=self,
            )
            tile.openRequested.connect(self.openRequested.emit)
            tile.changeRequested.connect(self.changeRequested.emit)
            tile.removeRequested.connect(self.removeRequested.emit)
            tile.primaryRequested.connect(self.primaryRequested.emit)
            tiles.append(tile)
        can_add = self._add_text is not None and (self._max_count <= 0 or len(locations) < self._max_count)
        if can_add and self._add_text is not None:
            add_tile = DiskAddTile(self._add_text, self)
            add_tile.clicked.connect(self.addRequested.emit)
            tiles.append(add_tile)
        columns = min(self._max_columns, max(len(tiles), 1))
        # 先清掉上一轮的列拉伸：删掉目录后列数变少，残留的旧列 stretch 会继续
        # 平分宽度，让「添加目录」卡只占半格 / 三分之一格（例如 2 个目录 → 0 个）。
        for column in range(self._max_columns):
            self._grid.setColumnStretch(column, 0)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        for index, tile in enumerate(tiles):
            self._grid.addWidget(tile, index // columns, index % columns)
        # 末行不足列时补占位，避免「添加」卡独占整行把列宽拉满。
        for index in range(len(tiles), ((len(tiles) + columns - 1) // columns) * columns):
            spacer = QWidget(self)
            spacer.setMinimumHeight(1)
            self._grid.addWidget(spacer, index // columns, index % columns)


class PathPairCard(Card):  # pragma: no cover - Qt 控件
    """成对目录卡片：左侧 / 右侧两行路径 + 「交换左右」，用于比较 / 同步 / 迁移。

    与 :class:`StorageCard` 的分工：多目录管理需要磁盘容量与主目录语义时用
    StorageCard；只需要两个对等目录、且希望保持紧凑表单行的场景用本类。
    字段本身复用 :class:`~fcmd.gui.widgets.forms.PathField`，调用方可继续用
    ``card.left_field`` / ``card.right_field`` 读写路径与提示。
    """

    changed = Signal(str, str)

    def __init__(  # noqa: PLR0913 - 成对目录的标签 / 标题都是展示选项
        self,
        left: str = "",
        right: str = "",
        *,
        title: str = "文件夹对",
        left_label: str = "左侧：",
        right_label: str = "右侧：",
        swap_text: str = "交换左右",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.left_field = PathField(value=left, placeholder="选择文件夹", title="选择文件夹", parent=self)
        self.right_field = PathField(value=right, placeholder="选择文件夹", title="选择文件夹", parent=self)
        self.left_field.edit.textChanged.connect(self._emit_changed)
        self.right_field.edit.textChanged.connect(self._emit_changed)
        grid.addWidget(QLabel(left_label, self), 0, 0)
        grid.addWidget(self.left_field, 0, 1)
        grid.addWidget(QLabel(right_label, self), 1, 0)
        grid.addWidget(self.right_field, 1, 1)
        self.swap_button = button(swap_text)
        self.swap_button.clicked.connect(self.swap)
        grid.addWidget(self.swap_button, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

    def sides(self) -> tuple[str, str]:
        """当前左右目录（已去首尾空白）。"""
        return self.left_field.value(), self.right_field.value()

    def set_sides(self, left: str, right: str) -> None:
        """整体写入左右目录（不触发额外业务动作）。"""
        self.left_field.set_value(left)
        self.right_field.set_value(right)

    def swap(self) -> None:
        """交换左右目录（textChanged 会各发一次 changed）。"""
        left, right = self.sides()
        self.set_sides(right, left)

    def _emit_changed(self, _text: str = "") -> None:
        self.changed.emit(*self.sides())


class StorageRoots:
    """多根目录的纯逻辑：去重、增删、切换主目录（至少保留一个）。"""

    def __init__(self, roots: Sequence[Path | str] = (), *, min_count: int = 1) -> None:
        self.roots: list[Path] = []
        self.min_count = min_count
        self.set_roots(roots)

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(os.fspath(path.expanduser().resolve()))

    def set_roots(self, roots: Sequence[Path | str]) -> None:
        """整体替换并去重；保持传入顺序（首个即主目录）。"""
        seen: set[str] = set()
        normalized: list[Path] = []
        for item in roots:
            path = Path(str(item)).expanduser()
            key = self._key(path)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(path)
        self.roots = normalized

    @property
    def paths(self) -> list[Path]:
        return list(self.roots)

    @property
    def primary(self) -> Path | None:
        return self.roots[0] if self.roots else None

    def add(self, path: Path | str) -> bool:
        """追加目录；重复目录返回 ``False``。"""
        candidate = Path(str(path)).expanduser()
        if any(self._key(item) == self._key(candidate) for item in self.roots):
            return False
        self.roots.append(candidate)
        return True

    def remove(self, path: Path | str) -> bool:
        """移除目录；找不到或低于 ``min_count`` 时返回 ``False``（不删磁盘文件）。"""
        key = self._key(Path(str(path)).expanduser())
        if len(self.roots) <= self.min_count:
            return False
        remaining = [item for item in self.roots if self._key(item) != key]
        if len(remaining) == len(self.roots):
            return False
        self.roots = remaining
        return True

    def make_primary(self, path: Path | str) -> bool:
        """把目录移到首位（即主目录）；不存在时返回 ``False``。"""
        key = self._key(Path(str(path)).expanduser())
        chosen = [item for item in self.roots if self._key(item) == key]
        if not chosen:
            return False
        self.roots = [*chosen, *(item for item in self.roots if self._key(item) != key)]
        return True


def default_disk_locations(
    roots: Sequence[Path],
    *,
    primary_label: str = "主目录",
    labels: Sequence[str] = (),
    metas: Sequence[str] = (),
) -> list[DiskLocation]:
    """默认容量探测：盘符 / 挂载点 + ``shutil.disk_usage``；应用可覆盖以显示文件系统。

    ``labels`` / ``metas`` 按目录下标覆盖卡片标题与副标题，用于「左目录 / 右目录」
    这类逻辑分组；未给出时沿用盘符 / 挂载点。
    """
    locations: list[DiskLocation] = []
    for index, path in enumerate(roots):
        probe = path
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        total = free = 0
        try:
            usage = shutil.disk_usage(probe)
            total, free = int(usage.total), int(usage.free)
        except OSError:
            pass
        anchor = path.drive or path.anchor or str(path.parent)
        anchor = anchor.rstrip("/") or anchor
        label = labels[index] if index < len(labels) and labels[index] else anchor
        meta = metas[index] if index < len(metas) and metas[index] else (anchor if label != anchor else "")
        locations.append(
            DiskLocation(
                path=path,
                label=label,
                meta=meta,
                total=total,
                free=free,
                exists=path.is_dir(),
                primary=index == 0,
                primary_label=primary_label,
            )
        )
    return locations


class StorageCard(Card):  # pragma: no cover - Qt 控件
    """可复用的「多存储目录」设置卡：磁盘网格 + 添加 / 移除 / 设为主目录。

    宿主提供初始目录列表与 ``locations`` 映射（默认用容量探测）；任何增删 /
    更改 / 主目录切换只改组件内的 :class:`StorageRoots` 并发 ``changed``，由宿主
    负责落盘。三种常见用法：

    - **单目录**：``add_text=None``、``min_count=1``、``max_count=1``、
      ``show_primary=False``、``change_text="更改"``；
    - **多目录 + 主目录**（llmw 模型目录 / wslman 工作目录）：默认配置；
    - **多目录平权**（filescan 扫描目录）：``show_primary=False``，可设 ``max_count``；
    - **成对目录**（左右比较 / 同步）且要紧凑表单行时用 :class:`PathPairCard`。

    ``labels`` / ``metas`` 可按下标覆盖卡片标题与副标题（如「左目录 / 右目录」），
    达到 ``max_count`` 后不再显示「添加」卡片。

    ``confirm_remove=False`` 时移除目录不弹确认框：适合「移除只清列表、随时能再加回」
    的宿主（如 filescanw 扫描目录），避免每次点「移除」都多一次模态确认。
    """

    changed = Signal(object)
    message = Signal(str)

    def __init__(  # noqa: PLR0913 - 复用组件选项成组出现
        self,
        roots: Sequence[Path | str],
        *,
        title: str = "存储",
        hint: str = "",
        add_text: str | None = "添加目录",
        change_text: str = "",
        primary_label: str = "主目录",
        dialog_title: str = "添加目录",
        remove_title: str = "移除目录",
        confirm_remove: bool = True,
        note: str = "",
        min_count: int = 1,
        max_count: int = 0,
        labels: Sequence[str] = (),
        metas: Sequence[str] = (),
        show_primary: bool = True,
        locations: Callable[[Sequence[Path]], list[DiskLocation]] | None = None,
        open_path: Callable[[Path], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self._roots = StorageRoots([Path(str(item)) for item in roots], min_count=min_count)
        self._primary_label = primary_label
        self._labels = tuple(labels)
        self._metas = tuple(metas)
        self._locations = locations or (
            lambda items: default_disk_locations(
                items,
                primary_label=primary_label,
                labels=self._labels,
                metas=self._metas,
            )
        )
        self._open_path = open_path or (lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        self._dialog_title = dialog_title
        self._remove_title = remove_title
        self._confirm_remove = confirm_remove
        if hint:
            # 添加入口只保留网格末尾的虚线卡片（Steam 式），不再在标题行重复放按钮。
            self.body.addWidget(hint_label(hint, wrap=False))
        self.grid = DiskGrid(
            self,
            add_text=add_text,
            change_text=change_text,
            min_count=min_count,
            max_count=max_count,
            show_primary=show_primary,
        )
        self.grid.addRequested.connect(self._add)
        self.grid.changeRequested.connect(self._change)
        self.grid.openRequested.connect(self._open)
        self.grid.removeRequested.connect(self._remove)
        self.grid.primaryRequested.connect(self._make_primary)
        self.body.addWidget(self.grid)
        self.note_label = hint_label(note)
        self.note_label.setVisible(bool(note))
        self.body.addWidget(self.note_label)
        self._refresh()

    # ------------------------------------------------------------------ #
    # 对外状态
    # ------------------------------------------------------------------ #
    def roots(self) -> list[Path]:
        return self._roots.paths

    def set_roots(self, roots: Sequence[Path | str]) -> None:
        """外部配置刷新后整体替换列表（不触发 ``changed``）。"""
        self._roots.set_roots(roots)
        self._refresh()

    def set_note(self, note: str) -> None:
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))

    def _refresh(self) -> None:
        self.grid.set_locations(self._locations(self._roots.paths))

    def _notify(self, message: str) -> None:
        self._refresh()
        self.message.emit(message)
        self.changed.emit(self._roots.paths)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def swap(self, first: int = 0, second: int = 1) -> bool:
        """交换两个目录的位置（双目录 / 手动排序场景）；越界或相同时返回 ``False``。"""
        roots = self._roots.paths
        if first == second or not (0 <= first < len(roots) and 0 <= second < len(roots)):
            return False
        roots[first], roots[second] = roots[second], roots[first]
        self._roots.set_roots(roots)
        self._notify("已交换目录位置")
        return True

    def _change(self, path: str) -> None:
        """把某个目录替换为新选目录（单目录卡片的「更改」走这里）。"""
        roots = self._roots.paths
        target = Path(str(path)).expanduser()
        index = next((i for i, item in enumerate(roots) if self._roots._key(item) == self._roots._key(target)), -1)
        if index < 0:
            self.message.emit(f"目录不在列表中：{path}")
            return
        chosen = QFileDialog.getExistingDirectory(self, self._dialog_title, str(roots[index]))
        if not chosen:
            return
        new_path = Path(chosen).expanduser()
        if any(i != index and self._roots._key(item) == self._roots._key(new_path) for i, item in enumerate(roots)):
            self.message.emit(f"目录已在列表中：{chosen}")
            return
        roots[index] = new_path
        self._roots.set_roots(roots)
        self._notify(f"已更改目录：{chosen}")

    def _add(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, self._dialog_title, str(Path.home()))
        if not chosen:
            return
        if not self._roots.add(chosen):
            self.message.emit(f"目录已在列表中：{chosen}")
            return
        self._notify(f"已添加目录：{chosen}")

    def _remove(self, path: str) -> None:
        if len(self._roots.roots) <= self._roots.min_count:
            self.message.emit("至少要保留一个目录")
            return
        if self._confirm_remove and not confirm(
            self, self._remove_title, f"从列表移除以下目录？\n{path}\n\n磁盘文件不会被删除。"
        ):
            return
        if not self._roots.remove(path):
            self.message.emit(f"目录不在列表中：{path}")
            return
        self._notify(f"已移除目录：{path}")

    def _make_primary(self, path: str) -> None:
        if not self._roots.make_primary(path):
            return
        self._notify(f"主目录已切换为：{path}")

    def _open(self, path: str) -> None:
        self._open_path(Path(path))
