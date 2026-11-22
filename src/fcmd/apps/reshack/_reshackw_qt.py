"""reshackw 的 Qt 主窗口与入口。

侧边栏分页窗口（与 wslmanw 同构）：左侧是共享 ``SidebarNav``，「资源」页平铺
资源行卡（类型筛选 + 行内展开文本 / 十六进制 / 图像预览，行内直接导出 / 替换），
「文件」页放文件信息、最近打开与全部导出。加载、替换、全部导出都在后台线程
执行，结果经信号桥回投 GUI 线程。

任务日志不占分页：从「帮助 → 任务日志…」以非模态窗口打开（与 wslmanw 的任务
日志同形态），历史来自 ``_log_lines`` 缓冲，窗口打开时回放、之后实时追加。

布局口径：资源页固定行只有动作条与类型筛选，行卡列表吃满剩余空间；预览放在
行内展开详情中（同一时间只展开一行）；长文件名放窗口标题与日志，状态栏只留
简短结论。
"""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
from fcmd.gui.page import PageBody, PageSpec
from fcmd.gui.widgets import (
    Card,
    ItemRow,
    RowDetailMixin,
    RowListWidget,
    StatusTag,
    button,
    confirm,
    mono_font,
    tab_widget,
)
from fcmd.gui.window import QtMainWindow
from fcmd.gui.workers import EventBridge, submit

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["ReshackWindow", "run_qt"]

DEFAULT_SIZE = (1080, 720)
_LOG_LINES_MAX = 1000

_PE_FILTER = "PE 文件 (*.exe *.dll *.sys *.ocx *.scr *.cpl *.ax *.acm *.drv);;所有文件 (*.*)"
_RESIZE_DEBOUNCE_MS = 100
_FILTER_ALL = "all"

PAGES: tuple[PageSpec, ...] = (
    PageSpec("resources", "资源", "资源行卡、类型筛选、预览与导出 / 替换", "images"),
    PageSpec("file", "文件", "文件信息、最近打开与全部导出", "folder"),
)
"""侧边栏页面顺序；任务日志不占分页，从「帮助 → 任务日志…」打开。"""


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


class _ResourceDetailView(QWidget):  # pragma: no cover - Qt 控件
    """行内展开的预览详情：文本 / 十六进制 / 图像三个页签 + 图像缩放条。

    详情控件在展开行之间复用（挂进 ``row.detail_layout``），窗口通过
    :attr:`text_edit` / :attr:`hex_edit` / :attr:`image_scroll` / :attr:`image_label`
    / :attr:`zoom_label` 写入内容；图像缩放动作仍由窗口统一处理。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 10)
        layout.setSpacing(6)

        self.tabs = tab_widget(self, object_name="RowTabs")
        layout.addWidget(self.tabs, 1)

        text_page = QWidget(self.tabs)
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_edit = QPlainTextEdit(text_page)
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(mono_font())
        text_layout.addWidget(self.text_edit)
        self.tabs.addTab(text_page, "文本")

        hex_page = QWidget(self.tabs)
        hex_layout = QVBoxLayout(hex_page)
        hex_layout.setContentsMargins(0, 0, 0, 0)
        self.hex_edit = QPlainTextEdit(hex_page)
        self.hex_edit.setReadOnly(True)
        self.hex_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.hex_edit.setFont(mono_font())
        hex_layout.addWidget(self.hex_edit)
        self.tabs.addTab(hex_page, "十六进制")

        image_page = QWidget(self.tabs)
        image_layout = QVBoxLayout(image_page)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(6)

        zoom_bar = QHBoxLayout()
        zoom_bar.setContentsMargins(0, 0, 0, 0)
        zoom_bar.setSpacing(6)
        zoom_bar.addWidget(QLabel("缩放", image_page))
        self.zoom_out_button = button("－")
        self.zoom_label = QLabel("100%", image_page)
        self.zoom_label.setMinimumWidth(56)
        self.zoom_in_button = button("＋")
        self.zoom_reset_button = button("1:1")
        self.zoom_fit_button = button("适应")
        for widget in (
            self.zoom_out_button,
            self.zoom_label,
            self.zoom_in_button,
            self.zoom_reset_button,
            self.zoom_fit_button,
        ):
            zoom_bar.addWidget(widget)
        zoom_bar.addStretch(1)
        image_layout.addLayout(zoom_bar)

        self.image_scroll = QScrollArea(image_page)
        self.image_scroll.setWidgetResizable(False)
        self.image_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label = _ImageLabel("选择资源后显示预览", self.image_scroll)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_scroll.setWidget(self.image_label)
        image_layout.addWidget(self.image_scroll, 1)
        self.tabs.addTab(image_page, "图像")


class _ResourceItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """资源行卡：共享 :class:`ItemRow` 骨架，编排对齐 llmw 模型行卡。

    标题是 ``类型 #名称``（完整定位串，复制按钮给同串），副标题行是 RVA 与语言，
    行尾胶囊标预览形态（文本 / 图像 / 二进制），右侧数值列是资源大小，动作是
    导出 / 替换，展开箭头进预览详情。
    """

    exportRequested = Signal(object)
    replaceRequested = Signal(object)

    def __init__(self, resource: Any, parent: QWidget | None = None) -> None:
        title = f"{resource.type_name} #{resource.name_id}"
        super().__init__(
            title,
            "",
            elide=Qt.TextElideMode.ElideMiddle,
            expand_hint="展开预览",
            collapse_hint="收起预览",
            copy_text=title,
            parent=parent,
        )
        self.resource = resource
        self.setToolTip(f"{title}\nRVA: 0x{resource.offset:X} · 语言 {resource.language_id}")
        self.set_subtitle(f"RVA 0x{resource.offset:X} · 语言 {resource.language_id}")
        if is_text_resource(resource.type_id):
            self.add_tag(StatusTag("文本", tone="muted", parent=self.header))
        elif resource.type_id in (RT_BITMAP, RT_ICON):
            self.add_tag(StatusTag("图像", tone="accent", parent=self.header))
        else:
            self.add_tag(StatusTag("二进制", tone="muted", parent=self.header))
        self.set_metrics(format_file_size(resource.size))
        export_button = button("导出", kind="ghost")
        export_button.clicked.connect(lambda: self.exportRequested.emit(self.resource))
        self.add_action(export_button)
        replace_button = button("替换", kind="ghost")
        replace_button.setToolTip("替换为新数据（写入前自动备份）")
        replace_button.clicked.connect(lambda: self.replaceRequested.emit(self.resource))
        self.add_action(replace_button)


class _RecentItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """最近打开行卡：标题是文件名，副标题是完整路径，动作是「打开」。"""

    openRequested = Signal(str)

    def __init__(self, path_text: str, parent: QWidget | None = None) -> None:
        super().__init__(Path(path_text).name, "", expand_hint="", collapse_hint="", parent=parent)
        self.path_text = path_text
        self.setToolTip(path_text)
        self.set_subtitle(path_text)
        open_button = button("打开", kind="ghost")
        open_button.clicked.connect(lambda: self.openRequested.emit(path_text))
        self.add_action(open_button)


class ReshackWindow(RowDetailMixin, QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """PE 资源编辑器主窗口：侧边栏分页（资源 / 文件）+ 后台任务。

    任务日志不占分页：「帮助 → 任务日志…」打开非模态日志窗口，历史来自
    ``_log_lines`` 缓冲，窗口打开时回放、之后按 :attr:`logLine` 实时追加。
    """

    log_limit = _LOG_LINES_MAX
    log_hint = "汇总打开、导出、替换与预览操作；复制、清空、导出见日志工具条。"

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._pe: PEFile | None = None
        self._current_file: Path | None = None
        self._resources: list[Any] = []
        self._preview_image: Any = None
        self._shown_resource: Any = None
        self._zoom = 1.0
        self._fit_mode = False
        self._busy = False
        self._bridge: EventBridge | None = None
        self._filter_key = _FILTER_ALL
        self._buttons: list[QPushButton] = []
        super().__init__(
            title=f"PE 资源编辑器 v{__version__}",
            config_name="reshackw",
            nav_title="PE 资源",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._close_pe,
            persist=self._persist,
            sidebar_collapsed=self._settings.sidebar_collapsed,
            settings_path=settings_path,
            theme_app="reshackw",
        )
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._fit_image)
        builders: dict[str, Callable[[], QWidget]] = {
            "resources": self._build_resources_page,
            "file": self._build_file_page,
        }
        factories: dict[str, Callable[[], QWidget]] = {}
        for spec in PAGES:
            page = builders[spec.key]()
            page.hide()
            factories[spec.key] = lambda page=page: page
        self.add_pages(PAGES, factories)
        self._build_extra_menu()
        self._refresh_recent_views()
        self._set_busy(False)
        self.log("打开 PE 文件（exe/dll/sys）后，在「资源」页选择资源查看预览。")
        if self._settings.file_path:
            path = Path(self._settings.file_path).expanduser()
            if is_pe_file(path):
                QTimer.singleShot(50, lambda: self._start_load(path))

    # ------------------------------------------------------------------ #
    # 页面构建
    # ------------------------------------------------------------------ #
    def _build_resources_page(self) -> QWidget:
        """资源页：动作行 + 类型筛选 + 行卡列表（预览在行内展开）。"""
        page = PageBody(self, spacing=8)
        body = page.body
        body.addLayout(self._build_actions_row(page))

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(6)
        self._filter_layout = filters
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_buttons: dict[str, QPushButton] = {}
        filters.addStretch(1)
        body.addLayout(filters)
        self._rebuild_filter_chips([])

        self._resource_list = RowListWidget(page)
        self._resource_list.setObjectName("ResourceResultList")
        self._resource_list.setSpacing(4)
        self._resource_list.setMinimumHeight(200)
        self._resource_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self._resource_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._resource_list.currentRowChanged.connect(self._on_row_selected)
        self._detail_view = _ResourceDetailView(page)
        self._detail_view.setVisible(False)
        self.bind_row_detail("resources", self._resource_list, self._detail_view)
        body.addWidget(self._resource_list, 1)

        image_label = self._detail_view.image_label
        image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        image_label.customContextMenuRequested.connect(self._show_image_menu)
        image_label.zoom_requested.connect(self._on_wheel_zoom)
        self._detail_view.zoom_out_button.clicked.connect(self._zoom_out_image)
        self._detail_view.zoom_in_button.clicked.connect(self._zoom_in_image)
        self._detail_view.zoom_reset_button.clicked.connect(self._reset_zoom_image)
        self._detail_view.zoom_fit_button.clicked.connect(self._fit_image)
        self._detail_view.image_scroll.viewport().installEventFilter(self)
        return page

    def _build_actions_row(self, _page: QWidget) -> QHBoxLayout:
        """资源页动作行：打开 / 导出 / 替换 / 全部导出。"""
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        for text, handler, kind in (
            ("打开…", self._choose_file, "primary"),
            ("导出…", self._export_selected, ""),
            ("替换…", self._replace_selected, "danger"),
            ("全部导出…", self._export_all, ""),
        ):
            widget = button(text, kind=kind)
            widget.clicked.connect(handler)
            toolbar.addWidget(widget)
            self._buttons.append(widget)
            setattr(self, _BUTTON_ATTRS[text], widget)
        toolbar.addStretch(1)
        return toolbar

    def _build_file_page(self) -> QWidget:
        """文件页：打开 / 全部导出 + 文件信息卡 + 最近打开列表。"""
        page = PageBody(self, spacing=8)
        body = page.body

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        file_open_button = button("打开…", kind="primary")
        file_open_button.clicked.connect(self._choose_file)
        actions.addWidget(file_open_button)
        self._buttons.append(file_open_button)
        self._file_open_button = file_open_button
        actions.addStretch(1)
        body.addLayout(actions)

        info_card = Card("文件信息", page)
        grid = QHBoxLayout()
        grid.setSpacing(24)
        info_card.body.addLayout(grid)
        self._info_labels: dict[str, QLabel] = {}
        for key, title in (
            ("路径", "路径"),
            ("类型", "类型"),
            ("机器", "机器"),
            ("资源数", "资源数"),
        ):
            column = QVBoxLayout()
            column.setSpacing(2)
            name = QLabel(title, info_card)
            name.setObjectName("Hint")
            value = QLabel("—", info_card)
            value.setWordWrap(False)
            column.addWidget(name)
            column.addWidget(value)
            grid.addLayout(column)
            self._info_labels[key] = value
        grid.addStretch(1)
        body.addWidget(info_card)

        recent_card = Card("最近打开", page)
        self._recent_list = RowListWidget(recent_card)
        self._recent_list.setSpacing(4)
        self._recent_list.setMinimumHeight(120)
        self._recent_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self._recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        recent_card.body.addWidget(self._recent_list)
        body.addWidget(recent_card, 1)
        return page

    def _build_extra_menu(self) -> None:
        """在骨架菜单上补「打开 / 最近打开」「资源」与「帮助 → 任务日志…」。"""
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
    # 日志
    # ------------------------------------------------------------------ #
    def format_log_line(self, message: str) -> str:
        """任务日志前缀：``[HH:MM:SS]``。"""
        return f"[{time.strftime('%H:%M:%S')}] {message}"

    def show_log(self) -> None:
        """打开（或聚焦）任务日志窗口；历史来自 ``_log_lines`` 缓冲。"""
        self.show_log_dialog()

    # ------------------------------------------------------------------ #
    # 基础交互
    # ------------------------------------------------------------------ #
    def _set_status(self, text: str) -> None:
        """更新资源页状态栏文字（后台任务的状态都归属资源页）。"""
        self.set_page_status("resources", text)

    def _set_busy(self, busy: bool) -> None:
        """任务执行期间禁用动作按钮，避免重复提交。"""
        self._busy = busy
        for widget in self._buttons:
            widget.setEnabled(not busy)

    def _refresh_recent_views(self) -> None:
        """重建「最近打开」菜单与文件页的最近列表。"""
        self._recent_menu.clear()
        if not self._settings.recent_files:
            empty = self._recent_menu.addAction("（空）")
            empty.setEnabled(False)
        else:
            for item in self._settings.recent_files:
                action = self._recent_menu.addAction(item)
                action.triggered.connect(lambda _checked=False, value=item: self._open_recent(value))
        self._render_recent_rows()

    def _render_recent_rows(self) -> None:
        """按最近列表重建文件页的行卡。"""
        self._recent_list.clear()
        for item in self._settings.recent_files:
            row = _RecentItemRow(item, self._recent_list)
            row.expand_button.setVisible(False)
            row.openRequested.connect(self._open_recent)
            list_item = QListWidgetItem(self._recent_list)
            list_item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._recent_list.setItemWidget(list_item, row)
        self._recent_list.sync_row_widths()

    def _open_recent(self, value: str) -> None:
        path = Path(value).expanduser()
        if not is_pe_file(path):
            QMessageBox.critical(self, "无法打开", f"文件不存在或不是有效的 PE 文件:\n{value}")
            return
        self._start_load(path)

    def _show_properties(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            QMessageBox.information(self, "未选择资源", "请先在资源页选择资源。")
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
        item = self._resource_list.currentItem()
        if item is None:
            return None
        row = self._resource_list.itemWidget(item)
        if isinstance(row, _ResourceItemRow):
            return row.resource
        return None

    def _on_row_selected(self, index: int) -> None:
        self._resource_list.sync_selection(index)
        row = self._selected_resource()
        if row is not None:
            self._set_status(f"{row.type_name} #{row.name_id}（{format_file_size(row.size)}）")

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
        self.log(f"打开 {path}")
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
        """后台失败：恢复界面、写日志并弹错误框。"""
        self._set_busy(False)
        self._bridge = None
        self._set_status("操作失败")
        self.log(f"错误：{message}")
        QMessageBox.critical(self, "操作失败", message)

    def _apply_loaded(self, event: tuple[Any, ...]) -> None:
        _, pe, path, info, resources = event
        if self._pe is not None:
            self._pe.close()
        self._pe = pe
        self._current_file = path
        self._resources = resources
        self._filter_key = _FILTER_ALL
        self._populate_resources(resources)
        self._clear_preview()
        self._update_file_info(info)
        self.setWindowTitle(f"PE 资源编辑器 v{__version__} - {info.path.name}")
        self._settings.file_path = str(path)
        self._settings.recent_files = add_recent_file(self._settings.recent_files, path)
        save_settings(self._settings, self._settings_path)
        self._refresh_recent_views()
        self.log(f"已加载 {len(resources)} 个资源")
        self._set_status(f"{path.name}：{len(resources)} 个资源")
        self._bridge = None
        self._set_busy(False)

    def _apply_replaced(self, event: tuple[Any, ...]) -> None:
        _, old_size, new_size, backup, info, resources = event
        self._resources = resources
        self._populate_resources(resources)
        self._update_file_info(info)
        self.log(f"已替换资源：{format_file_size(old_size)} -> {format_file_size(new_size)}，备份 {backup}")
        self._set_status("资源替换完成")
        self._bridge = None
        self._set_busy(False)
        QMessageBox.information(self, "替换完成", f"资源已写入，原文件备份在:\n{backup}")

    def _apply_exported(self, event: tuple[Any, ...]) -> None:
        _, directory, exported, total = event
        self.log(f"已导出 {exported}/{total} 个资源 -> {directory}")
        self._set_status(f"已导出 {exported}/{total} 个资源")
        self._bridge = None
        self._set_busy(False)
        QMessageBox.information(self, "导出完成", f"已导出 {exported}/{total} 个资源到:\n{directory}")

    def _export_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None:
            QMessageBox.information(self, "未选择资源", "请先在资源页选择资源。")
            return
        self._export_resource(resource)

    def _export_resource(self, resource: Any) -> None:
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
        self.log(f"已导出 {resource.type_name} #{resource.name_id} -> {output}")
        self._set_status(f"已导出 {output.name}")

    def _replace_selected(self) -> None:
        resource = self._selected_resource()
        if resource is None or self._pe is None:
            QMessageBox.information(self, "未选择资源", "请先在资源页选择资源。")
            return
        self._replace_resource(resource)

    def _replace_resource(self, resource: Any) -> None:
        if self._pe is None:
            QMessageBox.information(self, "未打开文件", "请先打开 PE 文件。")
            return
        if self._busy:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择新的资源数据")
        if not filename:
            return
        try:
            new_data = Path(filename).read_bytes()
        except OSError as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return
        if not confirm(
            self,
            "确认替换",
            f"类型: {resource.type_name} ({resource.type_id})\n"
            f"名称: {resource.name_id}，语言: {resource.language_id}\n"
            f"原大小: {format_file_size(resource.size)}，新大小: {format_file_size(len(new_data))}\n\n"
            "将自动生成 .backup 备份后写入，继续？",
        ):
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
    # 资源列表与类型筛选
    # ------------------------------------------------------------------ #
    def _populate_resources(self, resources: list[Any]) -> None:
        """按当前类型筛选重建行卡列表（平铺，一行一个资源）。"""
        self._resources = resources
        self.detach_row_detail("resources")
        self._resource_list.clear()
        self._rebuild_filter_chips(resources)
        for _type_id, type_name, items in group_resources(resources):
            if self._filter_key not in (_FILTER_ALL, type_name):
                continue
            for resource in items:
                row = _ResourceItemRow(resource, self._resource_list)
                row.exportRequested.connect(self._export_resource)
                row.replaceRequested.connect(self._replace_resource)
                row.toggleRequested.connect(lambda _row: self.toggle_row("resources", _row))
                list_item = QListWidgetItem(self._resource_list)
                list_item.setSizeHint(QSize(0, row.sizeHint().height()))
                self._resource_list.setItemWidget(list_item, row)
        self._resource_list.sync_row_widths()
        self._sync_selected_status()

    def _rebuild_filter_chips(self, resources: list[Any]) -> None:
        """按当前资源的类型重建筛选行：全部 + 各类型（平铺列表不分组）。"""
        for chip in self._filter_buttons.values():
            self._filter_group.removeButton(chip)
            chip.deleteLater()
        self._filter_buttons.clear()
        keys: list[tuple[str, str]] = [(_FILTER_ALL, "全部")]
        for _type_id, type_name, _items in group_resources(resources):
            keys.append((type_name, type_name))
        for key, label in keys:
            chip = button(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, key=key: self._on_filter_changed(key))
            self._filter_group.addButton(chip)
            self._filter_layout.insertWidget(self._filter_layout.count() - 1, chip)
            self._filter_buttons[key] = chip
        if self._filter_key not in self._filter_buttons:
            self._filter_key = _FILTER_ALL
        self._filter_buttons[self._filter_key].setChecked(True)

    def _on_filter_changed(self, key: str) -> None:
        """切换类型筛选并重建列表。"""
        if key == self._filter_key:
            return
        self._filter_key = key
        self._populate_resources(self._resources)

    def _sync_selected_status(self) -> None:
        """把当前选中行的摘要同步到状态栏（无选中时给计数）。"""
        row = self._selected_resource()
        if row is not None:
            self._set_status(f"{row.type_name} #{row.name_id}（{format_file_size(row.size)}）")
        elif self._resources:
            self._set_status(f"{self._current_file.name}：{len(self._resources)} 个资源" if self._current_file else "")

    def _update_file_info(self, info: Any) -> None:
        """刷新文件页的信息卡。"""
        bits = "64 位" if info.is_64bit else "32 位"
        kind = "DLL" if info.is_dll else "EXE"
        self._info_labels["路径"].setText(str(info.path))
        self._info_labels["路径"].setToolTip(str(info.path))
        self._info_labels["类型"].setText(f"{kind} / {info.subsystem}")
        self._info_labels["机器"].setText(f"{info.machine} / {bits}")
        self._info_labels["资源数"].setText(str(len(self._resources)))
        self.log(f"{kind} / {info.machine} / {bits} / {info.subsystem}")

    # ------------------------------------------------------------------ #
    # 预览（行内展开详情）
    # ------------------------------------------------------------------ #
    def render_row_detail(self, row: ItemRow) -> None:
        """展开资源行时把预览刷进共享详情控件。"""
        if isinstance(row, _ResourceItemRow):
            self._show_preview(row.resource)

    def _clear_preview(self) -> None:
        self._detail_view.text_edit.setPlainText("")
        self._detail_view.hex_edit.setPlainText("")
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
        self._detail_view.text_edit.setPlainText(text)
        self._detail_view.hex_edit.setPlainText(format_hex_preview(resource.data))
        self._show_image(resource)

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
        chosen = menu.exec(self._detail_view.image_label.mapToGlobal(position))
        if chosen is copy_action:
            self._copy_preview_image()
        elif chosen is save_action:
            self._save_preview_image()

    def _copy_preview_image(self) -> None:
        """把当前预览图像复制到 Windows 剪贴板。"""
        if self._preview_image is None:
            return
        if copy_image_to_clipboard(self._preview_image):
            self.log("已复制预览图像到剪贴板")
            self._set_status("图像已复制到剪贴板")
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
        self.log(f"已保存预览图像 -> {output}")
        self._set_status(f"已保存 {output.name}")

    def _show_image_message(self, message: str) -> None:
        """无图像时在预览区居中显示提示文字。"""
        self._preview_image = None
        self._detail_view.image_label.setPixmap(QPixmap())
        self._detail_view.image_label.setText(message)
        self._detail_view.image_label.adjustSize()

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
        self._detail_view.image_label.setText("")
        self._detail_view.image_label.setPixmap(pixmap)
        self._detail_view.image_label.adjustSize()
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
        view_width = self._detail_view.image_scroll.viewport().width()
        view_height = self._detail_view.image_scroll.viewport().height()
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
        self._detail_view.zoom_label.setText(f"{round(self._zoom * 100)}%")

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """预览视口尺寸变化时，在适应模式下延迟重算倍率。"""
        try:
            is_viewport = watched is self._detail_view.image_scroll.viewport()
        except RuntimeError:  # pragma: no cover - 窗口销毁后仍派发的事件
            return False
        if is_viewport and event.type() == QEvent.Type.Resize and self._fit_mode and self._preview_image is not None:
            self._resize_timer.start(_RESIZE_DEBOUNCE_MS)
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _close_pe(self) -> bool:
        """关闭前释放 PE 句柄、关掉日志窗口并停止延迟重绘。"""
        if self._pe is not None:
            self._pe.close()
            self._pe = None
        self._resize_timer.stop()
        # 摘掉预览视口的尺寸监听：窗口销毁后 atexit 阶段 Qt 仍可能给已删的
        # viewport 派发事件，eventFilter 里再碰已删除的 C++ 对象会抛 RuntimeError。
        with contextlib.suppress(RuntimeError):
            self._detail_view.image_scroll.viewport().removeEventFilter(self)
        return True

    def _persist(self, geometry: str) -> None:
        """关闭时写回上次文件、最近打开、侧边栏折叠与 geometry。"""
        save_settings(
            ReshackSettings(
                file_path=self._settings.file_path,
                recent_files=self._settings.recent_files[:RECENT_LIMIT],
                sidebar_collapsed=self.nav.is_collapsed(),
                geometry=geometry,
            ),
            self._settings_path,
        )


_BUTTON_ATTRS = {
    "打开…": "_open_button",
    "导出…": "_export_button",
    "替换…": "_replace_button",
    "全部导出…": "_export_all_button",
}
"""动作行按钮的属性名映射：兼容旧属性名（测试与外部脚本访问）。"""


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
    from fcmd.gui.app import run_window

    run_window(lambda: ReshackWindow(settings_path), app_name="reshackw")
