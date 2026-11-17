"""cmderw（Qt）主窗口：双面板 + 菜单 / 工具栏 / 目录树 / 快速查看 / 命令行 / 传输队列。

窗口只做编排：面板扫描与归档浏览在 :mod:`_cmder_qt_panels`，各功能对话框在
:mod:`_cmder_qt_dialogs`，文件操作全部复用 :mod:`fcmd.apps.cmder.app` 的纯逻辑与
``_ops`` / ``_rename`` / ``_attrs`` / ``_split`` / ``_archwrite`` / ``_arcsearch``。
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.cmder import _archwrite
from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._cmder_qt_dialogs import (
    ArchiveOptionsDialog,
    BookmarkDialog,
    ColorRulesDialog,
    CompareDialog,
    DuplicateDialog,
    FilterDialog,
    MultiRenameDialog,
    OpenWithDialog,
    SearchDialog,
    SplitDialog,
    SyncDialog,
    TextDiffDialog,
    TransferQueueDialog,
    UserCommandsDialog,
)
from fcmd.apps.cmder._cmder_qt_panels import FilePanelWidget, QuickView, color_subject
from fcmd.apps.cmder._ops import (
    ITEM_CANCELLED,
    ITEM_DONE,
    ITEM_FAILED,
    ITEM_RUNNING,
    QUEUE_CANCELLED,
    QUEUE_DONE,
    QUEUE_FAILED,
    QUEUE_PENDING,
    QUEUE_RUNNING,
    TransferQueue,
    resolve_color_style,
)
from fcmd.apps.cmder._queue import parallel_map
from fcmd.apps.cmder._tabs import FilterDefinition
from fcmd.apps.cmder._volumes import enumerate_volumes
from fcmd.gui import geometry as _geometry
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.theme_menu import ThemeMenu
from fcmd.gui.qt.widgets import button, hint_label
from fcmd.gui.qt.window import restore_geometry
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.cmder._ops import QueueItem, QueueTask

__all__ = ["CmderQtWindow", "run_qt"]

DEFAULT_SIZE = (1240, 760)
MIN_SIZE = (960, 600)


class _QueueBridge(QObject):
    """队列进度信号（工作线程 → 主线程）。"""

    changed = Signal(int)


class CmderQtWindow(QMainWindow):
    """双面板文件管理器：左右面板 + 快速查看 + 目录树 + 命令行 + 队列。"""

    def __init__(self, settings_path: Path | None = None, launch: Any = None) -> None:
        super().__init__()
        self.settings_path = settings_path or core.config_path()
        self.settings = core.load_settings(self.settings_path)
        self.queue = TransferQueue()
        self.archive_options = _archwrite.ArchiveWriteOptions()
        self.color_rules = list(self.settings.color_rules)
        self.closing = False
        self._pause = threading.Event()
        self._pause.set()
        self._active_queue_task: QueueTask | None = None
        self._queue_running = False
        self._queue_dialog: TransferQueueDialog | None = None
        self._queue_bridge = _QueueBridge(self)
        self._queue_bridge.changed.connect(lambda _value: self._refresh_queue_ui())
        self._command_history = list(self.settings.command_history)
        self._command_index = len(self._command_history)
        self._command_draft = ""
        self.theme = _guitheme.resolve_theme(self.settings.theme)
        self._build_window()
        self._build_panels()
        self._build_quick_view()
        self._build_command_bar()
        self._build_tree()
        self._build_menus()
        self._wire_panels()
        self._apply_launch(launch)
        self._sync_actions()
        self.set_status("就绪")

    # ------------------------------------------------------------------ #
    # 窗口骨架
    # ------------------------------------------------------------------ #
    def _build_window(self) -> None:
        self.setWindowTitle("fcmd 双面板文件管理器")
        self.setMinimumSize(*MIN_SIZE)
        restore_geometry(
            self,
            self.settings.geometry,
            default_size=DEFAULT_SIZE,
            min_size=MIN_SIZE,
        )
        central = QWidget(self)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)
        self.toolbar_row = QHBoxLayout()
        self.toolbar_row.setContentsMargins(0, 0, 0, 0)
        self.toolbar_row.setSpacing(4)
        layout.addLayout(self.toolbar_row)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.status_label = QLabel("就绪", self)
        self.statusBar().addWidget(self.status_label, 1)
        self.selection_label = QLabel("", self)
        self.statusBar().addPermanentWidget(self.selection_label)

    def _build_panels(self) -> None:
        self.left_panel = FilePanelWidget(self, self.settings, "left", self.splitter)
        self.right_panel = FilePanelWidget(self, self.settings, "right", self.splitter)
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_panel)
        self.splitter.setSizes([580, 580])
        self.active = self.left_panel
        if self.settings.active == "right":
            self.switch_panel(self.right_panel)

    def _build_quick_view(self) -> None:
        self.quick_view = QuickView(self.splitter)
        self.quick_view.setMinimumWidth(320)
        self.quick_view.hide()
        self.splitter.addWidget(self.quick_view)

    def _build_command_bar(self) -> None:
        self.command_bar = QWidget(self)
        layout = QHBoxLayout(self.command_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("命令"))
        self.command_edit = QLineEdit(self.command_bar)
        self.command_edit.setPlaceholderText("输入命令后回车执行；↑/↓ 历史，Tab 补全，Esc 清空")
        self.command_edit.returnPressed.connect(self.run_command)
        self.command_edit.installEventFilter(self)
        layout.addWidget(self.command_edit, 1)
        self.command_hint = hint_label("", wrap=False)
        layout.addWidget(self.command_hint)
        self.command_bar.hide()
        self.centralWidget().layout().addWidget(self.command_bar)  # type: ignore[union-attr]

    def _build_tree(self) -> None:
        self.tree_dock = QDockWidget("目录树", self)
        self.tree_dock.setObjectName("DirectoryTree")
        self.directory_tree = QTreeWidget(self.tree_dock)
        self.directory_tree.setHeaderHidden(True)
        self.directory_tree.itemExpanded.connect(self._on_tree_expanded)
        self.directory_tree.itemDoubleClicked.connect(lambda item, _column: self._on_tree_open(item))
        self.directory_tree.itemSelectionChanged.connect(self._on_tree_select)
        self.tree_dock.setWidget(self.directory_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tree_dock)
        self.tree_dock.hide()
        self._tree_loaded: set[str] = set()

    # ------------------------------------------------------------------ #
    # 菜单与工具栏
    # ------------------------------------------------------------------ #
    def _add(  # noqa: PLR0913 - 菜单项参数成组出现，显式优于配置对象
        self,
        menu: QMenu,
        text: str,
        slot: Any,
        shortcut: str | None = None,
        *,
        checkable: bool = False,
        checked: bool = False,
    ) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setCheckable(checkable)
        action.setChecked(checked)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_menus(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        self._add(file_menu, "新建目录…", self.new_folder, "F7")
        self._add(file_menu, "重命名…", self.rename_selected, "F2")
        self._add(file_menu, "删除到回收站", self.delete_selected, "F8")
        self._add(file_menu, "永久删除", self.permanent_delete_selected, "Shift+F8")
        self._add(file_menu, "属性…", self.show_properties)
        file_menu.addSeparator()
        self._add(file_menu, "退出", self.close, "Ctrl+Q")

        select_menu = menubar.addMenu("选中")
        self._add(select_menu, "全选", self.select_all, "Ctrl+A")
        self._add(select_menu, "反选", self.invert_selection)
        self._add(select_menu, "按掩码选择…", self.select_by_mask, "Num+")
        self._add(select_menu, "按掩码取消…", self.deselect_by_mask, "Num-")
        select_menu.addSeparator()
        self._add(select_menu, "复制到对侧面板", self.copy_selected, "F5")
        self._add(select_menu, "移动到对侧面板", self.move_selected, "F6")
        self._add(select_menu, "多重命名…", self.bulk_rename, "Ctrl+M")
        select_menu.addSeparator()
        self._add(select_menu, "打包…", self.pack_selected, "Alt+F5")
        self._add(select_menu, "解压…", self.unpack_selected, "Alt+F9")
        self._add(select_menu, "加入压缩包…", self.add_to_archive)
        select_menu.addSeparator()
        self._add(select_menu, "计算哈希", self.hash_selected)
        self._add(select_menu, "生成校验文件", self.generate_checksum_file)
        self._add(select_menu, "校验校验文件", self.verify_checksum_file)
        select_menu.addSeparator()
        self._add(select_menu, "分割文件…", self.split_selected)
        self._add(select_menu, "合并分卷", self.merge_selected)
        self._add(select_menu, "查找重复文件…", self.find_duplicates)

        view_menu = menubar.addMenu("视图")
        self._add(view_menu, "刷新", self.refresh_both, "Ctrl+R")
        self._add(view_menu, "简要列表", lambda: self.set_view_mode("brief"), "Ctrl+F1")
        self._add(view_menu, "详细列表", lambda: self.set_view_mode("detailed"), "Ctrl+F2")
        self._add(view_menu, "缩略图", lambda: self.set_view_mode("thumbnails"), "Ctrl+Shift+F1")
        view_menu.addSeparator()
        self._add(
            view_menu,
            "同步浏览",
            self.toggle_sync_browse,
            "Ctrl+Shift+S",
            checkable=True,
            checked=self.settings.sync_browse,
        )
        self._add(view_menu, "分支视图", self.toggle_branch_view, "Ctrl+B", checkable=True)
        self._add(
            view_menu, "显示隐藏文件", self.toggle_hidden, "Ctrl+H", checkable=True, checked=self.settings.show_hidden
        )
        view_menu.addSeparator()
        self._add(view_menu, "快速查看", self.toggle_quick_view, "Ctrl+Q", checkable=True)
        self._add(view_menu, "目录树", self.toggle_directory_tree, "Ctrl+F8", checkable=True)
        self._add(view_menu, "命令行", self.focus_command_bar, "Ctrl+Enter", checkable=True)
        view_menu.addSeparator()
        self.theme_menu = ThemeMenu(self, self.theme)
        self.theme_menu.attach(view_menu.addMenu("主题"))

        tools_menu = menubar.addMenu("工具")
        self._add(tools_menu, "搜索…", self.open_search, "Ctrl+F")
        self._add(tools_menu, "搜索压缩包…", self.open_archive_search)
        self._add(tools_menu, "书签…", self.open_bookmarks, "Ctrl+D")
        self._add(tools_menu, "传输队列…", self.show_transfer_queue)
        self._add(tools_menu, "目录比较…", self.open_compare, "Ctrl+Shift+C")
        self._add(tools_menu, "比较两个文本文件", self.compare_text_files)
        self._add(tools_menu, "打开方式…", self.open_with_selected)

        config_menu = menubar.addMenu("配置")
        self._add(config_menu, "颜色规则…", self.open_color_rules)
        self._add(config_menu, "定义过滤器…", self.open_filter_definition)
        self._add(config_menu, "用户命令…", self.open_user_commands)
        self._add(config_menu, "压缩选项…", self.show_archive_options)

        help_menu = menubar.addMenu("帮助")
        self._add(help_menu, "关于", self._show_about)
        self._build_toolbar()

    def _build_toolbar(self) -> None:
        buttons = (
            ("刷新", self.refresh_both),
            ("上级", self.active.go_up),
            ("复制", self.copy_selected),
            ("移动", self.move_selected),
            ("新建", self.new_folder),
            ("删除", self.delete_selected),
            ("搜索", self.open_search),
            ("队列", self.show_transfer_queue),
            ("书签", self.open_bookmarks),
        )
        for text, handler in buttons:
            widget = button(text)
            widget.clicked.connect(handler)
            self.toolbar_row.addWidget(widget)
        self.toolbar_row.addStretch(1)
        view_row = QHBoxLayout()
        view_row.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("简要", lambda: self.set_view_mode("brief")),
            ("详细", lambda: self.set_view_mode("detailed")),
            ("缩略图", lambda: self.set_view_mode("thumbnails")),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            view_row.addWidget(widget)
        self.toolbar_row.addLayout(view_row)

    # ------------------------------------------------------------------ #
    # 面板接线
    # ------------------------------------------------------------------ #
    def _wire_panels(self) -> None:
        for panel in (self.left_panel, self.right_panel):
            panel.activated.connect(lambda entry, source=panel: self.open_entry(source, entry))
            panel.selectionChanged.connect(lambda source=panel: self._on_selection(source))
            panel.contextRequested.connect(lambda position, source=panel: self.show_context_menu(source, position))
            panel.tabs_bar.currentChanged.connect(lambda _index, source=panel: self.set_active(source))
            self._refresh_panel_button_states(panel)
        self.set_active(self.active)

    def _refresh_panel_button_states(self, panel: FilePanelWidget) -> None:
        if hasattr(panel, "back_button"):
            panel.back_button.setEnabled(panel.state.history_index > 0)

    def set_active(self, panel: FilePanelWidget) -> None:
        """切换活动面板并同步高亮。"""
        self.active = panel
        self.left_panel.set_active(panel is self.left_panel)
        self.right_panel.set_active(panel is self.right_panel)
        self.settings.active = "right" if panel is self.right_panel else "left"
        self._sync_actions()
        self._update_quick_view()

    def switch_panel(self, panel: FilePanelWidget | None = None) -> None:  # noqa: ARG002 - 接口保留可选面板参数
        """Tab 在左右面板之间切换。"""
        self.set_active(self.right_panel if self.active is self.left_panel else self.left_panel)

    def other_panel(self, panel: FilePanelWidget) -> FilePanelWidget:
        """返回对侧面板。"""
        return self.right_panel if panel is self.left_panel else self.left_panel

    def _on_selection(self, panel: FilePanelWidget) -> None:
        if panel is self.active:
            self._update_selection_status(panel)
            self._update_quick_view()
            self._sync_actions()

    def _update_selection_status(self, panel: FilePanelWidget) -> None:
        selected = panel.selected_entries()
        if not selected:
            self.selection_label.setText("")
            self.set_status(f"{panel.state.path} · {len(panel.entries)} 项")
            return
        total = sum(entry.size for entry in selected if not entry.is_dir)
        self.selection_label.setText(f"选中 {len(selected)} 项 · {core.format_size(total)}")
        self.set_status(f"选中 {len(selected)} 项：{core.describe_entry(selected[0])}")

    def _update_quick_view(self) -> None:  # noqa: PLR0911 - 三态预览按类型提前返回
        if not self.quick_view.isVisible():
            return
        entry = self.active.quick_entry()
        if entry is None:
            self.quick_view.show_note("快速查看", "未选择条目")
            return
        path = self.active.preview_path(entry)
        if path is None:
            self.quick_view.show_note(entry.label, "无法读取（目录或归档解压失败）")
            return
        if entry.is_dir:
            self.quick_view.show_note(entry.label, "目录（Enter 进入）")
            return
        from fcmd.apps.cmder._ops import THUMBNAIL_IMAGE, thumbnail_plan

        if thumbnail_plan(entry.name) == THUMBNAIL_IMAGE and path.stat().st_size <= 32 * 1024 * 1024:
            self.quick_view.show_image(entry.label, path)
            return
        try:
            text = core.load_preview(path)
        except (OSError, ValueError):
            try:
                data = path.read_bytes()[:4096]
            except OSError as exc:
                self.quick_view.show_note(entry.label, f"读取失败：{exc}")
                return
            from fcmd.apps.cmder._preview import hex_dump

            self.quick_view.show_text(entry.label, hex_dump(data))
            return
        self.quick_view.show_text(entry.label, text)

    # ------------------------------------------------------------------ #
    # 状态 / 对话框工具
    # ------------------------------------------------------------------ #
    def set_status(self, message: str) -> None:
        """更新状态栏。"""
        self.status_label.setText(message)

    def show_error(self, title: str, message: str) -> None:
        """错误提示（模态）。"""
        QMessageBox.critical(self, title, message)

    def show_info(self, title: str, message: str) -> None:
        """信息提示（模态）。"""
        QMessageBox.information(self, title, message)

    def show_text(self, title: str, text: str) -> None:
        """只读文本对话框（结果 / 预览 / 帮助）。"""
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setText(text)
        dialog.setDetailedText("")
        dialog.exec()

    def ask(self, title: str, question: str) -> bool:
        """是 / 否确认。"""
        answer = QMessageBox.question(
            self,
            title,
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def ask_text(self, title: str, label: str, default: str = "") -> tuple[str, bool]:
        """单行输入框。"""
        return QInputDialog.getText(self, title, label, text=default)

    def color_for(self, entry: core.FileEntry) -> tuple[QColor | None, QColor | None]:
        """按颜色规则计算条目前景 / 背景。"""
        style = resolve_color_style(self.color_rules, color_subject(entry), time.time())
        if style is None:
            return None, None
        foreground = QColor(style.foreground) if style.foreground.startswith("#") else None
        background = QColor(style.background) if style.background.startswith("#") else None
        return foreground, background

    def _show_about(self) -> None:
        self.show_text(
            "关于 cmderw",
            "fcmd 双面板文件管理器（Qt）\n\n"
            "F5 复制 / F6 移动 / F7 新建 / F8 删除到回收站 / Shift+F8 永久删除 / F2 重命名 / F3 查看\n"
            "Tab 切换面板，Ctrl+T / Ctrl+W / Ctrl+Tab 管理标签，Ctrl+D 书签，Ctrl+F 搜索\n"
            f"设置文件：{self.settings_path}",
        )

    # ------------------------------------------------------------------ #
    # 持久化与生命周期
    # ------------------------------------------------------------------ #
    def persist_settings(self) -> None:
        """把面板标签、书签、主题、颜色规则等写回设置文件。"""
        for panel in (self.left_panel, self.right_panel):
            panel.panel_state()
        self.settings.left_tabs = list(self.left_panel.tabs)
        self.settings.right_tabs = list(self.right_panel.tabs)
        self.settings.left_tab_index = self.left_panel.index
        self.settings.right_tab_index = self.right_panel.index
        self.settings.left = self.left_panel.state
        self.settings.right = self.right_panel.state
        self.settings.active = "right" if self.active is self.right_panel else "left"
        self.settings.show_hidden = bool(self.left_panel.settings.show_hidden)
        self.settings.theme = self.theme.name
        self.settings.color_rules = list(self.color_rules)
        self.settings.queue_parallel = self.settings.queue_parallel
        self.settings.command_history = list(self._command_history[-100:])
        self.settings.geometry = self._geometry_text()
        core.save_settings(self.settings, self.settings_path)

    def _geometry_text(self) -> str:
        """保存客户区几何（不含窗口装饰），与 ``restore_geometry`` 口径一致。"""
        rect = self.geometry()
        return _geometry.format_geometry(rect.width(), rect.height(), rect.x(), rect.y())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.closing:
            event.accept()
            return
        if self._queue_running and not self.ask("传输进行中", "队列仍有任务在执行，取消并退出？"):
            event.ignore()
            return
        self.closing = True
        self._pause.set()
        for task in self.queue.tasks:
            task.cancel_requested = True
        self.persist_settings()
        for panel in (self.left_panel, self.right_panel):
            panel.dispose()
        event.accept()

    def _apply_launch(self, launch: Any) -> None:
        """应用 --left / --right / --select 启动参数。"""
        if launch is None:
            return
        for side, raw in (("left", getattr(launch, "left", None)), ("right", getattr(launch, "right", None))):
            if not raw:
                continue
            path, error = core.resolve_path_input(str(raw), base=Path.cwd())
            if error:
                self.show_error("启动参数", error)
                continue
            panel = self.left_panel if side == "left" else self.right_panel
            panel.set_path(path)
        select = getattr(launch, "select", None)
        if select:
            path, error = core.resolve_path_input(str(select), base=Path.cwd())
            if error is None:
                panel = self.left_panel
                panel.set_path(path.parent, push=False)
                panel.refresh(select=path.name)

    def _show_count(self) -> None:
        self.set_status(f"{self.active.state.path} · {len(self.active.entries)} 项")

    # ------------------------------------------------------------------ #
    # 视图与导航
    # ------------------------------------------------------------------ #
    def refresh_both(self) -> None:
        """刷新两个面板。"""
        self.left_panel.refresh()
        self.right_panel.refresh()

    def set_view_mode(self, mode: str) -> None:
        """活动面板切换视图模式。"""
        self.active.set_view_mode(mode)

    def toggle_hidden(self) -> None:
        """切换显示隐藏文件（两个面板共用）。"""
        self.settings.show_hidden = not self.settings.show_hidden
        self.active.settings.show_hidden = self.settings.show_hidden
        self.left_panel.settings.show_hidden = self.settings.show_hidden
        self.right_panel.settings.show_hidden = self.settings.show_hidden
        self.refresh_both()

    def toggle_branch_view(self) -> None:
        """活动面板切换分支视图。"""
        self.active.state.branch_view = not self.active.state.branch_view
        self.active.refresh()

    def toggle_sync_browse(self) -> None:
        """同步浏览开关：导航一侧时另一侧按相对动作跟随。"""
        self.settings.sync_browse = not self.settings.sync_browse
        self.set_status("同步浏览：开" if self.settings.sync_browse else "同步浏览：关")

    def toggle_quick_view(self) -> None:
        """显示 / 隐藏内嵌快速查看。"""
        visible = not self.quick_view.isVisible()
        self.quick_view.setVisible(visible)
        if visible:
            self._update_quick_view()

    def toggle_directory_tree(self) -> None:
        """显示 / 隐藏目录树并同步到活动面板。"""
        visible = not self.tree_dock.isVisible()
        self.tree_dock.setVisible(visible)
        if visible:
            self._populate_tree()

    def focus_command_bar(self) -> None:
        """显示并聚焦命令行栏。"""
        self.command_bar.show()
        self.command_edit.setFocus()

    def hide_command_bar(self) -> None:
        self.command_bar.hide()

    def open_entry(self, panel: FilePanelWidget, entry: core.FileEntry) -> None:
        """Enter / 双击：目录进入、归档进入浏览、文件用默认程序打开。"""
        if panel.archive is not None:
            if entry.is_dir:
                panel.archive_enter(entry.name)
            else:
                target = panel.preview_path(entry)
                if target is not None:
                    core.open_externally(target)
            return
        if entry.is_dir:
            if core.archive_browsable(entry.path):
                panel.open_archive(entry.path)
            else:
                panel.set_path(entry.path)
                self._sync_other_panel(panel, entry.path)
            return
        try:
            core.open_externally(entry.path)
        except OSError as exc:
            self.show_error("打开失败", str(exc))

    def _sync_other_panel(self, panel: FilePanelWidget, new_path: str) -> None:
        """同步浏览：对侧面板按相对名称跟随切换。"""
        if not self.settings.sync_browse:
            return
        other = self.other_panel(panel)
        name = Path(new_path).name
        candidate = Path(other.state.path) / name
        if candidate.is_dir():
            other.set_path(candidate)

    def open_selected(self) -> None:
        for entry in self.active.selected_entries()[:1]:
            self.open_entry(self.active, entry)

    def view_selected(self) -> None:
        """F3：查看当前条目（文本 / 图片 / 十六进制）。"""
        entry = self.active.quick_entry()
        if entry is None:
            return
        self.quick_view.setVisible(True)
        self._update_quick_view()

    def edit_selected(self) -> None:
        """F4：用系统关联程序编辑。"""
        for entry in self.active.selected_entries()[:1]:
            if entry.is_dir:
                continue
            try:
                core.open_externally(self.active.preview_path(entry) or entry.path)
            except OSError as exc:
                self.show_error("编辑失败", str(exc))
                return

    def show_properties(self) -> None:
        """属性摘要对话框。"""
        entries = self.active.selected_entries()
        if not entries:
            return
        lines = [core.describe_entry(entry) for entry in entries[:50]]
        if len(entries) > 50:
            lines.append(f"… 其余 {len(entries) - 50} 项已省略")
        self.show_text("属性", "\n".join(lines))

    def select_all(self) -> None:
        self.active.select_all()

    def invert_selection(self) -> None:
        self.active.invert_selection()

    def clear_selection(self) -> None:
        self.active.clear_selection()

    def select_by_mask(self) -> None:
        mask, accepted = self.ask_text("按掩码选择", "掩码（支持 ; 分隔多个）", "*")
        if accepted and mask.strip():
            count = self.active.apply_mask_selection(mask, select=True)
            self.set_status(f"已选中 {count} 项")

    def deselect_by_mask(self) -> None:
        mask, accepted = self.ask_text("按掩码取消", "掩码（支持 ; 分隔多个）", "*")
        if accepted and mask.strip():
            count = self.active.apply_mask_selection(mask, select=False)
            self.set_status(f"已取消 {count} 项")

    def focus_filter(self) -> None:
        self.active.filter_edit.setFocus()

    def clear_filter(self) -> None:
        self.active.filter_edit.clear()

    def show_selected_size(self) -> None:
        entries = self.active.selected_entries()
        if not entries:
            return
        total = 0
        for entry in entries:
            if entry.is_dir:
                size, _files = core.directory_size(entry.path)
                total += size
            else:
                total += entry.size
        self.set_status(f"选中 {len(entries)} 项，共 {core.format_size(total)}")

    def rename_tab(self) -> None:
        text, accepted = self.ask_text("重命名标签", "标签标题（留空恢复路径名）", self.active.state.title)
        if accepted:
            self.active.rename_tab(text)

    def new_tab(self) -> None:
        self.active.new_tab()

    def close_tab(self) -> None:
        if not self.active.close_tab():
            self.set_status("当前标签已锁定或只剩一个标签")

    def close_other_tabs(self) -> None:
        closed = self.active.close_other_tabs()
        self.set_status(f"已关闭 {closed} 个标签")

    def toggle_tab_lock(self) -> None:
        locked = self.active.toggle_lock()
        self.set_status("标签已锁定" if locked else "标签已解锁")

    def next_tab(self, delta: int = 1) -> None:
        self.active.next_tab(delta)

    def build_tab_menu(self, panel: FilePanelWidget) -> QMenu:
        """标签右键菜单。"""
        menu = QMenu(self)
        menu.addAction("新建标签", panel.new_tab)
        menu.addAction("关闭标签", panel.close_tab)
        menu.addAction("关闭其他标签", panel.close_other_tabs)
        menu.addAction("重命名标签…", self.rename_tab)
        lock = menu.addAction("锁定标签")
        lock.setCheckable(True)
        lock.setChecked(panel.state.locked)
        lock.triggered.connect(panel.toggle_lock)
        return menu

    # ------------------------------------------------------------------ #
    # 删除
    # ------------------------------------------------------------------ #
    def delete_selected(self) -> None:
        self.delete_paths([entry.path for entry in self.active.selected_entries()])

    def permanent_delete_selected(self) -> None:
        self.delete_paths([entry.path for entry in self.active.selected_entries()], permanent=True)

    def delete_paths(self, paths: Sequence[Path], *, permanent: bool = False) -> bool:
        """删除选中路径：默认移入回收站，``permanent`` 为永久删除。"""
        if not paths:
            return False
        if self.active.archive is not None:
            return self._remove_archive_members()
        action = "永久删除" if permanent else "移入回收站"
        if not self.ask(action, f"确定{action} {len(paths)} 项？"):
            return False
        try:
            if permanent:
                for path in paths:
                    core.delete_entry(path)
                report = core.OperationReport(completed=list(paths))
            else:
                report = core.delete_entries(paths)
        except OSError as exc:
            self.show_error("删除失败", str(exc))
            return False
        self.set_status(f"已删除 {len(report.completed)} 项，失败 {len(report.errors)} 项")
        self.after_operation()
        return True

    # ------------------------------------------------------------------ #
    # 复制 / 移动 / 队列
    # ------------------------------------------------------------------ #
    def _selected_paths(self) -> list[Path]:
        return [entry.path for entry in self.active.selected_entries()]

    def copy_selected(self) -> None:
        self.transfer_paths(
            self._selected_paths(), move=False, destination=Path(self.other_panel(self.active).state.path)
        )

    def move_selected(self) -> None:
        self.transfer_paths(
            self._selected_paths(), move=True, destination=Path(self.other_panel(self.active).state.path)
        )

    def transfer_paths(
        self,
        paths: Sequence[Path],
        *,
        move: bool,
        destination: Path | None = None,
        preserve_root: Path | None = None,
    ) -> bool:
        """把路径加入传输队列（按目标分组，逐项询问覆盖）。"""
        if not paths:
            return False
        target_dir = destination or Path(self.other_panel(self.active).state.path)
        items: list[core.TransferItem] = []
        for source in paths:
            target = target_dir / source.name
            if preserve_root is not None:
                try:
                    relative = source.relative_to(preserve_root)
                except ValueError:
                    relative = Path(source.name)
                target = target_dir / relative
            overwrite = False
            if target.exists() or target.is_symlink():
                answer = self._ask_conflict(target)
                if answer == "cancel":
                    return False
                if answer == "skip":
                    continue
                overwrite = True
            items.append(core.TransferItem(source=source, target=target, overwrite=overwrite))
        if not items:
            return False
        return self.enqueue_transfer(items, target_dir, move=move)

    def _ask_conflict(self, target: Path) -> str:
        answer = QMessageBox.question(
            self,
            "目标已存在",
            f"{target}\n\n覆盖、跳过，还是取消本次传输？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return "cancel"
        return "overwrite" if answer == QMessageBox.StandardButton.Yes else "skip"

    def enqueue_transfer(self, items: Sequence[Any], destination: Path, *, move: bool) -> bool:
        """加入队列并启动执行；目标与未完成任务冲突时拒绝。"""
        triples = [(item.source, item.target, item.overwrite) for item in items]
        task, conflicts = self.queue.add(destination, triples, move=move)
        if task is None:
            self.show_error("无法入队", "\n".join(conflicts))
            return False
        self.set_status(f"已入队 #{task.task_id}：{task.label}")
        self._start_next_task()
        self._refresh_queue_ui()
        return True

    def _start_next_task(self) -> None:
        if self._queue_running:
            return
        task = next((candidate for candidate in self.queue.tasks if candidate.status == QUEUE_PENDING), None)
        if task is None:
            return
        self._queue_running = True
        task.status = QUEUE_RUNNING
        self._active_queue_task = task
        submit(
            self._run_task,
            task,
            on_done=self._task_finished,
            on_error=lambda message, current=task: self._task_failed(current, message),
        )

    def _run_task(self, task: QueueTask) -> QueueTask:
        def work(item: QueueItem) -> None:
            if task.cancel_requested:
                item.status = ITEM_CANCELLED
                return
            self._pause.wait()
            item.status = ITEM_RUNNING
            item.started = True
            self._queue_bridge.changed.emit(task.task_id)
            try:
                item.existed_before = item.target.exists() or item.target.is_symlink()
                if item.existed_before and item.overwrite:
                    core.delete_entry(item.target)
                if task.move:
                    core.move_entry(item.source, item.target)
                else:
                    core.copy_entry(item.source, item.target)
            except (OSError, ValueError) as exc:
                item.status = ITEM_FAILED
                item.error = str(exc)
            else:
                item.status = ITEM_DONE
            self._queue_bridge.changed.emit(task.task_id)

        parallel_map(task.items, work, parallel=max(1, self.settings.queue_parallel))
        if task.cancel_requested and task.failed_count == 0:
            task.status = QUEUE_CANCELLED
        elif task.failed_count:
            task.status = QUEUE_FAILED
        else:
            task.status = QUEUE_DONE
        return task

    def _task_finished(self, task: QueueTask) -> None:  # noqa: ARG002 - 信号回调参数
        self._queue_running = False
        self._active_queue_task = None
        self.after_operation()
        self._start_next_task()
        self._refresh_queue_ui()

    def _task_failed(self, task: QueueTask, message: str) -> None:
        task.status = QUEUE_FAILED
        self._queue_running = False
        self.show_error("传输失败", message)
        self._refresh_queue_ui()

    def _refresh_queue_ui(self) -> None:
        if self._queue_dialog is not None:
            self._queue_dialog.refresh()

    def queue_paused(self) -> bool:
        """队列是否已暂停（传输项在每项开始前等待）。"""
        return not self._pause.is_set()

    def toggle_queue_pause(self) -> None:
        if self._pause.is_set():
            self._pause.clear()
            self.set_status("队列已暂停")
        else:
            self._pause.set()
            self.set_status("队列已恢复")

    def set_queue_parallel(self, value: int) -> None:
        from fcmd.apps.cmder._queue import clamp_parallel

        self.settings.queue_parallel = clamp_parallel(value)
        self.persist_settings()

    def retry_queue_items(self, task_id: int, items: Sequence[QueueItem] | None = None) -> bool:
        task, conflicts = self.queue.retry(task_id, items)
        if task is None:
            self.show_error("无法重试", "\n".join(conflicts))
            return False
        self._start_next_task()
        self._refresh_queue_ui()
        return True

    def show_transfer_queue(self) -> None:
        self._queue_dialog = TransferQueueDialog(self)
        self._queue_dialog.exec()
        self._queue_dialog = None

    def after_operation(self) -> None:
        """文件操作后刷新两个面板与状态。"""
        self.refresh_both()
        self._sync_actions()

    # ------------------------------------------------------------------ #
    # 压缩包
    # ------------------------------------------------------------------ #
    def pack_selected(self) -> None:
        """把当前选择打成一个新压缩包（zip / tar 由扩展名决定）。"""
        paths = self._selected_paths()
        if not paths:
            return
        default = self.active.state.path + "/" + core.default_archive_name(paths)
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "打包到",
            default,
            "zip (*.zip);;tar (*.tar);;tar.gz (*.tar.gz);;所有文件 (*.*)",
        )
        if not chosen:
            return
        try:
            count = _archwrite.create_archive_file(Path(chosen), paths, options=self.archive_options)
        except (OSError, ValueError) as exc:
            self.show_error("打包失败", str(exc))
            return
        self.set_status(f"已打包 {count} 个文件到 {chosen}")
        self.after_operation()

    def unpack_selected(self) -> None:
        """解压当前压缩包 / 选中压缩包到对侧面板目录。"""
        entries = self.active.selected_entries()
        archive: Path | None = None
        if self.active.archive is not None:
            archive = self.active.archive
        elif entries and entries[0].path.suffix.casefold() in core.ARCHIVE_SUFFIXES:
            archive = entries[0].path
        if archive is None:
            self.set_status("请选择压缩包或先进入包内浏览")
            return
        destination, _ = QFileDialog.getExistingDirectory(
            self,
            "解压到",
            str(core.default_extract_dir(archive, Path(self.other_panel(self.active).state.path))),
        )
        if not destination:
            return
        try:
            target = core.extract_archive_to(archive, Path(destination))
        except (OSError, ValueError) as exc:
            self.show_error("解压失败", str(exc))
            return
        self.set_status(f"已解压到 {target}")
        self.after_operation()

    def add_to_archive(self) -> None:
        """把选中项加入现有压缩包。"""
        if self.active.archive is not None:
            target = self.active.archive
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "选择压缩包", "", "压缩包 (*.zip *.tar *.tar.gz *.tgz)")
            if not chosen:
                return
            target = Path(chosen)
        paths = self._selected_paths()
        if not paths:
            return
        try:
            count = _archwrite.add_paths(
                target,
                paths,
                self.active.archive_prefix,
                options=self.archive_options,
                replace=self.ask("覆盖同名成员", "成员已存在时是否覆盖？"),
            )
        except (OSError, ValueError) as exc:
            self.show_error("加入压缩包失败", str(exc))
            return
        self.set_status(f"已加入 {count} 个文件到 {target.name}")
        self.active.reload_archive()
        self.after_operation()

    def _remove_archive_members(self) -> bool:
        if self.active.archive is None:
            return False
        names = [entry.archive_member for entry in self.active.selected_entries() if entry.archive_member]
        if not names or not self.ask("删除成员", f"从压缩包删除 {len(names)} 个成员？"):
            return False
        try:
            count = _archwrite.remove_members(self.active.archive, names, options=self.archive_options)
        except (OSError, ValueError) as exc:
            self.show_error("删除成员失败", str(exc))
            return False
        self.set_status(f"已删除 {count} 个成员")
        self.active.reload_archive()
        return True

    def _rename_archive_member(self) -> None:
        if self.active.archive is None:
            return
        entries = self.active.selected_entries()
        if not entries:
            return
        entry = entries[0]
        if not entry.archive_member:
            return
        name, accepted = self.ask_text("重命名成员", "新名称", entry.name)
        if not accepted or not name.strip():
            return
        prefix = entry.archive_member.rsplit("/", 1)[0] if "/" in entry.archive_member else ""
        new_name = f"{prefix}/{name.strip()}" if prefix else name.strip()
        try:
            _archwrite.rename_member(self.active.archive, entry.archive_member, new_name, options=self.archive_options)
        except (OSError, ValueError) as exc:
            self.show_error("重命名失败", str(exc))
            return
        self.active.reload_archive()
        self.set_status(f"已重命名为 {new_name}")

    def show_archive_options(self) -> None:
        ArchiveOptionsDialog(self).exec()

    # ------------------------------------------------------------------ #
    # 哈希 / 校验文件
    # ------------------------------------------------------------------ #
    def hash_selected(self) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        self.set_status("正在计算哈希…")
        submit(
            core.hash_paths,
            paths,
            "sha256",
            on_done=lambda results: self.show_text("SHA256", core.format_hash_report(results, "sha256")),
            on_error=lambda message: self.show_error("哈希失败", message),
        )

    def generate_checksum_file(self) -> None:
        paths = [path for path in self._selected_paths() if path.is_file()]
        if not paths:
            self.set_status("请选择要生成校验文件的文件")
            return
        algorithm, accepted = self.ask_text("生成校验文件", "算法（sha256 / sha1 / md5）", "sha256")
        if not accepted:
            return
        algorithm = algorithm.strip().lower() or "sha256"
        chosen, _ = QFileDialog.getSaveFileName(
            self, "保存校验文件", str(paths[0].parent / f"{algorithm}.txt"), "校验文件 (*.txt *.sha256 *.sfv)"
        )
        if not chosen:
            return
        from fcmd.infras import hashing

        lines: list[str] = []
        try:
            for path in paths:
                digest = hashing.compute_file_hash(path, algorithm)
                lines.append(f"{digest}  {path.name}")
        except (OSError, ValueError) as exc:
            self.show_error("生成失败", str(exc))
            return
        try:
            Path(chosen).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.show_error("写入失败", str(exc))
            return
        self.set_status(f"已生成 {len(lines)} 条校验（{algorithm}）→ {chosen}")

    def verify_checksum_file(self) -> None:
        entries = self.active.selected_entries()
        if not entries or not entries[0].path.is_file():
            self.set_status("请选择校验文件")
            return
        source = entries[0].path
        from fcmd.infras import hashing

        lines: list[str] = []
        try:
            for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = raw.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                digest, name = parts[0], parts[1].lstrip("*")
                target = source.parent / name
                if not target.is_file():
                    lines.append(f"MISSING  {name}")
                    continue
                algorithm = {32: "md5", 40: "sha1", 64: "sha256"}.get(len(digest))
                if algorithm is None:
                    lines.append(f"SKIP     {name}（未知摘要长度）")
                    continue
                actual = hashing.compute_file_hash(target, algorithm)
                lines.append(("OK       " if actual == digest.casefold() else "FAIL     ") + name)
        except OSError as exc:
            self.show_error("校验失败", str(exc))
            return
        self.show_text("校验结果", "\n".join(lines) or "（没有可校验的行）")

    # ------------------------------------------------------------------ #
    # 分割 / 合并 / 重复文件
    # ------------------------------------------------------------------ #
    def split_selected(self) -> None:
        entries = self.active.selected_entries()
        if not entries or entries[0].is_dir:
            self.set_status("请选择一个文件")
            return
        SplitDialog(self, entries[0]).exec()

    def start_split(self, path: Path, *, part_size: int | None = None, parts: int | None = None) -> None:
        from fcmd.apps.cmder import _split

        self.set_status(f"正在分割 {path.name}…")
        submit(
            _split.split_file,
            path,
            part_size=part_size,
            parts=parts,
            on_done=self._split_done,
            on_error=lambda message: self.show_error("分割失败", message),
        )

    def _split_done(self, result: Any) -> None:
        self.set_status(f"已分割为 {len(result.parts)} 份，校验文件 {result.checksum.name}")
        self.after_operation()

    def merge_selected(self) -> None:
        from fcmd.apps.cmder import _split

        parts = [entry.path for entry in self.active.selected_entries() if not entry.is_dir]
        if not parts:
            self.set_status("请选择分卷文件")
            return
        output, _ = QFileDialog.getSaveFileName(
            self,
            "合并到",
            str(parts[0].parent / _split.default_merged_name(parts[0])),
            "所有文件 (*.*)",
        )
        if not output:
            return
        submit(
            _split.merge_parts,
            parts,
            Path(output),
            on_done=self._merge_done,
            on_error=lambda message: self.show_error("合并失败", message),
        )

    def _merge_done(self, result: Any) -> None:
        self.set_status(f"已合并到 {result.output}")
        self.after_operation()

    def find_duplicates(self) -> None:
        from fcmd.apps.cmder._dupes import find_duplicates as finder

        directory = Path(self.active.state.path)
        self.set_status(f"正在查找 {directory} 下的重复文件…")
        submit(
            finder,
            [directory],
            on_done=self._duplicates_done,
            on_error=lambda message: self.show_error("查找失败", message),
        )

    def _duplicates_done(self, report: Any) -> None:
        if not report.groups:
            self.set_status(f"扫描 {report.scanned} 个文件，未发现重复")
            return
        DuplicateDialog(self, report).exec()

    # ------------------------------------------------------------------ #
    # 新建 / 重命名 / 多重命名
    # ------------------------------------------------------------------ #
    def new_folder(self) -> None:
        name, accepted = self.ask_text("新建目录", "目录名称", "新建目录")
        if not accepted or not name.strip():
            return
        try:
            created = core.create_directory(Path(self.active.state.path), name.strip())
        except (OSError, ValueError) as exc:
            self.show_error("新建失败", str(exc))
            return
        self.active.refresh(select=created.name)
        self.set_status(f"已创建 {created.name}")

    def rename_selected(self) -> bool:
        if self.active.archive is not None:
            self._rename_archive_member()
            return True
        entries = self.active.selected_entries()
        if len(entries) != 1:
            self.set_status("请选择单个条目重命名")
            return False
        entry = entries[0]
        name, accepted = self.ask_text("重命名", "新名称", entry.name)
        if not accepted or not name.strip():
            return False
        try:
            target = core.rename_entry(entry.path, name.strip())
        except (OSError, ValueError) as exc:
            self.show_error("重命名失败", str(exc))
            return False
        self.active.refresh(select=target.name)
        return True

    def bulk_rename(self) -> None:
        entries = self.active.selected_entries()
        if not entries:
            self.set_status("请先选择要命名的条目")
            return
        dialog = MultiRenameDialog(self, self.active, entries)
        dialog.exec()
        self.after_operation()

    # ------------------------------------------------------------------ #
    # 搜索 / 比较
    # ------------------------------------------------------------------ #
    def open_search(self) -> None:
        SearchDialog(self, Path(self.active.state.path)).exec()

    def open_archive_search(self) -> None:
        entries = self.active.selected_entries()
        archive = self.active.archive or (
            entries[0].path if entries and entries[0].path.suffix.casefold() in core.ARCHIVE_SUFFIXES else None
        )
        if archive is None:
            chosen, _ = QFileDialog.getOpenFileName(self, "选择压缩包", "", "压缩包 (*.zip *.tar *.tar.gz *.tgz)")
            if not chosen:
                return
            archive = Path(chosen)
        SearchDialog(self, archive, archive=True).exec()

    def open_compare(self) -> None:
        CompareDialog(self).exec()

    def open_sync(self, report: Any, tolerance: float = 2.0) -> None:
        SyncDialog(self, report, tolerance).exec()

    def compare_text_files(self) -> None:
        entries = self.active.selected_entries()
        if len(entries) != 2:
            self.set_status("请选择两个文件比较")
            return
        TextDiffDialog(self, entries[0].path, entries[1].path).exec()

    # ------------------------------------------------------------------ #
    # 书签 / 主题 / 颜色规则 / 用户命令 / 过滤器
    # ------------------------------------------------------------------ #
    def open_bookmarks(self) -> None:
        BookmarkDialog(self, self.active).exec()

    def open_color_rules(self) -> None:
        ColorRulesDialog(self, self.color_rules).exec()

    def apply_color_rules(self, rules: Sequence[Any]) -> None:
        self.color_rules = list(rules)
        self.settings.color_rules = list(rules)
        self.refresh_both()
        self.persist_settings()

    def open_user_commands(self) -> None:
        UserCommandsDialog(self).exec()

    def apply_user_commands(self, commands: Sequence[Any]) -> None:
        self.settings.user_commands = list(commands)
        self.persist_settings()
        self._rebuild_user_actions()

    def apply_rename_presets(self, presets: Sequence[Any]) -> None:
        self.settings.rename_presets = list(presets)
        self.persist_settings()

    def open_filter_definition(self) -> None:
        FilterDialog(self).exec()

    def apply_filter_definition(self, definition: FilterDefinition) -> None:
        self.settings.filter_definition = definition
        for panel in (self.left_panel, self.right_panel):
            panel._filter_definition = definition
        self.refresh_both()
        self.persist_settings()

    def clear_filter_definition(self) -> None:
        self.apply_filter_definition(FilterDefinition())

    def remember_open_with(self, program: str) -> None:
        recent = [item for item in self.settings.open_with if item != program]
        recent.insert(0, program)
        self.settings.open_with = recent[:10]
        self.persist_settings()

    def open_with_selected(self) -> None:
        entries = self.active.selected_entries()
        if not entries:
            return
        dialog = OpenWithDialog(self, entries[0])
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.program:
            self.launch_with(dialog.program, entries[0].path)

    def launch_with(self, program: str, path: Path) -> bool:
        try:
            subprocess.Popen([program, str(path)])
        except OSError as exc:
            self.show_error("无法启动程序", str(exc))
            return False
        self.set_status(f"已用 {program} 打开 {path.name}")
        return True

    def run_user_command(self, command: Any) -> None:
        """展开占位符并在后台执行用户命令。"""
        from fcmd.apps.cmder._commands import CommandContext, PlaceholderError, expand_placeholders

        panel = self.active
        other = self.other_panel(panel)
        selected = self._selected_paths()
        context = CommandContext(
            source=Path(panel.state.path),
            target=Path(other.state.path),
            current=selected[0] if selected else None,
            selection=tuple(selected),
            list_file="",
        )
        try:
            expanded = expand_placeholders(command.full_command, context)
        except PlaceholderError as exc:
            self.show_error("占位符无效", str(exc))
            return
        self._run_shell_command(expanded)

    def _run_shell_command(self, command: str) -> None:
        self.set_status(f"执行：{command}")
        submit(
            self._run_shell,
            command,
            Path(self.active.state.path),
            on_done=lambda result: self._command_finished(command, result),
            on_error=lambda message: self.show_error("命令失败", message),
        )

    @staticmethod
    def _run_shell(command: str, cwd: Path) -> tuple[int, str]:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        output = (completed.stdout or "") + (completed.stderr or "")
        return completed.returncode, output

    def _command_finished(self, command: str, result: tuple[int, str]) -> None:
        code, output = result
        self.set_status(f"命令完成（退出码 {code}）：{command}")
        if output.strip():
            self.show_text("命令输出", output)
        self.refresh_both()

    # ------------------------------------------------------------------ #
    # 上下文菜单
    # ------------------------------------------------------------------ #
    def show_context_menu(self, panel: FilePanelWidget, position: Any) -> None:
        self.set_active(panel)
        menu = QMenu(self)
        menu.addAction("打开", self.open_selected)
        menu.addAction("查看", self.view_selected)
        menu.addAction("编辑", self.edit_selected)
        menu.addAction("属性…", self.show_properties)
        menu.addSeparator()
        menu.addAction("复制到对侧面板", self.copy_selected)
        menu.addAction("移动到对侧面板", self.move_selected)
        menu.addAction("删除到回收站", self.delete_selected)
        menu.addAction("重命名…", self.rename_selected)
        menu.addAction("多重命名…", self.bulk_rename)
        menu.addSeparator()
        menu.addAction("打包…", self.pack_selected)
        menu.addAction("解压…", self.unpack_selected)
        menu.addAction("打开方式…", self.open_with_selected)
        for command in self.settings.user_commands:
            if command.in_context:
                menu.addAction(command.name, lambda _checked=False, cmd=command: self.run_user_command(cmd))
        menu.exec(panel.tree.mapToGlobal(position))

    # ------------------------------------------------------------------ #
    # 目录树
    # ------------------------------------------------------------------ #
    def _populate_tree(self) -> None:
        self.directory_tree.clear()
        roots: list[Path] = []
        for panel in (self.left_panel, self.right_panel):
            path = Path(panel.state.path)
            if path not in roots:
                roots.append(path)
        for volume in enumerate_volumes():
            if volume.mount not in roots:
                roots.append(volume.mount)
        for root in roots:
            item = QTreeWidgetItem([str(root)])
            item.setData(0, Qt.ItemDataRole.UserRole, str(root))
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            self.directory_tree.addTopLevelItem(item)
        self.directory_tree.expandAll()

    def _on_tree_expanded(self, item: QTreeWidgetItem) -> None:
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(raw, str):
            return
        path = Path(raw)
        if item.childCount() == 1 and item.child(0).text(0) == "…":
            item.takeChildren()
            try:
                children = sorted(
                    (child for child in path.iterdir() if child.is_dir()), key=lambda p: p.name.casefold()
                )
            except OSError:
                return
            for child in children[:500]:
                node = QTreeWidgetItem([child.name])
                node.setData(0, Qt.ItemDataRole.UserRole, str(child))
                node.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
                item.addChild(node)

    def _on_tree_open(self, item: QTreeWidgetItem) -> None:
        raw = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(raw, str):
            self.set_active(self.active)
            self.active.set_path(Path(raw))

    def _on_tree_select(self) -> None:
        items = self.directory_tree.selectedItems()
        if items:
            raw = items[0].data(0, Qt.ItemDataRole.UserRole)
            if isinstance(raw, str):
                self.set_status(raw)

    # ------------------------------------------------------------------ #
    # 命令行
    # ------------------------------------------------------------------ #
    def eventFilter(self, obj: Any, event: Any) -> bool:
        from PySide6.QtCore import QEvent

        if obj is self.command_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.command_edit.clear()
                self.hide_command_bar()
                return True
            if key == Qt.Key.Key_Up:
                self._move_command_history(older=True)
                return True
            if key == Qt.Key.Key_Down:
                self._move_command_history(older=False)
                return True
            if key == Qt.Key.Key_Tab:
                self._command_complete()
                return True
        return super().eventFilter(obj, event)

    def _move_command_history(self, *, older: bool) -> None:
        if not self._command_history:
            return
        if older:
            if self._command_index == len(self._command_history):
                self._command_draft = self.command_edit.text()
            self._command_index = max(0, self._command_index - 1)
        else:
            self._command_index = min(len(self._command_history), self._command_index + 1)
        if self._command_index == len(self._command_history):
            self.command_edit.setText(self._command_draft)
        else:
            self.command_edit.setText(self._command_history[self._command_index])

    def _command_complete(self) -> None:
        text = self.command_edit.text()
        head, _sep, token = text.rpartition(" ")
        base = Path(self.active.state.path)
        candidates: list[str] = []
        for command in self.settings.user_commands:
            if command.command.startswith(token):
                candidates.append(command.command)
        try:
            for child in base.iterdir():
                if child.name.startswith(token):
                    candidates.append(child.name)
        except OSError:
            pass
        if len(candidates) == 1:
            completed = candidates[0]
            self.command_edit.setText((head + " " if head else "") + completed)
        elif candidates:
            self.command_hint.setText(" | ".join(sorted(candidates)[:8]))

    def run_command(self) -> None:
        command = self.command_edit.text().strip()
        if not command:
            return
        self._command_history.append(command)
        del self._command_history[:-100]
        self._command_index = len(self._command_history)
        self._command_draft = ""
        self.command_edit.clear()
        self.command_hint.setText("")
        self._run_shell_command(command)

    def _rebuild_user_actions(self) -> None:
        """重建菜单 / 工具栏中的用户命令入口（配置变更后调用）。"""
        self.set_status("用户命令已更新")

    # ------------------------------------------------------------------ #
    # 外部回调 / 动作状态
    # ------------------------------------------------------------------ #
    def adopt_paths(self, paths: Sequence[Path]) -> str:
        """把搜索结果加入当前选择（必要时先切到父目录）。"""
        if not paths:
            return "没有可加入的路径"
        target = paths[0].parent
        if Path(self.active.state.path) != target:
            self.active.set_path(target)
        count = self.active.select_names([path.name for path in paths], focus=True)
        self.set_active(self.active)
        return f"已加入选择 {count} 项"

    def reveal_path(self, path: Path) -> None:
        """在活动面板定位到路径。"""
        self.active.set_path(path.parent)
        self.active.refresh(select=path.name)

    def reveal_archive_member(self, archive: Path, member: str) -> None:
        """在面板中打开归档并定位成员（目录层级逐级进入）。"""
        if not self.active.open_archive(archive):
            return
        parts = [part for part in member.split("/") if part]
        for part in parts[:-1]:
            self.active.archive_enter(part)

    def _sync_actions(self) -> None:
        """根据当前选择更新状态栏动作计数。"""
        selected = self.active.selected_entries()
        if selected:
            self.selection_label.setText(
                f"选中 {len(selected)} 项 · {core.format_size(sum(entry.size for entry in selected if not entry.is_dir))}"
            )
        else:
            self.selection_label.setText("")
        self.left_panel.set_active(self.active is self.left_panel)
        self.right_panel.set_active(self.active is self.right_panel)


def run_qt(settings_path: Path | None = None, launch: Any = None) -> None:
    """创建应用与主窗口并进入事件循环。"""
    from fcmd.gui.qt.app import apply_theme, create_application

    app = create_application("cmderw")
    apply_theme(app)
    window = CmderQtWindow(settings_path, launch)
    window.show()
    app.exec()
