"""cmderw（Qt）面板层：文件面板、快速查看与缩略图控件。

数据源与操作全部复用 :mod:`fcmd.apps.cmder.app` 的纯逻辑（扫描、排序、历史、
归档成员折算、预览读取）与 :mod:`fcmd.apps.cmder._ops` / ``_preview`` 的判定；
本模块只负责 Qt 控件、后台扫描与缩略图队列。

面板约定：

- 标签页保存完整的 :class:`PanelState`（路径 / 历史 / 排序 / 视图模式 / 选择），
  切换标签只切换状态并重扫，不重建控件；
- 扫描走 :func:`fcmd.gui.qt.workers.submit`，世代号丢弃过期结果；
- 缩略图用 ``QListWidget`` 图标模式，图片经 Pillow 后台解码，非图片显示类型徽标；
- 长路径在路径栏中可编辑，卷下拉复用 ``_volumes`` 的枚举口径。
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, override

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QTabBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._ops import (
    COLUMN_SORT,
    PANEL_COLUMNS,
    THUMBNAIL_BADGE,
    THUMBNAIL_GAP,
    THUMBNAIL_IMAGE,
    THUMBNAIL_TILE_SIZE,
    VIEW_MODE_BRIEF,
    VIEW_MODE_THUMBNAILS,
    VIEW_MODES,
    ColorSubject,
    filter_names,
    thumbnail_plan,
)
from fcmd.apps.cmder._tabs import tab_bar_label
from fcmd.apps.cmder._volumes import enumerate_volumes
from fcmd.gui.qt.widgets import button, hint_label, mono_font
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["FilePanelWidget", "QuickView", "color_subject"]

_COLUMN_IDS = tuple(column for column, _title, _width in PANEL_COLUMNS)
_IMAGE_LIMIT = 32 * 1024 * 1024


class QuickView(QWidget):
    """内嵌快速查看：文本 / 图片 / 提示三态。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.setSpacing(4)
        self.title = hint_label("快速查看（Ctrl+Q）", wrap=False)
        layout.addWidget(self.title)
        self.stack = QStackedWidget(self)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(mono_font())
        self.stack.addWidget(self.text)
        self.image_scroll = QScrollArea(self)
        self.image_scroll.setWidgetResizable(True)
        self.image = QLabel(self.image_scroll)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_scroll.setWidget(self.image)
        self.stack.addWidget(self.image_scroll)
        self.note = QLabel("", self)
        self.note.setWordWrap(True)
        self.note.setObjectName("Hint")
        self.stack.addWidget(self.note)
        layout.addWidget(self.stack, 1)
        self._pixmap: QPixmap | None = None

    def clear(self) -> None:
        """清空并显示占位提示。"""
        self.title.setText("快速查看（Ctrl+Q）")
        self.text.clear()
        self.image.clear()
        self.note.setText("")
        self._pixmap = None
        self.stack.setCurrentWidget(self.note)

    def show_note(self, title: str, message: str) -> None:
        """显示一条提示（目录、二进制、读取失败）。"""
        self.title.setText(title)
        self.note.setText(message)
        self.stack.setCurrentWidget(self.note)

    def show_text(self, title: str, text: str) -> None:
        """显示文本内容（只读、等宽）。"""
        self.title.setText(title)
        self.text.setPlainText(text)
        self.stack.setCurrentWidget(self.text)

    def show_image(self, title: str, path: Path) -> None:
        """显示图片（按视口等比缩放，不做放大）。"""
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.show_note(title, "图片解码失败")
            return
        self.title.setText(title)
        self._pixmap = pixmap
        self._apply_pixmap()
        self.stack.setCurrentWidget(self.image_scroll)

    def _apply_pixmap(self) -> None:
        if self._pixmap is None:
            return
        available = self.image_scroll.viewport().size()
        if available.width() > 4 and available.height() > 4:
            scaled = self._pixmap.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            scaled = self._pixmap
        self.image.setPixmap(scaled)

    @override
    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.stack.currentWidget() is self.image_scroll:
            self._apply_pixmap()


class FilePanelWidget(QWidget):
    """单个文件面板：标签 + 路径 + 过滤 + 列表 / 缩略图 + 归档浏览。"""

    activated = Signal(object)
    selectionChanged = Signal()
    refreshed = Signal()
    contextRequested = Signal(object)

    def __init__(self, window: Any, settings: Any, side: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.owner = window
        self.side = side
        self.settings = settings
        self.tabs: list[core.PanelState] = self._initial_tabs()
        self.index = self._initial_index()
        self.archive: Path | None = None
        self.archive_prefix = ""
        self._archive_members: list[core.ArchiveMember] = []
        self._archive_temp: Path | None = None
        self.entries: list[core.FileEntry] = []
        self._generation = 0
        self._thumb_generation = 0
        self._filter_definition: Any = None
        self._active = False
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _initial_tabs(self) -> list[core.PanelState]:
        raw = self.settings.left_tabs if self.side == "left" else self.settings.right_tabs
        active = self.settings.left if self.side == "left" else self.settings.right
        tabs = [state for state in raw if isinstance(state, core.PanelState)]
        return tabs or [active]

    def _initial_index(self) -> int:
        raw = self.settings.left_tab_index if self.side == "left" else self.settings.right_tab_index
        return core.normalize_tab_index(raw, len(self.tabs))

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.tabs_bar = QTabBar(self)
        self.tabs_bar.setTabsClosable(True)
        self.tabs_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs_bar.currentChanged.connect(self._on_tab_changed)
        self.tabs_bar.tabCloseRequested.connect(lambda _index: self.close_tab())
        self.tabs_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs_bar.customContextMenuRequested.connect(self._tab_context_menu)
        layout.addWidget(self.tabs_bar)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(4)
        self.back_button = button("←", tooltip="后退 (Alt+Left)")
        self.back_button.clicked.connect(self.go_back)
        self.forward_button = button("→", tooltip="前进 (Alt+Right)")
        self.forward_button.clicked.connect(self.go_forward)
        self.up_button = button("↑", tooltip="上级 (Backspace)")
        self.up_button.clicked.connect(self.go_up)
        for widget in (self.back_button, self.forward_button, self.up_button):
            widget.setMaximumWidth(40)
            path_row.addWidget(widget)
        self.path_edit = QLineEdit(self)
        self.path_edit.returnPressed.connect(self._on_path_enter)
        path_row.addWidget(self.path_edit, 1)
        self.volume_combo = QComboBox(self)
        self.volume_combo.setMinimumWidth(150)
        self._populate_volumes()
        self.volume_combo.activated.connect(self._on_volume)
        path_row.addWidget(self.volume_combo)
        layout.addLayout(path_row)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)
        filter_row.addWidget(QLabel("过滤"))
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText("快速过滤 (Ctrl+S)")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _text: self.refresh())
        filter_row.addWidget(self.filter_edit, 1)
        self.count_label = hint_label("", wrap=False)
        filter_row.addWidget(self.count_label)
        layout.addLayout(filter_row)

        self.stack = QStackedWidget(self)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels([title for _id, title, _width in PANEL_COLUMNS])
        for index, (_id, _title, width) in enumerate(PANEL_COLUMNS):
            self.tree.setColumnWidth(index, width)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionsClickable(True)
        self.tree.header().sectionClicked.connect(self._on_header_clicked)
        self.tree.itemSelectionChanged.connect(self.selectionChanged.emit)
        self.tree.itemDoubleClicked.connect(lambda item, _column: self._activate_row(item))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.contextRequested.emit)
        self.stack.addWidget(self.tree)

        self.thumb_list = QListWidget(self)
        self.thumb_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumb_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumb_list.setMovement(QListWidget.Movement.Static)
        self.thumb_list.setIconSize(QSize(THUMBNAIL_TILE_SIZE, THUMBNAIL_TILE_SIZE))
        self.thumb_list.setGridSize(QSize(THUMBNAIL_TILE_SIZE + 2 * THUMBNAIL_GAP, THUMBNAIL_TILE_SIZE + 30))
        self.thumb_list.setWordWrap(True)
        self.thumb_list.setUniformItemSizes(True)
        self.thumb_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.thumb_list.itemSelectionChanged.connect(self.selectionChanged.emit)
        self.thumb_list.itemDoubleClicked.connect(self._activate_row)
        self.thumb_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.thumb_list.customContextMenuRequested.connect(self.contextRequested.emit)
        self.stack.addWidget(self.thumb_list)
        layout.addWidget(self.stack, 1)

        self._sync_tabs()
        self._apply_view_mode()
        self.set_path(Path(self.state.path or core.default_directory()), push=False)

    # ------------------------------------------------------------------ #
    # 状态与标签
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> core.PanelState:
        """当前活动标签的可变状态。"""
        return self.tabs[self.index]

    def panel_state(self) -> core.PanelState:
        """持久化用状态（含当前选择）。"""
        self.state.selection = [entry.name for entry in self.selected_entries()]
        return self.state

    def _sync_tabs(self) -> None:
        self.tabs_bar.blockSignals(True)
        while self.tabs_bar.count():
            self.tabs_bar.removeTab(0)
        for state in self.tabs:
            self.tabs_bar.addTab(tab_bar_label(state.path, state.title, locked=state.locked))
        self.tabs_bar.setCurrentIndex(self.index)
        self.tabs_bar.blockSignals(False)

    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self.tabs) or index == self.index:
            return
        self.state.selection = [entry.name for entry in self.selected_entries()]
        self.index = index
        self.archive = None
        self.archive_prefix = ""
        self._archive_members = []
        self.path_edit.setText(self.state.path)
        self.refresh()
        self.selectionChanged.emit()

    def _tab_context_menu(self, position: Any) -> None:
        menu = self.owner.build_tab_menu(self)
        if menu is not None:
            menu.exec(self.tabs_bar.mapToGlobal(position))

    def new_tab(self, path: str | None = None) -> None:
        """新建标签：默认沿用当前目录，可指定初始路径。"""
        target = path or self.state.path or str(core.default_directory())
        state = core.PanelState(path=target)
        state.history, state.history_index = core.push_history([], -1, target)
        self.tabs.append(state)
        self.index = len(self.tabs) - 1
        self._sync_tabs()
        self.archive = None
        self.refresh()

    def close_tab(self) -> bool:
        """关闭当前标签（锁定的标签拒绝关闭，至少保留一个）。"""
        if self.state.locked or len(self.tabs) <= 1:
            return False
        self.tabs.pop(self.index)
        self.index = min(self.index, len(self.tabs) - 1)
        self._sync_tabs()
        self.refresh()
        return True

    def close_other_tabs(self) -> int:
        """关闭除当前外所有未锁定标签，返回关闭数量。"""
        from fcmd.apps.cmder._tabs import close_other_indices

        keep = close_other_indices(len(self.tabs), self.index, [i for i, s in enumerate(self.tabs) if s.locked])
        current = self.state
        closed = 0
        for index in sorted((i for i in range(len(self.tabs)) if i not in keep), reverse=True):
            self.tabs.pop(index)
            closed += 1
        self.index = next((i for i, state in enumerate(self.tabs) if state is current), 0)
        self._sync_tabs()
        self.refresh()
        return closed

    def toggle_lock(self) -> bool:
        """切换当前标签锁定状态。"""
        self.state.locked = not self.state.locked
        self._sync_tabs()
        return self.state.locked

    def rename_tab(self, title: str) -> None:
        """设置标签自定义标题（空串恢复路径名）。"""
        from fcmd.apps.cmder._tabs import normalize_tab_title

        self.state.title = normalize_tab_title(title)
        self._sync_tabs()

    def next_tab(self, delta: int = 1) -> None:
        """循环切换标签。"""
        if len(self.tabs) > 1:
            self.tabs_bar.setCurrentIndex((self.index + delta) % len(self.tabs))

    def set_active(self, active: bool) -> None:
        """标记活动面板（边框由窗口样式区分）。"""
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    # ------------------------------------------------------------------ #
    # 路径与导航
    # ------------------------------------------------------------------ #
    def _populate_volumes(self) -> None:
        self.volume_combo.clear()
        self.volume_combo.addItem("卷…")
        self._volumes = enumerate_volumes()
        for volume in self._volumes:
            self.volume_combo.addItem(volume.label)

    def _on_volume(self, index: int) -> None:
        if 0 < index <= len(self._volumes):
            self.set_path(self._volumes[index - 1].mount)

    def _on_path_enter(self) -> None:
        target, error = core.resolve_path_input(self.path_edit.text(), base=Path(self.state.path or "."))
        if error:
            self.owner.show_error("路径无效", error)
            return
        self.set_path(target)

    def set_path(self, path: Path, *, push: bool = True) -> None:
        """切换目录（默认写入历史）；归档浏览状态下先退出。"""
        try:
            directory = path.resolve(strict=False)
        except OSError as exc:
            self.owner.show_error("路径无效", str(exc))
            return
        if directory.is_file():
            directory = directory.parent
        if not directory.is_dir():
            self.owner.show_error("路径无效", f"目录不存在: {directory}")
            return
        self._clear_archive()
        text = str(directory)
        if push and text != self.state.path:
            self.state.history, self.state.history_index = core.push_history(
                self.state.history, self.state.history_index, text
            )
        self.state.path = text
        self.path_edit.setText(text)
        self.refresh()

    def go_up(self) -> None:
        if self.archive is not None:
            self.archive_up()
            return
        parent = core.parent_directory(Path(self.state.path))
        if parent is not None:
            self.set_path(parent)

    def go_back(self) -> None:
        if self.archive is not None:
            self._clear_archive()
        index = core.history_back(self.state.history, self.state.history_index)
        if index == self.state.history_index:
            return
        self.state.history_index = index
        self.state.path = self.state.history[index]
        self.path_edit.setText(self.state.path)
        self.refresh()

    def go_forward(self) -> None:
        if self.archive is not None:
            self._clear_archive()
        index = core.history_forward(self.state.history, self.state.history_index)
        if index == self.state.history_index:
            return
        self.state.history_index = index
        self.state.path = self.state.history[index]
        self.path_edit.setText(self.state.path)
        self.refresh()

    # ------------------------------------------------------------------ #
    # 扫描与渲染
    # ------------------------------------------------------------------ #
    def refresh(self, select: str | None = None) -> None:
        """重新扫描当前目录（后台线程）并保留可见选择。"""
        directory = Path(self.state.path or core.default_directory())
        self._generation += 1
        generation = self._generation
        previous = tuple(filter_names([entry.name for entry in self.entries], self.filter_edit.text()))
        if select:
            previous = (*previous, select)
        request = core.PanelScanRequest(
            generation=generation,
            directory=directory,
            archive=self.archive,
            archive_members=tuple(self._archive_members),
            archive_prefix=self.archive_prefix,
            branch_view=self.state.branch_view,
            show_hidden=self.settings.show_hidden,
            sort_key=self.state.sort_key,
            descending=self.state.descending,
            filter_text=self.filter_edit.text(),
            filter_definition=self._filter_definition,
            previous=previous,
        )
        self.count_label.setText("正在读取…")
        submit(core.run_panel_scan, request, on_done=self._apply_scan, on_error=self._scan_failed)

    def _scan_failed(self, message: str) -> None:
        self.count_label.setText("读取失败")
        self.owner.show_error("读取目录失败", message)

    def _apply_scan(self, result: core.PanelScanResult) -> None:
        if result.generation != self._generation:
            return
        if result.error is not None:
            self.owner.show_error("读取目录失败", str(result.error))
            self.count_label.setText("读取失败")
            return
        self.entries = result.entries
        self._render_entries(set(result.previous))
        self.count_label.setText(f"{len(self.entries)} 项" + ("（已截断）" if result.truncated else ""))
        self._update_nav_buttons()
        self.refreshed.emit()

    def _row_colors(self, entry: core.FileEntry) -> tuple[QColor | None, QColor | None]:
        if self.archive is not None:
            return None, None
        return self.owner.color_for(entry)

    def _render_entries(self, previous: set[str]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self.thumb_list.clear()
        selected: QTreeWidgetItem | None = None
        for entry in self.entries:
            item = QTreeWidgetItem(
                [
                    entry.label,
                    Path(entry.name).suffix.lstrip(".").upper(),
                    core.format_size(entry.size, is_dir=entry.is_dir),
                    core.format_mtime(entry.mtime),
                    core.describe_attributes(entry),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(0, entry.label)
            if entry.is_dir:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            foreground, background = self._row_colors(entry)
            for column in range(len(PANEL_COLUMNS)):
                if foreground is not None:
                    item.setForeground(column, foreground)
                if background is not None:
                    item.setBackground(column, background)
            self.tree.addTopLevelItem(item)
            if entry.name in previous:
                item.setSelected(True)
                selected = selected or item
            thumb = QListWidgetItem(entry.label)
            thumb.setData(Qt.ItemDataRole.UserRole, entry)
            thumb.setToolTip(entry.label)
            thumb.setSizeHint(QSize(THUMBNAIL_TILE_SIZE + 2 * THUMBNAIL_GAP, THUMBNAIL_TILE_SIZE + 30))
            self.thumb_list.addItem(thumb)
        if selected is not None:
            self.tree.setCurrentItem(selected)
        self.tree.blockSignals(False)
        self._schedule_thumbnails()
        self.selectionChanged.emit()

    def _schedule_thumbnails(self) -> None:
        if self.state.view_mode != VIEW_MODE_THUMBNAILS:
            return
        self._thumb_generation += 1
        generation = self._thumb_generation
        for index in range(self.thumb_list.count()):
            item = self.thumb_list.item(index)
            entry = item.data(Qt.ItemDataRole.UserRole)
            if entry is None or entry.is_dir:
                continue
            plan = THUMBNAIL_BADGE if self.archive is not None else thumbnail_plan(entry.name)
            if plan != THUMBNAIL_IMAGE:
                continue
            submit(
                core.load_thumbnail_payload,
                entry.path,
                on_done=lambda payload, row=index, gen=generation: self._apply_thumbnail(row, gen, payload),
            )

    def _apply_thumbnail(self, row: int, generation: int, payload: object) -> None:
        if generation != self._thumb_generation or not isinstance(payload, bytes):
            return
        if row >= self.thumb_list.count():
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(payload, "PNG"):
            self.thumb_list.item(row).setIcon(QIcon(pixmap))

    def _update_nav_buttons(self) -> None:
        self.back_button.setEnabled(self.state.history_index > 0 or self.archive is not None)
        self.forward_button.setEnabled(self.state.history_index < len(self.state.history) - 1)
        parent = core.parent_directory(Path(self.state.path))
        self.up_button.setEnabled(self.archive is not None or parent is not None)

    def set_view_mode(self, mode: str) -> None:
        """切换简要 / 详细 / 缩略图视图。"""
        if mode not in VIEW_MODES:
            return
        self.state.view_mode = mode
        self._apply_view_mode()
        if mode == VIEW_MODE_THUMBNAILS:
            self._schedule_thumbnails()

    def _apply_view_mode(self) -> None:
        if self.state.view_mode == VIEW_MODE_THUMBNAILS:
            self.stack.setCurrentWidget(self.thumb_list)
            return
        self.stack.setCurrentWidget(self.tree)
        brief = self.state.view_mode == VIEW_MODE_BRIEF
        for index, column in enumerate(_COLUMN_IDS):
            self.tree.setColumnHidden(index, brief and column != "name")

    def sort_by(self, column: str) -> None:
        """点击列头：同一列切换升降序，否则换排序列。"""
        key = COLUMN_SORT.get(column, "name")
        if self.state.sort_key == key:
            self.state.descending = not self.state.descending
        else:
            self.state.sort_key = key
            self.state.descending = False
        self.refresh()

    def _on_header_clicked(self, index: int) -> None:
        if 0 <= index < len(_COLUMN_IDS):
            self.sort_by(_COLUMN_IDS[index])

    # ------------------------------------------------------------------ #
    # 选择
    # ------------------------------------------------------------------ #
    def _visible_items(self) -> list[Any]:
        if self.stack.currentWidget() is self.thumb_list:
            return [self.thumb_list.item(i) for i in range(self.thumb_list.count())]
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    @staticmethod
    def _entry_of(item: Any) -> core.FileEntry | None:
        if isinstance(item, QTreeWidgetItem):
            return item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(item, QListWidgetItem):
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def selected_entries(self) -> list[core.FileEntry]:
        """当前选中的条目（按列表顺序）。"""
        widget = self.thumb_list if self.stack.currentWidget() is self.thumb_list else self.tree
        items = widget.selectedItems()
        return [entry for item in items if (entry := self._entry_of(item)) is not None]

    def select_name(self, name: str) -> None:
        """按名称选中并滚动到可见。"""
        self.select_names([name], focus=True)

    def select_names(self, names: Sequence[str], *, focus: bool = False) -> int:
        """批量选中名称，返回命中数量。"""
        wanted = set(names)
        count = 0
        first: Any = None
        for item in self._visible_items():
            entry = self._entry_of(item)
            if entry is not None and entry.name in wanted:
                item.setSelected(True)
                count += 1
                first = first or item
        if focus and first is not None:
            widget = self.thumb_list if self.stack.currentWidget() is self.thumb_list else self.tree
            widget.setCurrentItem(first)
            widget.scrollToItem(first)
        self.selectionChanged.emit()
        return count

    def select_all(self) -> None:
        widget = self.thumb_list if self.stack.currentWidget() is self.thumb_list else self.tree
        widget.selectAll()

    def clear_selection(self) -> None:
        widget = self.thumb_list if self.stack.currentWidget() is self.thumb_list else self.tree
        widget.clearSelection()

    def invert_selection(self) -> int:
        """反选并返回新选中数量。"""
        count = 0
        for item in self._visible_items():
            item.setSelected(not item.isSelected())
            count += int(item.isSelected())
        self.selectionChanged.emit()
        return count

    def apply_mask_selection(self, mask: str, *, select: bool) -> int:
        """按掩码选中 / 取消，返回改动数量。"""
        wanted = set(core.match_names([entry.name for entry in self.entries], mask))
        count = 0
        for item in self._visible_items():
            entry = self._entry_of(item)
            if entry is not None and entry.name in wanted:
                item.setSelected(select)
                count += 1
        self.selectionChanged.emit()
        return count

    def _activate_row(self, item: Any) -> None:
        entry = self._entry_of(item)
        if entry is not None:
            self.activated.emit(entry)

    # ------------------------------------------------------------------ #
    # 归档浏览
    # ------------------------------------------------------------------ #
    def open_archive(self, archive: Path) -> bool:
        """进入归档只读浏览（不支持的格式提示后返回 False）。"""
        if not core.archive_browsable(archive):
            self.owner.show_error("无法浏览", f"不支持浏览该压缩包格式（7z / rar 等请用「解压…」）：\n{archive.name}")
            return False
        try:
            members = core.read_archive_members(archive)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self.owner.show_error("无法浏览", str(exc))
            return False
        self.archive = archive
        self.archive_prefix = ""
        self._archive_members = list(members)
        self.refresh()
        return True

    def archive_enter(self, name: str) -> None:
        if self.archive is None:
            return
        self.archive_prefix = f"{self.archive_prefix}/{name}".strip("/") if self.archive_prefix else name
        self.refresh()

    def archive_up(self) -> None:
        if self.archive is None:
            return
        if not self.archive_prefix:
            self.exit_archive()
            return
        parent = str(PurePosixPath(self.archive_prefix).parent)
        self.archive_prefix = "" if parent == "." else parent
        self.refresh()

    def exit_archive(self) -> None:
        if self.archive is None:
            return
        self._clear_archive()
        self.refresh()

    def _clear_archive(self) -> None:
        self.archive = None
        self.archive_prefix = ""
        self._archive_members = []
        if self._archive_temp is not None:
            shutil.rmtree(self._archive_temp, ignore_errors=True)
            self._archive_temp = None

    def reload_archive(self) -> None:
        if self.archive is None:
            return
        try:
            self._archive_members = list(core.read_archive_members(self.archive))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self.owner.show_error("无法浏览", str(exc))
            return
        self.refresh()

    def preview_path(self, entry: core.FileEntry) -> Path | None:
        """查看用路径：包内文件先解压到临时目录。"""
        if self.archive is None:
            return entry.path
        return self.materialize(entry)

    def materialize(self, entry: core.FileEntry) -> Path | None:
        """按需解压包内文件到临时目录（超过 32 MiB 拒绝）。"""
        if self.archive is None or not entry.archive_member:
            return None
        if entry.size > _IMAGE_LIMIT:
            self.owner.show_error("无法打开", f"文件超过 32 MiB，请先解压：{entry.label}")
            return None
        if self._archive_temp is None:
            self._archive_temp = Path(tempfile.mkdtemp(prefix="fcmd-arcview-"))
        target = self._archive_temp / entry.archive_member
        if not target.is_file():
            try:
                core.extract_archive_members(
                    self.archive,
                    [core.ArchiveMember(entry.archive_member, entry.size, entry.mtime)],
                    self._archive_temp,
                )
            except (OSError, ValueError, zipfile.BadZipFile) as exc:
                self.owner.show_error("无法解压", str(exc))
                return None
        return target if target.is_file() else None

    def dispose(self) -> None:
        """关闭面板时清理临时目录。"""
        if self._archive_temp is not None:
            shutil.rmtree(self._archive_temp, ignore_errors=True)
            self._archive_temp = None

    def quick_entry(self) -> core.FileEntry | None:
        """当前应快速查看的条目：选中项优先，否则光标行。"""
        selected = self.selected_entries()
        if selected:
            return selected[0]
        widget = self.thumb_list if self.stack.currentWidget() is self.thumb_list else self.tree
        item = widget.currentItem()
        return self._entry_of(item) if item is not None else None


def color_subject(entry: core.FileEntry) -> ColorSubject:
    """把条目转成颜色规则匹配主体。"""
    return ColorSubject(
        name=entry.name,
        is_dir=entry.is_dir,
        is_symlink=entry.is_symlink,
        hidden=entry.hidden,
        size=entry.size,
        mtime=entry.mtime,
    )
