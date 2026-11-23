"""wslmanw 的 Qt 主窗口与入口。

窗口只做编排：业务判断全部走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出、预检、删除实例前备份、镜像下载），
与 CLI 共用同一套口径；界面负责列表 / 详情 / 下载与下载配置，配置项集中在设置页；
任务日志从「帮助 → 任务日志…」以非模态窗口打开（与 llmw 的服务日志同形态）。

布局与 llmw 对齐：复用共享 :class:`fcmd.gui.window.QtMainWindow` 的左侧
:class:`~fcmd.gui.sidebar.SidebarNav` 导航（折叠、``Alt+1..4`` 切换与窗口
几何记忆都由基座提供），右侧按「实例 / 文件 / 镜像 / 设置」分页，每页拿满整个
内容区，不再把详情、下载与配置挤进同一个底部标签页；任务日志不占分页，从
「帮助 → 任务日志…」打开。实例页用一个
纵向分栏把列表与详情分开；下载目录 / 下载源 / 校验这类配置统一放设置页，
镜像页只留列表与下载动作；镜像页参考 llmw 模型列表：自定义行卡铺满整页，
详情与下载地址通过行内「展开」就地显示，不再占用右侧面板；进页先回显磁盘缓存
（`cache/wslman-available.json`）再后台刷新，「更新列表」可手动绕过 30 分钟缓存。
后台任务一律走
:func:`fcmd.gui.workers.submit`，运行状态每 3 秒轮询一次（最小化时暂停），
关窗时取消进行中的任务。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``（与旧设置文件同名字段互读）。
"""

from __future__ import annotations

import contextlib
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
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

from fcmd.apps.wslman import _files, _images, _initenv, _mirrors, _open, _sshfiles
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._config import load_config
from fcmd.apps.wslman._decode import decode_console
from fcmd.apps.wslman._disk import distro_disk_bytes, distro_export_bytes, format_bytes
from fcmd.apps.wslman._distro import Distro, state_label
from fcmd.apps.wslman._errors import WslmanError, invalid_argument, runtime_error
from fcmd.apps.wslman._terminal import LaunchError, SubprocessLauncher, build_command, spawn_detached
from fcmd.apps.wslman._transfer import sha256_file
from fcmd.gui import icons as _qt_icons
from fcmd.gui.geometry import format_geometry
from fcmd.gui.page import PageBody, PageSpec
from fcmd.gui.widgets import (
    Card,
    DiskGrid,
    ElidedLabel,
    FormGrid,
    ItemRow,
    PageScroll,
    PathRow,
    RowDetailMixin,
    RowListWidget,
    StatusTag,
    button,
    confirm,
    default_disk_locations,
    fit_dialog_to_content,
    hint_label,
    icon_button,
    make_table,
    make_table_item,
    set_button_kind,
    set_button_text,
)
from fcmd.gui.window import QtMainWindow, restore_geometry
from fcmd.gui.workers import EventBridge, ProgressBridge, periodic, submit, submit_daemon
from fcmd.infras.download import SpeedMeter, format_speed

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from fcmd.apps.wslman._images import ImageEntry

__all__ = ["WslmanQtWindow", "run_qt"]

DEFAULT_SIZE = (1160, 760)

_KEEPALIVE_LOOP = "while :; do sleep 3600; done"
"""启动实例时的看护命令：常驻一个 sleep 循环，让实例在 wslmanw 关闭后仍保持运行。

用 POSIX ``sh`` 循环而不是 ``sleep infinity``：部分镜像的 ``sleep`` 不认 ``infinity``，
循环在任何有 ``/bin/sh`` 的发行版里都能work；用户点「停止」（``wsl --terminate``）
会连同它一起结束。
"""

_LOG_LINES_MAX = 1000
"""任务日志缓冲上限（行）；超出后丢弃最旧的，避免长时间运行吃内存。"""

TITLE = "WSL · 实例管理"
NAV_TITLE = "WSL"

PAGES: tuple[PageSpec, ...] = (
    PageSpec("distros", "实例", "列表、详情、启动停止、导出与删除实例", "distros"),
    PageSpec("files", "文件", "浏览实例文件系统、上传下载与增删改", "folder"),
    PageSpec("images", "镜像", "在线镜像清单、本地安装与清单更新", "disc"),
    PageSpec("settings", "设置", "下载目录、下载源与校验", "settings"),
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


def _state_tone(state: str) -> str:
    """实例状态 → 胶囊色调（与 ``state_label`` 的文案配套）。"""
    return {"running": "success", "stopped": "muted", "unknown": "warning"}.get(state, "muted")


@dataclass
class _DownloadTask:
    """镜像下载队列里的一项：独立取消标记、进度与状态，互不影响。

    ``state`` 取 ``queued`` / ``running`` / ``ok`` / ``failed`` / ``cancelled``：
    排队中也能单独取消；单项失败只影响自己，调度器会继续跑后面的项。
    """

    entry: ImageEntry
    cancel: threading.Event = field(default_factory=threading.Event)
    state: str = "queued"
    done: int = 0
    total: int | None = None
    message: str = ""

    @property
    def finished(self) -> bool:
        return self.state in ("ok", "failed", "cancelled")


class _DistroItemRow(ItemRow):  # pragma: no cover - Qt 控件
    """实例行卡：共享 :class:`ItemRow` 骨架 + 实例自己的状态位。

    标题是实例名，副标题行是「版本 + 对应的在线镜像」；状态胶囊与默认胶囊
    贴在同一行行尾，右侧是磁盘占用、行内启停按钮与展开箭头。详情（BasePath /
    虚拟磁盘 / 占用 / 默认用户 / 来源）由 ``_DistroDetailView`` 挂到
    ``detail_layout`` 就地显开，同一时间只展开一行。
    """

    startStopRequested = Signal(str)
    configRequested = Signal(str)

    def __init__(
        self,
        distro: Distro,
        size: int | None,
        estimated: bool,
        work_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(distro.name, "", parent=parent)
        self.distro = distro
        self.size = size
        self.estimated = estimated
        self.work_dir = work_dir
        self.state_tag = StatusTag("", tone="muted", parent=self.header)
        self.add_tag(self.state_tag)
        self.default_tag = StatusTag("默认", tone="accent", parent=self.header)
        self.add_tag(self.default_tag)
        # 发行版图标：按实例名挑对应发行版，识别不出回落通用图标（不留空、不显示问号）。
        # 与动作按钮同一套 ``icons`` 线图标，尺寸 / 色调跟随主题。
        self.icon_label = QLabel(self.header)
        self.icon_label.setObjectName("DistroIcon")
        self.icon_label.setToolTip(distro.name)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title_row.insertWidget(0, self.icon_label)
        _qt_icons.set_icon(self.icon_label, _qt_icons.distro_icon(distro.name), tone="accent")
        # 对应的在线镜像（自定义导入 / 匹配不到时给「本地导入」）放在副标题行，
        # 跟版本同一排：状态胶囊也在副标题行尾，两行各自独立，窄窗口或大字体现
        # 也不会和「镜像 …」挤在一起。超长时右侧省略，完整文案走 tooltip。
        self.image_label = ElidedLabel("", mode=Qt.TextElideMode.ElideRight, parent=self.header)
        self.image_label.setObjectName("Hint")
        self.image_label.setVisible(False)
        # 吃掉版本与状态胶囊之间的剩余宽度，长镜像名右侧省略，不会挤到状态胶囊。
        self.add_note_widget(self.image_label, stretch=1)

        # 启停是动态动作：待机是主按钮（启动），运行中是红色危险按钮（停止）。
        self.start_stop_button = button("启动", kind="primary")
        self.start_stop_button.clicked.connect(lambda: self.startStopRequested.emit(distro.name))
        self.add_action(self.start_stop_button)
        # 配置放在启停右侧：设为默认、终端默认目录等实例级设置不强制用户先选中再跑工具栏。
        self.config_button = button("配置", kind="ghost")
        self.config_button.setToolTip(f"配置 {distro.name}：默认发行版、终端目录、初始化环境与 SSH 迁移")
        self.config_button.clicked.connect(lambda: self.configRequested.emit(distro.name))
        self.add_action(self.config_button)
        self._busy = False

        self.update_distro(distro, size, estimated)

    def _work_dir_text(self) -> str:
        """副标题里的工作目录：显示目录名；不属于任何已配置目录时如实说明。"""
        if self.work_dir is None:
            return "未在已配置目录"
        return self.work_dir.name or str(self.work_dir)

    def _row_tooltip(self) -> str:
        """完整磁盘位置与所属工作目录；副标题空间有限，完整路径放 tooltip。"""
        place = self.distro.base_path or "未知"
        if self.work_dir is None:
            return f"{self.distro.name}\n磁盘位置：{place}\n工作目录：未在已配置目录"
        return f"{self.distro.name}\n磁盘位置：{place}\n工作目录：{self.work_dir}"

    def update_distro(self, distro: Distro, size: int | None, estimated: bool) -> None:
        """就地刷新状态 / 占用 / 默认标记与启停按钮（轮询不重建行）。"""
        self.distro = distro
        self.size = size
        self.estimated = estimated
        self.setToolTip(self._row_tooltip())
        self.set_title(distro.name)
        # 与 llmw 模型行同构：左侧副标题只放简短口径（版本 + 所属工作目录）；
        # 右侧数值放磁盘占用。所属目录按实例实际 BasePath 归属，找不到就如实说明。
        self.set_subtitle(f"v{distro.version or '未知'} · {self._work_dir_text()}")
        self.set_metrics(format_bytes(size, estimated=estimated))
        self.state_tag.set_status(state_label(distro), _state_tone(distro.state))
        self.default_tag.setVisible(distro.default)
        self._apply_action_state()

    def update_image(self, entry: ImageEntry | None, *, known: bool) -> None:
        """标注该实例对应的在线镜像；``known=False`` 表示清单还没加载（留空）。"""
        text = w.distro_image_text(self.distro, (entry,) if entry is not None else (), known=known)
        self.image_label.setText(text)
        self.image_label.setVisible(bool(text))
        if entry is None:
            self.image_label.setToolTip("未匹配到在线镜像（自定义导入或已下架）" if known else "")
            return
        arches = "/".join(entry.arches) or "任意架构"
        sources = w.image_source_text(entry)
        self.image_label.setToolTip(f"{entry.friendly_name or entry.name}\n可用架构：{arches}\n来源：{sources}")

    def _apply_action_state(self) -> None:
        """启停按钮的文案 / 变体 / 图标色调：运行中给红色的「停止」，否则主按钮「启动」。

        禁用态（任务进行中 / 状态不可操作）统一用 ``muted`` 图标色调：主按钮与危险
        按钮的禁用底色都会变浅，保留 ``on_accent`` 的白勾或 ``danger`` 的红标在白底上
        会看不清。
        """
        can_start, can_stop = w.action_states(self.distro.state)
        enabled = (can_start or can_stop) and not self._busy
        if can_stop:
            set_button_kind(self.start_stop_button, "danger")
            tone = "danger" if enabled else "muted"
            text = "停止"
        else:
            set_button_kind(self.start_stop_button, "primary")
            tone = "on_accent" if enabled else "muted"
            text = "启动"
        set_button_text(self.start_stop_button, text, tone=tone)
        self.start_stop_button.setEnabled(enabled)
        self.start_stop_button.setToolTip(f"{text} {self.distro.name}")
        self.config_button.setEnabled(not self._busy)

    def set_busy(self, busy: bool) -> None:
        """任务进行中禁用行内启停 / 配置按钮；结束后按当前状态重新计算。"""
        self._busy = busy
        self._apply_action_state()


class _DistroDetailView(QWidget):  # pragma: no cover - Qt 控件
    """展开行里的实例详情：字段网格 + 长路径可选中复制。

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
            # 镜像行的状态列会出现「仅 wsl --install」这类长文案：和它在同一列的
            # 短文案也按同一宽度留位，上下行的分隔线 / 动作按钮才不会错位。
            metrics_samples=("仅 wsl --install", "已下载 100%"),
            parent=parent,
        )
        self.entry = entry
        self.setToolTip(entry.name)
        self.title_label.setToolTip(entry.name)
        self._busy = False
        self._active = False
        for text, tone in w.image_marks(entry):
            self.add_tag(StatusTag(text, tone=tone, parent=self.header))
        self.set_metrics(metrics)

        # 每行自带动作按钮：未下载 →「下载」；已下载未安装 →「安装」；已安装 → 无按钮。
        # 下载 / 安装进行中时该行按钮变成「取消」，不再依赖页面底部的动作条。
        self.action = self._action_kind(entry)
        self.download_button: QPushButton | None = None
        if self.action:
            # 「安装」不命中关键词（避免抢走「安装本地镜像」的磁盘图标），显式给下载图标。
            self.download_button = button(
                self._action_text(), kind="primary", icon="download" if self.action == "install" else None
            )
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
        # 按钮在任务进行中会变成「取消」：此时必须发取消信号，而不是再发一次
        # 下载 / 安装（后者会被窗口的忙态挡下，用户会觉得点了没反应）。
        if self._busy and self._active:
            self.cancelRequested.emit()
            return
        if self.action == "install":
            self.installRequested.emit(self.entry.name)
        else:
            self.downloadRequested.emit(self.entry.name)

    def set_download_state(
        self,
        *,
        busy: bool,
        active: bool = False,
        queued: bool = False,
        enabled: bool = True,
    ) -> None:
        """按下载队列刷新按钮：有任务的显示「取消」（排队中同样可取消），其余按忙态禁用。"""
        self._busy = busy
        self._active = busy and active
        if self.download_button is None:
            return
        if self._active:
            self.download_button.setText("取消")
            if queued:
                self.download_button.setToolTip("取消排队中的这项下载")
            else:
                self.download_button.setToolTip("取消本次下载（已下载的部分保留 .part，可续传）")
            self.download_button.setEnabled(True)
            return
        self.download_button.setText(self._action_text())
        self.download_button.setToolTip(self._action_tooltip())
        self.download_button.setEnabled(enabled)


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


class WslmanQtWindow(RowDetailMixin, QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """WSL 实例管理：侧边栏分页（实例 / 文件 / 镜像 / 设置）+ 后台任务。

    任务日志不占分页：顶部「帮助 → 任务日志…」打开非模态日志窗口，历史来自
    ``_log_lines`` 缓冲，窗口打开时回放、之后按 :attr:`logLine` 实时追加。
    """

    log_limit = _LOG_LINES_MAX
    log_hint = "汇总启动 / 停止、导出、安装与下载操作；复制、清空、导出见日志工具条。"

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
        self._refresh_pending = False
        self._list_pending = False
        self._opener_detections: dict[str, tuple[dict[str, _open.Detection], dict[str, _open.Detection]]] = {}
        self._detecting = False
        self._detect_token = 0
        self._settings_seen = False
        self._label = ""
        self._cancel = threading.Event()
        self._status_busy = False
        self._images_busy = False
        self._images_from_cache = False
        self._states_busy = False
        self._states_pending = False
        self._active_entry = ""
        self._task_page = ""
        self._closing = False
        self._bridge: EventBridge | None = None
        # 镜像下载队列：每项独立取消 / 进度，按设置里的并发上限调度；不占用全局
        # ``_busy``，避免和安装、迁移、刷新等重操作互相误判。
        self._downloads: dict[str, _DownloadTask] = {}
        self._download_meters: dict[str, SpeedMeter] = {}
        self._export_meter = SpeedMeter()
        self._poll: QTimer | None = None
        self._filter_timer: QTimer | None = None
        self._buttons: list[QPushButton] = []
        self._button_by_text: dict[str, QPushButton] = {}
        # 文件页状态：UNC 根按实例缓存；``_files_dir`` / ``_files_home`` 均为
        # 实例内 POSIX 路径，空串表示尚未加载。``_files_seq`` 用来丢弃用户快速
        # 切换目录后迟到的旧结果。
        self._files_roots: dict[str, str] = {}
        self._files_loaded = ""
        self._files_root = ""
        self._files_home = ""
        self._files_dir = ""
        self._files_user = ""
        self._files_entries: tuple[_files.FileEntry, ...] = ()
        self._files_loading = False
        self._files_busy = False
        self._files_seq = 0
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
            theme_app="wslmanw",
        )
        self._progress_bridge = ProgressBridge(self)
        self._export_bridge = EventBridge(self)
        self._progress_bridge.progress.connect(self._on_download_event)
        self._export_bridge.event.connect(self._on_export_event)
        # 四页都很轻（表格 + 表单），建窗即构造：镜像页 / 设置页的控件是设置持久化
        # 与下载动作的读取来源，延迟构建会让「没切到该页就关窗」时缺控件。
        # 但提前构造的页在未插入页面栈前是主窗口的子控件，默认可见并落在 (0,0)，
        # 会把页内容画到菜单栏上；先 hide，等共享页面栈切换时再 show。
        builders = {
            "distros": self._build_distros_page,
            "files": self._build_files_page,
            "images": self._build_images_page,
            "settings": self._build_settings_page,
        }
        factories: dict[str, Any] = {}
        for spec in PAGES:
            page = builders[spec.key]()
            page.hide()
            factories[spec.key] = lambda page=page: page
        self.add_pages(PAGES, factories)
        # 实例行要注明对应的在线镜像：启动时先把镜像缓存回显出来（同步读盘，
        # 只有几十 KB），镜像页进入时再按 30 分钟新鲜度决定是否联网刷新。
        self._images_cache_fresh = False
        cache = self._restore_images_cache()
        self._images_cache_fresh = cache is not None and (time.time() - cache.fetched_at <= _images.CACHE_TTL_SECONDS)
        self._schedule_poll()
        self.refresh()
        note = w.legacy_dir_note(self.settings)
        if note:
            self._log(f"[设置] {note}")

    def show_log(self) -> None:
        """打开（或聚焦）任务日志窗口；历史来自 ``_log_lines`` 缓冲。"""
        self.show_log_dialog()

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
        elif key == "files":
            self._open_files_page()
        elif key == "settings":
            self._open_settings_page()

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
        """实例页：动作行 + 行卡列表（详情在行内展开）。

        与 llmw 模型页同构：固定行只有动作条，列表吃满剩余空间并自带滚动；
        BasePath / 虚拟磁盘 / 占用等详情通过行内「展开」就地显示，不再占用
        下方分栏，实例多时也不会被详情面板压掉一半高度。
        """
        page = PageBody(self, spacing=8)
        body = page.body
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
        self.bind_row_detail("distros", self.distro_list, self.distro_detail_view)
        body.addWidget(self.distro_list, 1)
        return page

    def _build_distro_actions(self, page: QWidget) -> QHBoxLayout:
        """实例动作行：按「终端 / 启停 → 导出 → 危险操作」分组。"""
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)
        groups: tuple[tuple[tuple[str, Any, str], ...], ...] = (
            (
                ("打开终端", self._open_selected, "primary"),
                ("启动", self._start_selected, ""),
                ("重启", self._restart_selected, ""),
                ("停止", self._terminate_selected, ""),
                ("更新列表", self._on_reload_distros, "ghost"),
            ),
            (("导出", self._export_selected, ""), ("迁移", self._migrate_selected, "")),
            (
                ("删除实例", self._unregister, "danger"),
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
        self.restart_button = self._button_by_text["重启"]
        self.stop_button = self._button_by_text["停止"]
        return toolbar

    def _build_files_page(self) -> QWidget:
        """文件页：实例选择 + 可编辑路径栏 + 只读表格 + 上传下载 / 增删改。

        浏览走 UNC 根，不逐条执行命令；默认进入所选实例的家目录（``~``）。
        表格整页铺满（与实例 / 镜像页同构），所有 UNC / 文件操作都放到后台线程，
        避免网络路径卡住界面。
        """
        page = PageBody(self, spacing=8)
        body = page.body

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        path_row.addWidget(QLabel("实例"))
        self.files_distro = QComboBox(page)
        self.files_distro.setMinimumWidth(160)
        self.files_distro.setToolTip("选择要浏览的 WSL 实例")
        self.files_distro.currentIndexChanged.connect(self._on_files_distro_changed)
        path_row.addWidget(self.files_distro)
        self.files_up = button("上级", kind="ghost", icon="chevron-up")
        self.files_up.setToolTip("回到上一级目录")
        self.files_up.clicked.connect(self._files_go_up)
        path_row.addWidget(self.files_up)
        self.files_home = button("家目录", kind="ghost", icon="folder")
        self.files_home.setToolTip("回到当前实例的家目录（~）")
        self.files_home.clicked.connect(self._files_go_home)
        path_row.addWidget(self.files_home)
        self.files_path = QLineEdit(page)
        self.files_path.setPlaceholderText("~ 或 /home/me/project；按 Enter 跳转")
        self.files_path.setToolTip("实例内的 POSIX 路径；~ 表示家目录")
        self.files_path.returnPressed.connect(self._files_commit_path)
        path_row.addWidget(self.files_path, 1)
        self.files_refresh = button("刷新", kind="ghost")
        self.files_refresh.setToolTip("重新读取当前目录")
        self.files_refresh.clicked.connect(self._files_refresh_dir)
        path_row.addWidget(self.files_refresh)
        body.addLayout(path_row)

        self.files_status = ElidedLabel("选择实例后显示其家目录", parent=page)
        self.files_status.setObjectName("Hint")
        self.files_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        body.addWidget(self.files_status)

        self.files_table = make_table(
            ("名称", "类型", "大小", "修改时间"),
            widths=(320, 80, 110, 150),
            stretch_columns=(0,),
            parent=page,
        )
        self.files_table.setObjectName("FileTable")
        self.files_table.itemSelectionChanged.connect(self._sync_files_actions)
        self.files_table.itemDoubleClicked.connect(self._files_open_row)
        body.addWidget(self.files_table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.files_open_ide = button("用 IDE 打开", kind="ghost", icon="code")
        self.files_open_ide.setToolTip("在当前目录启动设置里选用的 IDE")
        self.files_open_ide.clicked.connect(lambda: self._files_open_with(_open.KIND_IDE))
        actions.addWidget(self.files_open_ide)
        self.files_open_agent = button("用 AI Agent 打开", kind="ghost", icon="bot")
        self.files_open_agent.setToolTip("在当前目录启动设置里选用的 AI Agent")
        self.files_open_agent.clicked.connect(lambda: self._files_open_with(_open.KIND_AGENT))
        actions.addWidget(self.files_open_agent)
        actions.addSpacing(8)
        self.files_upload = button("上传…", kind="primary", icon="upload")
        self.files_upload.setToolTip("把 Windows 上的文件复制到当前目录")
        self.files_upload.clicked.connect(self._files_upload)
        actions.addWidget(self.files_upload)
        self.files_download = button("下载…", kind="ghost")
        self.files_download.setToolTip("把选中的文件复制到 Windows 磁盘")
        self.files_download.clicked.connect(self._files_download)
        actions.addWidget(self.files_download)
        actions.addSpacing(8)
        self.files_mkdir = button("新建文件夹", kind="ghost")
        self.files_mkdir.setToolTip("在当前目录下新建文件夹")
        self.files_mkdir.clicked.connect(self._files_new_folder)
        actions.addWidget(self.files_mkdir)
        self.files_rename = button("重命名", kind="ghost")
        self.files_rename.setToolTip("重命名选中的文件或文件夹")
        self.files_rename.clicked.connect(self._files_rename)
        actions.addWidget(self.files_rename)
        actions.addStretch(1)
        self.files_delete = button("删除", kind="danger")
        self.files_delete.setToolTip("删除选中的文件或文件夹（会二次确认）")
        self.files_delete.clicked.connect(self._files_delete)
        actions.addWidget(self.files_delete)
        body.addLayout(actions)

        self._sync_files_actions()
        return page

    def _open_files_page(self) -> None:
        """文件页入口：同步实例下拉框；首次进入时读取家目录并列出内容。"""
        if self._files_loading or self._files_busy:
            return
        view = self.view
        names = [distro.name for distro in view.distros] if view is not None else []
        if not names:
            self._files_loaded = ""
            self._files_root = ""
            self._files_home = ""
            self._files_dir = ""
            self._files_entries = ()
            if self.files_distro.count():
                self.files_distro.blockSignals(True)
                self.files_distro.clear()
                self.files_distro.blockSignals(False)
            self.files_path.clear()
            self._render_files()
            self._set_files_status("暂无实例：先在「实例」页创建或导入后再浏览文件")
            self._files_loading = False
            self._sync_files_actions()
            return
        existing = [self.files_distro.itemText(index) for index in range(self.files_distro.count())]
        if existing != names:
            previous = self.files_distro.currentText()
            preferred = self._files_loaded if self._files_loaded in names else previous
            if preferred not in names:
                preferred = view.default if view is not None and view.default in names else names[0]
            self.files_distro.blockSignals(True)
            self.files_distro.clear()
            self.files_distro.addItems(names)
            self.files_distro.setCurrentIndex(max(self.files_distro.findText(preferred), 0))
            self.files_distro.blockSignals(False)
        name = self.files_distro.currentText()
        if name and (self._files_loaded != name or not self._files_dir):
            self._files_enter(name)

    def _on_files_distro_changed(self, _index: int) -> None:
        if self._files_busy or self._files_loading:
            return
        name = self.files_distro.currentText()
        if name:
            self._files_enter(name)

    def _files_enter(self, name: str) -> None:
        """切到某实例：清空旧目录状态并重新探测家目录。"""
        self._files_loaded = name
        self._files_root = ""
        self._files_home = ""
        self._files_user = ""
        self._files_dir = ""
        self._files_entries = ()
        self._render_files()
        self._files_load(name, None)

    def _files_load(self, name: str, path: str | None) -> None:
        """后台读取目录；``path=None`` 表示先探测家目录再列它。

        探测与列目录合并在同一个任务里，避免「先 wsl 取 HOME，再等 UNC 列目录」
        的两次跳转；用户快速切目录时用递增序号丢弃迟到的旧结果。
        """
        if not name or self._closing:
            return
        self._files_seq += 1
        seq = self._files_seq
        self._files_loading = True
        self._sync_files_actions()
        label = "家目录" if path is None else path
        self._set_files_status(f"正在读取 {name} 的{label}…")
        cached_root = self._files_roots.get(name, "")
        cached_home = self._files_home
        cached_user = self._files_user

        def work() -> tuple[str, str, str, str, tuple[_files.FileEntry, ...]]:
            root = cached_root or _files.pick_unc_root(name)
            user, home = cached_user, cached_home
            if not home:
                env = cli._build_environment()
                wsl_path = cli._require_wsl(env)
                result = env.runner.run(
                    [wsl_path, "-d", name, "--exec", "bash", "-lc", _files.home_probe_script()],
                    timeout=60.0,
                )
                text = decode_console(result.stdout + result.stderr)
                user, detected = _files.parse_home(text)
                if not detected:
                    tail = _initenv.tail_lines(text, 3)
                    raise runtime_error(
                        f"无法读取 {name} 的家目录（退出码 {result.returncode}）",
                        hint=tail[-1] if tail else f"确认实例可以启动：wsl -d {name} --exec true",
                    )
                home = detected
            target = _files.normalize_path(path if path is not None else home, home)
            entries = _files.list_entries(_files.to_local(root, target))
            return root, user, home, target, entries

        submit_daemon(
            work,
            on_done=lambda payload: self._files_loaded_ok(seq, name, payload),
            on_error=lambda message: self._files_loaded_error(seq, name, message),
            on_finished=lambda: self._files_load_finished(seq),
        )

    def _files_loaded_ok(
        self,
        seq: int,
        name: str,
        payload: tuple[str, str, str, str, tuple[_files.FileEntry, ...]],
    ) -> None:
        if self._closing or seq != self._files_seq:
            return
        root, user, home, target, entries = payload
        self._files_roots[name] = root
        self._files_root = root
        self._files_home = home
        self._files_user = user
        self._files_dir = target
        self._files_entries = entries
        self.files_path.setText(_files.display_path(target, home))
        self._render_files()
        who = f" · {user}" if user else ""
        self._set_files_status(f"{name}{who} · {_files.display_path(target, home)} · {len(entries)} 项")

    def _files_loaded_error(self, seq: int, name: str, message: str) -> None:
        if self._closing or seq != self._files_seq:
            return
        # 导航失败时保留上一级已列出的内容与路径栏，只把错误写进状态行；
        # 首次进入失败时本来就没有内容，表格保持为空。
        self._set_files_status(f"{name}：{message}")
        self._log(f"[文件] {message}")

    def _files_load_finished(self, seq: int) -> None:
        if self._closing or seq != self._files_seq:
            return
        self._files_loading = False
        self._sync_files_actions()

    def _render_files(self) -> None:
        """按 ``_files_entries`` 重绘表格；选中清空，避免操作落到旧行。"""
        self.files_table.setRowCount(0)
        self.files_table.setRowCount(len(self._files_entries))
        for row, entry in enumerate(self._files_entries):
            name_item = make_table_item(entry.name)
            name_item.setToolTip(entry.name)
            self.files_table.setItem(row, 0, name_item)
            self.files_table.setItem(row, 1, make_table_item(_files.type_text(entry)))
            size_item = make_table_item(_files.size_text(entry))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.files_table.setItem(row, 2, size_item)
            self.files_table.setItem(row, 3, make_table_item(_files.format_mtime(entry)))
        self.files_table.clearSelection()
        self._sync_files_actions()

    def _files_selected(self) -> _files.FileEntry | None:
        row = self.files_table.currentRow()
        if 0 <= row < len(self._files_entries):
            return self._files_entries[row]
        return None

    def _files_selected_local(self) -> tuple[Path, _files.FileEntry] | None:
        entry = self._files_selected()
        if entry is None or not self._files_root or not self._files_dir:
            return None
        target = _files.to_local(self._files_root, _files.join_path(self._files_dir, entry.name))
        return target, entry

    def _sync_files_actions(self) -> None:
        """按「是否加载完成 / 是否选中 / 是否任务中」同步按钮可用性。"""
        busy = self._files_busy or self._files_loading
        loaded = bool(self._files_dir)
        entry = self._files_selected()
        self.files_distro.setEnabled(not busy)
        self.files_path.setEnabled(not busy)
        self.files_upload.setEnabled(loaded and not busy)
        self.files_mkdir.setEnabled(loaded and not busy)
        self.files_refresh.setEnabled(loaded and not busy)
        self.files_up.setEnabled(loaded and self._files_dir != "/" and not busy)
        self.files_home.setEnabled(loaded and not busy)
        self.files_download.setEnabled(entry is not None and not busy)
        self.files_rename.setEnabled(entry is not None and not busy)
        self.files_delete.setEnabled(entry is not None and not busy)
        for kind, widget in (
            (_open.KIND_IDE, self.files_open_ide),
            (_open.KIND_AGENT, self.files_open_agent),
        ):
            opener = w.selected_opener(self.settings, kind)
            widget.setEnabled(opener is not None and loaded and not busy)
            widget.setToolTip(self._open_tooltip(kind, opener))

    def _open_tooltip(self, kind: str, opener: _open.Opener | None) -> str:
        label = _open.KIND_LABELS[kind]
        if opener is None:
            return f"还没有{label}打开方式：到「设置 → 打开方式」添加"
        where = _open.HOST_LABELS[opener.host]
        return f"用 {opener.name} 打开当前目录（{where}）\n命令模板：{opener.command}"

    def _files_open_with(self, kind: str) -> None:
        """按当前选中条目在正在浏览的目录打开 IDE / AI Agent。

        Windows 宿主机的 GUI 程序直接按 argv 启动；WSL 内的终端程序先确认命令
        存在，再通过终端 + ``--cd`` 进入当前目录。两条路径失败都弹窗说明原因，
        不静默失败。
        """
        if self._closing or self._files_busy or self._files_loading:
            return
        distro = self._files_loaded or self.files_distro.currentText()
        directory = self._files_dir
        opener = w.selected_opener(self.settings, kind)
        if opener is None:
            QMessageBox.information(
                self,
                f"尚未配置{_open.KIND_LABELS[kind]}打开方式",
                f"到「设置 → 打开方式」添加或选中一个{_open.KIND_LABELS[kind]}条目后再试。",
            )
            return
        if not distro or not directory:
            return
        root = self._files_root
        if not root:
            try:
                root = _files.pick_unc_root(distro)
            except WslmanError as exc:
                QMessageBox.critical(self, "打开失败", w._error_text(exc))
                return
            self._files_roots[distro] = root
        unc = str(_files.to_local(root, directory))
        home = self._files_home or "/"
        label = f"用 {_open.KIND_LABELS[kind]} 打开"
        if opener.host == _open.HOST_WINDOWS:
            self._files_open_windows(opener, label, directory=directory, unc=unc, distro=distro, home=home)
            return
        self._set_files_status(f"{label}：正在 {distro} 里启动 {opener.name}…")
        self._files_busy = True
        self._sync_files_actions()

        def work() -> str:
            env = cli._build_environment()
            wsl_path = cli._require_wsl(env)
            probe = env.runner.run(
                [
                    wsl_path,
                    "-d",
                    distro,
                    "--exec",
                    "bash",
                    "-lc",
                    _open.probe_script(opener, dir=directory, unc=unc, distro=distro, home=home),
                ],
                timeout=60.0,
            )
            found = decode_console(probe.stdout + probe.stderr).strip()
            exe = _open.command_name(opener, dir=directory, unc=unc, distro=distro, home=home)
            if probe.returncode != 0 or not found:
                raise runtime_error(
                    f"{distro} 里找不到 {exe}",
                    hint=f"先在实例里安装 {opener.name}，或在「设置 → 打开方式」把命令模板改成实际可用的命令",
                )
            command = build_command(
                wsl_path=wsl_path,
                distro=distro,
                cwd=directory,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
                run=(
                    "--exec",
                    "bash",
                    "-lc",
                    _open.wsl_command(opener, dir=directory, unc=unc, distro=distro, home=home),
                ),
            )
            self.launcher.launch(command)
            return f"已在 {distro}:{directory} 启动 {opener.name}"

        def runner() -> tuple[bool, str]:
            try:
                return True, work()
            except Exception as exc:  # 后台任务统一转成界面文案
                return False, w._error_text(exc)

        submit_daemon(
            runner,
            on_done=lambda payload: self._files_open_done(label, payload),
            on_finished=self._files_task_finished,
        )

    def _files_open_windows(  # noqa: PLR0913 - 目标信息按目录 / UNC / 实例 / 家目录逐项展开
        self,
        opener: _open.Opener,
        label: str,
        *,
        directory: str,
        unc: str,
        distro: str,
        home: str,
    ) -> None:
        """Windows 宿主机程序：先确认可执行文件在 PATH，再按 argv 无窗口启动。"""
        try:
            argv = _open.windows_argv(opener, dir=directory, unc=unc, distro=distro, home=home)
            exe = argv[0]
            if shutil.which(exe) is None and not Path(exe).is_file():
                raise runtime_error(
                    f"没有找到 {exe}",
                    hint=f"确认 {opener.name} 已安装并加入 PATH；或在「设置 → 打开方式」把命令模板改成完整路径",
                )
            spawn_detached(_open.wrap_windows_argv(argv))
        except (WslmanError, LaunchError, OSError) as exc:
            message = w._error_text(exc)
            self._set_files_status(f"{label}失败：{message}")
            QMessageBox.critical(self, label, message)
            return
        self._set_files_status(f"{label}：{opener.name} 正在打开 {distro}:{directory}")
        self._log(f"[文件] 用 {opener.name} 打开 {distro}:{directory}")

    def _files_open_done(self, label: str, payload: tuple[bool, str]) -> None:
        if self._closing:
            return
        ok, message = payload
        if not ok:
            self._set_files_status(f"{label}失败：{message}")
            QMessageBox.critical(self, label, message)
            return
        self._set_files_status(f"{label}完成：{message}")
        self._log(f"[文件] {message}")

    def _set_files_status(self, text: str) -> None:
        self.files_status.setText(text)
        self.files_status.setToolTip(text)

    def _files_commit_path(self) -> None:
        """路径栏回车：按输入跳转（支持 ``~`` 与相对家目录的路径）。"""
        if self._files_busy or self._files_loading or not self._files_loaded:
            return
        text = self.files_path.text().strip()
        if not text:
            return
        try:
            target = _files.normalize_path(text, self._files_home or "/")
        except WslmanError as exc:
            QMessageBox.warning(self, "路径不可用", w._error_text(exc))
            return
        self._files_load(self._files_loaded, target)

    def _files_go_up(self) -> None:
        if self._files_busy or self._files_loading or self._files_dir in {"", "/"}:
            return
        self._files_load(self._files_loaded, _files.parent_path(self._files_dir))

    def _files_go_home(self) -> None:
        if self._files_busy or self._files_loading or not self._files_home:
            return
        self._files_load(self._files_loaded, self._files_home)

    def _files_refresh_dir(self) -> None:
        if self._files_busy or self._files_loading or not self._files_loaded:
            return
        self._files_load(self._files_loaded, self._files_dir or None)

    def _files_open_row(self, item: Any) -> None:
        """双击文件夹进入下一级；双击文件不做动作（下载走底部按钮）。"""
        row = item.row()
        if not 0 <= row < len(self._files_entries):
            return
        entry = self._files_entries[row]
        if not entry.is_dir:
            return
        self._files_load(self._files_loaded, _files.join_path(self._files_dir, entry.name))

    def _files_run(self, label: str, work: Any) -> None:
        """在守护线程执行文件操作；失败统一转成可读文案并弹窗。"""
        if self._files_busy or self._files_loading or self._closing:
            return
        self._files_busy = True
        self._sync_files_actions()
        self._set_files_status(f"{label}…")

        def runner() -> tuple[bool, str]:
            try:
                return True, work()
            except Exception as exc:  # 后台任务统一转成界面文案
                return False, w._error_text(exc)

        submit_daemon(
            runner,
            on_done=lambda payload: self._files_task_done(label, payload),
            on_finished=self._files_task_finished,
        )

    def _files_task_done(self, label: str, payload: tuple[bool, str]) -> None:
        if self._closing:
            return
        ok, message = payload
        if not ok:
            self._set_files_status(f"{label}失败：{message}")
            QMessageBox.critical(self, label, message)
            return
        self._set_files_status(f"{label}完成：{message}")
        self._log(f"[文件] {message}")
        if self._files_loaded and self._files_dir:
            self._files_load(self._files_loaded, self._files_dir)

    def _files_task_finished(self) -> None:
        if self._closing:
            return
        self._files_busy = False
        self._sync_files_actions()

    def _files_new_folder(self) -> None:
        if self._files_busy or self._files_loading or not self._files_dir:
            return
        name, accepted = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not accepted:
            return
        try:
            clean = _files.check_name(name)
        except WslmanError as exc:
            QMessageBox.warning(self, "名称不可用", w._error_text(exc))
            return
        root, directory = self._files_root, self._files_dir
        home = self._files_home

        def work() -> str:
            target = _files.create_dir(_files.to_local(root, directory), clean)
            return f"已在 {_files.display_path(directory, home)} 新建 {target.name}"

        self._files_run("新建文件夹", work)

    def _files_rename(self) -> None:
        selected = self._files_selected_local()
        if selected is None:
            return
        path, entry = selected
        new_name, accepted = QInputDialog.getText(self, "重命名", f"{entry.name} 的新名称：", text=entry.name)
        if not accepted:
            return
        try:
            clean = _files.check_name(new_name)
        except WslmanError as exc:
            QMessageBox.warning(self, "名称不可用", w._error_text(exc))
            return
        if clean == entry.name:
            return

        def work() -> str:
            target = _files.rename_entry(path, clean)
            return f"{entry.name} → {target.name}"

        self._files_run("重命名", work)

    def _files_delete(self) -> None:
        selected = self._files_selected_local()
        if selected is None:
            return
        path, entry = selected
        kind = "文件夹" if entry.is_dir else "文件"
        suffix = "（含其中全部内容）" if entry.is_dir else ""
        if not confirm(self, "删除确认", f"删除{kind} {entry.name}{suffix}？此操作不可恢复。"):
            return
        root = self._files_root

        def work() -> str:
            _files.delete_entry(path, _files.to_local(root, "/"))
            return f"已删除{kind} {entry.name}"

        self._files_run("删除", work)

    def _files_upload(self) -> None:
        if self._files_busy or self._files_loading or not self._files_dir:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            f"上传到 {self.files_path.text() or self._files_dir}",
            "",
            "所有文件 (*.*)",
        )
        if not paths:
            return
        root, directory = self._files_root, self._files_dir
        home = self._files_home

        def work() -> str:
            copied = _files.upload_files([Path(path) for path in paths], _files.to_local(root, directory))
            return f"已上传 {len(copied)} 个文件到 {_files.display_path(directory, home)}"

        self._files_run("上传", work)

    def _files_download(self) -> None:
        selected = self._files_selected_local()
        if selected is None:
            return
        path, entry = selected
        if entry.is_dir:
            QMessageBox.information(
                self,
                "暂不支持下载文件夹",
                "文件夹下载暂不支持；请进入文件夹后选择具体文件。",
            )
            return
        target, _ = QFileDialog.getSaveFileName(self, f"下载 {entry.name}", str(Path.home() / entry.name))

        if not target:
            return

        def work() -> str:
            saved = _files.download_file(path, Path(target), overwrite=True)
            return f"已下载到 {saved}"

        self._files_run("下载", work)

    def _build_images_page(self) -> QWidget:
        """镜像页：筛选条（含手动更新）+ 搜索行 + 单行状态 + 行卡列表 + 下载动作。

        布局与 llmw 模型页同构：固定行都是单行控件（不换行、不抢高度），列表吃满
        剩余空间并自带滚动，底部动作行只放下载相关控件。这样窗口压到最小高度时
        也不会出现「标签被压扁、底部动作行与列表重叠」。
        """
        page = PageBody(self, spacing=8)
        body = page.body

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
        self.image_install_local = button("安装镜像…", kind="ghost")
        self.image_install_local.setToolTip(f"安装磁盘上已有的镜像包；{w._DOWNLOAD_HINT}")
        self.image_install_local.clicked.connect(self._install_local)
        filters.addWidget(self.image_install_local)
        self._buttons.append(self.image_install_local)
        self._button_by_text["安装镜像…"] = self.image_install_local
        self.image_manage = button("管理镜像…", kind="ghost")
        self.image_manage.setToolTip("管理下载目录里的镜像包：导出、删除、清理缓存与未完成下载")
        self.image_manage.clicked.connect(self._open_image_manager)
        filters.addWidget(self.image_manage)
        self._buttons.append(self.image_manage)
        self._button_by_text["管理镜像…"] = self.image_manage
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
        self.bind_row_detail("images", self.image_list, self.image_detail_view)
        body.addWidget(self.image_list, 1)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._render_images)
        self.image_filter_buttons[self._image_filter_key].setChecked(True)

        return page

    def _build_settings_page(self) -> QWidget:
        """设置页：工作目录 + 下载源 / 校验 + 打开方式。

        工作目录排在最上方（用户最常改的一项）；下载 / 备份 / 安装目录不再单独配置，
        也不在界面上逐项展示，统一按工作目录推演。页面用 :class:`PageScroll` 包裹，
        窗口变矮时滚动而不是压扁控件。
        """
        page = PageBody(self, spacing=0)
        outer = page.body
        scroll = PageScroll(page)
        outer.addWidget(scroll)
        layout = scroll.body

        layout.addWidget(self._build_work_card())

        download = Card("下载")
        form = FormGrid(download)
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

        # 并发下载上限：默认 2 个（保守值），超出上限的任务在队列里排队、可单独取消。
        self.download_limit_combo = QComboBox(download)
        for value in range(w.MIN_DOWNLOADS, w.MAX_DOWNLOADS + 1):
            self.download_limit_combo.addItem(f"{value} 个", value)
        self.download_limit_combo.setCurrentIndex(
            max(0, w.download_limit(self.settings.max_downloads) - w.MIN_DOWNLOADS)
        )
        self.download_limit_combo.setToolTip("同时进行的镜像下载数上限；其余任务排队等待，也能单独取消")
        self.download_limit_combo.currentIndexChanged.connect(self._on_download_limit_changed)

        # 同一行两对：下载源 + 并发上限；校验单独一行（摘要较长，需要整行宽度）。
        form.add_pair("下载源", self.mirror, "并发下载", self.download_limit_combo)
        form.add_pair("校验 sha256", self.expected_sha)
        download.body.addWidget(form)
        download.body.addWidget(
            hint_label(f"镜像归档统一存到工作目录的 download 子目录（见上方「工作目录」卡片）。{w._DOWNLOAD_HINT}")
        )
        layout.addWidget(download)

        layout.addWidget(self._build_openers_card())
        layout.addStretch(1)
        return page

    def _build_openers_card(self) -> Card:
        """「打开方式」卡片：IDE / AI Agent 两类的当前条目与增删改。

        条目在设置页维护，文件页只按当前选中的条目启动；选择与自定义模板都写进
        ``wslmanw.json``，下次启动直接沿用。右上「智能检测」扫描宿主机与当前实例，
        只补全没被用户手改过的条目。
        """
        card = Card("打开方式")
        self.opener_detect_button = button("智能检测", kind="ghost", icon="search")
        self.opener_detect_button.setToolTip(
            "在 Windows 宿主机（PATH 与常见安装目录）和当前 WSL 实例内（command -v）"
            "查找已安装的 IDE / AI Agent，补全没被手动改过的命令；手动改过的条目不会被覆盖。"
        )
        self.opener_detect_button.clicked.connect(lambda: self._detect_openers(force=True))
        card.add_title_action(self.opener_detect_button)
        form = FormGrid(card)
        self.opener_combos: dict[str, QComboBox] = {}
        self.opener_tags: dict[str, StatusTag] = {}
        self.opener_edit_buttons: dict[str, QPushButton] = {}
        self.opener_delete_buttons: dict[str, QPushButton] = {}
        for kind in _open.KINDS:
            row = QWidget(card)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            combo = QComboBox(row)
            combo.setMinimumWidth(220)
            combo.setToolTip(f"文件页「用 {_open.KIND_LABELS[kind]} 打开」使用的条目")
            combo.currentIndexChanged.connect(lambda _index, item=kind: self._on_opener_selected(item))
            row_layout.addWidget(combo, 1)
            tag = StatusTag("", tone="muted", parent=row)
            tag.setVisible(False)
            row_layout.addWidget(tag)
            add = button("添加…", kind="ghost")
            add.setToolTip(f"新增自定义 {_open.KIND_LABELS[kind]}打开方式")
            add.clicked.connect(lambda _checked=False, item=kind: self._opener_add(item))
            row_layout.addWidget(add)
            edit = button("编辑…", kind="ghost")
            edit.setToolTip("修改选中条目的名称、运行位置或命令模板")
            edit.clicked.connect(lambda _checked=False, item=kind: self._opener_edit(item))
            row_layout.addWidget(edit)
            remove = button("删除", kind="danger")
            remove.setToolTip("删除选中的条目（内置项也可以删除）")
            remove.clicked.connect(lambda _checked=False, item=kind: self._opener_delete(item))
            row_layout.addWidget(remove)
            form.add_row(_open.KIND_LABELS[kind], row)
            self.opener_combos[kind] = combo
            self.opener_tags[kind] = tag
            self.opener_edit_buttons[kind] = edit
            self.opener_delete_buttons[kind] = remove
            self._reload_opener_combo(kind)
        card.body.addWidget(form)
        card.body.addWidget(
            hint_label(
                "模板占位符：{dir} 实例内绝对路径、{unc} Windows UNC 路径、{distro} 实例名、{home} 实例家目录。"
                "Windows 宿主机条目需要 {dir} 或 {unc}；WSL 内条目以当前目录启动，可不写占位符。"
            )
        )
        self.opener_detect_hint = hint_label("智能检测会检查宿主机与本页当前实例；切换实例后自动重检。")
        card.body.addWidget(self.opener_detect_hint)
        self._sync_opener_actions()
        return card

    def _reload_opener_combo(self, kind: str) -> None:
        combo = self.opener_combos[kind]
        current = w.selected_opener(self.settings, kind)
        combo.blockSignals(True)
        combo.clear()
        for opener in w.openers_for(self.settings, kind):
            combo.addItem(self._opener_label(opener), opener.key)
            combo.setItemData(combo.count() - 1, opener.command, Qt.ItemDataRole.ToolTipRole)
        if current is not None:
            combo.setCurrentIndex(max(combo.findData(current.key), 0))
        combo.blockSignals(False)
        tag = self.opener_tags[kind]
        tag.setVisible(current is not None)
        if current is not None:
            label, tone, detail = self._opener_install_state(current)
            tag.set_status(label, tone)
            tag.setToolTip(f"{current.name} 的命令模板：{current.command}\n{detail}")

    def _opener_install_state(self, opener: _open.Opener) -> tuple[str, str, str]:
        """条目在当前环境（宿主机 / WSL 内）的安装状态：文案、色调与 tooltip。"""
        builtin = any(item.key == opener.key for item in _open.default_openers(opener.kind))
        if not builtin:
            return "自定义", "muted", "自定义条目按命令模板启动；智能检测只补全内置预设。"
        data = self._opener_detections.get(self._detect_instance_name())
        if data is None:
            if self._detecting:
                return "检测中", "warning", "正在检查宿主机与当前实例…"
            return (
                _open.HOST_LABELS[opener.host],
                "accent" if opener.host == _open.HOST_WINDOWS else "warning",
                "点右上「智能检测」查看宿主机 / WSL 内是否已安装。",
            )
        host_map, wsl_map = data
        if opener.host == _open.HOST_WINDOWS:
            here, other, other_label = (
                host_map.get(opener.key),
                wsl_map.get(opener.key),
                "WSL 内",
            )
        else:
            here, other, other_label = (
                wsl_map.get(opener.key),
                host_map.get(opener.key),
                "宿主机",
            )
        if here is not None and here.found:
            return f"{_open.HOST_LABELS[opener.host]}已安装", "success", f"路径：{here.path}"
        if other is not None and other.found:
            return (
                f"仅{other_label}已安装",
                "warning",
                f"{other_label}路径：{other.path}；当前条目的运行位置是 {_open.HOST_LABELS[opener.host]}，"
                "可编辑条目切换运行位置。",
            )
        return "未安装", "muted", "宿主机与 WSL 内均未找到（可能未安装，或安装在非标准位置）。"

    def _opener_label(self, opener: _open.Opener) -> str:
        builtin = any(item.key == opener.key for item in _open.default_openers(opener.kind))
        return f"{opener.name}（内置）" if builtin else opener.name

    def _sync_opener_actions(self) -> None:
        for kind in _open.KINDS:
            has = bool(w.openers_for(self.settings, kind))
            self.opener_edit_buttons[kind].setEnabled(has)
            self.opener_delete_buttons[kind].setEnabled(has)
            self.opener_combos[kind].setEnabled(has)
            self.opener_combos[kind].setToolTip(
                "还没有条目：点「添加…」创建" if not has else f"文件页「用 {_open.KIND_LABELS[kind]} 打开」使用的条目"
            )

    def _open_settings_page(self) -> None:
        """设置页入口：按当前实例做一次智能检测（已有缓存直接复用）。"""
        self._settings_seen = True
        self._sync_opener_detection()

    def _detect_instance_name(self) -> str:
        """智能检测使用的实例：优先当前选中行，其次列表第一项。"""
        distro = self._selected()
        if distro is not None:
            return distro.name
        return self._rows[0][0] if self._rows else ""

    def _sync_opener_detection(self) -> None:
        """实例变化后刷新标签；没有该实例的检测缓存就重新检测。"""
        if not self._settings_seen or not hasattr(self, "opener_detect_button"):
            return
        name = self._detect_instance_name()
        if name and name not in self._opener_detections:
            self._detect_openers()
            return
        for kind in _open.KINDS:
            self._reload_opener_combo(kind)

    def _detect_openers(self, *, force: bool = False) -> None:
        """检测宿主机与当前实例内的 IDE / AI Agent；结果按实例缓存。"""
        if self._detecting or not hasattr(self, "opener_detect_button"):
            return
        name = self._detect_instance_name()
        cached = self._opener_detections.get(name)
        if cached is not None and not force:
            self._apply_detections(cached, log=False)
            return
        self._detecting = True
        self._detect_token += 1
        token = self._detect_token
        self.opener_detect_button.setEnabled(False)
        set_button_text(self.opener_detect_button, "检测中…")
        self.opener_detect_hint.setText(f"正在检查宿主机与 {name or '当前环境'}…")
        for kind in _open.KINDS:
            self._reload_opener_combo(kind)
        submit(
            lambda: self._detect_worker(name),
            on_done=lambda result: self._detect_done(token, name, result),
            on_error=self._detect_error,
            on_finished=self._detect_finished,
        )

    def _detect_worker(self, name: str) -> tuple[dict[str, _open.Detection], dict[str, _open.Detection], str]:
        """后台探测：宿主机 PATH / 常见安装目录 + 当前实例 ``command -v``。"""
        host: dict[str, _open.Detection] = {}
        for kind in _open.KINDS:
            for opener in _open.default_openers(kind):
                host.setdefault(opener.key, _open.detect_host(opener.key))
        wsl: dict[str, _open.Detection] = {}
        warning = ""
        if name:
            try:
                env = cli._build_environment()
                wsl_exe = cli._require_wsl(env)
                result = env.runner.run(
                    [wsl_exe, "-d", name, "--exec", "bash", "-lc", _open.detect_script()],
                    timeout=120.0,
                )
            except WslmanError as exc:
                warning = str(exc)
            else:
                found = _open.parse_detect(decode_console(result.stdout)) if result.returncode == 0 else {}
                if result.returncode != 0 and not found:
                    warning = f"实例 {name} 内检测失败（退出码 {result.returncode}）"
                for kind in _open.KINDS:
                    for opener in _open.default_openers(kind):
                        wsl.setdefault(opener.key, _open.detect_wsl(opener.key, found))
        return host, wsl, warning

    def _detect_done(
        self,
        token: int,
        name: str,
        result: tuple[dict[str, _open.Detection], dict[str, _open.Detection], str],
    ) -> None:
        if self._closing or token != self._detect_token:
            return
        host, wsl, warning = result
        self._opener_detections[name] = (host, wsl)
        self._apply_detections((host, wsl), log=True)
        installed: list[str] = []
        missing: list[str] = []
        for kind in _open.KINDS:
            for opener in _open.default_openers(kind):
                here = (host if opener.host == _open.HOST_WINDOWS else wsl).get(opener.key)
                (installed if here is not None and here.found else missing).append(opener.name)
        summary = (
            f"检测完成（宿主机 + {name or '未选择实例'}）："
            f"{('、'.join(installed)) or '无'} 可用；{('、'.join(missing)) or '无'} 未找到。"
        )
        if warning:
            summary += f" WSL 内检测未完成：{warning}（标签仅反映宿主机结果）"
        self.opener_detect_hint.setText(summary)
        self._log(f"[打开方式] {summary}")

    def _detect_error(self, message: str) -> None:
        if self._closing:
            return
        self.opener_detect_hint.setText(f"智能检测失败：{message}")
        self._log(f"[打开方式] 智能检测失败：{message}")

    def _detect_finished(self) -> None:
        self._detecting = False
        if not hasattr(self, "opener_detect_button"):
            return
        set_button_text(self.opener_detect_button, "智能检测")
        self.opener_detect_button.setEnabled(True)
        for kind in _open.KINDS:
            self._reload_opener_combo(kind)

    def _apply_detections(
        self,
        data: tuple[dict[str, _open.Detection], dict[str, _open.Detection]],
        *,
        log: bool,
    ) -> None:
        """把检测到的绝对路径补进「没被用户手改过」的内置条目。"""
        host, wsl = data
        for kind in _open.KINDS:
            openers = w.openers_for(self.settings, kind)
            current = w.selected_opener(self.settings, kind)
            changed = False
            updated: list[_open.Opener] = []
            for opener in openers:
                if not opener.builtin or opener.user_edited:
                    updated.append(opener)
                    continue
                found = (host if opener.host == _open.HOST_WINDOWS else wsl).get(opener.key)
                if found is None or not found.found or not found.command or found.command == opener.command:
                    updated.append(opener)
                    continue
                updated.append(
                    _open.Opener(
                        opener.key,
                        opener.name,
                        opener.kind,
                        opener.host,
                        found.command,
                        True,
                        opener.note,
                        _open.ORIGIN_DETECTED,
                    )
                )
                changed = True
                if log:
                    self._log(f"[打开方式] {opener.name}：补全为 {found.host_label} 检测到的路径")
            if changed:
                self._store_openers(kind, tuple(updated), current.key if current is not None else "")
        for kind in _open.KINDS:
            self._reload_opener_combo(kind)

    def _store_openers(self, kind: str, openers: Sequence[_open.Opener], selected: str) -> None:
        """写回内存设置并立即落盘，避免关窗前异常导致选择丢失。"""
        self.settings = w.with_openers(self.settings, kind, openers, selected)
        self._reload_opener_combo(kind)
        self._sync_opener_actions()
        self._sync_files_actions()
        try:
            w.save_settings(self.settings, self._own_settings_path)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"打开方式已生效，但写入设置文件失败：\n{exc}")

    def _on_opener_selected(self, kind: str) -> None:
        key = self.opener_combos[kind].currentData() or ""
        self._store_openers(kind, w.openers_for(self.settings, kind), str(key))

    def _opener_add(self, kind: str) -> None:
        dialog = _OpenerDialog(self, kind)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, host, command = dialog.values()
        openers = w.openers_for(self.settings, kind)
        key = _open.new_key(name, [item.key for item in openers])
        opener = _open.Opener(key, name, kind, host, command, origin=_open.ORIGIN_USER)
        self._store_openers(kind, (*openers, opener), key)

    def _opener_edit(self, kind: str) -> None:
        current = w.selected_opener(self.settings, kind)
        if current is None:
            return
        dialog = _OpenerDialog(self, kind, current)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, host, command = dialog.values()
        openers = tuple(
            # 用户改过的条目记成 user：之后的智能检测不会再覆盖这条命令。
            _open.Opener(item.key, name, kind, host, command, item.builtin, item.note, _open.ORIGIN_USER)
            if item.key == current.key
            else item
            for item in w.openers_for(self.settings, kind)
        )
        self._store_openers(kind, openers, current.key)

    def _opener_delete(self, kind: str) -> None:
        current = w.selected_opener(self.settings, kind)
        if current is None:
            return
        if not confirm(
            self,
            f"删除{_open.KIND_LABELS[kind]}打开方式",
            f"删除「{current.name}」？\n\n命令模板：{current.command}",
        ):
            return
        openers = tuple(item for item in w.openers_for(self.settings, kind) if item.key != current.key)
        self._store_openers(kind, openers, "")

    def _build_work_card(self) -> Card:
        """「工作目录」卡片：llmw 同款多磁盘卡，可添加 / 移除多个目录并指定默认。

        默认目录排在第一张卡上：新下载、新安装落到它的 ``download`` / ``install``
        子目录；备份仍然按默认目录推演。外层与其他设置项一样套设置卡（标题 + 边框），
        里面的磁盘卡、卡上的「更改 / 打开」与多目录操作保持原样。
        """
        card = Card("工作目录")
        grid = DiskGrid(card, add_text="添加目录", max_columns=2, min_count=1)
        grid.addRequested.connect(self._add_work_dir)
        grid.removeRequested.connect(self._remove_work_dir)
        grid.primaryRequested.connect(self._set_default_work_dir)
        grid.openRequested.connect(self._open_work_root)
        self.work_grid = grid
        card.body.addWidget(grid)
        self._refresh_work_card()
        return card

    def _refresh_work_card(self) -> None:
        """按当前设置重画磁盘卡：默认目录在第一张，其余按设置顺序排。"""
        roots = list(w.work_dir_paths(self.settings))
        self.work_grid.set_locations(default_disk_locations(roots, primary_label="默认工作目录"))

    def _store_work_dirs(self, dirs: Sequence[str | Path], default: str | Path, message: str) -> None:
        """写回多目录与默认项，立即落盘；实例行上的归属目录同步刷新。

        重操作（刷新 / 迁移 / 下载等）进行中不重建列表：``self.view`` 可能正被
        工作线程改写，此时只记「列表待刷新」，任务收尾后自动重算；「更新列表」也可
        随时手动重读注册信息。
        """
        self.settings = w.with_work_dirs(self.settings, dirs, default)
        self._refresh_work_card()
        if hasattr(self, "distro_list"):
            if self._busy:
                self._list_pending = True
                self.set_page_status("distros", "工作目录已更新；任务进行中，列表待刷新")
            else:
                self._list_pending = False
                self._refresh_rows()
        try:
            w.save_settings(self.settings, self._own_settings_path)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"工作目录已生效，但写入设置文件失败：\n{exc}")
            return
        self.set_page_status("settings", message)
        self._log(f"[设置] {message}")

    def _add_work_dir(self) -> None:
        """添加一个工作目录（重复目录不重复添加）。"""
        roots = list(w.work_dir_paths(self.settings))
        chosen = QFileDialog.getExistingDirectory(self, "添加工作目录", str(roots[0] if roots else Path.home()))
        if not chosen:
            return
        if any(w.same_dir(chosen, item) for item in roots):
            self.set_page_status("settings", f"目录已在列表中：{chosen}")
            return
        self._store_work_dirs([*roots, chosen], roots[0], f"已添加工作目录：{chosen}")

    def _remove_work_dir(self, path: str) -> None:
        """从列表移除工作目录（至少保留一个，不删磁盘文件）。"""
        roots = list(w.work_dir_paths(self.settings))
        if len(roots) <= 1:
            self.set_page_status("settings", "至少要保留一个工作目录")
            return
        target = Path(path)
        if not confirm(
            self,
            "移除工作目录",
            f"从列表移除以下目录？\n{target}\n\n只从设置里移除，磁盘文件不会被删除。",
        ):
            return
        remaining = [item for item in roots if not w.same_dir(item, target)]
        if len(remaining) == len(roots):
            self.set_page_status("settings", f"目录不在列表中：{target}")
            return
        self._store_work_dirs(remaining, remaining[0], f"已移除工作目录：{target}")

    def _set_default_work_dir(self, path: str) -> None:
        """把目录设为默认：新下载 / 新安装落到它的子目录。"""
        roots = list(w.work_dir_paths(self.settings))
        target = Path(path)
        chosen = next((item for item in roots if w.same_dir(item, target)), None)
        if chosen is None:
            return
        rest = [item for item in roots if not w.same_dir(item, chosen)]
        self._store_work_dirs(
            [chosen, *rest],
            chosen,
            f"默认工作目录已切换为：{chosen}；新下载 / 新安装落到这里",
        )

    def _open_work_root(self, root: str | None = None) -> None:
        """打开工作目录（不存在时先创建）。"""
        target = Path(root) if root else w.work_dir_path(self.settings)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法创建目录：{target}\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

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
        self._bridge = EventBridge(self)
        self._bridge.event.connect(self._on_log_event)
        self._set_task_status(f"{label}…")
        self._set_buttons(False)
        # 任务进行中在状态栏右侧显示加载条；可预估总量的导出会切成确定进度。
        self._export_meter.reset()
        self.show_loading(f"{label}…")

        def runner(cancel: threading.Event) -> tuple[str, str]:
            try:
                return "ok", work(cancel)
            except KeyboardInterrupt:
                # 取消不是失败：单独的终态，避免弹「下载失败」并让行内进度残留。
                return "cancelled", "已取消；未完成的 .part 保留在下载目录，可续传"
            except Exception as exc:  # 统一把失败转成可读文案
                return "failed", w._error_text(exc)

        submit(
            runner,
            self._cancel,
            on_done=lambda payload: self._task_done(label, payload),
            on_error=lambda message: self._task_error(label, message),
            on_finished=self._task_finished,
        )

    def _task_done(self, label: str, payload: tuple[str, str]) -> None:
        status, message = payload
        if status == "cancelled":
            self._log(f"[{label}] {message}")
            self._set_task_status(f"{label}已取消")
            self._refresh_image_states()
            return
        if status != "ok":
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
        # 下载 / 安装 / 删除实例后重扫本地状态：行内标签与动作按钮（已下载→安装、
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
        self._export_meter.reset()
        self._set_buttons(True)
        self._update_download_status()
        if self._closing:
            return
        self._update_action_states()
        if self._list_pending:
            # 工作目录在任务进行中改过：当时没重建列表，收尾后按新配置重算归属。
            self._list_pending = False
            if hasattr(self, "distro_list"):
                self._refresh_rows()
        if self._refresh_pending:
            # 任务收尾前发起的列表刷新（``_task_done`` 里的 ``refresh()``）会被忙态挡下。
            # 这里改走轻量状态刷新而不是再起一个「刷新」任务：窗口保持空闲，只重读
            # ``wsl -l -v`` 并就地更新行卡，默认胶囊在任务结束后立刻换行。
            self._refresh_pending = False
            QTimer.singleShot(0, self._request_status)

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
            # 镜像行的按钮由下载队列决定：有任务的保持「取消」，其余按重操作忙闲禁用；
            # 不能把全局忙态当成「正在下载」，否则安装 / 迁移时整列都变成取消。
            self._refresh_download_rows()
        if enabled:
            self._update_action_states()

    def _set_task_status(self, text: str) -> None:
        """把任务状态写到发起任务的那一页（没有记录时退回当前页）。"""
        self.set_page_status(self._task_page or self.current_page(), text)

    def _on_log_event(self, message: Any) -> None:
        """共享事件桥 → 任务日志（桥只对 ``str`` 载荷生效）。"""
        if isinstance(message, str):
            self._log(message)

    def _on_download_event(self, payload: Any) -> None:
        """下载线程的 ``(镜像名, 累计字节, 总量 / None)`` → 该行进度与队列状态。"""
        if not (isinstance(payload, tuple) and len(payload) == 3):
            return
        name, done, total = payload
        task = self._downloads.get(name)
        if task is None:
            return
        task.done = done if isinstance(done, int) else 0
        task.total = total if isinstance(total, int) and total > 0 else None
        meter = self._download_meters.get(name)
        if meter is not None:
            meter.update(task.done)
        row = self._image_row(name)
        if row is not None:
            row.set_metrics(self._download_metrics(task))
        self._update_download_status()

    def _download_metrics(self, task: _DownloadTask) -> str:
        if task.state == "queued":
            return "排队中"
        meter = self._download_meters.get(task.entry.name)
        speed = meter.speed if meter is not None else 0.0
        return w.download_metrics_text(task.done, task.total, speed)

    def _update_download_status(self) -> None:
        """镜像页状态行：并发数 / 上限 / 排队数；重操作占用状态行时不抢。"""
        if self._busy:
            return
        running = sum(1 for task in self._downloads.values() if task.state == "running")
        queued = sum(1 for task in self._downloads.values() if task.state == "queued")
        text = w.download_queue_text(running, queued, w.download_limit(self.settings.max_downloads))
        if text:
            self.set_page_status("images", text)

    def _image_row(self, name: str) -> _ImageItemRow | None:
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _ImageItemRow) and row.entry.name == name:
                return row
        return None

    def _refresh_download_rows(self) -> None:
        """按下载队列重算每一行：有任务的显示「取消」，其余按重操作忙态禁用。"""
        if not hasattr(self, "image_list"):
            return
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if not isinstance(row, _ImageItemRow):
                continue
            task = self._downloads.get(row.entry.name)
            row.set_download_state(
                busy=task is not None,
                active=task is not None,
                queued=task is not None and task.state == "queued",
                enabled=not self._busy,
            )
            if task is not None:
                row.set_metrics(self._download_metrics(task))

    def _on_export_event(self, payload: Any) -> None:
        """共享事件桥的 ``(发行版, 累计字节, 总量 / None)`` → 导出进度。"""
        name, done, total = payload
        self._on_export_progress(name, done, total)

    def _on_export_progress(self, name: str, done: int, total: object) -> None:
        """导出进度：左侧状态栏写「百分比 · 已写 / 估算 · 速度 · 剩余」，右侧进度条同步。

        总量是发行版占用的估算值（稀疏 vhdx 取实际占用），可能不完全准确；任务结束
        后由 ``_task_done`` 把进度条收起并改写「导出完成」。
        """
        if not self._busy:
            return
        total_bytes = total if isinstance(total, int) and total > 0 else None
        self._export_meter.update(done)
        speed = self._export_meter.speed
        self.set_page_status(self._task_page or "distros", w.export_progress_text(name, done, total_bytes, speed))
        if total_bytes:
            brief = f"导出 {min(100, int(done * 100 / total_bytes))}% · {format_speed(speed)}"
        else:
            brief = f"导出 {format_bytes(done)} · {format_speed(speed)}"
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
        # 文件页可见时同步实例下拉框：首次进入通常列表还没回来，名单到位后补一次。
        if self.current_page() == "files":
            self._open_files_page()
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
        """重新读取实例注册信息，并按当前工作目录配置重算每个实例的归属目录。"""
        if self._busy:
            self._refresh_pending = True
            return
        self._list_pending = False
        self._start("刷新", self._load)

    def _on_reload_distros(self) -> None:
        """「更新列表」：手动重读注册信息；重操作进行中只提示待刷新。"""
        if self._busy:
            self._refresh_pending = True
            self.set_page_status("distros", "已有任务在执行；结束后自动刷新，也可稍后点「更新列表」")
            return
        self.refresh()

    def _load(self, _cancel: threading.Event) -> str:
        env = cli._build_environment()
        view = cli._view(env)
        self.view = view
        sizes: dict[str, tuple[int | None, bool]] = {}
        for distro in view.distros:
            sizes[distro.name] = distro_disk_bytes(distro.base_path, distro.version)
        self.sizes = sizes
        return f"已加载 {len(view.distros)} 个实例"

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
        expanded = self.expanded_row("distros")
        expanded_name = expanded.distro.name if isinstance(expanded, _DistroItemRow) else ""
        self.detach_row_detail("distros")
        self.distro_list.clear()
        distros = tuple(view.distros) if view is not None else ()
        for distro in distros:
            size, estimated = self.sizes.get(distro.name, (None, False))
            item = QListWidgetItem(self.distro_list)
            row = _DistroItemRow(
                distro, size, estimated, w.work_dir_of(self.settings, distro.base_path), self.distro_list
            )
            row.toggleRequested.connect(self._on_distro_toggle)
            row.startStopRequested.connect(self._toggle_distro_state)
            row.configRequested.connect(self._configure_distro)
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
        if expanded_name:
            for index in range(self.distro_list.count()):
                row = self.distro_list.itemWidget(self.distro_list.item(index))
                if isinstance(row, _DistroItemRow) and row.distro.name == expanded_name:
                    self.expand_row("distros", row)
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
        """选中行变化：只重抛光旧 / 新两行，再更新工具栏与状态栏摘要。"""
        self.distro_list.sync_selection(current)
        self._show_details()
        # 智能检测按实例缓存：切到另一个实例时刷新安装状态标签。
        self._sync_opener_detection()

    def _on_distro_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.distro_list.itemWidget(item)
        if isinstance(row, _DistroItemRow):
            self._on_distro_toggle(row)

    def _on_distro_toggle(self, row: _DistroItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行（与镜像行一致）。"""
        self.toggle_row("distros", row)

    def render_row_detail(self, row: ItemRow) -> None:
        """展开时刷新详情内容（实例与镜像各自一套标题 / 指标）。"""
        if isinstance(row, _DistroItemRow):
            size, estimated = self.sizes.get(row.distro.name, (None, False))
            self.distro_detail_view.render(row.distro, size, estimated)
        elif isinstance(row, _ImageItemRow):
            self.image_detail_view.render(row.entry)

    def _show_details(self) -> None:
        """选中行变化时只更新状态栏摘要与工具栏启停按钮（详情在行内）。

        摘要属于**实例页**：3 秒状态轮询在任意页都会跑到这里，必须写页内固定 key，
        否则用户在镜像 / 设置页时会看到「Ubuntu · 已停止」这类实例摘要混进
        当前页的状态栏。
        """
        distro = self._selected()
        if distro is None:
            self.set_page_status_detail("distros", "")
            self._update_action_states()
            return
        size, estimated = self.sizes.get(distro.name, (None, False))
        self.set_page_status_detail("distros", w.summary_text(distro, size, estimated))
        self._update_action_states()

    def _update_action_states(self) -> None:
        if self._busy:
            return
        distro = self._selected()
        can_start, can_stop = w.action_states(distro.state if distro is not None else None)
        self.start_button.setEnabled(can_start)
        # 重启只对运行中的实例有意义：停止 / 启动已能覆盖其余状态。
        self.restart_button.setEnabled(can_stop)
        self.stop_button.setEnabled(can_stop)

    def _toggle_distro_state(self, name: str) -> None:
        """行内「启动 / 停止」：聚焦该行后复用工具栏动作（口径完全一致）。"""
        row = self._row_for_distro(name)
        if row is None:
            return
        self.focus_row("distros", row)
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

    def _other_instance_names(self, name: str) -> tuple[str, ...]:
        """除当前实例外的已列出实例：作为 SSH 迁移的候选来源。"""
        key = name.casefold()
        names: list[str] = []
        for index in range(self.distro_list.count()):
            row = self.distro_list.itemWidget(self.distro_list.item(index))
            if isinstance(row, _DistroItemRow) and row.distro.name.casefold() != key:
                names.append(row.distro.name)
        return tuple(names)

    def _configure_distro(self, name: str) -> None:
        """行内「配置」：保存终端默认目录，按需把实例设为默认发行版。"""
        row = self._row_for_distro(name)
        if row is None or self._busy:
            return
        self.focus_row("distros", row)
        dialog = _DistroConfigDialog(
            self,
            row.distro,
            w.terminal_cwd(self.settings, row.distro.name),
            selected=w.init_task_keys(self.settings, row.distro.name),
            instances=self._other_instance_names(row.distro.name),
        )
        self._restore_dialog_geometry("distro_config", dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._save_dialog_geometry("distro_config", dialog)
            return
        self._save_dialog_geometry("distro_config", dialog)
        self._save_init_tasks(row.distro.name, dialog.init_env.selected_keys())
        make_default, cwd = dialog.payload()
        self._save_terminal_cwd(row.distro.name, cwd)
        if not make_default:
            self.set_page_status("distros", f"已保存 {row.distro.name} 的配置")
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, row.distro.name)
            cli._run_step(env, ["--set-default", target.name], action=f"设为默认 {target.name}")
            return f"已把 {target.name} 设为默认发行版"

        self._start("设为默认", work)

    def _save_terminal_cwd(self, name: str, cwd: str) -> None:
        """把实例的终端默认目录写进窗口设置（``~`` 等同于未配置）。"""
        self.settings = w.with_terminal_cwd(self.settings, name, cwd)
        self._persist_settings(self.geometry_text())

    def _save_init_tasks(self, name: str, keys: Sequence[str]) -> None:
        """保存实例的初始化勾选，下次打开配置窗口沿用。"""
        self.settings = w.with_init_task_keys(self.settings, name, keys)

    def _restore_dialog_geometry(self, key: str, dialog: QDialog) -> None:
        """对话框打开前恢复上次的位置与尺寸；未记录时保持按内容居中。"""
        saved = w.dialog_geometry(self.settings, key)
        if not saved:
            return
        restore_geometry(
            dialog,
            saved,
            default_size=(dialog.width(), dialog.height()),
            min_size=(max(dialog.minimumWidth(), 360), max(dialog.minimumHeight(), 240)),
        )

    def _save_dialog_geometry(self, key: str, dialog: QDialog) -> None:
        """关闭对话框时写回几何（取消打开也记位置，下次在原处打开）。"""
        geometry = getattr(dialog, "geometry", None)
        if not callable(geometry):
            return
        rect = geometry()
        text = format_geometry(rect.width(), rect.height(), rect.x(), rect.y())
        self.settings = w.with_dialog_geometry(self.settings, key, text)
        self._persist_settings(self.geometry_text())

    def _log(self, text: str) -> None:
        """追加一行任务日志（时间戳由 :meth:`format_log_line` 统一加）。"""
        self.log(text)

    def format_log_line(self, message: str) -> str:
        """任务日志前缀：``[HH:MM:SS]``。"""
        return f"[{time.strftime('%H:%M:%S')}] {message}"

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
                cwd=w.terminal_cwd(self.settings, distro.name),
                user=None,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
            )
            self.launcher.launch(command)
        except (LaunchError, WslmanError) as exc:
            QMessageBox.critical(self, "打开失败", w._error_text(exc))
            return
        self._log(f"[打开] 已启动 {distro.name}")

    def _launch_keepalive(self, env: Any, name: str) -> None:
        """用看护进程把实例带起来，并等状态真正变成 Running（后台线程调用）。

        一次性命令（如 ``/bin/true``）跑完 WSL 就把实例收回 Stopped，
        wslmanw 关闭后更会失去唯一的进程；这里让实例里常驻一个 sleep 循环，
        关掉 wslmanw 也不会结束实例，只有用户点「停止」或 ``wsl --shutdown`` 才会退出。
        启动是异步的：``wsl -l -v`` 要过几百毫秒才看到 Running（实测约 0.7s），
        立刻断言会误报「启动失败」，统一在这里等一小段时间。
        """
        wsl_path = cli._require_wsl(env)
        spawn_detached(
            [wsl_path, "-d", name, "--exec", "/bin/sh", "-c", _KEEPALIVE_LOOP],
        )
        current = cli.wait_until_running(env, name)
        if current.state != "running":
            raise runtime_error(
                f"{name} 启动后状态仍为 {state_label(current)}（已等待 {cli.START_TIMEOUT_S:.0f}s）",
                hint="实例可能仍在启动：稍等后点「刷新」；持续未运行请检查 wsl -l -v 或运行 fcmd wslman doctor",
            )

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
            self._launch_keepalive(env, target.name)
            return f"已启动 {target.name}"

        self._start("启动", work)

    def _restart_selected(self) -> None:
        """重启：先停止再重新拉起；只在运行中可用，忙碌时与其他动作一起禁用。"""
        distro = self._selected()
        if distro is None:
            return
        if distro.state != "running":
            self.set_page_status("distros", f"{distro.name} 未运行，无需重启")
            self._update_action_states()
            return
        if not confirm(self, "重启实例", f"重启 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            cli._run_step(env, ["--terminate", distro.name], action=f"停止 {distro.name}（重启）")
            target = cli._require_target(env, distro.name)
            self._launch_keepalive(env, target.name)
            return f"已重启 {target.name}"

        self._start("重启", work)

    def _terminate_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if not confirm(self, "停止实例", f"停止 {distro.name}？正在运行的任务会被中断。"):
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
            f"导出实例 {distro.name}",
            str(self._backup_dir_path() / f"{distro.name}.tar"),
            "tar 归档 (*.tar)",
        )
        if not target:
            return
        terminate_first = False
        if distro.state == "running":
            answer = QMessageBox.question(
                self,
                "实例运行中",
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
            progress_estimate, _ = distro_export_bytes(current.base_path, current.version)
            check = cli._preflight_for(env, path, estimate, estimated=estimated)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个位置或清理磁盘")
            parts = cli._export_parts(path, "none")

            def report(done: int, total: int | None) -> None:
                self._export_bridge.event.emit((current.name, done, total))

            try:
                size, digest = cli._export_stream(
                    env,
                    current.name,
                    path,
                    parts,
                    level=6,
                    terminate_first=terminate_first,
                    estimate=progress_estimate if progress_estimate is not None else estimate,
                    progress=report,
                )
            except BaseException:
                cli._abort_parts([item for item in parts if item is not None], keep=env.config.keep_partial)
                raise
            return f"已导出 {path}（{format_bytes(size)}，sha256 {digest}）"

        self._start("导出", work)

    def _migrate_selected(self) -> None:
        """把实例整体迁到另一个已配置的工作目录（导出 → 删除 → 导入，失败回滚）。"""
        distro = self._selected()
        if distro is None:
            return
        if distro.state == "running":
            QMessageBox.information(
                self,
                "请先停止实例",
                f"{distro.name} 正在运行。迁移会导出并重新导入整个实例，请先点「停止」再迁移。",
            )
            return
        roots = list(w.work_dir_paths(self.settings))
        current_root = w.work_dir_of(self.settings, distro.base_path)
        targets = [item for item in roots if current_root is None or not w.same_dir(item, current_root)]
        if not targets:
            QMessageBox.information(
                self,
                "没有其他工作目录",
                "先在设置页添加另一个工作目录，再迁移实例。",
            )
            return
        labels = [f"{item.name or item} · {item}" for item in targets]
        choice, accepted = QInputDialog.getItem(
            self,
            f"迁移 {distro.name}",
            "目标工作目录（会导入到它的 install 子目录）：",
            labels,
            0,
            False,
        )
        if not accepted or not choice:
            return
        target_root = targets[labels.index(choice)]
        target = target_root / "install" / distro.name
        if not confirm(
            self,
            "迁移实例",
            f"将 {distro.name} 整体迁移到：\n{target}\n\n"
            "迁移会先导出、删除原实例，再导入到新位置；任一步失败会自动回滚到原位置。\n"
            "实例必须已停止。继续？",
        ):
            return

        def work(_cancel: threading.Event) -> str:
            return self._migrate_worker(distro, target_root, target)

        self._start("迁移", work)

    def _migrate_worker(self, distro: Distro, target_root: Path, target: Path) -> str:
        """迁移工作线程：导出到目标卷 → 删除旧实例 → 导入；失败尝试导回原位置。"""
        env = cli._build_environment()
        current = cli._require_target(env, distro.name)
        if current.state == "running":
            raise runtime_error(f"实例 {current.name} 正在运行", hint="先停止实例再迁移")
        if current.base_path and w.same_dir(Path(current.base_path), target):
            return f"{current.name} 已经位于 {target}"
        problem = cli._dir_problem(target)
        if problem:
            raise invalid_argument(f"目标目录不可用：{problem}", hint="目标目录必须为空且位于本地磁盘")

        estimate, estimated = distro_disk_bytes(current.base_path, current.version)
        export_estimate, _ = distro_export_bytes(current.base_path, current.version)
        need = estimate + (export_estimate or 0)
        check = cli._preflight_for(env, target_root, need, estimated=estimated)
        if not check.ok:
            raise runtime_error(
                f"目标工作目录可用空间不足：{check.detail}",
                hint="换一个工作目录，或清理磁盘后重试",
            )

        original = Path(current.base_path) if current.base_path else None
        version = str(current.version) if current.version else ""
        # 临时包放在目标工作目录根下（与最终 vhdx 同一卷）：导入时不用跨盘读大文件，
        # 又不会把临时文件放进要求为空的 install/<实例> 目录里。
        temp = target_root / f".{current.name}-migrate.tar"
        part = cli.part_path(temp)
        target_root.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            part.unlink()

        def emit(message: str) -> None:
            if self._bridge is not None:
                self._bridge.event.emit(message)

        emit(f"[迁移] 导出 {current.name} → {temp}")
        try:
            cli._run_step(
                env,
                ["--export", current.name, str(part)],
                action=f"导出 {current.name}",
                hint="检查目标工作目录的空间与权限",
                timeout=3600.0,
            )
            cli.atomic_replace(part, temp)
            emit(f"[迁移] 删除旧实例 {current.name}（数据已导出到临时包）")
            cli._run_step(env, ["--unregister", current.name], action=f"删除实例 {current.name}")
            import_args = ["--import", current.name, str(target), str(temp)]
            if version:
                import_args.extend(["--version", version])
            target.mkdir(parents=True, exist_ok=True)
            emit(f"[迁移] 导入 {current.name} → {target}")
            cli._run_step(
                env,
                import_args,
                action=f"迁移 {current.name}",
                hint="导入失败会自动尝试导回原位置",
                timeout=3600.0,
            )
        except BaseException as exc:
            emit(f"[迁移] 导入失败，正在回滚到原位置 {original or '未知'}")
            with contextlib.suppress(Exception):
                cli._run_step(env, ["--unregister", current.name], action=f"清理半成品 {current.name}")
            rollback_error: str | None = None
            if original is None or not temp.is_file():
                rollback_error = f"缺少原位置或临时包（{temp}）"
            else:
                try:
                    original.mkdir(parents=True, exist_ok=True)
                    args = ["--import", current.name, str(original), str(temp)]
                    if version:
                        args.extend(["--version", version])
                    cli._run_step(env, args, action=f"回滚 {current.name}", timeout=3600.0)
                except Exception as rollback_exc:  # 回滚失败要让用户知道临时包仍在
                    rollback_error = w._error_text(rollback_exc)
            if rollback_error is not None:
                raise runtime_error(
                    f"迁移失败且回滚未完成：{rollback_error}",
                    hint=f'临时包保留在 {temp}；可手动执行 wsl --import {current.name} <原目录> "{temp}"',
                ) from exc
            raise runtime_error(
                f"迁移失败，已回滚到原位置 {original or '未知'}：{w._error_text(exc)}",
                hint="原实例已恢复；检查目标目录空间与权限后重试",
            ) from exc

        with contextlib.suppress(OSError):
            temp.unlink()
        after = cli._try_view(env)
        landed = after.find(current.name) if after is not None else None
        if landed is not None and landed.base_path and not w.same_dir(landed.base_path, target):
            return f"已迁移 {current.name}，但复核时磁盘位置仍为 {landed.base_path}；请刷新列表确认"
        return f"已迁移 {current.name} 到 {target}"

    def _open_image_manager(self) -> None:
        """「管理镜像」：导出 / 删除本地镜像包，并清理清单缓存与未完成下载。"""
        if self._busy:
            QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        dialog = _ImageManagerDialog(self, self._download_dir_path(), self._catalog_entries())
        self._restore_dialog_geometry("image_manager", dialog)
        try:
            dialog.exec()
            self._save_dialog_geometry("image_manager", dialog)
        finally:
            changed = dialog.changed
            cleared = dialog.cache_cleared
            dialog.deleteLater()
        if changed:
            self._refresh_image_states()
        if cleared:
            self.set_page_status("images", "镜像清单缓存已清理；点「更新列表」可重新获取")

    def _install_local(self) -> None:
        """安装磁盘上已有的镜像包：扫描下载目录或手动选包，确认信息后走 ``wsl --import``。"""
        if self._busy:
            QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        dialog = _InstallLocalDialog(self, self._catalog_entries())
        self._restore_dialog_geometry("install_local", dialog)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._save_dialog_geometry("install_local", dialog)
        if not accepted:
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
                        f"实例 {name} 已存在",
                        hint=f"勾选「替换同名实例」后重试，或改用 CLI：{command} --replace --yes",
                    )
                running = existing.state == "running"
                steps = [
                    step for step, flag in (("停止", running), ("备份", backup is not None), ("删除实例", True)) if flag
                ]
                if self._bridge is not None:
                    self._bridge.event.emit(f"[安装] 替换 {existing.name}：" + " → ".join(steps) + " → 重新导入")
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
                        hint="备份失败时不会删除旧实例",
                        timeout=3600.0,
                    )
                cli._run_step(env, ["--unregister", existing.name], action=f"删除实例 {existing.name}")
            # 目录校验放在替换处理之后：替换会删除并清空旧目录，先校验会把「已存在」误报成目录问题。
            problem = cli._dir_problem(target)
            if problem:
                raise invalid_argument(f"安装目录不可用：{problem}", hint="选择一个空目录（不存在会自动创建）")
            # 默认装到工作目录的 install/<名称>，父目录可能还没建过；``wsl --import``
            # 只创建最后一级目录，父目录缺失会直接报「系统找不到指定的路径」。
            # CLI 导入同样是先 ``mkdir(parents=True)`` 再执行，这里保持同一口径。
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise invalid_argument(
                    f"无法创建安装目录：{target}",
                    hint=f"换一个可写的本地目录（{exc}）",
                ) from exc
            if self._bridge is not None:
                self._bridge.event.emit(f"[安装] 导入 {archive.name} → {target}")
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
        """下载目录：工作目录的 download 子目录（不再单独配置）。"""
        return str(w.derived_dirs(self.settings)["download"])

    def _download_entry(self, name: str) -> None:
        """行内「下载 / 安装」按钮：先聚焦该行，再复用同一套下载流程。"""
        for index in range(self.image_list.count()):
            row = self.image_list.itemWidget(self.image_list.item(index))
            if isinstance(row, _ImageItemRow) and row.entry.name == name:
                self.image_list.setCurrentRow(index)
                self._download_payload(row.entry)
                return

    def _download_payload(self, entry: ImageEntry) -> None:
        """把一次镜像下载放进队列；并发上限由设置决定，超出上限先排队。

        每一项各有取消标记、进度与速度，互相独立：单项失败 / 取消只影响自己。
        下载落点、校验、续传仍复用 ``_source.download_mirrored``，与 CLI 同一口径。
        """
        if entry.name in self._downloads:
            self._cancel_download(entry.name)
            return
        if self._busy:
            self.set_page_status("images", "已有重操作在执行；等它结束再下载，避免磁盘与网络互相拖慢")
            return
        self._downloads[entry.name] = _DownloadTask(entry)
        self._download_meters[entry.name] = SpeedMeter()
        self._pump_downloads()

    def _pump_downloads(self) -> None:
        """按并发上限启动排队项；完成 / 取消 / 改上限后都重新收敛一次。"""
        limit = w.download_limit(self.settings.max_downloads)
        running = sum(1 for task in self._downloads.values() if task.state == "running")
        for task in list(self._downloads.values()):
            if running >= limit:
                break
            if task.state != "queued":
                continue
            task.state = "running"
            running += 1
            self._launch_download(task)
        self._refresh_download_rows()
        self._update_download_status()

    def _cancel_download(self, name: str) -> None:
        """单独取消一项：排队项直接移除；运行中置取消标记，由下载层尽快收尾。"""
        task = self._downloads.get(name)
        if task is None:
            return
        if task.state == "queued":
            self._downloads.pop(name, None)
            self._download_meters.pop(name, None)
            self._log(f"[下载] {name} 已取消（尚未开始）")
            self._refresh_download_rows()
            self._update_download_status()
            if not self._downloads and not self._busy:
                self._render_images()
            return
        task.cancel.set()
        self._log(f"[下载] {name} 取消中…")
        if not self._busy:
            self.set_page_status("images", f"{name} 取消中…")

    def _launch_download(self, task: _DownloadTask) -> None:
        """启动一项下载；设置项在主线程读好，工作线程只碰纯数据。"""
        entry = task.entry
        expected = self.expected_sha.text().strip() or None
        mirror_index = self.mirror.currentIndex()
        mirror_policy = self.mirror_ids[mirror_index] if mirror_index >= 0 else "auto"
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
                progress=lambda done, total: self._progress_bridge.progress.emit((entry.name, done, total)),
            )
            return (
                f"已下载 {target}（{format_bytes(result_data.size)}，"
                f"来源 {_mirrors.label(result_data.source)}，sha256 {result_data.sha256}）；"
                "点行内「安装」导入 WSL"
            )

        def runner(cancel: threading.Event) -> tuple[str, str]:
            try:
                return "ok", work(cancel)
            except KeyboardInterrupt:
                return "cancelled", "已取消；未完成的 .part 保留在下载目录，可续传"
            except Exception as exc:  # 统一转成可读文案，单项失败不打断队列
                return "failed", w._error_text(exc)

        submit(
            runner,
            task.cancel,
            on_done=lambda payload: self._download_done(task, payload),
            on_error=lambda message: self._download_done(task, ("failed", message)),
            on_finished=lambda: self._download_finished(task),
        )

    def _download_done(self, task: _DownloadTask, payload: tuple[str, str]) -> None:
        status, message = payload
        task.state = status
        task.message = message
        if self._closing:
            return
        self._log(f"[下载] {task.entry.name}：{message}")
        if self._busy:
            # 重操作占着镜像页状态行时只留日志，避免把「迁移 / 安装」误标成下载。
            return
        if status == "ok":
            self.set_page_status("images", f"{task.entry.name} 下载完成")
        elif status == "cancelled":
            self.set_page_status("images", f"{task.entry.name} 已取消")
        else:
            self.set_page_status("images", f"{task.entry.name} 下载失败：{message}")

    def _download_finished(self, task: _DownloadTask) -> None:
        """单项收尾：释放槽位并启动下一项；单点失败 / 取消不影响其它下载。"""
        name = task.entry.name
        self._downloads.pop(name, None)
        self._download_meters.pop(name, None)
        if self._closing:
            return
        # 已下载标记按磁盘重扫，行内按钮随结果从「下载」切到「安装」。
        self._refresh_image_states()
        self._render_images()
        self._pump_downloads()

    def _install_entry(self, name: str) -> None:
        """行内「安装」：已下载的归档 → 复用「安装镜像」对话框；无归档 → wsl --install。"""
        entry = self._entry_by_name(name)
        if entry is None:
            return
        if entry.installed:
            self.set_page_status("images", f"{entry.name} 已安装，无需重复安装")
            return
        if entry.archive and Path(entry.archive).is_file():
            dialog = _InstallLocalDialog(self, self._catalog_entries(), preset=Path(entry.archive))
            self._restore_dialog_geometry("install_local", dialog)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            self._save_dialog_geometry("install_local", dialog)
            if not accepted:
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
        """按实例名（大小写不敏感）取当前列表里的条目。"""
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
        """下载目录（与「安装镜像」扫描目录同一口径）。"""
        return Path(self._local_start_dir())

    def _install_dir_path(self) -> Path:
        """安装目录：工作目录的 install 子目录。"""
        return w.derived_dirs(self.settings)["install"]

    def _backup_dir_path(self) -> Path:
        """备份目录：工作目录的 backup 子目录（不再单独配置）。"""
        return w.derived_dirs(self.settings)["backup"]

    # ------------------------------------------------------------------ #
    # 镜像列表
    # ------------------------------------------------------------------ #
    def _on_mirror_changed(self, _index: int) -> None:
        """切换下载源：立刻写回设置并提示（配置只落在设置页，镜像页不再放下拉）。"""
        if 0 <= self.mirror.currentIndex() < len(self.mirror_ids):
            self.set_page_status("settings", f"下载源：{_mirrors.label(self.mirror_ids[self.mirror.currentIndex()])}")
            self._persist_image_view()

    def _on_download_limit_changed(self, _index: int) -> None:
        """调整并发上限：写回设置，并立刻让排队的下载补上新增的槽位。"""
        value = self.download_limit_combo.currentData()
        if not isinstance(value, int):
            return
        self.settings = w.with_max_downloads(self.settings, value)
        self._persist_settings(self.geometry_text())
        if not self._busy:
            self.set_page_status("settings", f"并发下载：最多 {value} 个")
        self._pump_downloads()

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

        下载完成 → 行标签变「已下载」且按钮变「安装」；安装 / 删除实例完成 → 「已安装」
        随之增减。只跑 ``wsl -l -v`` 与下载目录扫描，代价远小于重新联网拉清单。
        """
        if self._closing or not self.image_entries:
            return
        if self._states_busy:
            # 并发下载同时收尾时，合并成一次补扫，避免最后一个完成项漏掉状态。
            self._states_pending = True
            return
        self._states_busy = True
        entries = self.image_entries

        def worker() -> tuple[ImageEntry, ...]:
            return self._mark_local(entries, cli._build_environment())

        submit_daemon(
            worker,
            on_done=self._apply_image_states,
            on_error=lambda message: self._log(f"[镜像状态] 刷新失败：{message}"),
            on_finished=self._finish_image_states,
        )

    def _finish_image_states(self) -> None:
        self._states_busy = False
        if self._states_pending and not self._closing:
            self._states_pending = False
            self._refresh_image_states()

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
        self.detach_row_detail("images")
        self.image_list.clear()
        for entry in rows:
            item = QListWidgetItem(self.image_list)
            row = _ImageItemRow(entry, self.image_list)
            row.toggleRequested.connect(self._on_image_toggle)
            row.downloadRequested.connect(self._download_entry)
            row.installRequested.connect(self._install_entry)
            # 每行的「取消」只作用于这一项下载，不碰其它并发任务。
            row.cancelRequested.connect(lambda name=entry.name: self._cancel_download(name))
            task = self._downloads.get(entry.name)
            row.set_download_state(
                busy=task is not None,
                active=task is not None,
                queued=task is not None and task.state == "queued",
                enabled=not self._busy,
            )
            if task is not None:
                row.set_metrics(self._download_metrics(task))
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
        # 重绘会重写镜像页状态行：有并发下载时改回「正在下载 / 排队」口径。
        self._update_download_status()

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
        self.image_list.sync_selection(current)

    def _on_image_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.image_list.itemWidget(item)
        if isinstance(row, _ImageItemRow):
            self._on_image_toggle(row)

    def _on_image_toggle(self, row: _ImageItemRow) -> None:
        """展开一行并在行下方挂详情；同一时间只展开一行。"""
        self.toggle_row("images", row)

    def _selected_image(self) -> ImageEntry | None:
        item = self.image_list.currentItem()
        row = self.image_list.itemWidget(item) if item is not None else None
        return row.entry if isinstance(row, _ImageItemRow) else None

    # ------------------------------------------------------------------ #
    # 删除实例 / 关闭全部
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
                cli._run_step(env, ["--unregister", target.name], action=f"删除实例 {target.name}")
            return f"已删除实例 {target.name}" + (f"；备份在 {backup}" if backup else "")

        self._start("删除实例", work)

    def _shutdown(self) -> None:
        dialog = _ShutdownDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            cli._run_step(env, ["--shutdown"], action="wsl --shutdown")
            return "已关闭全部实例与 WSL 虚拟机"

        self._start("关闭全部", work)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _persist_settings(self, geometry: str) -> None:
        w.save_settings(
            w.WslmanwSettings(
                geometry=geometry,
                work_dir=self.settings.work_dir,
                work_dirs=self.settings.work_dirs,
                mirror=self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto",
                max_downloads=w.download_limit(self.settings.max_downloads),
                sidebar_collapsed=self.nav.is_collapsed(),
                image_filter=self._image_filter_key,
                image_sort=self.image_sort.currentData() or w.DEFAULT_IMAGE_SORT,
                image_source=self.image_source.currentData() or w.DEFAULT_IMAGE_SOURCE,
                distro_terminal_dirs=self.settings.distro_terminal_dirs,
                dialog_geometry=self.settings.dialog_geometry,
                distro_init_tasks=self.settings.distro_init_tasks,
                ide_openers=self.settings.ide_openers,
                agent_openers=self.settings.agent_openers,
                ide_opener=self.settings.ide_opener,
                agent_opener=self.settings.agent_opener,
            ),
            self._own_settings_path,
        )

    def _on_close(self) -> bool:
        if self._busy and not confirm(self, "任务进行中", "有任务正在执行，取消并退出？"):
            return False
        if self._downloads and not confirm(self, "下载进行中", "还有镜像正在下载，取消并退出？"):
            return False
        self._cancel.set()
        for task in self._downloads.values():
            task.cancel.set()
        self._closing = True
        if self._poll is not None:
            self._poll.stop()
        for timer in (self._filter_timer,):
            if timer is not None:
                timer.stop()
        return True


_INIT_LOG_LINES_MAX = 400
"""初始化环境卡片的日志缓冲上限（行）。"""

_INIT_ENV_HINT = "检测与执行都会启动该实例（若尚未运行）；已就绪的项目保持勾选并禁用，不会重复执行。"
"""展开区的说明文字：收进 tooltip，不占版面高度。"""


class _InitEnvSection(Card):  # pragma: no cover - Qt 控件
    """初始化环境卡片：就地检测、勾选项目、执行并复制 SSH 公钥。

    探测与安装都走 ``wsl.exe -d <实例> --exec bash -lc``，命令在后台线程执行，
    界面只负责调度；需要 root 的步骤（APT / 系统源）由
    :class:`~fcmd.apps.wslman._initenv.InitStep` 的 ``root`` 标记决定加 ``-u root``。
    不做成独立对话框：配置对话框一层里就能看到各项状态并操作，避免多层弹窗。
    """

    def __init__(self, parent: QWidget, distro: Distro, selected: Sequence[str] | None = None) -> None:
        super().__init__("初始化环境", parent)
        self._distro = distro
        self._selected = None if selected is None else {key for key in selected if key in _initenv.task_keys()}
        """上次保存的勾选（``None`` 表示未保存过，按默认勾选）。"""
        self.setToolTip(_INIT_ENV_HINT)
        self._status: _initenv.EnvStatus | None = None
        self._done: set[str] = set()
        """检测结果里已就绪的项目 key；这些勾选框保持勾选 + 禁用，并显示绿色标签。"""
        self._busy = False
        self._closing = False
        self._refresh_after_run = False
        self._log_lines: list[str] = []
        self._bridge = EventBridge(self)
        self._bridge.event.connect(self._append_log)
        # 对话框先于后台任务结束就被销毁时，回调只写日志、不再碰控件。
        self.destroyed.connect(self._on_destroyed)

        self._collapsed = True
        self._collapsed_tags: dict[str, StatusTag] = {}

        # 收起 / 展开共用一行：收起显示「已初始化情况」，展开换成一行摘要。
        # 三个动作（检测 / 复制 SSH / 开始初始化）与全选不随详情收起。
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.collapsed_hint = hint_label("尚未检测环境。", wrap=False)
        header.addWidget(self.collapsed_hint)
        for task in _initenv.TASKS:
            tag = StatusTag(_initenv.short_label(task.key), tone="success", parent=self)
            tag.setToolTip(f"{task.label}：已初始化")
            tag.setVisible(False)
            header.addWidget(tag)
            self._collapsed_tags[task.key] = tag
        self.summary = ElidedLabel("尚未检测环境。", parent=self)
        self.summary.setObjectName("Hint")
        self.summary.setToolTip(_INIT_ENV_HINT)
        self.summary.setVisible(False)
        header.addWidget(self.summary, 1)
        self.toggle_button = icon_button("", tooltip="展开详情")
        self.toggle_button.setIconSize(QSize(16, 16))
        _qt_icons.set_icon(self.toggle_button, "chevron-down", tone="muted")
        self.toggle_button.setAccessibleName("展开详情")
        # ``clicked`` 会带一个 checked(bool) 参数；直接连 ``toggle_collapsed`` 会把
        # 它当成 ``collapsed=False``，展开后再也收不起来。
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self.toggle_button)
        self.body.addLayout(header)

        # 展开后才出现的内容：左右两栏（勾选 / 状态）+ 日志；说明只在 tooltip。
        self.details = QWidget(self)
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(2)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(12)
        checks_col = QVBoxLayout()
        checks_col.setContentsMargins(0, 0, 0, 0)
        checks_col.setSpacing(2)
        status_col = QVBoxLayout()
        status_col.setContentsMargins(0, 0, 0, 0)
        status_col.setSpacing(2)

        self.checks: dict[str, QCheckBox] = {}
        self.status_tags: dict[str, StatusTag] = {}
        for task in _initenv.TASKS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            box = QCheckBox(task.label, self)
            box.setToolTip(task.detail)
            box.setChecked(self._initial_checked(task.key))
            row.addWidget(box)
            tag = StatusTag("已初始化", tone="success", parent=self)
            tag.setToolTip("环境已就绪：保持勾选但不可再执行，需要重跑时请手动在实例内操作")
            tag.setVisible(False)
            row.addWidget(tag)
            row.addStretch(1)
            checks_col.addLayout(row)
            self.checks[task.key] = box
            self.status_tags[task.key] = tag
        checks_col.addStretch(1)

        # 状态值单行显示（不换行才不撑高），完整内容放 tooltip；每项独占一行，
        # 避免两对一行时 URL 之类长值被压到 1/4 卡片宽而大量截断。
        self.status_grid = FormGrid(self)
        self._status_values: dict[str, QLabel] = {}
        for label, _value, _ready in _initenv.status_rows(_initenv.EnvStatus()):
            value = self._make_status_value()
            self._status_values[label] = value
            self.status_grid.add_row(label, value)
        status_col.addWidget(self.status_grid)
        status_col.addStretch(1)

        columns.addLayout(checks_col, 1)
        columns.addLayout(status_col, 1)
        details.addLayout(columns)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("执行日志…")
        self.log.setMinimumHeight(56)
        self.log.setMaximumHeight(72)
        details.addWidget(self.log)

        self.body.addWidget(self.details)
        self.details.setVisible(False)

        # 动作行固定在卡片里，不随详情显隐：收起态下也能全选 / 检测 / 复制 / 初始化。
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.copy_key = button("复制 SSH 公钥", kind="ghost")
        self.copy_key.setEnabled(False)
        self.copy_key.setToolTip("SSH 密钥生成后可用；复制的是 ~/.ssh/id_ed25519.pub 全文")
        self.copy_key.clicked.connect(self._copy_ssh_key)
        actions.addWidget(self.copy_key)
        select_all = button("全选", kind="ghost")
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none = button("全不选", kind="ghost")
        select_none.clicked.connect(lambda: self._set_all(False))
        actions.addWidget(select_all)
        actions.addWidget(select_none)
        actions.addStretch(1)
        self.detect_button = button("检测环境")
        self.detect_button.clicked.connect(self.refresh_status)
        actions.addWidget(self.detect_button)
        self.run_button = button("开始初始化", kind="primary")
        self.run_button.clicked.connect(self._run_selected)
        actions.addWidget(self.run_button)
        self.body.addLayout(actions)

        self._sync_collapsed_view()

    @property
    def collapsed(self) -> bool:
        """是否收起（默认收起：只显示已初始化情况）。"""
        return self._collapsed

    def _on_toggle_clicked(self, _checked: bool = False) -> None:
        """按钮点击入口：忽略 ``clicked`` 带上的 checked 参数，只做反转。"""
        self.toggle_collapsed()

    def toggle_collapsed(self, collapsed: bool | None = None) -> None:
        """展开 / 收起；不传参时反转。收起后只留已初始化标签、动作行与展开按钮。"""
        self._collapsed = (not self._collapsed) if collapsed is None else bool(collapsed)
        self._sync_collapsed_view()
        window = self.window()
        if window is not self and isinstance(window, QDialog):
            # 行内控件显示 / 隐藏后，窗口的 minimumSizeHint 要等事件循环重算；
            # 立即 adjustSize 会读到展开时的旧值，收起后高度就回不去。
            # PySide2 singleShot 的 (int, QObject, Callable) 签名不支持，
            # 用 (int, Callable) 两参数版本，lambda 捕获 window 保持引用。
            QTimer.singleShot(0, lambda w=window: w.adjustSize())

    def _sync_collapsed_view(self) -> None:
        """收起：只显示已初始化情况；展开：额外显示勾选、状态与日志（动作行始终显示）。"""
        if self._status is None:
            hint = "尚未检测环境。"
        elif not self._done:
            hint = "尚未检测到已初始化的项目。"
        else:
            hint = "已初始化："
        self.collapsed_hint.setText(hint)
        for key, tag in self._collapsed_tags.items():
            tag.setVisible(self._collapsed and key in self._done)
        self.collapsed_hint.setVisible(self._collapsed)
        self.summary.setVisible(not self._collapsed)
        self.details.setVisible(not self._collapsed)
        _qt_icons.set_icon(self.toggle_button, "chevron-up" if not self._collapsed else "chevron-down", tone="muted")
        self.toggle_button.setToolTip("收起详情" if not self._collapsed else "展开详情")
        self.toggle_button.setAccessibleName(self.toggle_button.toolTip())

    def _make_status_value(self) -> QLabel:
        """状态值控件：单行 + 超宽省略靠 tooltip，避免长 URL 换行把卡片撑高。"""
        value = QLabel("—", self)
        value.setWordWrap(False)
        value.setMinimumWidth(64)
        value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return value

    def _initial_checked(self, key: str) -> bool:
        """首次渲染的勾选：有保存值用保存值，否则按「未完成就勾选」默认。"""
        if self._selected is not None:
            return key in self._selected
        return _initenv.default_checked(key, _initenv.EnvStatus())

    def selected_keys(self) -> tuple[str, ...]:
        """当前勾选且未就绪的项目 key，用于保存「已设置参数」。"""
        return tuple(key for key, box in self.checks.items() if box.isChecked() and key not in self._done)

    @property
    def busy(self) -> bool:
        """是否有检测 / 初始化任务在跑。"""
        return self._busy

    def stop(self) -> None:
        """对话框关闭时调用：忽略后续回调；后台命令本身继续执行。"""
        self._closing = True

    def _on_destroyed(self) -> None:
        self._closing = True

    # ------------------------------------------------------------------ #
    # 展示
    # ------------------------------------------------------------------ #
    def _set_all(self, checked: bool) -> None:
        if self._busy:
            return
        # 已就绪的项目是禁用状态，全选 / 全不选都不应改变它们的勾选。
        for box in self.checks.values():
            if box.isEnabled():
                box.setChecked(checked)

    def _apply_status(self, status: _initenv.EnvStatus) -> None:
        """回填详情；已就绪的项目勾选 + 禁用 + 绿色标签，其余按「尚未完成」默认勾选。"""
        self._status = status
        self._done = {key for key in self.checks if _initenv.present(key, status)}
        summary = _initenv.summary_text(status)
        self.summary.setText(summary)
        # 摘要行会自动省略，完整文案放 tooltip。
        self.summary.setToolTip(summary)
        for key, box in self.checks.items():
            if key in self._done:
                box.setChecked(True)
            elif self._selected is not None:
                box.setChecked(key in self._selected)
            else:
                box.setChecked(_initenv.default_checked(key, status))
        for label, value, _ok in _initenv.status_rows(status):
            widget = self._status_values.get(label)
            if widget is not None:
                widget.setText(value)
                widget.setToolTip(value)
        self._sync_check_states()
        self._sync_collapsed_view()
        self.copy_key.setEnabled(status.ssh_ready)
        if status.user:
            self._append_log(f"用户 {status.user} · 主目录 {status.home or '未知'} · {status.os_name or '未知系统'}")

    def _append_log(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                self._log_lines.append(line)
        if len(self._log_lines) > _INIT_LOG_LINES_MAX:
            del self._log_lines[: len(self._log_lines) - _INIT_LOG_LINES_MAX]
        self.log.setPlainText("\n".join(self._log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _copy_ssh_key(self) -> None:
        key = self._status.ssh_public_key if self._status is not None else ""
        if not key:
            return
        QGuiApplication.clipboard().setText(key)
        self.summary.setText("已复制 SSH 公钥")
        # 收起态下 summary 不可见：把回执写到收起行，避免点了没反应。
        self.collapsed_hint.setText("已复制 SSH 公钥")
        self._append_log("已复制 SSH 公钥到剪贴板")

    # ------------------------------------------------------------------ #
    # 检测 / 执行
    # ------------------------------------------------------------------ #
    def _sync_check_states(self) -> None:
        """按「已就绪 / 执行中」同步勾选框、绿色标签与「开始初始化」可用性。"""
        all_done = bool(self.checks) and len(self._done) == len(self.checks)
        for key, box in self.checks.items():
            done = key in self._done
            box.setEnabled(not self._busy and not done)
            self.status_tags[key].setVisible(done)
        self.run_button.setEnabled(not self._busy and not all_done)
        self.run_button.setToolTip("所有项目均已初始化，无需重复执行" if all_done else "")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.detect_button.setEnabled(not busy)
        self._sync_check_states()
        self.copy_key.setEnabled(not busy and self._status is not None and self._status.ssh_ready)

    def refresh_status(self) -> None:
        """后台探测实例环境；探测失败只写日志，不打断用户操作。"""
        if self._busy:
            return
        self._set_busy(True)
        self.summary.setText("正在检测环境…")
        submit_daemon(
            self._detect,
            on_done=self._detection_done,
            on_error=self._detection_error,
            on_finished=self._detection_finished,
        )

    def _detect(self) -> _initenv.EnvStatus:
        env = cli._build_environment()
        wsl_path = cli._require_wsl(env)
        result = env.runner.run(
            [wsl_path, "-d", self._distro.name, "--exec", "bash", "-lc", _initenv.probe_script()],
            timeout=180.0,
        )
        text = decode_console(result.stdout + result.stderr)
        status = _initenv.parse_status(text)
        if result.returncode != 0 and not status.user:
            raise runtime_error(
                f"检测 {self._distro.name} 的环境失败（退出码 {result.returncode}）",
                hint=_initenv.tail_lines(text, 3)[-1] if text.strip() else "确认实例可启动，或运行 fcmd wslman doctor",
            )
        return status

    def _detection_done(self, status: _initenv.EnvStatus) -> None:
        if self._closing:
            return
        self._apply_status(status)
        self._append_log("环境检测完成")

    def _detection_error(self, message: str) -> None:
        if self._closing:
            return
        self.summary.setText("检测失败，可点「检测环境」重试")
        self.collapsed_hint.setText("检测失败，展开后可重试。")
        self._append_log(f"检测失败：{message}")

    def _detection_finished(self) -> None:
        if not self._closing:
            self._set_busy(False)

    def _run_selected(self) -> None:
        # 已就绪的项目是禁用状态，勾着也不重新执行。
        keys = [key for key, box in self.checks.items() if box.isChecked() and box.isEnabled()]
        if not keys:
            QMessageBox.information(self, "未选择项目", "请至少勾选一项初始化内容。")
            return
        steps = _initenv.steps_for(keys)
        self._set_busy(True)
        self._refresh_after_run = False
        self._append_log("开始初始化：" + "、".join(_initenv.task_labels(keys)))
        submit_daemon(
            lambda: self._execute(steps),
            on_done=self._run_done,
            on_error=self._run_error,
            on_finished=self._run_finished,
        )

    def _execute(self, steps: Sequence[_initenv.InitStep]) -> str:
        """按顺序执行步骤；输出通过事件桥增量回到界面，失败即停止。"""
        env = cli._build_environment()
        wsl_path = cli._require_wsl(env)
        for step in steps:
            self._bridge.event.emit(f"[{step.label}] 开始…")
            argv = [wsl_path, "-d", self._distro.name]
            if step.root:
                argv.extend(["-u", "root"])
            # 必须用 --exec：``wsl --`` 仍会经过默认 shell，脚本里的 ``$HOME`` /
            # ``$(...)`` 会被提前展开成空串。--exec 直接 exec，变量留给 bash -lc。
            argv.extend(["--exec", "bash", "-lc", step.script])
            result = env.runner.run(argv, timeout=step.timeout_s)
            text = decode_console(result.stdout + result.stderr).strip()
            for line in _initenv.tail_lines(text):
                self._bridge.event.emit(line)
            if result.returncode != 0:
                tail = _initenv.tail_lines(text, 3)
                raise runtime_error(
                    f"{step.label}失败（退出码 {result.returncode}）",
                    hint=tail[-1] if tail else "查看上方日志，或在实例内手动执行",
                )
            self._bridge.event.emit(f"[{step.label}] 完成")
        return "初始化完成"

    def _run_done(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(message)
        self.summary.setText("初始化完成，正在刷新环境…")

    def _run_error(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(f"初始化失败：{message}")
        self.summary.setText("初始化失败")
        QMessageBox.critical(self, "初始化失败", message)

    def _run_finished(self) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if self._refresh_after_run:
            self.refresh_status()


_SSH_HINT = (
    "从 Windows 的 ~/.ssh 或其它 WSL 实例的 ~/.ssh 读取私钥、公钥、known_hosts 与 config，"
    "写入当前实例的 ~/.ssh；目录权限 700、私钥 600。实例内已有同名文件时不会静默覆盖：先确认，"
    "再备份到 ~/.ssh/fcmd-ssh-backup-<时间>/ 后写入。要把当前实例的 SSH 推到别的实例，"
    "打开目标实例的配置，把来源选成当前实例即可。"
)
"""迁移 SSH 卡片说明：收进 tooltip 与确认框，不占版面高度。"""

_SSH_LOG_LINES_MAX = 200
"""迁移日志缓冲上限（行）。"""


class _SshMigrateSection(Card):  # pragma: no cover - Qt 控件
    """迁移 SSH 文件卡片：从 Windows 宿主或另一个实例读取，备份后写入当前实例。

    与「初始化环境」并列放在实例配置对话框里：初始化负责在实例内生成密钥，
    本卡片负责把已经在用的密钥、known_hosts、config 带进来，两者互不替代。
    跨实例来源通过 ``wsl.exe`` 的 ``cat`` 文件流读取，不依赖实例内是否装了 rsync；
    目标侧始终是当前实例，反向迁移用目标实例的配置卡片完成。文件内容通过 stdin
    送进目标实例，命令行与日志里只有文件名和权限。
    """

    def __init__(self, parent: QWidget, distro: Distro, *, instances: Sequence[str] = ()) -> None:
        super().__init__("迁移 SSH 文件", parent)
        self._distro = distro
        self._instances = tuple(instances)
        self._busy = False
        self._closing = False
        self._files: dict[str, _sshfiles.SshFile] = {}
        self._target: _sshfiles.SshTarget | None = None
        self._source_snapshot: _sshfiles.SshSource | None = None
        self._boxes: dict[str, QCheckBox] = {}
        self._log_lines: list[str] = []
        self._refresh_after_run = False
        self._bridge = EventBridge(self)
        self._bridge.event.connect(self._append_log)
        self.destroyed.connect(self._on_destroyed)
        self.setToolTip(_SSH_HINT)

        self._collapsed = True
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.collapsed_hint = hint_label("尚未扫描来源目录。", wrap=False)
        header.addWidget(self.collapsed_hint)
        # 迁移完成度：每次扫描按实际文件推导，不依赖「点过迁移按钮」。
        self.state_tag = StatusTag("", tone="muted", parent=self)
        self.state_tag.setVisible(False)
        header.addWidget(self.state_tag)
        self.summary = ElidedLabel("", parent=self)
        self.summary.setObjectName("Hint")
        self.summary.setToolTip(_SSH_HINT)
        self.summary.setVisible(False)
        header.addWidget(self.summary, 1)
        self.toggle_button = icon_button("", tooltip="展开详情")
        self.toggle_button.setIconSize(QSize(16, 16))
        _qt_icons.set_icon(self.toggle_button, "chevron-down", tone="muted")
        self.toggle_button.setAccessibleName("展开详情")
        self.toggle_button.clicked.connect(self._on_toggle_clicked)
        header.addWidget(self.toggle_button)
        self.body.addLayout(header)

        self.details = QWidget(self)
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(4)

        # 来源类型：Windows 宿主，或另一个已安装的 WSL 实例（当前实例不作为来源）。
        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        source_row.addWidget(hint_label("来源", wrap=False))
        self.source_kind = QComboBox(self)
        self.source_kind.addItem(_sshfiles.SOURCE_LABELS[_sshfiles.SOURCE_WINDOWS], _sshfiles.SOURCE_WINDOWS)
        if self._instances:
            self.source_kind.addItem(_sshfiles.SOURCE_LABELS[_sshfiles.SOURCE_WSL], _sshfiles.SOURCE_WSL)
        self.source_kind.setToolTip("Windows 宿主的 %USERPROFILE%\\.ssh，或另一个 WSL 实例的 ~/.ssh")
        self.source_kind.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_kind)
        self.source_instance = QComboBox(self)
        for name in self._instances:
            self.source_instance.addItem(name, name)
        self.source_instance.setToolTip("从这里读取 SSH 文件；实例未运行时会由 wsl.exe 启动后再读")
        self.source_instance.setVisible(False)
        self.source_instance.currentIndexChanged.connect(self._on_source_changed)
        source_row.addWidget(self.source_instance, 1)
        source_row.addStretch(1)
        details.addLayout(source_row)

        path_row = QHBoxLayout()
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)
        self.source_label = hint_label("Windows 侧目录", wrap=False)
        path_row.addWidget(self.source_label)
        self.source = PathRow(
            value=str(_sshfiles.default_source_dir()),
            placeholder=r"%USERPROFILE%\.ssh",
            directory=True,
            parent=self,
        )
        path_row.addWidget(self.source, 1)
        self.source_remote = ElidedLabel("", parent=self)
        self.source_remote.setObjectName("Hint")
        self.source_remote.setVisible(False)
        path_row.addWidget(self.source_remote, 1)
        details.addLayout(path_row)

        self.file_hint = ElidedLabel("点「扫描」读取来源目录并检测实例。", parent=self)
        self.file_hint.setObjectName("Hint")
        details.addWidget(self.file_hint)

        self.files_box = QWidget(self)
        self.files_layout = QVBoxLayout(self.files_box)
        self.files_layout.setContentsMargins(0, 0, 0, 0)
        self.files_layout.setSpacing(2)
        details.addWidget(self.files_box)

        # 实例侧现状（含同名文件与权限过宽的提示）直接铺开，不用再点一层。
        self.target_hint = hint_label("尚未检测实例的 ~/.ssh。")
        self.target_hint.setWordWrap(True)
        details.addWidget(self.target_hint)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("迁移日志…")
        self.log.setMinimumHeight(56)
        self.log.setMaximumHeight(72)
        details.addWidget(self.log)

        self.body.addWidget(self.details)
        self.details.setVisible(False)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.select_all = button("全选", kind="ghost")
        self.select_all.clicked.connect(lambda: self._set_all(True))
        actions.addWidget(self.select_all)
        self.select_none = button("全不选", kind="ghost")
        self.select_none.clicked.connect(lambda: self._set_all(False))
        actions.addWidget(self.select_none)
        actions.addStretch(1)
        self.scan_button = button("扫描")
        self.scan_button.clicked.connect(self.refresh)
        actions.addWidget(self.scan_button)
        self.migrate_button = button("迁移所选文件", kind="primary")
        self.migrate_button.clicked.connect(self._migrate_selected)
        self.migrate_button.setEnabled(False)
        actions.addWidget(self.migrate_button)
        self.body.addLayout(actions)

        self._sync_collapsed_view()
        self._sync_source_controls()

    @property
    def collapsed(self) -> bool:
        """是否收起（默认收起，展开时才发起扫描）。"""
        return self._collapsed

    @property
    def busy(self) -> bool:
        """是否有扫描 / 迁移任务在跑。"""
        return self._busy

    def _on_toggle_clicked(self, _checked: bool = False) -> None:
        self.toggle_collapsed()

    def toggle_collapsed(self, collapsed: bool | None = None) -> None:
        """展开 / 收起；首次展开自动扫描一次（来源目录与实例现状）。"""
        was_collapsed = self._collapsed
        self._collapsed = (not self._collapsed) if collapsed is None else bool(collapsed)
        self._sync_collapsed_view()
        if was_collapsed and not self._collapsed and self._target is None and not self._busy:
            self.refresh()
        window = self.window()
        if window is not self and isinstance(window, QDialog):
            QTimer.singleShot(0, lambda w=window: w.adjustSize())

    def _sync_collapsed_view(self) -> None:
        self.collapsed_hint.setVisible(self._collapsed)
        self.summary.setVisible(not self._collapsed)
        self.details.setVisible(not self._collapsed)
        _qt_icons.set_icon(self.toggle_button, "chevron-up" if not self._collapsed else "chevron-down", tone="muted")
        self.toggle_button.setToolTip("收起详情" if not self._collapsed else "展开详情")
        self.toggle_button.setAccessibleName(self.toggle_button.toolTip())

    def stop(self) -> None:
        """对话框关闭时调用：忽略后续回调；后台命令本身继续执行。"""
        self._closing = True

    def _on_destroyed(self) -> None:
        self._closing = True

    # ------------------------------------------------------------------ #
    # 扫描 / 选择
    # ------------------------------------------------------------------ #
    def _source_dir(self) -> Path:
        text = self.source.value()
        return Path(text) if text else _sshfiles.default_source_dir()

    def _source(self) -> _sshfiles.SshSource:
        """当前选择的来源；WSL 来源的目录要等扫描探测后再补齐。"""
        if self.source_kind.currentData() == _sshfiles.SOURCE_WSL:
            return _sshfiles.SshSource(_sshfiles.SOURCE_WSL, str(self.source_instance.currentData() or ""))
        return _sshfiles.SshSource(_sshfiles.SOURCE_WINDOWS, "", str(self._source_dir()))

    def _on_source_changed(self, _index: int = 0) -> None:
        """切换来源类型 / 实例：清掉旧结果，展开态下立即重新扫描。"""
        self._reset_results()
        self._sync_source_controls()
        if not self._collapsed and not self._busy:
            self.refresh()

    def _sync_source_controls(self) -> None:
        """按来源类型切换「本地目录」与「实例 + 实际读取路径」两组控件。"""
        wsl_source = self.source_kind.currentData() == _sshfiles.SOURCE_WSL
        self.source_instance.setVisible(wsl_source)
        self.source.setVisible(not wsl_source)
        self.source_remote.setVisible(wsl_source)
        self.source_label.setText("来源实例目录" if wsl_source else "Windows 侧目录")
        if wsl_source:
            self.source_remote.setText(self._remote_path_text())
            self.source_remote.setToolTip(self.source_remote.text())

    def _remote_path_text(self) -> str:
        """WSL 来源实际读取的位置；扫描前给出预期位置，扫描后换成探测到的目录。"""
        distro = str(self.source_instance.currentData() or "")
        snapshot = self._source_snapshot
        if snapshot is not None and snapshot.is_wsl and snapshot.distro == distro:
            return snapshot.display_path()
        return f"{distro or '（未选择实例）'}:{_sshfiles.SSH_DIR}（扫描后显示实际路径）"

    def _reset_results(self) -> None:
        """来源变化或扫描失败后，旧的判定与勾选都不再成立。"""
        self._files = {}
        self._target = None
        self._source_snapshot = None
        self._rebuild_files()
        self.state_tag.setVisible(False)
        self.state_tag.setToolTip("")
        self.summary.setText("")
        self.file_hint.setText("点「扫描」读取来源目录并检测实例。")
        self.target_hint.setText("尚未检测实例的 ~/.ssh。")
        self.collapsed_hint.setText("尚未扫描来源目录。")
        self._sync_actions()

    def refresh(self) -> None:
        """后台扫描来源（Windows 目录或实例 ~/.ssh）并探测当前实例。"""
        if self._busy:
            return
        self._set_busy(True)
        self.file_hint.setText("正在扫描…")
        submit_daemon(
            self._scan,
            on_done=self._scan_done,
            on_error=self._scan_error,
            on_finished=self._scan_finished,
        )

    def _probe(self, env: Any, wsl_path: str, distro: str, action: str, hint: str) -> _sshfiles.SshTarget:
        """在实例内跑只读探测脚本，解析 ``~/.ssh`` 快照。"""
        result = env.runner.run(
            [wsl_path, "-d", distro, "--exec", "bash", "-lc", _sshfiles.probe_script()],
            timeout=180.0,
        )
        text = decode_console(result.stdout + result.stderr)
        target = _sshfiles.parse_probe(text)
        if result.returncode != 0 and not target.user:
            tail = _initenv.tail_lines(text, 3)
            # 保留可操作提示，把 wsl.exe 的原始错误附在后面便于排查。
            detail = tail[-1] if tail else ""
            raise runtime_error(
                f"{action} 失败（退出码 {result.returncode}）",
                hint=f"{hint}（wsl.exe：{detail}）" if detail else hint,
            )
        return target

    def _scan(
        self,
    ) -> tuple[tuple[_sshfiles.SshFile, ...], _sshfiles.SshTarget, _sshfiles.SshSource]:
        env = cli._build_environment()
        wsl_path = cli._require_wsl(env)
        source = self._source()
        if source.is_wsl:
            if not source.distro:
                raise invalid_argument("没有可选的 WSL 来源实例", hint="先在实例页创建或导入一个实例")
            remote = self._probe(
                env,
                wsl_path,
                source.distro,
                f"读取实例 {source.distro} 的 ~/.ssh",
                f"确认实例已安装；若未运行，先在实例页启动 {source.distro} 后重试"
                "（wsl.exe 通常也会自动拉起已停止的实例）。",
            )
            files = _sshfiles.remote_files(remote) if remote.dir else ()
            source = _sshfiles.SshSource(_sshfiles.SOURCE_WSL, source.distro, remote.dir)
        else:
            files = _sshfiles.scan_source(Path(source.directory))
        target = self._probe(
            env,
            wsl_path,
            self._distro.name,
            f"检测 {self._distro.name} 的 ~/.ssh",
            "确认实例可启动，或运行 fcmd wslman doctor",
        )
        return files, target, source

    def _scan_done(
        self,
        result: tuple[tuple[_sshfiles.SshFile, ...], _sshfiles.SshTarget, _sshfiles.SshSource],
    ) -> None:
        if self._closing:
            return
        files, target, source = result
        self._source_snapshot = source
        self._files = {item.name: item for item in files}
        self._target = target
        self._rebuild_files()
        names = "、".join(item.name for item in files)
        self.file_hint.setText(f"找到 {len(files)} 个文件：{names}" if files else "来源没有可迁移的普通文件")
        self.target_hint.setText(_sshfiles.target_summary(target))
        self.summary.setText(f"{source.label} {len(files)} 个文件 · 实例内 {len(target.files)} 个文件")
        state = _sshfiles.migration_state(files, target)
        self.state_tag.set_status(state.label, state.tone)
        self.state_tag.setToolTip(state.detail)
        self.state_tag.setVisible(True)
        self.collapsed_hint.setText(f"{source.label} {len(files)} 个文件")
        self._append_log(f"扫描完成：来源 {source.display_path()}（{len(files)} 个文件）")
        self._append_log(_sshfiles.target_summary(target))
        self._sync_source_controls()
        self._sync_actions()
        self._sync_collapsed_view()

    def _scan_error(self, message: str) -> None:
        if self._closing:
            return
        self._files = {}
        self._target = None
        self._source_snapshot = None
        self._rebuild_files()
        self.file_hint.setText("扫描失败，检查来源后重试")
        self.summary.setText("扫描失败")
        self.collapsed_hint.setText("扫描失败，展开后可重试。")
        self.target_hint.setText("")
        # 扫描失败时旧结果已经不成立，隐藏标签而不是继续展示过期状态。
        self.state_tag.setVisible(False)
        self.state_tag.setToolTip("")
        self._sync_source_controls()
        self._sync_actions()
        self._append_log(f"扫描失败：{message}")

    def _scan_finished(self) -> None:
        if not self._closing:
            self._set_busy(False)

    def _rebuild_files(self) -> None:
        while self.files_layout.count():
            item = self.files_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._boxes.clear()
        for entry in self._files.values():
            box = QCheckBox(f"{entry.name}  ·  {entry.kind_label}  ·  {entry.size_text}", self.files_box)
            tip = f"写入 ~/.ssh/{entry.name}，权限 {entry.mode_text}"
            if entry.warning:
                tip += f"\n注意：{entry.warning}"
            box.setToolTip(tip)
            box.setChecked(entry.checked_by_default)
            box.stateChanged.connect(self._sync_actions)
            self.files_layout.addWidget(box)
            self._boxes[entry.name] = box

    def _set_all(self, checked: bool) -> None:
        if self._busy:
            return
        for box in self._boxes.values():
            box.setChecked(checked)
        self._sync_actions()

    def _checked_names(self) -> list[str]:
        return [name for name, box in self._boxes.items() if box.isChecked() and box.isEnabled()]

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.scan_button.setEnabled(not busy)
        # 扫描 / 迁移期间锁住来源选择，避免结果回来时来源已经换了。
        self.source_kind.setEnabled(not busy)
        self.source_instance.setEnabled(not busy)
        self.source.setEnabled(not busy)
        for box in self._boxes.values():
            box.setEnabled(not busy)
        self._sync_actions()

    def _sync_actions(self) -> None:
        checked = self._checked_names()
        self.migrate_button.setEnabled(not self._busy and self._target is not None and bool(checked))
        if not checked:
            self.migrate_button.setToolTip("先扫描并勾选要迁移的文件")
        elif self._target is None:
            self.migrate_button.setToolTip("先扫描确认实例现状")
        else:
            self.migrate_button.setToolTip("")
        self.select_all.setEnabled(not self._busy)
        self.select_none.setEnabled(not self._busy)

    def _append_log(self, text: str) -> None:
        for line in str(text).splitlines():
            if line.strip():
                self._log_lines.append(line)
        if len(self._log_lines) > _SSH_LOG_LINES_MAX:
            del self._log_lines[: len(self._log_lines) - _SSH_LOG_LINES_MAX]
        self.log.setPlainText("\n".join(self._log_lines))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    # ------------------------------------------------------------------ #
    # 迁移
    # ------------------------------------------------------------------ #
    def _migrate_selected(self) -> None:
        names = self._checked_names()
        if not names:
            QMessageBox.information(self, "未选择文件", "请先勾选要迁移的 SSH 文件。")
            return
        if self._target is None:
            QMessageBox.information(self, "尚未扫描", "先点「扫描」确认来源目录与实例现状。")
            return
        overwrite = set(_sshfiles.conflicts(names, self._target))
        if overwrite:
            listing = "\n".join(f"· {name}" for name in names if name in overwrite)
            text = (
                "实例内已存在以下文件，迁移会先把原文件备份到 "
                f"~/.ssh/{_sshfiles.backup_name(time.time())}/ 再覆盖：\n\n{listing}\n\n继续？"
            )
            if not confirm(self, "覆盖实例内已有 SSH 文件", text):
                return
        self._set_busy(True)
        self._refresh_after_run = False
        self._append_log("开始迁移：" + "、".join(names))
        submit_daemon(
            lambda: self._migrate(names, overwrite),
            on_done=self._migrate_done,
            on_error=self._migrate_error,
            on_finished=self._migrate_finished,
        )

    def _read_remote(
        self,
        env: Any,
        wsl_path: str,
        source: _sshfiles.SshSource,
        name: str,
        entry: _sshfiles.SshFile,
    ) -> bytes:
        """经 ``wsl.exe --exec cat`` 读来源实例文件：内容只走 stdout，不进命令行。"""
        path = _sshfiles.source_file_path(source, name)
        self._bridge.event.emit(f"[{name}] 从实例 {source.distro} 读取…")
        result = env.runner.run(
            [wsl_path, "-d", source.distro, "--exec", *_sshfiles.remote_read_argv(path)],
            timeout=120.0,
        )
        if result.returncode != 0:
            tail = _initenv.tail_lines(decode_console(result.stderr).strip(), 3)
            raise runtime_error(
                f"从实例 {source.distro} 读取 {name} 失败（退出码 {result.returncode}）",
                hint=tail[-1] if tail else f"确认 {source.distro} 可启动，并重新扫描后再试",
            )
        data = result.stdout
        if len(data) != entry.size:
            raise runtime_error(
                f"{name} 自扫描后大小已变化（{len(data)} ≠ {entry.size} 字节）",
                hint="重新扫描后再迁移",
            )
        return data

    def _migrate(self, names: Sequence[str], overwrite: set[str]) -> str:
        """逐个文件写入：内容走 stdin，命令行只带文件名、权限与备份目录。"""
        env = cli._build_environment()
        wsl_path = cli._require_wsl(env)
        source = self._source_snapshot or self._source()
        backup = _sshfiles.backup_name(time.time())
        for name in names:
            entry = self._files.get(name)
            if entry is None:
                raise runtime_error(f"来源文件已变化：{name}", hint="重新扫描后再迁移")
            if source.is_wsl:
                data = self._read_remote(env, wsl_path, source, name, entry)
            else:
                data = _sshfiles.read_source(Path(source.directory) / name)
            script = _sshfiles.write_script(name, entry.mode, overwrite=name in overwrite, backup=backup)
            self._bridge.event.emit(f"[{name}] 写入 ~/.ssh（权限 {entry.mode_text}）…")
            result = env.runner.run(
                [wsl_path, "-d", self._distro.name, "--exec", "bash", "-lc", script],
                timeout=120.0,
                stdin=data,
            )
            text = decode_console(result.stdout + result.stderr).strip()
            for line in _initenv.tail_lines(text):
                self._bridge.event.emit(line)
            if result.returncode != 0:
                tail = _initenv.tail_lines(text, 3)
                hint = tail[-1] if tail else "查看日志，或在实例内手动检查 ~/.ssh"
                if result.returncode == 3:
                    hint = "目标文件在扫描后才出现；重新扫描并确认覆盖后再试"
                raise runtime_error(f"{name} 迁移失败（退出码 {result.returncode}）", hint=hint)
            mode, owner = _sshfiles.parse_write_result(text)
            if mode != entry.mode_text:
                raise runtime_error(
                    f"{name} 写入后权限为 {mode or '未知'}（期望 {entry.mode_text}）",
                    hint="确认实例的 ~/.ssh 位于 Linux 文件系统（不要落在 /mnt/c），再重新迁移",
                )
            self._bridge.event.emit(f"[{name}] 完成（{owner or '未知用户'} · {mode}）")
        return f"已迁移 {len(names)} 个文件"

    def _migrate_done(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(message)
        self.summary.setText("迁移完成，正在刷新…")

    def _migrate_error(self, message: str) -> None:
        self._refresh_after_run = True
        self._append_log(f"迁移失败：{message}")
        self.summary.setText("迁移失败")
        QMessageBox.critical(self, "迁移失败", message)

    def _migrate_finished(self) -> None:
        if self._closing:
            return
        self._set_busy(False)
        if self._refresh_after_run:
            self.refresh()


class _DistroConfigDialog(QDialog):  # pragma: no cover - 模态对话框
    """实例配置：设为默认发行版、终端默认目录，以及就地完成的环境初始化卡片。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        distro: Distro,
        cwd: str,
        *,
        selected: Sequence[str] | None = None,
        instances: Sequence[str] = (),
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"配置 {distro.name}")
        self.setToolTip(f"实例：{distro.name}")
        self._distro = distro
        self._detected = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self.make_default = QCheckBox("设为默认发行版", self)
        self.make_default.setChecked(distro.default)
        if distro.default:
            self.make_default.setEnabled(False)
            self.make_default.setToolTip("该实例已是默认发行版；设置为其它实例后才会取消")
        outer.addWidget(self.make_default)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel("终端默认目录", self), 0, 0)
        self.cwd = QLineEdit(cwd or w.DEFAULT_TERMINAL_CWD, self)
        self.cwd.setPlaceholderText(w.DEFAULT_TERMINAL_CWD)
        form.addWidget(self.cwd, 0, 1)
        reset = button("默认 ~")
        reset.clicked.connect(lambda: self.cwd.setText(w.DEFAULT_TERMINAL_CWD))
        form.addWidget(reset, 0, 2)
        outer.addLayout(form)
        outer.addWidget(
            hint_label("打开终端时作为 wsl --cd 的目录；可填 ~（用户主目录）或 /home/me/project 这类 Linux 路径。")
        )

        # 初始化环境就地铺在配置对话框里：状态、勾选与三个动作都在同一层，不再开二级对话框。
        self.init_env = _InitEnvSection(self, distro, selected)
        outer.addWidget(self.init_env)

        # SSH 迁移单独一张卡：要选来源目录、看冲突，比塞进「初始化环境」的勾选项更清楚。
        self.ssh_migrate = _SshMigrateSection(self, distro, instances=instances)
        outer.addWidget(self.ssh_migrate)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = button("保存", kind="primary")
        save.clicked.connect(self.accept)
        actions.addWidget(save)
        outer.addLayout(actions)
        fit_dialog_to_content(self, min_width=680)

    def showEvent(self, event: Any) -> None:
        """首次显示时自动检测环境；高度按当前内容重新收口，几何恢复只留位置与宽度。"""
        super().showEvent(event)
        # 上次展开态关窗会把「展开后的高度」存进几何；本次卡片默认收起，
        # 原样恢复会多出大块底部空白，所以每次显示都按内容收口一次。
        fit_dialog_to_content(self, min_width=680)
        if not self._detected:
            self._detected = True
            self.init_env.refresh_status()

    def _close_allowed(self) -> bool:
        """初始化 / 迁移在后台进行时允许关闭，但先说明任务不会中止。"""
        if self.init_env.busy and not confirm(self, "初始化仍在运行", "命令会在后台继续执行；现在关闭对话框？"):
            return False
        if self.ssh_migrate.busy and not confirm(
            self, "SSH 迁移仍在进行", "写入命令会在后台继续执行；现在关闭对话框？"
        ):
            return False
        self.init_env.stop()
        self.ssh_migrate.stop()
        return True

    def reject(self) -> None:
        if self._close_allowed():
            super().reject()

    def accept(self) -> None:
        if self._close_allowed():
            super().accept()

    def payload(self) -> tuple[bool, str]:
        """返回 ``(是否设为默认, 终端默认目录)``；目录为空时回落 ``~``。"""
        make_default = self.make_default.isChecked() and not self._distro.default
        return make_default, self.cwd.text().strip() or w.DEFAULT_TERMINAL_CWD


class _OpenerDialog(QDialog):  # pragma: no cover - 模态对话框
    """新增 / 编辑打开方式：名称、运行位置与命令模板。

    模板在这里就做语法与占位符校验（Windows 侧必须带 ``{dir}`` 或 ``{unc}``），
    避免保存后在文件页点开才报错。
    """

    def __init__(self, parent: QWidget, kind: str, opener: _open.Opener | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._values = (
            opener.name if opener else "",
            opener.host if opener else _open.HOST_WINDOWS,
            opener.command if opener else "",
        )
        self.setWindowTitle(f"{'编辑' if opener else '添加'}{_open.KIND_LABELS[kind]}打开方式")
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        form = FormGrid(self)
        self.name = QLineEdit(self._values[0], self)
        self.name.setPlaceholderText(f"例如 {_open.KIND_LABELS[kind]} 名称")
        form.add_row("名称", self.name)
        self.host = QComboBox(self)
        for value, label in _open.HOST_LABELS.items():
            self.host.addItem(label, value)
        index = self.host.findData(self._values[1])
        self.host.setCurrentIndex(index if index >= 0 else 0)
        self.host.setToolTip("Windows 宿主机里的 GUI 程序，或要在 WSL 实例内启动的终端程序")
        form.add_row("运行位置", self.host)
        self.command = QLineEdit(self._values[2], self)
        self.command.setPlaceholderText('code --remote wsl+{distro} "{dir}"')
        self.command.setToolTip("可执行命令；引号内的空格会保留")
        form.add_row("命令模板", self.command)
        layout.addWidget(form)

        if opener is not None and opener.note:
            layout.addWidget(hint_label(opener.note))
        layout.addWidget(
            hint_label(
                "占位符：{dir} 实例内绝对路径、{unc} Windows UNC 路径、{distro} 实例名、{home} 家目录。"
                "Windows 宿主机条目必须包含 {dir} 或 {unc}；WSL 内条目以当前目录启动，可不写占位符。"
            )
        )

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = button("保存", kind="primary")
        save.clicked.connect(self._on_save)
        actions.addWidget(save)
        layout.addLayout(actions)
        fit_dialog_to_content(self)
        self.name.setFocus()

    def _on_save(self) -> None:
        try:
            self._values = _open.validate_opener(self.name.text(), self.host.currentData(), self.command.text())
        except WslmanError as exc:
            QMessageBox.warning(self, "命令模板不可用", w._error_text(exc))
            return
        self.accept()

    def values(self) -> tuple[str, str, str]:
        return self._values


class _UnregisterDialog(QDialog):  # pragma: no cover - 模态对话框
    """删除实例确认：手输实例名、可选先停止、可选先备份；明确写清磁盘会被删除。"""

    def __init__(self, owner: WslmanQtWindow, distro: Distro, size: tuple[int | None, bool]) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"删除实例 {distro.name}")
        self.setMinimumWidth(560)
        self._owner = owner
        self._distro = distro
        estimate, estimated = size

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                f"BasePath: {distro.base_path or '未知'}\n"
                f"占用: {format_bytes(estimate, estimated=estimated)}\n\n"
                "删除会移除该实例的注册信息，并删除其 ext4.vhdx 虚拟磁盘（不可恢复，无回收站）。\n"
                "需要保留数据请勾选「先导出备份」。"
            )
        )
        self.terminate_first = QCheckBox("先停止实例", self)
        self.terminate_first.setChecked(distro.state == "running")
        outer.addWidget(self.terminate_first)
        self.backup_enabled = QCheckBox("先导出备份到", self)
        self.backup_enabled.setChecked(bool(owner.config_data.require_backup_on_replace))
        outer.addWidget(self.backup_enabled)
        backup_row = QHBoxLayout()
        backup_row.setContentsMargins(0, 0, 0, 0)
        backup_row.setSpacing(6)
        self.backup = QLineEdit(str(owner._backup_dir_path() / f"{distro.name}.tar"), self)
        backup_row.addWidget(self.backup, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_backup)
        backup_row.addWidget(choose)
        outer.addLayout(backup_row)
        outer.addWidget(hint_label(f"手输实例名以确认：{distro.name}"))
        self.typed = QLineEdit(self)
        self.typed.textChanged.connect(self._sync)
        outer.addWidget(self.typed)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.confirm = button("删除实例", kind="danger")
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
            str(self._owner._backup_dir_path() / f"{self._distro.name}.tar"),
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
        outer.addWidget(hint_label("会停止全部实例与 WSL 虚拟机，并连带停掉 Docker Desktop、IDE Remote 等后端。"))
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


class _ImageManagerDialog(QDialog):  # pragma: no cover - 模态对话框
    """管理下载目录里的镜像包：导出、删除、清理清单缓存与未完成下载。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        directory: Path,
        entries: tuple[ImageEntry, ...],
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle("管理镜像")
        self.setMinimumWidth(820)
        self.directory = directory
        self._entries = entries
        self._items: list[tuple[Path, int, str, bool]] = []
        self.changed = False
        self.cache_cleared = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label(f"下载目录：{directory}"))

        self.table = make_table(
            ("镜像包", "大小", "识别为", "状态"),
            widths=(430, 100, 160, 90),
            stretch_columns=(0,),
            parent=self,
        )
        self.table.setMinimumHeight(240)
        self.table.itemSelectionChanged.connect(self._on_select)
        outer.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.export_button = button("导出…")
        self.export_button.clicked.connect(self._export_selected)
        actions.addWidget(self.export_button)
        self.delete_button = button("删除", kind="danger")
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        clean = button("清理缓存")
        clean.setToolTip("删除镜像清单缓存与下载目录里的未完成 .part 文件")
        clean.clicked.connect(self._clean_cache)
        actions.addWidget(clean)
        open_dir = button("打开目录")
        open_dir.clicked.connect(self._open_folder)
        actions.addWidget(open_dir)
        refresh = button("刷新")
        refresh.clicked.connect(self._reload)
        actions.addWidget(refresh)
        actions.addStretch(1)
        close = button("关闭", kind="primary")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addLayout(actions)

        self.status = hint_label("选择一个镜像包后可导出或删除；.part 是未完成的下载")
        outer.addWidget(self.status)
        self._reload()
        fit_dialog_to_content(self, min_width=820)

    def _reload(self) -> None:
        """重扫下载目录：完整镜像包与 ``.part`` 未完成下载都列出来。"""
        self.table.setRowCount(0)
        self._items.clear()
        if not self.directory.is_dir():
            self.status.setText(f"下载目录不存在：{self.directory}")
            self._sync_actions()
            return
        try:
            paths = sorted(self.directory.iterdir(), key=lambda path: path.name.casefold())
        except OSError as exc:
            self.status.setText(f"无法读取下载目录：{exc}")
            self._sync_actions()
            return
        for path in paths:
            if not path.is_file():
                continue
            name = path.name.casefold()
            incomplete = name.endswith(".part")
            if not incomplete and not name.endswith(w._ARCHIVE_SUFFIXES):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if incomplete:
                distro, complete = "—", False
            else:
                info = w.inspect_archive(path, self._entries)
                distro, complete = info.distro, True
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, make_table_item(str(path)))
            self.table.setItem(row, 1, make_table_item(format_bytes(size)))
            self.table.setItem(row, 2, make_table_item(distro))
            self.table.setItem(row, 3, make_table_item("完整" if complete else "未完成"))
            self._items.append((path, size, distro, complete))
        if self.table.rowCount():
            self.table.selectRow(0)
            self.status.setText("选择一个镜像包后可导出或删除；.part 是未完成的下载")
        else:
            self.status.setText("下载目录里没有可管理的镜像包")
        self._sync_actions()

    def _current(self) -> tuple[Path, int, str, bool] | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _on_select(self) -> None:
        self._sync_actions()

    def _sync_actions(self) -> None:
        current = self._current()
        self.export_button.setEnabled(current is not None and current[3])
        self.delete_button.setEnabled(current is not None)

    def _export_selected(self) -> None:
        archive = self._current()
        if archive is None or not archive[3]:
            self.status.setText("未完成的 .part 不能导出；请先下载完成")
            return
        chosen = QFileDialog.getExistingDirectory(self, "导出镜像包到目录", str(self.directory))
        if not chosen:
            return
        target = Path(chosen) / archive[0].name
        if target.resolve() == archive[0].resolve():
            self.status.setText("导出目录就是当前下载目录")
            return
        if target.exists() and not confirm(self, "覆盖镜像包", f"目标已存在，覆盖？\n{target}"):
            return
        try:
            shutil.copy2(archive[0], target)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", f"无法复制镜像包：\n{exc}")
            return
        self.status.setText(f"已导出到 {target}")

    def _delete_selected(self) -> None:
        archive = self._current()
        if archive is None:
            return
        if not confirm(self, "删除镜像包", f"永久删除？\n{archive[0]}\n大小：{format_bytes(archive[1])}"):
            return
        try:
            archive[0].unlink()
        except OSError as exc:
            QMessageBox.critical(self, "删除失败", f"无法删除镜像包：\n{exc}")
            return
        self.changed = True
        self.status.setText(f"已删除 {archive[0].name}")
        self._reload()

    def _clean_cache(self) -> None:
        targets: list[Path] = []
        cache = _images.cache_path()
        if cache.is_file():
            targets.append(cache)
        if self.directory.is_dir():
            with contextlib.suppress(OSError):
                targets.extend(sorted(self.directory.glob("*.part")))
        if not targets:
            self.status.setText("没有可清理的缓存或未完成下载")
            return
        if not confirm(self, "清理缓存", f"删除镜像清单缓存与 {len(targets)} 个缓存 / 未完成文件？"):
            return
        removed = 0
        for path in targets:
            try:
                path.unlink()
                removed += 1
                if path == cache:
                    self.cache_cleared = True
            except OSError:
                continue
        self.changed = self.changed or removed > 0
        self.status.setText(f"已清理 {removed} 个缓存 / 未完成文件")
        self._reload()

    def _open_folder(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", f"无法创建下载目录：\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.directory)))


class _InstallLocalDialog(QDialog):  # pragma: no cover - 模态对话框
    """安装镜像：扫描目录 / 选文件、查看识别结果、填名称与安装目录、可选替换。"""

    def __init__(
        self,
        owner: WslmanQtWindow,
        entries: tuple[ImageEntry, ...],
        *,
        preset: Path | None = None,
    ) -> None:
        super().__init__(owner)
        self.setWindowTitle("安装镜像")
        self.setMinimumWidth(820)
        self._owner = owner
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
        for row, key in enumerate(("实例", "大小", "来源", "校验状态")):
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
        form.addWidget(QLabel("实例名称"), 0, 0)
        form.addWidget(self.name, 0, 1)
        self.target = QLineEdit(self)
        form.addWidget(QLabel("安装目录"), 1, 0)
        form.addWidget(self.target, 1, 1)
        choose = button("选择…")
        choose.clicked.connect(self._choose_dir)
        form.addWidget(choose, 1, 2)
        self.replace = QCheckBox("替换同名实例（会先删除现有实例，必要时先备份）", self)
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
        self._info["实例"].setText(f"{archive.distro}（{arch}）")
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
        self.target.setText(str(self._owner._install_dir_path() / archive.distro))

    def _start(self) -> None:
        archive = self._current()
        if archive is None:
            QMessageBox.critical(self, "安装镜像", "请先选择一个镜像包")
            return
        name = self.name.text().strip()
        target_text = self.target.text().strip()
        if not name:
            QMessageBox.critical(self, "安装镜像", "请填写实例名称")
            return
        if not target_text:
            QMessageBox.critical(self, "安装镜像", "请选择安装目录")
            return
        replace = self.replace.isChecked()
        if replace and not confirm(self, "替换实例", f"替换 {name} 会先删除现有实例（运行中会先停止），继续？"):
            return
        backup: Path | None = None
        if replace and load_config().require_backup_on_replace:
            chosen, _ = QFileDialog.getSaveFileName(
                self,
                "替换前先备份到",
                str(self._owner._backup_dir_path() / f"{name}.tar"),
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
    from fcmd.gui.app import run_window

    run_window(lambda: WslmanQtWindow(settings_path), app_name="WSL 管理")
