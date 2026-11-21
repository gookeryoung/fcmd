"""reshackw 的 Qt 主窗口与入口。

左侧资源树按类型分组，右侧提供文本 / 十六进制 / 图像三种预览；工具栏负责
打开、导出选中、替换（写入前自动备份）与全部导出。加载、替换、全部导出都在
后台线程执行，结果经信号桥回投 GUI 线程。

布局口径：资源树与预览用水平分栏，树列首列自适应、其余固定宽度；预览页签
铺满分栏，图像页签在「适应」模式下随视口尺寸延迟重算倍率；长文件名放窗口
标题与日志，状态栏只留简短结论；状态标签用主题 Success / Danger 语义色，
切换主题后重新 polish。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd import __version__
from fcmd.apps.reshack._core import (
    TEXT_PREVIEW_LIMIT,
    clamp_zoom,
    copy_image_to_clipboard,
    decode_text,
    default_export_name,
    fit_zoom,
    format_hex_preview,
    group_resources,
    is_text_resource,
    load_preview_image,
    zoom_in,
    zoom_out,
)
from fcmd.apps.reshack.reshack import (
    RT_BITMAP,
    RT_ICON,
    PEError,
    PEFile,
    export_payload,
    format_file_size,
    is_pe_file,
    resource_extension,
)
from fcmd.apps.reshack.reshackw import (
    MIN_HEIGHT,
    MIN_WIDTH,
    RECENT_LIMIT,
    ReshackSettings,
    add_recent_file,
    load_settings,
    save_settings,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, LogView, button, hint_label, make_splitter, mono_font
from fcmd.gui.qt.workers import EventBridge, submit

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

__all__ = ["ReshackWindow", "run_qt"]

DEFAULT_SIZE = (1080, 720)

_TREE_HEADERS = ("名称", "类型", "大小", "语言")
_TREE_WIDTHS = (200, 120, 90, 70)
_PE_FILTER = "PE 文件 (*.exe *.dll *.sys *.ocx *.scr *.cpl *.ax *.acm *.drv);;所有文件 (*.*)"
_RESIZE_DEBOUNCE_MS = 100


def _work_load(path: Path, bridge: EventBridge) -> None:
    """线程池里打开 PE 文件并读取资源列表。"""
    pe = PEFile()
    try:
        pe.open(path)
        info = pe.file_info()
        resources = pe.resources()
    except PEError as exc:
        pe.close()
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("loaded", pe, path, info, resources))


def _work_replace(  # noqa: PLR0913, PLR0917 - 线程池参数需与 PEFile 接口一一对应
    pe: PEFile,
    type_id: int,
    name_id: int,
    new_data: bytes,
    language_id: int,
    bridge: EventBridge,
) -> None:
    """线程池里替换单个资源并刷新资源列表。"""
    try:
        old_size, new_size, backup = pe.replace_resource(type_id, name_id, new_data, language_id)
        info = pe.file_info()
        resources = pe.resources()
    except PEError as exc:
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("replaced", old_size, new_size, backup, info, resources))


def _work_export_all(pe: PEFile, target: Path, bridge: EventBridge) -> None:
    """线程池里导出全部资源。"""
    try:
        exported, total, directory = pe.export_all(target)
    except PEError as exc:
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("exported", directory, exported, total))


class _ImageLabel(QLabel):  # pragma: no cover - Qt 控件
    """图像预览标签：Ctrl + 滚轮改为缩放，其余滚轮交给滚动区。"""

    zoom_requested = Signal(int)

    def wheelEvent(self, event: Any) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_requested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class ReshackWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """PE 资源编辑器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._pe: PEFile | None = None
        self._current_file: Path | None = None
        self._preview_image: Any = None
        self._shown_resource: Any = None
        self._zoom = 1.0
        self._fit_mode = False
        self._busy = False
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"PE 资源编辑器 v{__version__}",
            config_name="reshackw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._close_pe,
            persist=self._persist,
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._fit_image)
        self._build_extra_menu()
        self._build()
        self._refresh_recent_menu()
        self._set_busy(False)
        self._append_log("打开 PE 文件（exe/dll/sys）后，在左侧选择资源查看预览。")
        if self._settings.file_path:
            path = Path(self._settings.file_path).expanduser()
            if is_pe_file(path):
                QTimer.singleShot(50, lambda: self._start_load(path))

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """工具栏 / 资源树 + 预览分栏 / 日志。"""
        self.add(self._build_toolbar())
        self.add(self._build_splitter(), 1)
        self.add(self._build_log_card())

    def _build_toolbar(self) -> QWidget:
        """打开 / 导出 / 替换 / 全部导出与当前文件提示。"""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._open_button = button("打开…", kind="primary")
        self._open_button.clicked.connect(self._choose_file)
        self._export_button = button("导出…")
        self._export_button.clicked.connect(self._export_selected)
        self._replace_button = button("替换…", kind="danger")
        self._replace_button.clicked.connect(self._replace_selected)
        self._export_all_button = button("全部导出…")
        self._export_all_button.clicked.connect(self._export_all)
        self._file_hint = hint_label("尚未打开文件", wrap=False)
        layout.addWidget(self._open_button)
        layout.addWidget(self._export_button)
        layout.addWidget(self._replace_button)
        layout.addWidget(self._export_all_button)
        layout.addSpacing(8)
        layout.addWidget(self._file_hint, 1)
        return row

    def _build_splitter(self) -> QWidget:
        """左侧资源树，右侧三种预览页签。"""
        splitter = make_splitter(Qt.Orientation.Horizontal, sizes=(360, 700), parent=self)

        tree_card = Card("资源", splitter)
        self._tree = QTreeWidget(tree_card)
        self._tree.setHeaderLabels(list(_TREE_HEADERS))
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate(_TREE_WIDTHS[1:], start=1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self._tree.setColumnWidth(column, width)
        self._tree.setMinimumWidth(280)
        self._tree.currentItemChanged.connect(self._on_select)
        tree_card.body.addWidget(self._tree)
        splitter.addWidget(tree_card)

        preview_card = Card("预览", splitter)
        self._tabs = QTabWidget(preview_card)
        self._tabs.addTab(self._build_text_tab(), "文本")
        self._tabs.addTab(self._build_hex_tab(), "十六进制")
        self._tabs.addTab(self._build_image_tab(), "图像")
        preview_card.body.addWidget(self._tabs)
        splitter.addWidget(preview_card)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _build_text_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._text_preview = _preview_edit(page)
        layout.addWidget(self._text_preview)
        return page

    def _build_hex_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self._hex_preview = _preview_edit(page, wrap=False)
        layout.addWidget(self._hex_preview)
        return page

    def _build_image_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        zoom_bar = QHBoxLayout()
        zoom_bar.setContentsMargins(0, 0, 0, 0)
        zoom_bar.setSpacing(6)
        zoom_bar.addWidget(QLabel("缩放", page))
        self._zoom_out_button = button("－")
        self._zoom_out_button.clicked.connect(self._zoom_out_image)
        self._zoom_label = QLabel("100%", page)
        self._zoom_label.setMinimumWidth(56)
        self._zoom_in_button = button("＋")
        self._zoom_in_button.clicked.connect(self._zoom_in_image)
        self._zoom_reset_button = button("1:1")
        self._zoom_reset_button.clicked.connect(self._reset_zoom_image)
        self._zoom_fit_button = button("适应")
        self._zoom_fit_button.clicked.connect(self._fit_image)
        for widget in (
            self._zoom_out_button,
            self._zoom_label,
            self._zoom_in_button,
            self._zoom_reset_button,
            self._zoom_fit_button,
        ):
            zoom_bar.addWidget(widget)
        zoom_bar.addStretch(1)
        layout.addLayout(zoom_bar)

        self._image_scroll = QScrollArea(page)
        self._image_scroll.setWidgetResizable(False)
        self._image_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label = _ImageLabel("选择资源后显示预览", self._image_scroll)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._image_label.customContextMenuRequested.connect(self._show_image_menu)
        self._image_label.zoom_requested.connect(self._on_wheel_zoom)
        self._image_scroll.setWidget(self._image_label)
        self._image_scroll.viewport().installEventFilter(self)
        layout.addWidget(self._image_scroll, 1)
        return page

    def _build_log_card(self) -> Card:
        card = Card("日志", self)
        self._log = LogView(height=90, parent=card)
        card.body.addWidget(self._log)
        return card

    def _build_extra_menu(self) -> None:
        """在骨架菜单上补「打开 / 最近打开」与「资源」操作。"""
        file_menu = self._find_menu("文件")
        if file_menu is not None:
            anchor = file_menu.actions()[0] if file_menu.actions() else None
            open_action = QAction("打开…", self)
            open_action.setShortcut("Ctrl+O")
            open_action.triggered.connect(self._choose_file)
            self._recent_menu = QMenu("最近打开", self)
            if anchor is not None:
                file_menu.insertAction(anchor, open_action)
                file_menu.insertMenu(anchor, self._recent_menu)
                file_menu.insertSeparator(anchor)
            else:
                file_menu.addAction(open_action)
                file_menu.addMenu(self._recent_menu)

        resource_menu = QMenu("资源", self)
        self._add_action(resource_menu, "导出…", self._export_selected)
        self._add_action(resource_menu, "替换…", self._replace_selected)
        self._add_action(resource_menu, "全部导出…", self._export_all)
        resource_menu.addSeparator()
        self._add_action(resource_menu, "放大预览", self._zoom_in_image, "Ctrl+=")
        self._add_action(resource_menu, "缩小预览", self._zoom_out_image, "Ctrl+-")
        self._add_action(resource_menu, "实际大小", self._reset_zoom_image, "Ctrl+0")
        self._add_action(resource_menu, "适应窗口", self._fit_image)
        resource_menu.addSeparator()
        self._add_action(resource_menu, "属性", self._show_properties)
        help_action = next(
            (action for action in self.menuBar().actions() if action.text() == "帮助"),
            None,
        )
        if help_action is not None:
            self.menuBar().insertMenu(help_action, resource_menu)
        else:
            self.menuBar().addMenu(resource_menu)

    def _find_menu(self, title: str) -> QMenu | None:
        """按标题取骨架已建好的菜单。"""
        for action in self.menuBar().actions():
            if action.text() == title:
                return action.menu()
        return None

    def _add_action(self, menu: QMenu, text: str, slot: Any, shortcut: str = "") -> QAction:
        """向菜单追加动作，可选全局快捷键。"""
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    # ------------------------------------------------------------------ #
    # 基础交互
    # ------------------------------------------------------------------ #
    def _append_log(self, message: str) -> None:
        """向日志区追加带时间戳的一行。"""
        from datetime import datetime

        self._log.append(f"{datetime.now():%H:%M:%S} {message}")

    def _set_status(self, text: str, kind: str = "") -> None:
        """更新状态栏，并按 Success / Danger 切换语义色（空串恢复默认）。"""
        self.status_label.setText(text)
        if self.status_label.objectName() != kind:
            self.status_label.setObjectName(kind)
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)

    def _set_busy(self, busy: bool) -> None:
        """任务执行期间禁用工具栏按钮，避免重复提交。"""
        self._busy = busy
        for widget in (
            self._open_button,
            self._export_button,
            self._replace_button,
            self._export_all_button,
        ):
            widget.setEnabled(not busy)

    def _refresh_recent_menu(self) -> None:
        """重建「最近打开」菜单。"""
        self._recent_menu.clear()
        if not self._settings.recent_files:
            empty = self._recent_menu.addAction("（空）")
            empty.setEnabled(False)
            return
        for item in self._settings.recent_files:
            action = self._recent_menu.addAction(item)
            action.triggered.connect(lambda _checked=False, value=item: self._open_recent(value))

    def _open_recent(self, value: str) -> None:
        path = Path(value).expanduser()
        if not is_pe_file(path):
            QMessageBox.critical(self, "无法打开", f"文件不存在或不是有效的 PE 文件:\n{value}")
            return
        self._start_load(path)

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于",
            f"PE 资源编辑器 v{__version__}\n\n"
            "查看 / 导出 / 替换 Windows PE 资源\n"
            "替换前自动生成 .backup 备份\n"
            "依赖 pefile（pip install fcmd[pe]）",
        )

    def _show_properties(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            QMessageBox.information(self, "未选择资源", "请先在左侧选择资源。")
            return
        QMessageBox.information(
            self,
            "资源属性",
            f"类型: {resource.type_name} ({resource.type_id})\n"
            f"名称: {resource.name_id}\n"
            f"语言: {resource.language_id}\n"
            f"大小: {format_file_size(resource.size)}\n"
            f"RVA: 0x{resource.offset:X}\n\n"
            f"数据前 16 字节:\n{resource.data[:16].hex(' ')}",
        )

    def _selected_resource(self) -> Any:
        item = self._tree.currentItem()
        if item is None:
            return None
        return item.data(0, Qt.ItemDataRole.UserRole)

    def _on_select(self, *_args: Any) -> None:
        resource = self._selected_resource()
        if resource is None:
            return
        self._show_preview(resource)

    # ------------------------------------------------------------------ #
    # 打开 / 导出 / 替换
    # ------------------------------------------------------------------ #
    def _choose_file(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "打开 PE 文件", "", _PE_FILTER)
        if filename:
            self._start_load(Path(filename))

    def _start_load(self, path: Path) -> None:
        if self._busy:
            return
        if not is_pe_file(path):
            QMessageBox.critical(self, "无法打开", f"不是有效的 PE 文件:\n{path}")
            return
        self._set_busy(True)
        self._set_status(f"正在加载 {path.name}…")
        self._append_log(f"打开 {path}")
        self._start_task(_work_load, (path,))

    def _start_task(self, target: Any, args: tuple[Any, ...]) -> None:
        """创建信号桥并提交后台任务。"""
        bridge = EventBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(target, *args, bridge, on_error=self._on_worker_error)

    def _on_event(self, event: tuple[Any, ...]) -> None:
        kind = event[0]
        if kind == "loaded":
            self._apply_loaded(event)
        elif kind == "replaced":
            self._apply_replaced(event)
        elif kind == "exported":
            self._apply_exported(event)
        elif kind == "error":
            self._on_worker_error(str(event[1]))

    def _on_worker_error(self, message: str) -> None:
        """后台失败：恢复界面、标红状态并弹错误框。"""
        self._set_busy(False)
        self._bridge = None
        self._set_status("操作失败", "Danger")
        self._append_log(f"错误：{message}")
        QMessageBox.critical(self, "操作失败", message)

    def _apply_loaded(self, event: tuple[Any, ...]) -> None:
        _, pe, path, info, resources = event
        if self._pe is not None:
            self._pe.close()
        self._pe = pe
        self._current_file = path
        self._populate_tree(resources)
        self._clear_preview()
        self._update_file_info(info)
        self._file_hint.setText(str(path))
        self._file_hint.setToolTip(str(path))
        self._settings.file_path = str(path)
        self._settings.recent_files = add_recent_file(self._settings.recent_files, path)
        save_settings(self._settings, self._settings_path)
        self._refresh_recent_menu()
        self._append_log(f"已加载 {len(resources)} 个资源")
        self._set_status(f"{path.name}：{len(resources)} 个资源")
        self._bridge = None
        self._set_busy(False)

    def _apply_replaced(self, event: tuple[Any, ...]) -> None:
        _, old_size, new_size, backup, info, resources = event
        self._populate_tree(resources)
        self._update_file_info(info)
        self._append_log(f"已替换资源：{format_file_size(old_size)} -> {format_file_size(new_size)}，备份 {backup}")
        self._set_status("资源替换完成", "Success")
        self._bridge = None
        self._set_busy(False)
        QMessageBox.information(self, "替换完成", f"资源已写入，原文件备份在:\n{backup}")

    def _apply_exported(self, event: tuple[Any, ...]) -> None:
        _, directory, exported, total = event
        self._append_log(f"已导出 {exported}/{total} 个资源 -> {directory}")
        self._set_status(f"已导出 {exported}/{total} 个资源", "Success")
        self._bridge = None
        self._set_busy(False)
        QMessageBox.information(self, "导出完成", f"已导出 {exported}/{total} 个资源到:\n{directory}")

    def _export_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            QMessageBox.information(self, "未选择资源", "请先在左侧选择资源。")
            return
        extension = resource_extension(resource.type_id)
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "导出资源",
            default_export_name(resource),
            f"资源文件 (*{extension});;所有文件 (*.*)",
        )
        if not filename:
            return
        output = Path(filename).expanduser()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(export_payload(resource))
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._append_log(f"已导出 {resource.type_name} #{resource.name_id} -> {output}")
        self._set_status(f"已导出 {output.name}")

    def _replace_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None or self._pe is None:
            QMessageBox.information(self, "未选择资源", "请先在左侧选择资源。")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择新的资源数据")
        if not filename:
            return
        try:
            new_data = Path(filename).read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "确认替换",
            f"类型: {resource.type_name} ({resource.type_id})\n"
            f"名称: {resource.name_id}，语言: {resource.language_id}\n"
            f"原大小: {format_file_size(resource.size)}，新大小: {format_file_size(len(new_data))}\n\n"
            "将自动生成 .backup 备份后写入，继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes or self._busy:
            return
        self._set_busy(True)
        self._set_status("正在替换资源…")
        self._start_task(
            _work_replace,
            (self._pe, resource.type_id, resource.name_id, new_data, resource.language_id),
        )

    def _export_all(self) -> None:
        if self._pe is None or self._current_file is None:
            QMessageBox.information(self, "未打开文件", "请先打开 PE 文件。")
            return
        if self._busy:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择全部导出目录")
        if not directory:
            return
        target = Path(directory).expanduser() / f"{self._current_file.stem}_resources"
        self._set_busy(True)
        self._set_status("正在导出全部资源…")
        self._start_task(_work_export_all, (self._pe, target))

    # ------------------------------------------------------------------ #
    # 资源树与预览
    # ------------------------------------------------------------------ #
    def _populate_tree(self, resources: list[Any]) -> None:
        """按类型分组重建资源树；分组行不可选中，叶子行携带资源对象。"""
        self._tree.clear()
        for _type_id, type_name, items in group_resources(resources):
            group = QTreeWidgetItem([f"{type_name} ({len(items)})", "", "", ""])
            group.setFirstColumnSpanned(True)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self._tree.addTopLevelItem(group)
            for resource in items:
                child = QTreeWidgetItem(
                    [
                        str(resource.name_id),
                        resource.type_name,
                        format_file_size(resource.size),
                        str(resource.language_id),
                    ]
                )
                child.setData(0, Qt.ItemDataRole.UserRole, resource)
                group.addChild(child)
            group.setExpanded(True)

    def _update_file_info(self, info: Any) -> None:
        self.setWindowTitle(f"PE 资源编辑器 v{__version__} - {info.path.name}")
        bits = "64 位" if info.is_64bit else "32 位"
        kind = "DLL" if info.is_dll else "EXE"
        self._append_log(f"{kind} / {info.machine} / {bits} / {info.subsystem}")

    def _clear_preview(self) -> None:
        self._text_preview.setPlainText("")
        self._hex_preview.setPlainText("")
        self._preview_image = None
        self._shown_resource = None
        self._zoom = 1.0
        self._fit_mode = False
        self._update_zoom_label()
        self._show_image_message("选择资源后显示预览")

    def _show_preview(self, resource: Any) -> None:
        self._shown_resource = resource
        if is_text_resource(resource.type_id):
            data = resource.data[:TEXT_PREVIEW_LIMIT]
            text = decode_text(data)
            if len(resource.data) > TEXT_PREVIEW_LIMIT:
                text += f"\n\n... 仅显示前 {TEXT_PREVIEW_LIMIT} 字节，共 {len(resource.data)} 字节"
        else:
            text = f"(二进制资源，共 {format_file_size(resource.size)}，请查看「十六进制」或「图像」预览)"
        self._text_preview.setPlainText(text)
        self._hex_preview.setPlainText(format_hex_preview(resource.data))
        self._show_image(resource)
        self._set_status(f"{resource.type_name} #{resource.name_id}（{format_file_size(resource.size)}）")

    def _show_image(self, resource: Any) -> None:
        self._preview_image = None
        self._zoom = 1.0
        self._fit_mode = False
        self._update_zoom_label()
        if resource.type_id not in (RT_BITMAP, RT_ICON):
            self._show_image_message("此资源类型不支持图像预览")
            return
        image = load_preview_image(resource.data, resource.type_id)
        if image is None:
            self._show_image_message("无法解码图像（或未安装 Pillow）")
            return
        self._preview_image = image
        self._fit_image()

    def _show_image_menu(self, position: Any) -> None:
        """在图像上弹出右键菜单；无图像时置灰。"""
        menu = QMenu(self)
        copy_action = menu.addAction("复制图像")
        save_action = menu.addAction("保存图像为…")
        enabled = self._preview_image is not None
        copy_action.setEnabled(enabled)
        save_action.setEnabled(enabled)
        chosen = menu.exec(self._image_label.mapToGlobal(position))
        if chosen is copy_action:
            self._copy_preview_image()
        elif chosen is save_action:
            self._save_preview_image()

    def _copy_preview_image(self) -> None:
        """把当前预览图像复制到 Windows 剪贴板。"""
        if self._preview_image is None:
            return
        if copy_image_to_clipboard(self._preview_image):
            self._append_log("已复制预览图像到剪贴板")
            self._set_status("图像已复制到剪贴板", "Success")
        else:
            QMessageBox.warning(self, "复制失败", "无法写入剪贴板（需要 Windows 并安装 Pillow）。")

    def _preview_default_name(self) -> str:
        """预览图像的默认保存文件名（PNG）。"""
        if self._shown_resource is None:
            return "preview.png"
        return f"{Path(default_export_name(self._shown_resource)).stem}.png"

    def _save_preview_image(self) -> None:
        """把当前预览图像另存为 PNG / ICO / BMP。"""
        if self._preview_image is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "保存图像",
            self._preview_default_name(),
            "PNG 图像 (*.png);;ICO 图标 (*.ico);;BMP 图像 (*.bmp)",
        )
        if not filename:
            return
        output = Path(filename).expanduser()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            self._preview_image.save(output)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._append_log(f"已保存预览图像 -> {output}")
        self._set_status(f"已保存 {output.name}")

    def _show_image_message(self, message: str) -> None:
        """无图像时在预览区居中显示提示文字。"""
        self._preview_image = None
        self._image_label.setPixmap(QPixmap())
        self._image_label.setText(message)
        self._image_label.adjustSize()

    def _render_preview_image(self) -> None:
        """按当前倍率重绘图像预览（放大用邻近采样，缩小用 LANCZOS）。"""
        if self._preview_image is None:
            return
        try:
            from PIL import Image
        except ImportError:
            self._show_image_message("未安装 Pillow，无法显示图像")
            return
        image = self._preview_image
        width = max(1, round(image.width * self._zoom))
        height = max(1, round(image.height * self._zoom))
        resample = Image.Resampling.NEAREST if self._zoom >= 1 else Image.Resampling.LANCZOS
        try:
            resized = image if (width, height) == image.size else image.resize((width, height), resample)
            pixmap = _pil_to_pixmap(resized)
        except Exception as exc:  # 图像模式不支持 / 转换失败
            self._show_image_message(f"图像预览失败：{exc}")
            return
        if pixmap is None:
            self._show_image_message("图像预览失败：无法转换为 QPixmap")
            return
        self._image_label.setText("")
        self._image_label.setPixmap(pixmap)
        self._image_label.adjustSize()
        self._update_zoom_label()

    def _zoom_in_image(self) -> None:
        self._apply_zoom(zoom_in(self._zoom))

    def _zoom_out_image(self) -> None:
        self._apply_zoom(zoom_out(self._zoom))

    def _reset_zoom_image(self) -> None:
        self._apply_zoom(1.0)

    def _on_wheel_zoom(self, step: int) -> None:
        if step > 0:
            self._zoom_in_image()
        else:
            self._zoom_out_image()

    def _apply_zoom(self, value: float) -> None:
        if self._preview_image is None:
            return
        self._zoom = clamp_zoom(value)
        self._fit_mode = False
        self._render_preview_image()

    def _fit_image(self) -> None:
        """按预览视口可用区域计算并应用「适应窗口」倍率。"""
        if self._preview_image is None:
            return
        self._fit_mode = True
        view_width = self._image_scroll.viewport().width()
        view_height = self._image_scroll.viewport().height()
        if view_width > 1 and view_height > 1:
            self._zoom = fit_zoom(
                self._preview_image.width,
                self._preview_image.height,
                view_width,
                view_height,
            )
        else:
            self._zoom = 1.0
        self._render_preview_image()

    def _update_zoom_label(self) -> None:
        self._zoom_label.setText(f"{round(self._zoom * 100)}%")

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """预览视口尺寸变化时，在适应模式下延迟重算倍率。"""
        if (
            watched is self._image_scroll.viewport()
            and event.type() == QEvent.Type.Resize
            and self._fit_mode
            and self._preview_image is not None
        ):
            self._resize_timer.start(_RESIZE_DEBOUNCE_MS)
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _close_pe(self) -> bool:
        """关闭前释放 PE 句柄并停止延迟重绘。"""
        if self._pe is not None:
            self._pe.close()
            self._pe = None
        self._resize_timer.stop()
        return True

    def _persist(self, geometry: str) -> None:
        """关闭时写回上次文件、最近打开与 geometry。"""
        save_settings(
            ReshackSettings(
                file_path=self._settings.file_path,
                recent_files=self._settings.recent_files[:RECENT_LIMIT],
                geometry=geometry,
            ),
            self._settings_path,
        )

    def set_theme(self, theme: Theme) -> None:
        """切换主题后按当前状态刷新状态栏语义色。"""
        super().set_theme(theme)
        if self.status_label.objectName():
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)


def _preview_edit(parent: QWidget, *, wrap: bool = True) -> QPlainTextEdit:
    """只读等宽预览框（文本换行 / 十六进制不换行）。"""
    widget = QPlainTextEdit(parent)
    widget.setReadOnly(True)
    widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if wrap else QPlainTextEdit.LineWrapMode.NoWrap)
    widget.setFont(mono_font())
    return widget


def _pil_to_pixmap(image: Any) -> QPixmap | None:
    """把 PIL 图像转为 ``QPixmap``；模式不受支持时返回 ``None``。"""
    try:
        rgba = image.convert("RGBA")
        raw = rgba.tobytes("raw", "RGBA")
    except Exception:  # Pillow 对不支持的模式抛多种异常
        return None
    qimage = QImage(raw, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    if qimage.isNull():
        return None
    return QPixmap.fromImage(qimage.copy())


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: ReshackWindow(settings_path), app_name="reshackw")
