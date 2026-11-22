"""wslmanw 的 Qt 主窗口与入口。

窗口只做编排：业务判断全部走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出、预检、注销前备份、镜像下载），
与 CLI 共用同一套口径；界面负责列表 / 详情 / 下载与下载配置，配置项集中在设置页；
任务日志从「帮助 → 任务日志…」以非模态窗口打开（与 llmw 的服务日志同形态）。

布局与 llmw 对齐：复用共享 :class:`fcmd.gui.qt.window.QtMainWindow` 的左侧
:class:`~fcmd.gui.qt.sidebar.SidebarNav` 导航（折叠、``Alt+1..4`` 切换与窗口
几何记忆都由基座提供），右侧按「发行版 / 镜像 / 设置」分页，每页拿满整个
内容区，不再把详情、下载与配置挤进同一个底部标签页；任务日志不占分页，从
「帮助 → 任务日志…」打开。发行版页用一个
纵向分栏把列表与详情分开；下载目录 / 下载源 / 校验这类配置统一放设置页，
镜像页只留列表与下载动作；镜像页参考 llmw 模型列表：自定义行卡铺满整页，
详情与下载地址通过行内「展开」就地显示，不再占用右侧面板；进页先回显磁盘缓存
（`cache/wslman-available.json`）再后台刷新，「更新列表」可手动绕过 30 分钟缓存。
后台任务一律走
:func:`fcmd.gui.qt.workers.submit`，运行状态每 3 秒轮询一次（最小化时暂停），
关窗时取消进行中的任务。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``（与旧设置文件同名字段互读）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.wslman import _images, _mirrors
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._config import (
    default_backups_dir,
    default_download_dir,
    default_install_root,
    load_config,
    work_root,
)
from fcmd.apps.wslman._disk import distro_disk_bytes, format_bytes, recommended_backup_dir
from fcmd.apps.wslman._distro import Distro, state_label
from fcmd.apps.wslman._errors import WslmanError, invalid_argument, runtime_error
from fcmd.apps.wslman._terminal import LaunchError, SubprocessLauncher, build_command
from fcmd.apps.wslman._transfer import sha256_file
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    FormGrid,
    ItemRow,
    PageScroll,
    PathRow,
    RowListWidget,
    StatusTag,
    button,
    confirm,
    fit_dialog_to_content,
    hint_label,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.window import QtMainWindow
from fcmd.gui.qt.workers import periodic, submit, submit_daemon
from fcmd.infras.download import SpeedMeter, format_speed

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.wslman._images import ImageEntry

__all__ = ["WslmanQtWindow", "run_qt"]

DEFAULT_SIZE = (1160, 760)

_LOG_LINES_MAX = 1000
"""任务日志缓冲上限（行）；超出后丢弃最旧的，避免长时间运行吃内存。"""

TITLE = "WSL · 发行版管理"
NAV_TITLE = "WSL"

PAGES: tuple[tuple[str, str, str, str], ...] = (
    ("distros", "发行版", "列表、详情、启动停止、导出与注销", "distros"),
    ("images", "镜像", "在线镜像清单、本地安装与清单更新", "disc"),
    ("settings", "设置", "下载目录、下载源与校验", "settings"),
)
"""侧边栏页面顺序与提示；与 llmw 相同，左侧导航由共享 ``SidebarNav`` 提供。

任务日志不占分页：从顶部「帮助 → 任务日志…」以非模态窗口打开（见
:meth:`WslmanQtWindow.show_log`），与 llmw 的服务日志同一形态。
"""


def _images_stamp(fetched_at: float) -> str:
    """镜像清单时间戳：当天只显示 ``HH:MM``，跨天补 ``月-日``。"""
    stamp = time.localtime(fetched_at)
    today = time.localtime()
    if (stamp.tm_year, stamp.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", stamp)
    return time.strftime("%m-%d %H:%M", stamp)


def _images_note_text(catalog: Any) -> str:
    """状态行末尾的「来源 · 更新时间」备注（在线 / 缓存 / 过期缓存三种口径）。"""
    sources = "+".join(catalog.sources) or "缓存"
    stamp = _images_stamp(catalog.fetched_at)
    if catalog.stale:
        return f"{sources} · 过期缓存（{stamp}）"
    if catalog.from_cache:
        return f"本地缓存 · 更新于 {stamp}"
    return f"{sources} · 更新于 {stamp}"


class _LogBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台任务的日志行信号（跨线程排队到主线程）。"""

    message = Signal(str)


class _ProgressBridge(QObject):  # pragma: no cover - Qt 信号桥
    """下载进度信号：``(累计字节, 总量 / None)``，跨线程排队到主线程刷新状态栏。"""

    changed = Signal(int, object)


def _state_tone(state: str) -> str:
    """发行版状态 → 胶囊色调（与 ``state_label`` 的文案配套）。"""
    return {"running": "success", "stopped": "muted", "unknown": "warning"}.get(state, "muted")


class _DistroItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """发行版行卡：共享 :class:`ItemRow` 骨架 + 发行版自己的状态位。

    标题是发行版名，副标题行是「版本 + 对应的在线镜像」；状态胶囊与默认胶囊
    贴在同一行行尾，右侧是磁盘占用、行内启停按钮与展开箭头。详情（BasePath /
    虚拟磁盘 / 占用 / 默认用户 / 来源）由 ``_DistroDetailView`` 挂到
    ``detail_layout`` 就地显开，同一时间只展开一行。
    """

    startStopRequested = Signal(str)

    def __init__(
        self,
        distro: Distro,
        size: int | None,
        estimated: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(distro.name, "", parent=parent)
        self.distro = distro
        self.size = size
        self.estimated = estimated
        self.state_tag = StatusTag("", tone="muted", parent=self.header)
        self.add_tag(self.state_tag)
        self.default_tag = StatusTag("默认", tone="accent", parent=self.header)
        self.add_tag(self.default_tag)
        # 对应的在线镜像（自定义导入 / 匹配不到时给「本地导入」）放在副标题行，
        # 跟版本同一排：状态胶囊也在副标题行尾，两行各自独立，窄窗口或大字体现
        # 也不会和「镜像 …」挤在一起。超长时右侧省略，完整文案走 tooltip。
        self.image_label = ElidedLabel("", mode=Qt.TextElideMode.ElideRight, parent=self.header)
        self.image_label.setObjectName("Hint")
        self.image_label.setVisible(False)
        # 吃掉版本与状态胶囊之间的剩余宽度，长镜像名右侧省略，不会挤到状态胶囊。
        self.add_note_widget(self.image_label, stretch=1)

        self.start_stop_button = button("启动", kind="primary")
        self.start_stop_button.clicked.connect(lambda: self.startStopRequested.emit(distro.name))
        self.add_action(self.start_stop_button)

        self.update_distro(distro, size, estimated)

    def update_distro(self, distro: Distro, size: int | None, estimated: bool) -> None:
        """就地刷新状态 / 占用 / 默认标记与启停按钮（轮询不重建行）。"""
        self.distro = distro
        self.size = size
        self.estimated = estimated
        self.setToolTip(f"{distro.name}\n{distro.base_path or '未知'}")
        self.set_title(distro.name)
        # 与 llmw 模型行同构：左侧副标题只放简短口径（版本），右侧数值放磁盘占用。
        self.set_subtitle(f"v{distro.version or '未知'}")
        self.set_metrics(format_bytes(size, estimated=estimated))
        self.state_tag.set_status(state_label(distro), _state_tone(distro.state))
        self.default_tag.setVisible(distro.default)
        can_start, can_stop = w.action_states(distro.state)
        self.start_stop_button.setText("停止" if can_stop else "启动")
        self.start_stop_button.setEnabled(can_start or can_stop)
        self.start_stop_button.setToolTip(f"停止 {distro.name}" if can_stop else f"启动 {distro.name}")

    def update_image(self, entry: ImageEntry | None, *, known: bool) -> None:
        """标注该发行版对应的在线镜像；``known=False`` 表示清单还没加载（留空）。"""
        text = w.distro_image_text(self.distro, (entry,) if entry is not None else (), known=known)
        self.image_label.setText(text)
        self.image_label.setVisible(bool(text))
        if entry is None:
            self.image_label.setToolTip("未匹配到在线镜像（自定义导入或已下架）" if known else "")
            return
        arches = "/".join(entry.arches) or "任意架构"
        sources = w.image_source_text(entry)
        self.image_label.setToolTip(f"{entry.friendly_name or entry.name}\n可用架构：{arches}\n来源：{sources}")

    def set_busy(self, busy: bool) -> None:
        """任务进行中禁用行内启停按钮；结束后按当前状态重新计算。"""
        if busy:
            self.start_stop_button.setEnabled(False)
            return
        can_start, can_stop = w.action_states(self.distro.state)
        self.start_stop_button.setEnabled(can_start or can_stop)


class _DistroDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的发行版详情：字段网格 + 长路径可选中复制。

    同一个实例在行之间复用：展开哪一行就挂到哪一行的 ``detail_layout`` 下。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.values: dict[str, QLabel] = {}
        for index, label in enumerate(w._DETAIL_LABELS):
            row, column = divmod(index, 2)
            grid.addWidget(hint_label(f"{label}：", wrap=False), row, column * 2, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, column * 2 + 1, Qt.AlignmentFlag.AlignTop)
            self.values[label] = value
        layout.addLayout(grid)

    def render(self, distro: Distro, size: int | None, estimated: bool) -> None:
        """回填字段；未知字段统一显示 ``—``。"""
        fields = dict(w.detail_fields(distro, size, estimated))
        for label, widget in self.values.items():
            text = fields.get(label, "—")
            widget.setText(text)
            widget.setToolTip(text)


class _ImageItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """镜像列表行卡：共享 :class:`ItemRow` 骨架 + 镜像自己的动作与标记。

    标题是镜像名，副标题是友好名 / 架构 / 来源，右侧是来源摘要、已安装 / 已备份 /
    sha256 胶囊与行内下载 / 安装按钮。下载地址与短字段由 ``_ImageDetailView`` 挂到
    ``detail_layout`` 就地展开，列表铺满整页宽度。
    """

    downloadRequested = Signal(str)
    installRequested = Signal(str)
    cancelRequested = Signal()

    def __init__(self, entry: ImageEntry, parent: QWidget | None = None) -> None:
        title, subtitle, metrics = w.image_item_lines(entry)
        super().__init__(
            title,
            subtitle,
            elide=Qt.TextElideMode.ElideMiddle,
            expand_hint="展开详情与下载地址",
            collapse_hint="收起详情",
            parent=parent,
        )
        self.entry = entry
        self.setToolTip(entry.name)
        self.title_label.setToolTip(entry.name)
        for text, tone in w.image_marks(entry):
            self.add_tag(StatusTag(text, tone=tone, parent=self.header))
        self.set_metrics(metrics)

        # 每行自带动作按钮：未下载 →「下载」；已下载未安装 →「安装」；已安装 → 无按钮。
        # 下载 / 安装进行中时该行按钮变成「取消」，不再依赖页面底部的动作条。
        self.action = self._action_kind(entry)
        self.download_button: QPushButton | None = None
        if self.action:
            self.download_button = button(self._action_text(), kind="primary")
            self.download_button.setToolTip(self._action_tooltip())
            self.download_button.clicked.connect(self._on_action)
            self.add_action(self.download_button)

    @staticmethod
    def _action_kind(entry: ImageEntry) -> str:
        """``""``（无需动作）/ ``download`` / ``install``。"""
        if entry.installed:
            return ""
        if entry.downloaded:
            return "install"
        return "download" if entry.downloads else "install"

    def _action_text(self) -> str:
        return "安装" if self.action == "install" else "下载"

    def _action_tooltip(self) -> str:
        if self.action == "install":
            if self.entry.downloaded:
                return f"用已下载的镜像包安装：{self.entry.archive}" if self.entry.archive else "用已下载的镜像包安装"
            return w._DOWNLOAD_HINT
        return "下载镜像归档；完成后该行变为「已下载」，可直接安装"

    def _on_action(self) -> None:
        if self.action == "install":
            self.installRequested.emit(self.entry.name)
        else:
            self.downloadRequested.emit(self.entry.name)

    def set_download_state(self, *, busy: bool, active: bool = False) -> None:
        """任务进行中：正在跑的这一行按钮变「取消」，其它行禁用。"""
        if self.download_button is None:
            return
        if busy and active:
            self.download_button.setText("取消")
            self.download_button.setToolTip("取消本次任务（已下载的部分按配置保留 .part）")
            self.download_button.setEnabled(True)
            return
        self.download_button.setText(self._action_text())
        self.download_button.setToolTip(self._action_tooltip())
        self.download_button.setEnabled(not busy)


class _ImageDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的镜像详情：短字段网格 + 可换行的下载地址。

    同一个实例在行之间复用：展开哪一行就挂到哪一行的 ``detail_layout`` 下。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 10)
        layout.setSpacing(6)
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.values: dict[str, QLabel] = {}
        for index, label in enumerate(w._IMAGE_DETAIL_FIELDS):
            row, column = divmod(index, 2)
            grid.addWidget(hint_label(f"{label}：", wrap=False), row, column * 2, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, column * 2 + 1, Qt.AlignmentFlag.AlignTop)
            self.values[label] = value
        layout.addLayout(grid)
        layout.addWidget(hint_label("下载地址", wrap=False))
        self.url = QPlainTextEdit(self)
        self.url.setReadOnly(True)
        self.url.setMinimumHeight(72)
        self.url.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.url)

    def render(self, entry: ImageEntry) -> None:
        """回填选中条目的详情；未知字段统一显示 ``—``。"""
        values = dict(w.image_details(entry))
        for label, widget in self.values.items():
            widget.setText(values.get(label, "—"))
        self.url.setPlainText(values.get("下载地址", "—"))


class WslmanQtWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """WSL 发行版管理：侧边栏分页（发行版 / 镜像 / 设置）+ 后台任务。

    任务日志不占分页：顶部「帮助 → 任务日志…」打开非模态日志窗口，历史来自
    ``_log_lines`` 缓冲，窗口打开时回放、之后按 :attr:`logLine` 实时追加。
    """

    logLine = Signal(str)
    """任务日志行信号：后台任务写日志 → 已打开的日志窗口实时追加。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self.settings = w.load_settings(settings_path or w.config_path())
        self.config_data = load_config()
        self.launcher = SubprocessLauncher()
        self.view: Any = None
        self.sizes: dict[str, tuple[int | None, bool]] = {}
        self._rows: list[tuple[str, ...]] = []
        self.image_entries: tuple[ImageEntry, ...] = ()
        self._image_filter_key = self.settings.image_filter
        self._images_note = ""
        self._busy = False
        self._label = ""
        self._cancel = threading.Event()
        self._status_busy = False
        self._images_busy = False
        self._images_from_cache = False
        self._states_busy = False
        self._active_entry = ""
        self._task_page = ""
        self._closing = False
        self._log_lines: list[str] = []
        self._log_dialog: Any = None
        self._bridge: _LogBridge | None = None
        self._download_meter = SpeedMeter()
        self._progress_bridge: _ProgressBridge | None = None
        self._poll: QTimer | None = None
        self._filter_timer: QTimer | None = None
        self._buttons: list[QPushButton] = []
        self._button_by_text: dict[str, QPushButton] = {}
        super().__init__(
            title=TITLE,
            config_name=w.SETTINGS_NAME,
            nav_title=NAV_TITLE,
            default_size=DEFAULT_SIZE,
            min_size=(w.MIN_WIDTH, w.MIN_HEIGHT),
            on_close=self._on_close,
            persist=self._persist_settings,
            sidebar_collapsed=self.settings.sidebar_collapsed,
            settings_path=settings_path,
        )
        self._progress_bridge = _ProgressBridge(self)
        self._progress_bridge.changed.connect(self._on_download_progress)
        # 四页都很轻（表格 + 表单），建窗即构造：镜像页 / 设置页的控件是设置持久化
        # 与下载动作的读取来源，延迟构建会让「没切到该页就关窗」时缺控件。
        # 但提前构造的页在未插入页面栈前是主窗口的子控件，默认可见并落在 (0,0)，
        # 会把页内容画到菜单栏上；先 hide，等共享页面栈切换时再 show。
        builders = {
            "distros": self._build_distros_page,
            "images": self._build_images_page,
            "settings": self._build_settings_page,
        }
        for key, label, hint, icon in PAGES:
            page = builders[key]()
            page.hide()
            self.add_page(key, label, hint=hint, icon=icon, factory=lambda page=page: page)
        # 发行版行要注明对应的在线镜像：启动时先把镜像缓存回显出来（同步读盘，
        # 只有几十 KB），镜像页进入时再按 30 分钟新鲜度决定是否联网刷新。
        self._images_cache_fresh = False
        cache = self._restore_images_cache()
        self._images_cache_fresh = cache is not None and (time.time() - cache.fetched_at <= _images.CACHE_TTL_SECONDS)
        self._schedule_poll()
        self.refresh()

    def _build_menu(self) -> None:
        """共享菜单 + 「帮助 → 任务日志…」（放在「关于」之前）。"""
        super()._build_menu()
        log_action = QAction("任务日志…", self)
        log_action.setShortcut(QKeySequence("Ctrl+Shift+L"))
        log_action.triggered.connect(self.show_log)
        self.help_menu.insertAction(self.about_action, log_action)
        self.help_menu.insertSeparator(self.about_action)
        self.log_action = log_action

    def show_log(self) -> None:
        """打开（或聚焦）任务日志窗口；历史来自 ``_log_lines`` 缓冲。"""
        if self._log_dialog is None:
            from fcmd.gui.qt.logdialog import LogDialog

            self._log_dialog = LogDialog(
                "任务日志",
                text="\n".join(self._log_lines),
                append=self.logLine,
                on_clear=self._clear_log,
                hint="汇总启动 / 停止、导出、安装与下载操作；复制、清空、导出见日志工具条。",
                parent=self,
            )
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    def _clear_log(self) -> None:
        """日志窗口「清空」同时清掉缓冲，再次打开不会回放已清掉的行。"""
        self._log_lines.clear()

    def show_page(self, key: str) -> None:
        """切页：进入镜像页时先回显本地缓存，再后台刷新在线清单。

        ``super().show_page`` 内部会 ``nav.select``，侧边栏再回发一次 ``changed``
        到本方法；用重入标记把回调合并到最外层，避免同一页被处理两次
        （否则镜像页会重复回显缓存 / 重复拉清单）。
        """
        if getattr(self, "_switching_page", False):
            return
        self._switching_page = True
        try:
            super().show_page(key)
        finally:
            self._switching_page = False
        if key == "images":
            self._open_images_page()

    def _open_images_page(self) -> None:
        """镜像页入口：磁盘缓存秒开（有就画），随后按需后台更新。

        与 llmw 模型列表一致（陈旧回显 + 后台刷新）：断网 / 首次拉取较慢时
        用户先看到上一次的清单与更新时间，而不是空白列表；缓存仍在 30 分钟
        新鲜期内则直接当结果用，要强制刷新点「更新列表」。
        """
        if not self.image_entries:
            cache = self._restore_images_cache()
            self._images_cache_fresh = cache is not None and (
                time.time() - cache.fetched_at <= _images.CACHE_TTL_SECONDS
            )
        if self.image_entries and self._images_cache_fresh:
            # 新鲜缓存直接当结果用：不重复请求，要强制刷新点「更新列表」。
            self._images_from_cache = False
            return
        self._load_images()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_distros_page(self) -> QWidget:
        """发行版页：动作行 + 行卡列表（详情在行内展开）。

        与 llmw 模型页同构：固定行只有动作条，列表吃满剩余空间并自带滚动；
        BasePath / 虚拟磁盘 / 占用等详情通过行内「展开」就地显示，不再占用
        下方分栏，发行版多时也不会被详情面板压掉一半高度。
        """
        page = QWidget(self)
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addLayout(self._build_distro_actions(page))

        self.distro_list = RowListWidget(page)
        self.distro_list.setObjectName("DistroResultList")
        self.distro_list.setSpacing(4)
        self.distro_list.setMinimumHeight(200)
        self.distro_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self.distro_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.distro_list.currentRowChanged.connect(self._on_distro_selected)
        self.distro_list.itemDoubleClicked.connect(self._on_distro_double_clicked)
        self.distro_detail_view = _DistroDetailView(page)
        self.distro_detail_view.setVisible(False)
        self._expanded_distro_row: _DistroItemRow | None = None
        body.addWidget(self.distro_list, 1)
        return page

    def _build_distro_actions(self, page: QWidget) -> QHBoxLayout:
        """发行版动作行：按「终端 / 启停 → 导出 → 危险操作」分组。"""
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        groups: tuple[tuple[tuple[str, Any, str], ...], ...] = (
            (
                ("打开终端", self._open_selected, "primary"),
                ("启动", self._start_selected, ""),
                ("停止", self._terminate_selected, ""),
            ),
            (("导出", self._export_selected, ""),),
            (
                ("注销", self._unregister, "danger"),
                ("关闭全部", self._shutdown, "danger"),
            ),
        )
        for index, group in enumerate(groups):
            if index:
                separator = QLabel("│", page)
                separator.setObjectName("Hint")
                toolbar.addWidget(separator)
            for text, handler, kind in group:
                widget = button(text, kind=kind)
                widget.clicked.connect(handler)
                toolbar.addWidget(widget)
                self._buttons.append(widget)
                self._button_by_text[text] = widget
        toolbar.addStretch(1)
        self.start_button = self._button_by_text["启动"]
        self.stop_button = self._button_by_text["停止"]
        return toolbar

    def _build_images_page(self) -> QWidget:
        """镜像页：筛选条（含手动更新）+ 搜索行 + 单行状态 + 行卡列表 + 下载动作。

        布局与 llmw 模型页同构：固定行都是单行控件（不换行、不抢高度），列表吃满
        剩余空间并自带滚动，底部动作行只放下载相关控件。这样窗口压到最小高度时
        也不会出现「标签被压扁、底部动作行与列表重叠」。
        """
        page = QWidget(self)
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(6)
        self.image_filter_group = QButtonGroup(self)
        self.image_filter_group.setExclusive(True)
        self.image_filter_buttons: dict[str, QPushButton] = {}
        for key, label in w.IMAGE_FILTERS:
            chip = button(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            # ``_checked`` 必须有默认值：PySide 会按「可零参调用」挑 ``clicked()`` 重载。
            chip.clicked.connect(lambda _checked=False, key=key: self._on_image_filter_changed(key))
            self.image_filter_group.addButton(chip)
            filters.addWidget(chip)
            self.image_filter_buttons[key] = chip
        filters.addStretch(1)
        self.image_install_local = button("安装本地镜像…", kind="ghost")
        self.image_install_local.setToolTip(f"安装磁盘上已有的镜像包；{w._DOWNLOAD_HINT}")
        self.image_install_local.clicked.connect(self._install_local)
        filters.addWidget(self.image_install_local)
        self._buttons.append(self.image_install_local)
        self._button_by_text["安装本地镜像…"] = self.image_install_local
        # 手动更新（对齐 llmw「更新模型」）：绕过 30 分钟缓存重新拉两个在线源。
        self.images_refresh = button("更新列表", kind="ghost")
        self.images_refresh.setToolTip("重新获取 wsl --list --online 与官方镜像清单（绕过 30 分钟缓存）")
        self.images_refresh.clicked.connect(self._manual_update_images)
        filters.addWidget(self.images_refresh)
        body.addLayout(filters)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        search_row.addWidget(QLabel("搜索"))
        self.image_search = QLineEdit(self)
        self.image_search.setClearButtonEnabled(True)
        self.image_search.setPlaceholderText("按名称 / 友好名过滤…")
        self.image_search.setMinimumWidth(220)
        self.image_search.textChanged.connect(self._schedule_image_filter)
        search_row.addWidget(self.image_search, 1)
        self.image_sort = QComboBox(self)
        for key, label in w.IMAGE_SORTS:
            self.image_sort.addItem(label, key)
        self.image_sort.setCurrentIndex(max(self.image_sort.findData(self.settings.image_sort), 0))
        self.image_sort.currentIndexChanged.connect(self._on_image_sort_changed)
        search_row.addWidget(self.image_sort)
        self.image_source = QComboBox(self)
        for key, label in w.IMAGE_SOURCES:
            self.image_source.addItem(label, key)
        self.image_source.setCurrentIndex(max(self.image_source.findData(self.settings.image_source), 0))
        self.image_source.currentIndexChanged.connect(self._on_image_source_changed)
        search_row.addWidget(self.image_source)
        search_row.addStretch(0)
        body.addLayout(search_row)

        # 状态行固定单行（中间省略 + tooltip）：换行标签在矮窗口里会被压扁，
        # 文字叠到下一行控件上，是「底部重叠」的直接来源。
        self.images_status = ElidedLabel("打开本页时先显示本地缓存，再自动更新清单", parent=page)
        self.images_status.setObjectName("Hint")
        # 垂直策略固定：状态行永远只占一行高度，不参与窗口变高时的剩余空间分配。
        self.images_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        body.addWidget(self.images_status)

        # 列表铺满整页：详情通过行内展开显示（与 llmw 模型列表同构），不自带横向滚动。
        self.image_list = RowListWidget(page)
        self.image_list.setObjectName("ImageResultList")
        self.image_list.setSpacing(4)
        self.image_list.setMinimumHeight(200)
        self.image_list.setSelectionMode(RowListWidget.SelectionMode.SingleSelection)
        self.image_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.image_list.currentRowChanged.connect(self._on_image_selected)
        self.image_list.itemDoubleClicked.connect(self._on_image_double_clicked)
        self.image_detail_view = _ImageDetailView(page)
        self.image_detail_view.setVisible(False)
        self._expanded_row: _ImageItemRow | None = None
        body.addWidget(self.image_list, 1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._render_images)
        self.image_filter_buttons[self._image_filter_key].setChecked(True)

        return page

    def _build_settings_page(self) -> QWidget:
        """设置页：下载目录 / 下载源 / 校验 + 配置文件位置。

        原来塞在镜像页「高级」折叠区里的配置项全部搬到这里（镜像页只留列表与
        下载动作），页面用 :class:`PageScroll` 包裹，窗口变矮时滚动而不是压扁控件。
        """
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(page)
        outer.addWidget(scroll)
        layout = scroll.body

        download = Card("下载")
        form = FormGrid(download)
        self.download_dir = PathRow(
            value=self.settings.download_dir,
            placeholder=f"留空 = {default_download_dir()}",
            directory=True,
            show_reset=True,
            browse_text="更改",
            parent=download,
        )
        self.download_dir.setToolTip(
            "镜像归档的保存目录；留空时回退到 wslman.json 的 download_dir，再回退到工作目录的 download"
        )
        self.download_dir.committed.connect(self._on_download_dir_committed)
        self.download_dir.reset.clicked.connect(lambda: self.download_dir.set_value(""))
        # 「恢复默认」只在填了自定义目录时出现，默认状态下按钮不占位。
        self.download_dir.changed.connect(lambda text: self.download_dir.reset.setVisible(bool(text.strip())))
        self.download_dir.reset.setVisible(bool(self.settings.download_dir))
        form.add_row("下载目录", self.download_dir)

        self.mirror_ids = _mirrors.identifiers()
        self.mirror = QComboBox(download)
        self.mirror.addItems([_mirrors.label(ident) for ident in self.mirror_ids])
        index = self.mirror_ids.index(self.settings.mirror) if self.settings.mirror in self.mirror_ids else 0
        self.mirror.setCurrentIndex(index)
        self.mirror.setToolTip("国内镜像源；条目缺少 sha256 时只走官方源")
        self.mirror.currentIndexChanged.connect(self._on_mirror_changed)

        self.expected_sha = QLineEdit("", download)
        self.expected_sha.setPlaceholderText("可选：64 位十六进制摘要；留空用官方清单")
        self.expected_sha.setToolTip("只对下一次下载生效，不写入配置")
        # 同一行两对：下载源 + 校验，避免两个输入控件各占一整行。
        form.add_pair("下载源", self.mirror, "校验 sha256", self.expected_sha)
        download.body.addWidget(form)
        download.body.addWidget(
            hint_label(
                f"默认工作目录 {work_root()}：下载 / 安装 / 备份各自一个子目录（见下方「工作目录」卡片）；"
                f"下载目录留空时回退到 wslman.json 的 download_dir，再回退到 {default_download_dir()}。"
                f"{w._DOWNLOAD_HINT}"
            )
        )
        layout.addWidget(download)

        files = Card("配置文件")
        files_form = FormGrid(files)
        self.settings_file_label = ElidedLabel(str(self._settings_path), parent=files)
        self.settings_file_label.setObjectName("Mono")
        self.settings_file_label.setToolTip(str(self._settings_path))
        files_form.add_row("界面设置", self.settings_file_label)
        config_path = Path(self.config_data.path)
        self.cli_config_label = ElidedLabel(str(config_path), parent=files)
        self.cli_config_label.setObjectName("Mono")
        self.cli_config_label.setToolTip(str(config_path))
        files_form.add_row("命令行配置", self.cli_config_label)
        files.body.addWidget(files_form)
        files.body.addWidget(hint_label("界面设置保存下载目录 / 下载源 / 筛选等；命令行配置与 wslman CLI 共用。"))
        layout.addWidget(files)
        layout.addWidget(self._build_work_dirs())
        layout.addStretch(1)
        return page

    def _build_work_dirs(self) -> Card:
        """工作目录卡片：默认根 ``~/.fcmd/wsl`` 与下载 / 安装 / 备份三个用途子目录。"""
        card = Card("工作目录")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(hint_label("根目录", wrap=False))
        root = work_root()
        root_label = ElidedLabel(str(root), parent=card)
        root_label.setObjectName("Hint")
        root_label.setToolTip(str(root))
        row.addWidget(root_label, 1)
        open_button = button("打开", kind="ghost")
        open_button.setToolTip("在文件管理器中打开默认工作目录")
        open_button.clicked.connect(lambda: self._open_work_root(root))
        row.addWidget(open_button)
        card.body.addLayout(row)

        grid = FormGrid(card)
        for label, path, tip in (
            ("下载", default_download_dir(), "镜像归档（.wsl / .tar）默认保存位置"),
            ("安装", default_install_root(), "安装本地镜像时的默认目录根，每个发行版一个子目录"),
            ("备份", default_backups_dir(), "导出 / 备份的 .tar 默认保存位置"),
        ):
            value = ElidedLabel(str(path), mode=Qt.TextElideMode.ElideMiddle, parent=card)
            value.setObjectName("Mono")
            value.setToolTip(f"{tip}\n{path}")
            grid.add_row(label, value)
        card.body.addWidget(grid)
        card.body.addWidget(
            hint_label(
                "下载 / 安装 / 备份默认都写在这里；下载目录可在上方「下载」卡片覆盖，备份目录可用 wslman.json 的 backups.dir 覆盖。"
            )
        )
        return card

    def _open_work_root(self, root: Path) -> None:
        """打开默认工作目录（不存在时先创建）。"""
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法创建目录：{root}\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    # ------------------------------------------------------------------ #
    # 任务编排
    # ------------------------------------------------------------------ #
    def _start(self, label: str, work: Any) -> None:
        if self._busy:
            QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        self._busy = True
        self._label = label
        # 任务消息归属于「发起它的那一页」：下载在镜像页启动，用户切到日志页时
        # 完成提示不应写到日志页的状态栏上。
        self._task_page = self.current_page()
        self._cancel = threading.Event()
        self._bridge = _LogBridge(self)
        self._bridge.message.connect(self._log)
        self._set_task_status(f"{label}…")
        self._set_buttons(False)
        # 任务进行中在状态栏右侧显示加载条；可预估总量的下载会切成确定进度。
        self._download_meter.reset()
        self.show_loading(f"{label}…")

        def runner(cancel: threading.Event) -> tuple[bool, str]:
            try:
                return True, work(cancel)
            except KeyboardInterrupt:
                return False, "已取消；未完成的 .part 按配置保留或清理"
            except Exception as exc:  # 统一把失败转成可读文案
                return False, w._error_text(exc)

        submit(
            runner,
            self._cancel,
            on_done=lambda payload: self._task_done(label, payload),
            on_error=lambda message: self._task_error(label, message),
            on_finished=self._task_finished,
        )

    def _task_done(self, label: str, payload: tuple[bool, str]) -> None:
        ok, message = payload
        if not ok:
            self._log(f"[{label}] {message}")
            self._set_task_status(f"{label}失败")
            QMessageBox.critical(self, label, message)
            return
        self._log(f"[{label}] {message}")
        self._set_task_status(f"{label}完成")
        if label == "刷新":
            self._refresh_rows()
        else:
            self.refresh()
        # 下载 / 安装 / 注销后重扫本地状态：行内标签与动作按钮（已下载→安装、
        # 安装完成→已安装）要立刻跟着变，不必等用户手动「更新列表」。
        self._refresh_image_states()

    def _task_error(self, label: str, message: str) -> None:
        self._log(f"[{label}] 失败：{message}")
        self._set_task_status(f"{label}失败")
        QMessageBox.critical(self, label, message)

    def _task_finished(self) -> None:
        self._busy = False
        self._active_entry = ""
        self.show_loading("")
        self._download_meter.reset()
        self._set_buttons(True)
        if self._closing:
            return
        self._update_action_states()

    def _set_buttons(self, enabled: bool) -> None:
        for widget in self._buttons:
            widget.setEnabled(enabled)
        # 行内动作按钮跟着任务忙闲一起恢复：刷新任务结束时列表刚在忙态重建过，
        # 若只靠 3 秒轮询回填，启动按钮会先以禁用态（灰底）显示一段时间。
        if hasattr(self, "distro_list"):
            for index in range(self.distro_list.count()):
                row = self.distro_list.itemWidget(self.distro_list.item(index))
                if isinstance(row, _DistroItemRow):
                    row.set_busy(not enabled)
        if hasattr(self, "image_list"):
            for index in range(self.image_list.count()):
                row = self.image_list.itemWidget(self.image_list.item(index))
                if isinstance(row, _ImageItemRow):
                    row.set_download_state(busy=not enabled, active=row.entry.name == self._active_entry)
        if enabled:
            self._update_action_states()

    def _set_task_status(self, text: str) -> None:
        """把任务状态写到发起任务的那一页（没有记录时退回当前页）。"""
        self.set_page_status(self._task_page or self.current_page(), text)

    def _on_download_progress(self, done: int, total: object) -> None:
        """下载进度：状态栏写「百分比 · 已下载 / 总量 · 速度 · 剩余」，右侧进度条同步。

        进度信号从下载线程排队过来，``SpeedMeter`` 在主线程里按墙钟采样，数字平滑
        不抖；``start_page`` 固定写镜像页，用户切到别的页看日志时不会被进度文案刷屏。
        """
        total_bytes = total if isinstance(total, int) and total > 0 else None
        self._download_meter.update(done)
        speed = self._download_meter.speed
        self.set_page_status("images", w.download_progress_text(self._active_entry or "镜像", done, total_bytes, speed))
        # 右侧进度条用短文案：百分比 + 速度，长文案会把进度条挤窄。
        if total_bytes:
            brief = f"下载 {min(100, int(done * 100 / total_bytes))}% · {format_speed(speed)}"
        else:
            brief = f"下载 {format_bytes(done)} · {format_speed(speed)}"
        self.show_progress(brief, done, total_bytes)

    def _cancel_task(self) -> None:
        """取消按钮在「正在跑的那一行」里：只对进行中的任务生效。"""
        if self._busy:
            self._cancel.set()
            self._set_task_status(f"{self._label}取消中…")

    # ------------------------------------------------------------------ #
    # 运行状态轮询
    # ------------------------------------------------------------------ #
    def _schedule_poll(self) -> None:
        self._poll = periodic(self, w.POLL_INTERVAL_MS, self._poll_tick)

    def _poll_tick(self) -> None:
        """固定 3 秒轮询一次；窗口最小化时暂停，避免后台无意义开销。"""
        if self._closing or self.isMinimized():
            return
        self._request_status()

    def _request_status(self) -> None:
        if self._status_busy or self._busy:
            return
        self._status_busy = True
        submit(
            self._status_worker,
            on_done=self._on_status,
            on_error=self._on_status_error,
        )

    @staticmethod
    def _status_worker() -> Any:
        return cli._view(cli._build_environment())

    def _on_status(self, view: Any) -> None:
        self._status_busy = False
        if self._busy or self._closing:
            return
        self._apply_status(view)

    def _on_status_error(self, message: str) -> None:
        self._status_busy = False
        if not self._busy:
            self.set_page_status("distros", f"状态刷新失败：{message}")

    def _apply_status(self, view: Any) -> None:
        """轮询结果：名单没变就只刷新行内状态，不重建列表（选中与滚动位置不动）。"""
        previous = self.view
        self.view = view
        names = [distro.name for distro in view.distros]
        if previous is None or names != [distro.name for distro in previous.distros]:
            self._refresh_rows()
            return
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if not isinstance(row, _DistroItemRow):
                continue
            distro = view.find(row.distro.name)
            if distro is None:
                continue
            size, estimated = self.sizes.get(distro.name, (None, False))
            row.update_distro(distro, size, estimated)
            row.set_busy(self._busy)
            if row.is_expanded():
                self.distro_detail_view.render(distro, size, estimated)
        self._update_action_states()
        self._show_details()

    # ------------------------------------------------------------------ #
    # 列表与详情
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        if self._busy:
            return
        self._start("刷新", self._load)

    def _load(self, _cancel: threading.Event) -> str:
        env = cli._build_environment()
        view = cli._view(env)
        self.view = view
        sizes: dict[str, tuple[int | None, bool]] = {}
        for distro in view.distros:
            sizes[distro.name] = distro_disk_bytes(distro.base_path, distro.version)
        self.sizes = sizes
        return f"已加载 {len(view.distros)} 个发行版"

    def _selected(self) -> Distro | None:
        row = self._selected_row()
        return row.distro if row is not None else None

    def _selected_row(self) -> _DistroItemRow | None:
        item = self.distro_list.currentItem()
        row = self.distro_list.itemWidget(item) if item is not None else None
        return row if isinstance(row, _DistroItemRow) else None

    def _refresh_rows(self) -> None:
        """按当前视图重建行卡：保留选中行与展开行（只在名单变化时调用）。"""
        view = self.view
        selected = self._selected()
        expanded = self._expanded_distro_row.distro.name if self._expanded_distro_row is not None else ""
        self._detach_distro_detail()
        self.distro_list.clear()
        distros = tuple(view.distros) if view is not None else ()
        for distro in distros:
            size, estimated = self.sizes.get(distro.name, (None, False))
            item = QListWidgetItem(self.distro_list)
            row = _DistroItemRow(distro, size, estimated, self.distro_list)
            row.toggleRequested.connect(self._on_distro_toggle)
            row.startStopRequested.connect(self._toggle_distro_state)
            row.set_busy(self._busy)
            entry, known = self._distro_image(distro)
            row.update_image(entry, known=known)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.distro_list.setItemWidget(item, row)
        self.distro_list.sync_row_widths()
        target = 0 if distros else -1
        if selected is not None:
            found = next((index for index, distro in enumerate(distros) if distro.name == selected.name), None)
            if found is not None:
                target = found
        if target >= 0:
            self.distro_list.setCurrentRow(target)
        if expanded:
            for index in range(self.distro_list.count()):
                row = self.distro_list.itemWidget(self.distro_list.item(index))
                if isinstance(row, _DistroItemRow) and row.distro.name == expanded:
                    self._expand_distro_row(row)
                    break
        self._show_details()

    def _distro_image(self, distro: Distro) -> tuple[ImageEntry | None, bool]:
        """``(命中的在线镜像, 清单是否已知)``；清单为空时行内不写「本地导入」。"""
        entries = self._catalog_entries()
        return w.match_distro_image(distro, entries), bool(entries)

    def _refresh_distro_images(self) -> None:
        """镜像清单变化后就地刷新每行的「镜像 …」说明（不重建列表）。"""
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _DistroItemRow):
                entry, known = self._distro_image(row.distro)
                row.update_image(entry, known=known)

    def _on_distro_selected(self, current: int) -> None:
        """选中行变化：同步行卡选中态、工具栏启停按钮与状态栏摘要。"""
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _DistroItemRow):
                row.set_selected(index == current)
        self._show_details()

    def _on_distro_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.distro_list.itemWidget(item)
        if isinstance(row, _DistroItemRow):
            self._on_distro_toggle(row)

    def _on_distro_toggle(self, row: _DistroItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行（与镜像行一致）。"""
        self._focus_distro_row(row)
        if row.is_expanded():
            row.set_expanded(False)
            self.distro_detail_view.setVisible(False)
            self._expanded_distro_row = None
            self._update_distro_row_height(row)
            return
        self._expand_distro_row(row)

    def _expand_distro_row(self, row: _DistroItemRow) -> None:
        if self._expanded_distro_row is not None and self._expanded_distro_row is not row:
            self._expanded_distro_row.set_expanded(False)
            self._update_distro_row_height(self._expanded_distro_row)
        size, estimated = self.sizes.get(row.distro.name, (None, False))
        self.distro_detail_view.render(row.distro, size, estimated)
        row.detail_layout.addWidget(self.distro_detail_view)
        self.distro_detail_view.setVisible(True)
        row.set_expanded(True)
        self._expanded_distro_row = row
        self._update_distro_row_height(row)

    def _focus_distro_row(self, row: _DistroItemRow) -> None:
        for index in range(self.distro_list.count()):
            if self.distro_list.itemWidget(self.distro_list.item(index)) is row:
                self.distro_list.setCurrentRow(index)
                return

    def _update_distro_row_height(self, row: _DistroItemRow | None) -> None:
        if row is None:
            return
        layout = row.layout()
        if layout is not None:
            layout.activate()
        for index in range(self.distro_list.count()):
            item = self.distro_list.item(index)
            if self.distro_list.itemWidget(item) is row:
                item.setSizeHint(QSize(0, row.sizeHint().height()))
                self.distro_list.sync_row_widths()
                return

    def _detach_distro_detail(self) -> None:
        """列表重建前把复用的详情控件从旧行摘下来，避免随旧行一起销毁。"""
        self.distro_detail_view.setParent(None)
        self.distro_detail_view.setVisible(False)
        if self._expanded_distro_row is not None:
            self._expanded_distro_row.set_expanded(False)
            self._expanded_distro_row = None

    def _show_details(self) -> None:
        """选中行变化时只更新状态栏摘要与工具栏启停按钮（详情在行内）。"""
        distro = self._selected()
        if distro is None:
            self.set_status_detail("")
            self._update_action_states()
            return
        size, estimated = self.sizes.get(distro.name, (None, False))
        self.set_status_detail(w.summary_text(distro, size, estimated))
        self._update_action_states()

    def _update_action_states(self) -> None:
        if self._busy:
            return
        distro = self._selected()
        can_start, can_stop = w.action_states(distro.state if distro is not None else None)
        self.start_button.setEnabled(can_start)
        self.stop_button.setEnabled(can_stop)

    def _toggle_distro_state(self, name: str) -> None:
        """行内「启动 / 停止」：聚焦该行后复用工具栏动作（口径完全一致）。"""
        row = self._row_for_distro(name)
        if row is None:
            return
        self._focus_distro_row(row)
        if row.distro.state == "running":
            self._terminate_selected()
        else:
            self._start_selected()

    def _row_for_distro(self, name: str) -> _DistroItemRow | None:
        key = name.casefold()
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _DistroItemRow) and row.distro.name.casefold() == key:
                return row
        return None

    def _log(self, text: str) -> None:
        """追加一行任务日志：写缓冲（限量）并发信号给已打开的日志窗口。"""
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        self._log_lines.append(line)
        del self._log_lines[:-_LOG_LINES_MAX]
        self.logLine.emit(line)

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #
    def _open_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        try:
            env = cli._build_environment()
            command = build_command(
                wsl_path=cli._require_wsl(env),
                distro=distro.name,
                cwd=None,
                user=None,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
            )
            self.launcher.launch(command)
        except (LaunchError, WslmanError) as exc:
            QMessageBox.critical(self, "打开失败", w._error_text(exc))
            return
        self._log(f"[打开] 已启动 {distro.name}")

    def _start_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if distro.state == "running":
            self.set_page_status("distros", f"{distro.name} 已在运行")
            self._update_action_states()
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, distro.name)
            if target.state == "running":
                return f"{target.name} 已在运行"
            # 用一次性命令把发行版带起来；直接 wsl -d 会进交互式 shell 并挂住线程。
            cli._run_step(
                env,
                ["-d", target.name, "--exec", "/bin/true"],
                action=f"启动 {target.name}",
                hint="确认发行版仍已注册；若 WSL 未启用请以管理员运行 wsl --install",
                timeout=120.0,
            )
            current = cli._require_target(env, target.name)
            if current.state != "running":
                raise runtime_error(
                    f"{target.name} 启动后状态仍为 {state_label(current)}",
                    hint="运行 fcmd wslman doctor 检查 WSL 状态，稍后可点「刷新」再次确认",
                )
            return f"已启动 {target.name}"

        self._start("启动", work)

    def _terminate_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if not confirm(self, "停止发行版", f"停止 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            # CLI 子命令仍是 terminate，只有展示层文案叫「停止」。
            cli._run_step(env, ["--terminate", distro.name], action=f"停止 {distro.name}")
            return f"已停止 {distro.name}"

        self._start("停止", work)

    def _export_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            f"导出发行版 {distro.name}",
            f"{distro.name}.tar",
            "tar 归档 (*.tar)",
        )
        if not target:
            return
        terminate_first = False
        if distro.state == "running":
            answer = QMessageBox.question(
                self,
                "发行版运行中",
                "运行中导出可能不一致。选择「是」先停止，选择「否」接受风险继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            terminate_first = answer == QMessageBox.StandardButton.Yes

        path = Path(target)

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            current = cli._require_target(env, distro.name)
            cli._guard_running_export(
                current,
                terminate_first=terminate_first,
                allow_running=not terminate_first,
                command=f"fcmd wslman export {current.name} {path}",
            )
            estimate, estimated = distro_disk_bytes(current.base_path, current.version)
            check = cli._preflight_for(env, path, estimate, estimated=estimated)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个位置或清理磁盘")
            parts = cli._export_parts(path, "none")
            try:
                size, digest = cli._export_stream(
                    env,
                    current.name,
                    path,
                    parts,
                    level=6,
                    terminate_first=terminate_first,
                    estimate=estimate,
                )
            except BaseException:
                cli._abort_parts([item for item in parts if item is not None], keep=env.config.keep_partial)
                raise
            return f"已导出 {path}（{format_bytes(size)}，sha256 {digest}）"

        self._start("导出", work)

    def _install_local(self) -> None:
        """安装磁盘上已有的镜像包：扫描下载目录或手动选包，确认信息后走 ``wsl --import``。"""
        if self._busy:
            QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        dialog = _InstallLocalDialog(self, self._catalog_entries())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        payload = dialog.payload()
        if payload is None:
            return
        archive, name, target, replace, backup = payload
        self._active_entry = name
        self._install_archive(archive, name, target, replace=replace, backup=backup)

    def _install_archive(self, archive: Path, name: str, target: Path, *, replace: bool, backup: Path | None) -> None:
        """工作线程执行 ``wsl --import``；替换沿用 CLI 的 ``_guard_replace`` 与备份顺序。"""

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            existing = cli._view(env).find(name)
            command = f'fcmd wslman import {name} "{archive}" --dir "{target}"'
            if existing is not None:
                if not replace:
                    raise invalid_argument(
                        f"发行版 {name} 已存在",
                        hint=f"勾选「替换同名发行版」后重试，或改用 CLI：{command} --replace --yes",
                    )
                running = existing.state == "running"
                steps = [
                    step for step, flag in (("停止", running), ("备份", backup is not None), ("注销", True)) if flag
                ]
                if self._bridge is not None:
                    self._bridge.message.emit(f"[安装] 替换 {existing.name}：" + " → ".join(steps) + " → 重新导入")
                cli._guard_replace(
                    env, existing=existing, yes=True, backup=backup, terminate_first=running, command=command
                )
                if running:
                    cli._run_step(env, ["--terminate", existing.name], action=f"停止 {existing.name}")
                if backup is not None:
                    cli._run_step(
                        env,
                        ["--export", existing.name, str(backup)],
                        action=f"备份 {existing.name}",
                        hint="备份失败时不会注销旧发行版",
                        timeout=3600.0,
                    )
                cli._run_step(env, ["--unregister", existing.name], action=f"注销 {existing.name}")
            # 目录校验放在替换处理之后：替换会注销并清空旧目录，先校验会把「已存在」误报成目录问题。
            problem = cli._dir_problem(target)
            if problem:
                raise invalid_argument(f"安装目录不可用：{problem}", hint="选择一个空目录（不存在会自动创建）")
            if self._bridge is not None:
                self._bridge.message.emit(f"[安装] 导入 {archive.name} → {target}")
            cli._run_step(
                env,
                ["--import", name, str(target), str(archive), "--version", "2"],
                action=f"安装 {name}",
                hint="目标目录必须为空；WSL1 镜像请改用 CLI 指定 --version 1",
                timeout=3600.0,
            )
            size = archive.stat().st_size
            return f"已安装 {name} 到 {target}（{archive.name}，{format_bytes(size)}）"

        self._start("安装", work)

    def _catalog_entries(self) -> tuple[ImageEntry, ...]:
        """已知镜像清单：优先本次会话从下载页加载的结果，否则只读缓存。"""
        if self.image_entries:
            return self.image_entries
        cache = _images.load_cache()
        return cache.entries if cache is not None else ()

    def _local_start_dir(self) -> str:
        """下载目录：设置页 > wslmanw.json > wslman.json > 工作目录的 download。"""
        configured = self.download_dir.value() or self.settings.download_dir
        if configured:
            return configured
        fallback = load_config().download_dir.strip()
        return fallback or str(default_download_dir())

    def _download_entry(self, name: str) -> None:
        """行内「下载 / 安装」按钮：先聚焦该行，再复用同一套下载流程。"""
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _ImageItemRow) and row.entry.name == name:
                self.image_list.setCurrentRow(index)
                self._download_payload(row.entry)
                return

    def _download_payload(self, entry: ImageEntry) -> None:
        """行内「下载」：只把归档拉到下载目录；完成后行变「已下载」，再点「安装」。

        安装单独成为一个动作（``_install_entry``），避免「下载 + 自动导入」把两件
        事混在一次操作里；下载失败的半成品仍按配置保留 ``.part``。
        """
        expected = self.expected_sha.text().strip() or None
        mirror_policy = self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto"
        directory = self._download_dir_path()

        def work(cancel: threading.Event) -> str:
            env = cli._build_environment()
            if not entry.downloads:
                # 只有 CLI / Store 来源：没有归档可下载，直接走 wsl --install。
                cli._run_step(
                    env,
                    ["--install", "-d", entry.name, "--no-launch"],
                    action=f"安装 {entry.name}",
                    hint="系统级安装可能弹 UAC；失败时检查网络或 WSL 版本",
                    timeout=3600.0,
                )
                return f"已通过 wsl --install 安装 {entry.name}；用「打开终端」启动"

            from fcmd.apps.wslman import _source

            download = w.pick_download(entry)
            target = directory / w.image_filename(entry.name, download)
            if target.exists():
                raise invalid_argument(f"目标文件已存在：{target}", hint="换一个目录或先删除旧文件")
            check = cli._preflight_for(env, target, None, estimated=True)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个目录")
            result_data = _source.download_mirrored(
                target,
                candidates=_mirrors.candidates(
                    download.url,
                    policy=mirror_policy,
                    verifiable=bool(expected or download.sha256),
                ),
                expected_sha256=expected or download.sha256,
                cancelled=cancel.is_set,
                progress=self._progress_bridge.changed.emit,
            )
            return (
                f"已下载 {target}（{format_bytes(result_data.size)}，"
                f"来源 {_mirrors.label(result_data.source)}，sha256 {result_data.sha256}）；"
                "点行内「安装」导入 WSL"
            )

        self._active_entry = entry.name
        self._start("下载", work)

    def _install_entry(self, name: str) -> None:
        """行内「安装」：已下载的归档 → 复用「安装本地镜像」对话框；无归档 → wsl --install。"""
        entry = self._entry_by_name(name)
        if entry is None:
            return
        if entry.installed:
            self.set_page_status("images", f"{entry.name} 已安装，无需重复安装")
            return
        if entry.archive and Path(entry.archive).is_file():
            dialog = _InstallLocalDialog(self, self._catalog_entries(), preset=Path(entry.archive))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            payload = dialog.payload()
            if payload is None:
                return
            archive, chosen_name, target, replace, backup = payload
            self._active_entry = entry.name
            self._install_archive(archive, chosen_name, target, replace=replace, backup=backup)
            return
        # 没有本地归档（CLI / Store 来源，或归档已被删除）：走下载 / wsl --install 路径。
        self._download_entry(name)

    def _entry_by_name(self, name: str) -> ImageEntry | None:
        """按发行版名（大小写不敏感）取当前列表里的条目。"""
        key = name.casefold()
        for entry in self.image_entries:
            if entry.name.casefold() == key:
                return entry
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _ImageItemRow) and row.entry.name.casefold() == key:
                return row.entry
        return None

    def _download_dir_path(self) -> Path:
        """下载目录（与「安装本地镜像」扫描目录同一口径）。"""
        return Path(self._local_start_dir())

    # ------------------------------------------------------------------ #
    # 镜像列表
    # ------------------------------------------------------------------ #
    def _on_mirror_changed(self, _index: int) -> None:
        """切换下载源：立刻写回设置并提示（配置只落在设置页，镜像页不再放下拉）。"""
        if 0 <= self.mirror.currentIndex() < len(self.mirror_ids):
            self.set_page_status("settings", f"下载源：{_mirrors.label(self.mirror_ids[self.mirror.currentIndex()])}")
            self._persist_image_view()

    def _on_download_dir_committed(self, _text: str) -> None:
        """下载目录变更（回车 / 选目录）立即落盘，下次启动回填。"""
        self._persist_image_view()
        self.set_page_status("settings", "下载目录已保存" if self.download_dir.value() else "下载目录已恢复默认")

    def _restore_images_cache(self) -> Any:
        """把上次落盘的镜像清单直接铺到列表上（不等网络），返回命中的缓存。"""
        cache = _images.load_cache()
        if cache is None or not cache.entries:
            return None
        self.image_entries = cache.entries
        self._images_from_cache = True
        self._images_note = f"本地缓存 · 更新于 {_images_stamp(cache.fetched_at)}"
        self._render_images()
        self._refresh_distro_images()
        self._log(f"[镜像列表] 已载入本地缓存：{len(cache.entries)} 个，更新于 {_images_stamp(cache.fetched_at)}")
        return cache

    def _refresh_image_states(self) -> None:
        """重扫本地状态并重绘行（不重新拉在线清单）。

        下载完成 → 行标签变「已下载」且按钮变「安装」；安装 / 注销完成 → 「已安装」
        随之增减。只跑 ``wsl -l -v`` 与下载目录扫描，代价远小于重新联网拉清单。
        """
        if self._closing or self._states_busy or not self.image_entries:
            return
        self._states_busy = True
        entries = self.image_entries

        def worker() -> tuple[ImageEntry, ...]:
            return self._mark_local(entries, cli._build_environment())

        submit_daemon(
            worker,
            on_done=self._apply_image_states,
            on_error=lambda message: self._log(f"[镜像状态] 刷新失败：{message}"),
            on_finished=lambda: setattr(self, "_states_busy", False),
        )

    def _apply_image_states(self, entries: tuple[ImageEntry, ...]) -> None:
        if self._closing or not entries:
            return
        self.image_entries = entries
        self._render_images()
        self._refresh_distro_images()

    def _manual_update_images(self) -> None:
        """「更新列表」按钮：绕过缓存重新拉取两个在线源（对齐 llmw「更新模型」）。"""
        if self._images_busy:
            self.set_page_status("images", "镜像清单正在更新，请稍候…")
            return
        self.set_page_status("images", "正在更新镜像清单…")
        self._log("[镜像列表] 手动更新：重新获取 wsl --list --online 与官方清单")
        self._load_images(force=True)

    def _set_images_busy(self, busy: bool) -> None:
        """更新镜像清单期间禁用「更新列表」，避免重复拉取。"""
        self._images_busy = busy
        if hasattr(self, "images_refresh"):
            self.images_refresh.setEnabled(not busy)
            self.images_refresh.setText("更新中…" if busy else "更新列表")

    def _load_images(self, *, force: bool = False) -> None:
        if self._images_busy or self._closing:
            return
        # 只有「真正拉过一遍」的列表才跳过重复请求；缓存回显的列表必须继续后台刷新。
        if self.image_entries and not force and not self._images_from_cache:
            return
        self._set_images_busy(True)
        self.images_status.setText(
            "正在更新镜像清单…（跳过缓存，重新拉取 wsl --list --online 与官方清单）"
            if force
            else "正在获取镜像清单…（已显示本地缓存，稍后自动更新）"
            if self.image_entries
            else "正在获取镜像清单…（wsl --list --online 首次较慢）"
        )
        # 拉清单要跑 ``wsl --list --online`` 再下载官方 JSON，网络不好时会长时间阻塞；
        # 放进 ``QThreadPool`` 会让进程在关窗后仍等任务结束（窗口关了进程却退不出），
        # 因此走守护线程：结果回来前关窗，进程可以直接退出。
        submit_daemon(
            lambda: self._images_worker(force),
            on_done=self._apply_images,
            on_error=self._images_error,
            on_finished=lambda: self._set_images_busy(False),
        )

    def _images_worker(self, force: bool = False) -> tuple[Any, tuple[ImageEntry, ...]]:
        env = cli._build_environment()
        catalog = _images.collect_images(
            fetch_cli=lambda: cli._online_rows(env),
            fetch_manifest=cli._manifest_images,
            force=force,
        )
        return catalog, self._mark_local(catalog.entries, env)

    def _mark_local(self, entries: Any, env: Any) -> tuple[ImageEntry, ...]:
        """标注本地状态：已安装 / 已备份（``wsl`` 侧）+ 已下载（下载目录里的归档）。"""
        marked = _images.mark_local_state(
            entries,
            installed=cli._installed_names(env),
            backups=cli._backup_names(env),
        )
        return w.mark_downloaded(marked, self._download_dir_path())

    def _images_error(self, message: str) -> None:
        kept = "；列表仍是上一次的结果" if self.image_entries else ""
        self.images_status.setText(f"镜像清单获取失败：{message}{kept}")
        self._log(f"[镜像列表] {message}")

    def _apply_images(self, payload: tuple[Any, tuple[ImageEntry, ...]]) -> None:
        catalog, entries = payload
        self.image_entries = entries
        self._images_from_cache = catalog.from_cache
        self._images_note = _images_note_text(catalog)
        for warning in catalog.warnings:
            self._log(f"[镜像列表] {warning}")
        self._render_images()
        self._refresh_distro_images()

    def _render_images(self) -> None:
        filter_key = self._image_filter_key
        source = self.image_source.currentData()
        sort_key = self.image_sort.currentData()
        query = self.image_search.text().strip()
        keep = self._selected_image()
        rows = w.sort_images(
            w.filter_images(self.image_entries, query=query, filter_key=filter_key, source=source or "all"),
            sort_key if isinstance(sort_key, str) else w.DEFAULT_IMAGE_SORT,
        )
        self._detach_image_detail()
        self.image_list.clear()
        for entry in rows:
            item = QListWidgetItem(self.image_list)
            row = _ImageItemRow(entry, self.image_list)
            row.toggleRequested.connect(self._on_image_toggle)
            row.downloadRequested.connect(self._download_entry)
            row.installRequested.connect(self._install_entry)
            row.cancelRequested.connect(self._cancel_task)
            row.set_download_state(busy=self._busy, active=entry.name == self._active_entry)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.image_list.setItemWidget(item, row)
        self.image_list.sync_row_widths()
        self.images_status.setText(
            w.image_status_text(
                self.image_entries,
                len(rows),
                filter_key=filter_key,
                source=source or "all",
                note=self._images_note,
            )
        )
        target = None
        if keep is not None:
            target = next((index for index, entry in enumerate(rows) if entry.name == keep.name), None)
        if target is None and rows:
            target = 0
        if target is not None:
            self.image_list.setCurrentRow(target)

    def _on_image_filter_changed(self, key: str) -> None:
        """切换筛选按钮：只重绘列表，不重新拉取在线清单。"""
        if key == self._image_filter_key:
            return
        self._image_filter_key = key
        self._persist_image_view()
        self._render_images()

    def _on_image_sort_changed(self, _index: int) -> None:
        self._persist_image_view()
        self._render_images()

    def _on_image_source_changed(self, _index: int) -> None:
        self._persist_image_view()
        self._render_images()

    def _persist_image_view(self) -> None:
        """筛选 / 排序 / 来源立即写回设置，下次启动恢复上次视角。"""
        self._persist_settings(self.geometry_text())

    def _schedule_image_filter(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.start()

    def _on_image_selected(self, current: int) -> None:
        for index in range(self.image_list.count()):
            widget = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(widget, _ImageItemRow):
                widget.set_selected(index == current)

    def _on_image_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.image_list.itemWidget(item)
        if isinstance(row, _ImageItemRow):
            self._on_image_toggle(row)

    def _on_image_toggle(self, row: _ImageItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行。"""
        self._focus_image_row(row)
        if row.is_expanded():
            row.set_expanded(False)
            self.image_detail_view.setVisible(False)
            self._expanded_row = None
            self._update_image_row_height(row)
            return
        if self._expanded_row is not None and self._expanded_row is not row:
            self._expanded_row.set_expanded(False)
            self._update_image_row_height(self._expanded_row)
        self.image_detail_view.render(row.entry)
        row.detail_layout.addWidget(self.image_detail_view)
        self.image_detail_view.setVisible(True)
        row.set_expanded(True)
        self._expanded_row = row
        self._update_image_row_height(row)

    def _focus_image_row(self, row: _ImageItemRow) -> None:
        for index in range(self.image_list.count()):
            if self.image_list.itemWidget(self.image_list.item(index)) is row:
                self.image_list.setCurrentRow(index)
                return

    def _update_image_row_height(self, row: _ImageItemRow | None) -> None:
        if row is None:
            return
        layout = row.layout()
        if layout is not None:
            layout.activate()
        for index in range(self.image_list.count()):
            item = self.image_list.item(index)
            if self.image_list.itemWidget(item) is row:
                item.setSizeHint(QSize(0, row.sizeHint().height()))
                self.image_list.sync_row_widths()
                return

    def _detach_image_detail(self) -> None:
        """列表重建前把复用的详情控件从旧行摘下来，避免随旧行一起销毁。"""
        self.image_detail_view.setParent(None)
        self.image_detail_view.setVisible(False)
        if self._expanded_row is not None:
            self._expanded_row.set_expanded(False)
            self._expanded_row = None

    def _selected_image(self) -> ImageEntry | None:
        item = self.image_list.currentItem()
        row = self.image_list.itemWidget(item) if item is not None else None
        return row.entry if isinstance(row, _ImageItemRow) else None

    # ------------------------------------------------------------------ #
    # 注销 / 关闭全部
    # ------------------------------------------------------------------ #
    def _unregister(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        dialog = _UnregisterDialog(self, distro, self.sizes.get(distro.name, (None, False)))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        backup = dialog.backup_path()
        terminate_first = dialog.terminate_first.isChecked()

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, distro.name)
            if target.state == "running" and not terminate_first:
                raise invalid_argument(f"{target.name} 正在运行，请勾选「先停止」")
            estimate, estimated = distro_disk_bytes(target.base_path, target.version)
            with cli.TransferLock(target.name, directory=env.lock_dir):
                if backup is not None:
                    cli._export_for_backup(env, target.name, backup, estimate, estimated=estimated)
                if terminate_first:
                    cli._run_step(env, ["--terminate", target.name], action=f"停止 {target.name}")
                cli._run_step(env, ["--unregister", target.name], action=f"注销 {target.name}")
            return f"已注销 {target.name}" + (f"；备份在 {backup}" if backup else "")

        self._start("注销", work)

    def _shutdown(self) -> None:
        dialog = _ShutdownDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            cli._run_step(env, ["--shutdown"], action="wsl --shutdown")
            return "已关闭全部发行版与 WSL 虚拟机"

        self._start("关闭全部", work)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _persist_settings(self, geometry: str) -> None:
        w.save_settings(
            w.WslmanwSettings(
                geometry=geometry,
                download_dir=self.download_dir.value(),
                mirror=self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto",
                sidebar_collapsed=self.nav.is_collapsed(),
                image_filter=self._image_filter_key,
                image_sort=self.image_sort.currentData() or w.DEFAULT_IMAGE_SORT,
                image_source=self.image_source.currentData() or w.DEFAULT_IMAGE_SOURCE,
            ),
            self._own_settings_path,
        )

    def _on_close(self) -> bool:
        if self._busy and not confirm(self, "任务进行中", "有任务正在执行，取消并退出？"):
            return False
        self._cancel.set()
        self._closing = True
        if self._poll is not None:
            self._poll.stop()
        for timer in (self._filter_timer,):
            if timer is not None:
                timer.stop()
        return True


class _UnregisterDialog(QDialog):  # pragma: no cover - 模态对话框
    """注销确认：手输发行版名、可选先停止、可选先备份。"""

    def __init__(self, owner: WslmanQtWindow, distro: Distro, size: tuple[int | None, bool]) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"注销 {distro.name}")
        self.setMinimumWidth(560)
        self._distro = distro
        estimate, estimated = size

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                f"BasePath: {distro.base_path or '未知'}\n"
                f"占用: {format_bytes(estimate, estimated=estimated)}\n"
                "虚拟磁盘删除后无回收站。"
            )
        )
        self.terminate_first = QCheckBox("先停止发行版", self)
        self.terminate_first.setChecked(distro.state == "running")
        outer.addWidget(self.terminate_first)
        self.backup_enabled = QCheckBox("先导出备份到", self)
        self.backup_enabled.setChecked(bool(owner.config_data.require_backup_on_replace))
        outer.addWidget(self.backup_enabled)
        backup_row = QHBoxLayout()
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_row.setSpacing(6)
        self.backup = QLineEdit(str(recommended_backup_dir() / f"{distro.name}.tar"), self)
        backup_row.addWidget(self.backup, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_backup)
        backup_row.addWidget(choose)
        outer.addLayout(backup_row)
        outer.addWidget(hint_label(f"手输发行版名以确认：{distro.name}"))
        self.typed = QLineEdit(self)
        self.typed.textChanged.connect(self._sync)
        outer.addWidget(self.typed)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = button("注销", kind="danger")
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        outer.addLayout(actions)
        self._sync()
        self.typed.setFocus()

    def _sync(self) -> None:
        self.confirm.setEnabled(w.unregister_confirm_enabled(self.typed.text(), self._distro.name))

    def _choose_backup(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            "选择备份文件",
            str(recommended_backup_dir() / f"{self._distro.name}.tar"),
            "tar 归档 (*.tar)",
        )
        if chosen:
            self.backup.setText(chosen)

    def backup_path(self) -> Path | None:
        text = self.backup.text().strip()
        return Path(text) if self.backup_enabled.isChecked() and text else None


class _ShutdownDialog(QDialog):  # pragma: no cover - 模态对话框
    """``wsl --shutdown`` 确认：必须勾选影响说明。"""

    def __init__(self, owner: WslmanQtWindow) -> None:
        super().__init__(owner)
        self.setWindowTitle("关闭全部 WSL")
        self.setMinimumWidth(460)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label("会停止全部发行版与 WSL 虚拟机，并连带停掉 Docker Desktop、IDE Remote 等后端。"))
        self.checked = QCheckBox("我了解上述影响", self)
        self.checked.toggled.connect(lambda _checked: self._sync())
        outer.addWidget(self.checked)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = button("关闭全部", kind="danger")
        self.confirm.clicked.connect(self.accept)
        actions.addWidget(self.confirm)
        outer.addLayout(actions)
        self._sync()

    def _sync(self) -> None:
        self.confirm.setEnabled(w.shutdown_confirm_enabled(self.checked.isChecked()))


class _InstallLocalDialog(QDialog):  # pragma: no cover - 模态对话框
    """安装本地镜像：扫描目录 / 选文件、查看识别结果、填名称与安装目录、可选替换。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        entries: tuple[ImageEntry, ...],
        *,
        preset: Path | None = None,
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle("安装本地镜像")
        self.setMinimumWidth(820)
        self._entries = entries
        self._preset = preset
        self._archives: dict[int, Any] = {}
        self._payload: tuple[Path, str, Path, bool, Path | None] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(QLabel("扫描目录"))
        self.directory = QLineEdit(owner._local_start_dir(), self)
        top.addWidget(self.directory, 1)
        pick_file = button("选择文件…")
        pick_file.clicked.connect(self._add_file)
        top.addWidget(pick_file)
        rescan = button("重新扫描")
        rescan.clicked.connect(self._scan)
        top.addWidget(rescan)
        outer.addLayout(top)

        self.table = make_table(("镜像包", "大小", "识别为"), widths=(440, 100, 180), stretch_columns=(0,), parent=self)
        self.table.setMinimumHeight(220)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.cellDoubleClicked.connect(lambda _row, _column: self._start())
        outer.addWidget(self.table, 1)

        info_grid = QGridLayout()
        info_grid.setContentsMargins(0, 0, 0, 0)
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(4)
        info_grid.setColumnStretch(1, 1)
        self._info: dict[str, QLabel] = {}
        for row, key in enumerate(("发行版", "大小", "来源", "校验状态")):
            info_grid.addWidget(hint_label(key, wrap=False), row, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", self)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            info_grid.addWidget(value, row, 1)
            self._info[key] = value
        outer.addLayout(info_grid)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        self.name = QLineEdit(self)
        form.addWidget(QLabel("发行版名称"), 0, 0)
        form.addWidget(self.name, 0, 1)
        self.target = QLineEdit(self)
        form.addWidget(QLabel("安装目录"), 1, 0)
        form.addWidget(self.target, 1, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_dir)
        form.addWidget(choose, 1, 2)
        self.replace = QCheckBox("替换同名发行版（会先注销，必要时先备份）", self)
        form.addWidget(self.replace, 2, 1)
        outer.addLayout(form)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        start = button("开始安装", kind="primary")
        start.clicked.connect(self._start)
        footer.addWidget(start)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        footer.addStretch(1)
        outer.addLayout(footer)
        self._scan()
        fit_dialog_to_content(self, min_width=820)

    def _scan(self) -> None:
        self.table.setRowCount(0)
        self._archives.clear()
        paths = list(w.local_archives(Path(self.directory.text().strip() or ".")))
        # 行内「安装」带进来的归档一定在表里，即使它的目录与扫描目录不同。
        if self._preset is not None and self._preset.is_file() and self._preset not in paths:
            paths.insert(0, self._preset)
        for path in paths:
            self._add(w.inspect_archive(path, self._entries))
        preset_row = self._row_of(self._preset) if self._preset is not None else None
        if preset_row is not None:
            self.table.selectRow(preset_row)
        elif not self._archives:
            self._info["校验状态"].setText("该目录没有 .tar / .tar.gz / .tgz / .wsl 镜像包，可用「选择文件…」指定")
        elif self.table.rowCount():
            self.table.selectRow(0)

    def _row_of(self, path: Path | None) -> int | None:
        """归档路径在表格里的行号（用于预选行内「安装」带进来的包）。"""
        if path is None:
            return None
        target = path.resolve()
        for row, archive in self._archives.items():
            if archive.path.resolve() == target:
                return row
        return None

    def _add(self, archive: Any) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, make_table_item(str(archive.path)))
        self.table.setItem(row, 1, make_table_item(format_bytes(archive.size)))
        self.table.setItem(row, 2, make_table_item(archive.distro))
        self._archives[row] = archive

    def _add_file(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "选择本地镜像包",
            self.directory.text(),
            "镜像包 (*.tar *.tar.gz *.tgz *.wsl);;全部文件 (*.*)",
        )
        if not chosen:
            return
        self._add(w.inspect_archive(Path(chosen), self._entries))
        self.table.selectRow(self.table.rowCount() - 1)

    def _choose_dir(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = QFileDialog.getExistingDirectory(self, "选择安装目录（必须为空）")
        if chosen:
            self.target.setText(chosen)

    def _current(self) -> Any:
        row = self.table.currentRow()
        return self._archives.get(row)

    def _on_select(self) -> None:
        archive = self._current()
        if archive is None:
            return
        arch = archive.arch if archive.arch != "未知" else "架构未知"
        self._info["发行版"].setText(f"{archive.distro}（{arch}）")
        self._info["大小"].setText(format_bytes(archive.size))
        self._info["来源"].setText(archive.source)
        if archive.expected_sha256 is None:
            self._info["校验状态"].setText(w.verification_label(None, None))
        else:
            self._info["校验状态"].setText("正在计算 sha256…")
            archive_path = archive.path
            expected = archive.expected_sha256
            submit(
                lambda: w.verification_label(expected, sha256_file(archive_path)),
                on_done=self._info["校验状态"].setText,
            )
        self.name.setText(archive.distro)
        self.target.setText(str(default_install_root() / archive.distro))

    def _start(self) -> None:
        archive = self._current()
        if archive is None:
            QMessageBox.critical(self, "安装本地镜像", "请先选择一个镜像包")
            return
        name = self.name.text().strip()
        target_text = self.target.text().strip()
        if not name:
            QMessageBox.critical(self, "安装本地镜像", "请填写发行版名称")
            return
        if not target_text:
            QMessageBox.critical(self, "安装本地镜像", "请选择安装目录")
            return
        replace = self.replace.isChecked()
        if replace and not confirm(self, "替换发行版", f"替换 {name} 会先注销现有发行版（运行中会先停止），继续？"):
            return
        backup: Path | None = None
        if replace and load_config().require_backup_on_replace:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "替换前先备份到",
                str(recommended_backup_dir() / f"{name}.tar"),
                "tar 归档 (*.tar)",
            )
            if not chosen:
                return
            backup = Path(chosen)
        self._payload = (archive.path, name, Path(target_text), replace, backup)
        self.accept()

    def payload(self) -> tuple[Path, str, Path, bool, Path | None] | None:
        """确认后的安装参数；取消时为 ``None``。"""
        return self._payload


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.qt.app import run_window

    run_window(lambda: WslmanQtWindow(settings_path), app_name="WSL 管理")
