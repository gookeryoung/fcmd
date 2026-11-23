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
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from fcmd.apps.wslman import _files, _images, _initenv, _mirrors, _open
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman import wslmanw as w
from fcmd.apps.wslman._config import load_config
from fcmd.apps.wslman._decode import decode_console
from fcmd.apps.wslman._disk import distro_disk_bytes, distro_export_bytes, format_bytes
from fcmd.apps.wslman._distro import state_label
from fcmd.apps.wslman._errors import WslmanError, invalid_argument, runtime_error
from fcmd.apps.wslman._terminal import (
    LaunchError,
    SubprocessLauncher,
    build_command,
    spawn_detached,
    spawn_gui,
)
from fcmd.gui.geometry import format_geometry
from fcmd.gui.page import PageBody, PageSpec
from fcmd.gui.widgets import (
    Card,
    DiskGrid,
    ElidedLabel,
    FlowLayout,
    FormGrid,
    PageScroll,
    RowDetailMixin,
    RowListWidget,
    StatusTag,
    button,
    confirm,
    default_disk_locations,
    hint_label,
    make_table,
    make_table_item,
    set_button_icon,
    set_button_text,
)
from fcmd.gui.window import QtMainWindow, restore_geometry
from fcmd.gui.workers import EventBridge, ProgressBridge, periodic, submit, submit_daemon
from fcmd.infras.download import SpeedMeter, format_speed

from .qt.actions import ActionsMixin
from .qt.distros import DistrosMixin
from .qt.files import FilesMixin
from .qt.images_download import ImagesDownloadMixin
from .qt.images_manage import ImagesManageMixin
from .qt.images_page import ImagesPageMixin
from .qt.lifecycle import LifecycleMixin
from .qt.logging import LoggingMixin
from .qt.settings import SettingsMixin
from .qt.tasks import TasksMixin

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.wslman._images import ImageEntry

__all__ = ["WslmanQtWindow", "run_qt"]

DEFAULT_SIZE = (1160, 760)

_KEEPALIVE_LOOP = "while :; do sleep 3600; done"


def _ensure_running(env: Any, name: str) -> None:
    """用看护进程确保实例处于 Running；供启动按钮 / 配置内探测共用。"""
    wsl_path = cli._require_wsl(env)
    spawn_detached([wsl_path, "-d", name, "--exec", "/bin/sh", "-c", _KEEPALIVE_LOOP])
    current = cli.wait_until_running(env, name)
    if current.state != "running":
        raise runtime_error(
            f"{name} 启动后状态仍为 {state_label(current)}（已等待 {cli.START_TIMEOUT_S:.0f}s）",
            hint="实例可能仍在启动：稍等后点「刷新」；持续未运行请检查 wsl -l -v 或运行 fcmd wslman doctor",
        )


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


class WslmanQtWindow(
    FilesMixin,
    ImagesPageMixin,
    SettingsMixin,
    TasksMixin,
    DistrosMixin,
    ActionsMixin,
    ImagesManageMixin,
    ImagesDownloadMixin,
    LifecycleMixin,
    LoggingMixin,
    RowDetailMixin,
    QtMainWindow,
):  # pragma: no cover - 需要 Qt 事件循环
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
        self._reload_pending = False
        self._reload_quiet = False
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

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 任务编排
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 运行状态轮询
    # ------------------------------------------------------------------ #

    @staticmethod
    def _base_paths_changed(previous: Any, current: Any) -> bool:
        """注册表里的 ``BasePath`` 是否变了（按实例名比对）。"""
        before = {distro.name: distro.base_path for distro in previous.distros}
        return any(before.get(distro.name) != distro.base_path for distro in current.distros)

    # ------------------------------------------------------------------ #
    # 列表与详情
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 镜像列表
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 删除实例 / 关闭全部
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #


_INIT_LOG_LINES_MAX = 400
"""初始化环境卡片的日志缓冲上限（行）。"""

_INIT_ENV_HINT = "检测与执行都会启动该实例（若尚未运行）；已就绪的项目保持勾选并禁用，不会重复执行。"
"""展开区的说明文字：收进 tooltip，不占版面高度。"""


_SSH_HINT = (
    "从 Windows 的 ~/.ssh 或其它 WSL 实例的 ~/.ssh 读取私钥、公钥、known_hosts 与 config，"
    "写入当前实例的 ~/.ssh；目录权限 700、私钥 600。实例内已有同名文件时不会静默覆盖：先确认，"
    "再备份到 ~/.ssh/fcmd-ssh-backup-<时间>/ 后写入。要把当前实例的 SSH 推到别的实例，"
    "打开目标实例的配置，把来源选成当前实例即可。"
)
"""迁移 SSH 卡片说明：收进 tooltip 与确认框，不占版面高度。"""

_SSH_LOG_LINES_MAX = 200
"""迁移日志缓冲上限（行）。"""


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.app import run_window

    run_window(lambda: WslmanQtWindow(settings_path), app_name="WSL 管理")


_REEXPORTS = (
    Card,
    DiskGrid,
    ElidedLabel,
    EventBridge,
    FlowLayout,
    FormGrid,
    LaunchError,
    PageBody,
    PageScroll,
    Path,
    QButtonGroup,
    QComboBox,
    QDesktopServices,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QSize,
    QSizePolicy,
    QTimer,
    QUrl,
    QWidget,
    Qt,
    RowListWidget,
    SpeedMeter,
    StatusTag,
    WslmanError,
    _ensure_running,
    _files,
    _images,
    _images_note_text,
    _images_stamp,
    _initenv,
    _mirrors,
    _open,
    build_command,
    button,
    cli,
    confirm,
    contextlib,
    decode_console,
    default_disk_locations,
    distro_disk_bytes,
    distro_export_bytes,
    format_bytes,
    format_geometry,
    format_speed,
    hint_label,
    invalid_argument,
    make_table,
    make_table_item,
    periodic,
    restore_geometry,
    runtime_error,
    set_button_icon,
    set_button_text,
    shutil,
    spawn_detached,
    spawn_gui,
    submit,
    submit_daemon,
    threading,
    time,
    w,
)

from PySide6.QtCore import Signal  # noqa: F401, E402
from PySide6.QtGui import QGuiApplication  # noqa: F401, E402
from PySide6.QtWidgets import (  # noqa: F401, E402
    QCheckBox,
    QFrame,
    QGridLayout,
    QPlainTextEdit,
    QVBoxLayout,
)

from fcmd.apps.wslman import _sshfiles  # noqa: F401, E402
from fcmd.gui import icons as _qt_icons  # noqa: F401, E402
from fcmd.gui.widgets import PathRow, icon_button, set_button_kind  # noqa: F401, E402

from .qt import initenv as _qt_initenv  # noqa: E402
from .qt import rows as _qt_rows  # noqa: E402
from .qt import ssh as _qt_ssh  # noqa: E402
from .qt import state as _qt_state  # noqa: E402

_DownloadTask = _qt_state._DownloadTask
_DistroItemRow = _qt_rows._DistroItemRow
_DistroDetailView = _qt_rows._DistroDetailView
_ImageItemRow = _qt_rows._ImageItemRow
_ImageDetailView = _qt_rows._ImageDetailView
_InitEnvSection = _qt_initenv._InitEnvSection
_SshMigrateSection = _qt_ssh._SshMigrateSection

from fcmd.gui.widgets import ItemRow  # noqa: E402, F401

from .qt.dialogs import (  # noqa: E402, F401
    _DistroConfigDialog,
    _ImageManagerDialog,
    _InstallLocalDialog,
    _OpenerDialog,
    _ShutdownDialog,
    _UnregisterDialog,
)
