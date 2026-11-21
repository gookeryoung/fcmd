"""wslmanw 的 Qt 主窗口与入口。

窗口只做编排：业务判断全部走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出、预检、注销前备份、镜像下载），
与 CLI 共用同一套口径；界面负责列表 / 详情 / 下载 / 日志四块内容与任务编排。

布局与 llmw 对齐：复用共享 :class:`fcmd.gui.qt.window.QtMainWindow` 的左侧
:class:`~fcmd.gui.qt.sidebar.SidebarNav` 导航（折叠、``Alt+1..3`` 切换与窗口
几何记忆都由基座提供），右侧按「发行版 / 镜像 / 日志」分页，每页拿满整个
内容区，不再把详情、下载与日志挤进同一个底部标签页。发行版页用一个纵向
分栏把列表与详情分开；镜像页沿用列表 / 详情横向分栏。后台任务一律走
:func:`fcmd.gui.qt.workers.submit`，运行状态每 3 秒轮询一次（最小化时暂停），
关窗时取消进行中的任务。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``（与旧设置文件同名字段互读）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from fcmd.apps.wslman._config import load_config
from fcmd.apps.wslman._disk import distro_disk_bytes, format_bytes, recommended_backup_dir
from fcmd.apps.wslman._distro import Distro, state_label
from fcmd.apps.wslman._errors import WslmanError, invalid_argument, runtime_error
from fcmd.apps.wslman._terminal import LaunchError, SubprocessLauncher, build_command
from fcmd.apps.wslman._transfer import sha256_file
from fcmd.gui.qt.widgets import (
    Card,
    Collapsible,
    HintLabel,
    LogView,
    button,
    confirm,
    hint_label,
    make_splitter,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.window import QtMainWindow
from fcmd.gui.qt.workers import periodic, submit

if TYPE_CHECKING:
    from typing import Any

    from fcmd.apps.wslman._images import ImageEntry

__all__ = ["WslmanQtWindow", "run_qt"]

DEFAULT_SIZE = (1160, 760)

TITLE = "WSL · 发行版管理"
NAV_TITLE = "WSL"

PAGES: tuple[tuple[str, str, str], ...] = (
    ("distros", "发行版", "列表、详情、启动停止、导出与注销"),
    ("images", "镜像", "在线镜像清单、下载源与本地安装"),
    ("log", "日志", "启动、导出、安装与下载的任务日志"),
)
"""侧边栏页面顺序与提示；与 llmw 相同，左侧导航由共享 ``SidebarNav`` 提供。"""

_COLUMN_KEYS: tuple[str, ...] = tuple(key for key, _title, _minimum, _maximum in w._COLUMNS)


class _LogBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台任务的日志行信号（跨线程排队到主线程）。"""

    message = Signal(str)


class WslmanQtWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """WSL 发行版管理：侧边栏分页（发行版 / 镜像 / 日志）+ 后台任务。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self.settings = w.load_settings(settings_path or w.config_path())
        self.config_data = load_config()
        self.launcher = SubprocessLauncher()
        self.view: Any = None
        self.sizes: dict[str, tuple[int | None, bool]] = {}
        self._rows: list[tuple[str, ...]] = []
        self.image_entries: tuple[ImageEntry, ...] = ()
        self._busy = False
        self._label = ""
        self._cancel = threading.Event()
        self._status_busy = False
        self._images_busy = False
        self._closing = False
        self._bridge: _LogBridge | None = None
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
        # 三页都很轻（表格 + 表单），建窗即构造：镜像页的控件也是设置持久化与
        # 下载动作的读取来源，延迟构建会让「没切到镜像页就关窗」时缺控件。
        builders = {
            "distros": self._build_distros_page,
            "images": self._build_images_page,
            "log": self._build_log_page,
        }
        for key, label, hint in PAGES:
            page = builders[key]()
            self.add_page(key, label, hint=hint, factory=lambda page=page: page)
        self.hover_label = HintLabel("")
        self.hover_label.setTextFormat(Qt.TextFormat.PlainText)
        self.hover_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.statusBar().insertWidget(0, self.hover_label, 1)
        self._schedule_poll()
        self.refresh()

    def show_page(self, key: str) -> None:
        """切页：首次进入镜像页时拉取在线清单（等价于旧版切到「下载」标签）。"""
        super().show_page(key)
        if key == "images":
            self._load_images()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_distros_page(self) -> QWidget:
        """发行版页：动作行 + 纵向分栏（列表 / 详情）。"""
        page = QWidget(self)
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addLayout(self._build_distro_actions(page))

        split = make_splitter(Qt.Orientation.Vertical, sizes=(430, 250), parent=page)
        split.setMinimumHeight(300)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        self._table_card = Card("发行版", split)
        self._table = make_table(
            [title for _key, title, _minimum, _maximum in w._COLUMNS],
            widths=[minimum for _key, _title, minimum, _maximum in w._COLUMNS],
            parent=self._table_card,
        )
        self._table.setMinimumHeight(150)
        self._table.itemSelectionChanged.connect(self._show_details)
        self._table.cellDoubleClicked.connect(lambda _row, _column: self._open_selected())
        self._table_card.body.addWidget(self._table)
        split.addWidget(self._table_card)
        split.addWidget(self._build_detail_card(split))
        body.addWidget(split, 1)
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

    def _build_detail_card(self, parent: QWidget) -> Card:
        """详情卡：9 个字段分左右两组，可随窗口宽度换行。"""
        card = Card("详情", parent)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.detail_values: dict[str, QLabel] = {}
        for index, label in enumerate(w._DETAIL_LABELS):
            group, row = divmod(index, w._DETAIL_ROWS)
            column = group * 2
            grid.addWidget(hint_label(label, wrap=False), row, column, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", card)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(value, row, column + 1, Qt.AlignmentFlag.AlignTop)
            self.detail_values[label] = value
        card.body.addLayout(grid)
        self.detail_hint = hint_label("选择上方列表中的发行版查看详情")
        card.body.addWidget(self.detail_hint)
        card.body.addStretch(1)
        card.setMinimumHeight(170)
        return card

    def _build_images_page(self) -> QWidget:
        """镜像页：搜索 / 下载源、清单与详情分栏、下载动作与高级选项。"""
        page = QWidget(self)
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        search_row.addWidget(QLabel("搜索"))
        self.image_search = QLineEdit(self)
        self.image_search.setClearButtonEnabled(True)
        self.image_search.textChanged.connect(self._schedule_image_filter)
        search_row.addWidget(self.image_search, 1)
        search_row.addWidget(QLabel("下载源"))
        self.mirror_ids = _mirrors.identifiers()
        self.mirror = QComboBox(self)
        self.mirror.addItems([_mirrors.label(ident) for ident in self.mirror_ids])
        index = self.mirror_ids.index(self.settings.mirror) if self.settings.mirror in self.mirror_ids else 0
        self.mirror.setCurrentIndex(index)
        self.mirror.currentIndexChanged.connect(self._on_mirror_changed)
        search_row.addWidget(self.mirror)
        body.addLayout(search_row)
        self.images_status = hint_label("打开本页时自动获取镜像清单")
        body.addWidget(self.images_status)

        split = make_splitter(sizes=(520, 380), parent=page)
        split.setMinimumHeight(300)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        self.image_table = make_table(
            ("名称", "友好名", "架构", "标记"),
            widths=(150, 190, 90, 130),
            stretch_columns=(1,),
            parent=split,
        )
        self.image_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.image_table.itemSelectionChanged.connect(self._show_image_details)
        self.image_table.cellDoubleClicked.connect(lambda _row, _column: self._download())
        split.addWidget(self.image_table)
        split.addWidget(self._build_image_detail(split))
        self._image_split = split
        body.addWidget(split, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._download_button = button("开始下载", kind="primary")
        self._download_button.clicked.connect(self._download)
        actions.addWidget(self._download_button)
        self._cancel_button = button("取消")
        self._cancel_button.clicked.connect(self._cancel_task)
        actions.addWidget(self._cancel_button)
        self.install_after = QCheckBox("下载后立即安装", self)
        actions.addWidget(self.install_after)
        separator = QLabel("│", page)
        separator.setObjectName("Hint")
        actions.addWidget(separator)
        install_local = button("安装本地镜像…")
        install_local.clicked.connect(self._install_local)
        actions.addWidget(install_local)
        self._buttons.append(install_local)
        self._button_by_text["安装本地镜像…"] = install_local
        actions.addStretch(1)
        body.addLayout(actions)
        body.addWidget(hint_label(w._DOWNLOAD_HINT))

        self.advanced = Collapsible("高级：下载目录 / 校验", parent=page)
        self.download_dir = QLineEdit(self.settings.download_dir, self.advanced.content)
        self.download_dir.setPlaceholderText("留空时使用 wslman.json 的 download_dir 或系统下载目录")
        self.expected_sha = QLineEdit("", self.advanced.content)
        self.expected_sha.setPlaceholderText("可选：期望的 sha256（留空用官方清单）")
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel("下载目录"), 0, 0)
        form.addWidget(self.download_dir, 0, 1)
        form.addWidget(QLabel("期望 sha256"), 1, 0)
        form.addWidget(self.expected_sha, 1, 1)
        self.advanced.body.addLayout(form)
        body.addWidget(self.advanced)
        return page

    def _build_image_detail(self, parent: QWidget) -> QWidget:
        """镜像详情：短字段网格 + 可换行的只读下载地址。"""
        detail = QWidget(parent)
        detail_grid = QGridLayout(detail)
        detail_grid.setContentsMargins(8, 0, 0, 0)
        detail_grid.setHorizontalSpacing(8)
        detail_grid.setVerticalSpacing(4)
        detail_grid.setColumnStretch(1, 1)
        self.image_detail_values: dict[str, QLabel] = {}
        for row, label in enumerate(w._IMAGE_DETAIL_FIELDS):
            detail_grid.addWidget(hint_label(label, wrap=False), row, 0, Qt.AlignmentFlag.AlignTop)
            value = QLabel("—", detail)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail_grid.addWidget(value, row, 1)
            self.image_detail_values[label] = value
        url_row = len(w._IMAGE_DETAIL_FIELDS)
        detail_grid.addWidget(hint_label("下载地址", wrap=False), url_row, 0, Qt.AlignmentFlag.AlignTop)
        self.image_url = QPlainTextEdit(detail)
        self.image_url.setReadOnly(True)
        self.image_url.setMinimumHeight(72)
        self.image_url.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        detail_grid.addWidget(self.image_url, url_row, 1)
        detail_grid.setRowStretch(url_row, 1)
        return detail

    def _build_log_page(self) -> QWidget:
        """日志页：任务日志独占整页，复制 / 清空 / 导出沿用共享工具条。"""
        page = QWidget(self)
        body = QVBoxLayout(page)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        card = Card("任务日志", page)
        self._log_view = LogView(height=200, parent=card)
        card.body.addWidget(self._log_view, 1)
        card.body.addWidget(hint_label("汇总启动 / 停止、导出、安装与下载操作；复制、清空、导出见日志工具条。"))
        body.addWidget(card, 1)
        return page

    # ------------------------------------------------------------------ #
    # 任务编排
    # ------------------------------------------------------------------ #
    def _start(self, label: str, work: Any) -> None:
        if self._busy:
            QMessageBox.information(self, "请稍候", "已有任务在执行")
            return
        self._busy = True
        self._label = label
        self._cancel = threading.Event()
        self._bridge = _LogBridge(self)
        self._bridge.message.connect(self._log)
        self.set_status(f"{label}…")
        self._set_buttons(False)

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
            self.set_status(f"{label}失败")
            QMessageBox.critical(self, label, message)
            return
        self._log(f"[{label}] {message}")
        self.set_status(f"{label}完成")
        if label == "刷新":
            self._refresh_rows()
        else:
            self.refresh()

    def _task_error(self, label: str, message: str) -> None:
        self._log(f"[{label}] 失败：{message}")
        self.set_status(f"{label}失败")
        QMessageBox.critical(self, label, message)

    def _task_finished(self) -> None:
        self._busy = False
        self._set_buttons(True)
        if self._closing:
            return
        self._update_action_states()

    def _set_buttons(self, enabled: bool) -> None:
        for widget in self._buttons:
            widget.setEnabled(enabled)
        if enabled:
            self._update_action_states()

    def _cancel_task(self) -> None:
        if self._busy:
            self._cancel.set()
            self.set_status(f"{self._label}取消中…")

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
            self.set_status(f"状态刷新失败：{message}")

    def _apply_status(self, view: Any) -> None:
        """只改状态列与按钮态，不重建整表：选中行与滚动位置不受影响。"""
        previous = self.view
        self.view = view
        names = [distro.name for distro in view.distros]
        if previous is None or names != [distro.name for distro in previous.distros]:
            self._refresh_rows()
            return
        for row in range(self._table.rowCount()):
            distro = self._distro_at(row)
            if distro is not None:
                item = self._table.item(row, 1)
                if item is not None:
                    item.setText(state_label(distro))
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

    def _distro_at(self, row: int) -> Distro | None:
        if self.view is None or row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return self.view.find(name) if isinstance(name, str) else None

    def _selected(self) -> Distro | None:
        return self._distro_at(self._table.currentRow())

    def _refresh_rows(self) -> None:
        view = self.view
        selected = self._selected()
        self._rows = []
        self._table.setRowCount(0)
        if view is not None:
            self._table.setRowCount(len(view.distros))
            for row, distro in enumerate(view.distros):
                values = w.distro_row(distro, *self.sizes.get(distro.name, (None, False)))
                self._rows.append(values)
                for column, text in enumerate(values):
                    item = make_table_item(text)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, distro.name)
                        item.setToolTip(distro.name)
                    self._table.setItem(row, column, item)
        self._apply_column_widths(self._table.viewport().width())
        if selected is not None:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == selected.name:
                    self._table.selectRow(row)
                    break
        self._show_details()

    def _apply_column_widths(self, available: int) -> None:
        widths = w.column_widths(self._rows, available if available > 1 else None)
        for index, key in enumerate(_COLUMN_KEYS):
            self._table.setColumnWidth(index, widths[key])

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_table"):
            self._apply_column_widths(self._table.viewport().width())

    def _show_details(self) -> None:
        distro = self._selected()
        if distro is None:
            for value in self.detail_values.values():
                value.setText("—")
            self.detail_hint.setText("选择上方列表中的发行版查看详情")
            self.hover_label.setText("")
            self._update_action_states()
            return
        size, estimated = self.sizes.get(distro.name, (None, False))
        for label, value in w.detail_fields(distro, size, estimated):
            self.detail_values[label].setText(value)
        self.detail_hint.setText("")
        self.hover_label.setText(w.summary_text(distro, size, estimated))
        self._update_action_states()

    def _update_action_states(self) -> None:
        if self._busy:
            return
        distro = self._selected()
        can_start, can_stop = w.action_states(distro.state if distro is not None else None)
        self.start_button.setEnabled(can_start)
        self.stop_button.setEnabled(can_stop)

    def _log(self, text: str) -> None:
        self._log_view.append(f"[{time.strftime('%H:%M:%S')}] {text}")

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
            self.set_status(f"{distro.name} 已在运行")
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
        configured = self.download_dir.text().strip() or self.settings.download_dir
        if configured:
            return configured
        fallback = load_config().download_dir.strip()
        return fallback or str(Path.home() / "Downloads")

    def _download(self) -> None:
        entry = self._selected_image()
        if entry is None:
            QMessageBox.critical(self, "下载", "请先在列表中选择镜像")
            return
        directory_text = self.download_dir.text().strip()
        expected = self.expected_sha.text().strip() or None
        install_after = self.install_after.isChecked()
        mirror_policy = self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto"
        install_name = entry.name
        install_dir: Path | None = None
        if install_after:
            # 命名与安装位置必须在主线程问完：工作线程里不能再弹对话框。
            chosen_name, accepted = QInputDialog.getText(self, "下载后安装", "新发行版名称", text=entry.name)
            if not accepted or not chosen_name.strip():
                return
            chosen_dir = QFileDialog.getExistingDirectory(self, "选择安装目录（必须为空）")
            if not chosen_dir:
                return
            install_name, install_dir = chosen_name.strip(), Path(chosen_dir)

        def work(cancel: threading.Event) -> str:
            env = cli._build_environment()
            if not entry.downloads:
                # 只有 CLI / Store 来源：走 wsl --install，不下载归档。
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
            directory = Path(directory_text) if directory_text else Path(env.config.download_dir or ".")
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
            )
            summary = (
                f"已下载 {target}（{format_bytes(result_data.size)}，"
                f"来源 {_mirrors.label(result_data.source)}，sha256 {result_data.sha256}）"
            )
            if not install_after:
                return summary
            if install_dir is None:  # 上面已要求选择目录，这里只做类型收窄
                raise invalid_argument("未选择安装目录")
            if self._bridge is not None:
                self._bridge.message.emit(f"[下载] 下载完成，开始安装为 {install_name}…")
            problem = cli._dir_problem(install_dir)
            if problem:
                raise invalid_argument(
                    f"安装目录不可用：{problem}",
                    hint=f'删空或换目录后重试；也可只下载，再运行 fcmd wslman import {install_name} "{target}" --dir <空目录>',
                )
            view = cli._view(env)
            if view.find(install_name) is not None:
                raise invalid_argument(
                    f"发行版 {install_name} 已存在，未自动覆盖",
                    hint=(
                        f'如需替换：fcmd wslman import {install_name} "{target}" --dir "{install_dir}" '
                        "--replace --yes --backup <备份>"
                    ),
                )
            cli._run_step(
                env,
                ["--import", install_name, str(install_dir), str(target), "--version", "2"],
                action=f"安装 {install_name}",
                hint="目标目录必须为空；失败时用工具栏的「安装本地镜像…」重试",
                timeout=3600.0,
            )
            return f"{summary}；已安装为 {install_name}（{install_dir}），刷新后可在列表启动"

        self._start("下载", work)

    # ------------------------------------------------------------------ #
    # 镜像列表
    # ------------------------------------------------------------------ #
    def _on_mirror_changed(self, _index: int) -> None:
        if 0 <= self.mirror.currentIndex() < len(self.mirror_ids):
            self.set_status(f"下载源：{_mirrors.label(self.mirror_ids[self.mirror.currentIndex()])}")

    def _load_images(self) -> None:
        if self._images_busy or self.image_entries or self._closing:
            return
        self._images_busy = True
        self.images_status.setText("正在获取镜像清单…（wsl --list --online 首次较慢，可用缓存兜底）")
        submit(
            self._images_worker,
            on_done=self._apply_images,
            on_error=self._images_error,
            on_finished=lambda: setattr(self, "_images_busy", False),
        )

    @staticmethod
    def _images_worker() -> tuple[Any, tuple[ImageEntry, ...]]:
        env = cli._build_environment()
        catalog = _images.collect_images(
            fetch_cli=lambda: cli._online_rows(env),
            fetch_manifest=cli._manifest_images,
        )
        entries = _images.mark_local_state(
            catalog.entries,
            installed=cli._installed_names(env),
            backups=cli._backup_names(env),
        )
        return catalog, entries

    def _images_error(self, message: str) -> None:
        self.images_status.setText(f"镜像清单获取失败：{message}")
        self._log(f"[镜像列表] {message}")

    def _apply_images(self, payload: tuple[Any, tuple[ImageEntry, ...]]) -> None:
        catalog, entries = payload
        self.image_entries = entries
        stamp = time.strftime("%H:%M", time.localtime(catalog.fetched_at))
        marks = f"{len(entries)} 个镜像 · {(catalog.sources and '+'.join(catalog.sources)) or '缓存'} · {stamp}"
        if catalog.from_cache:
            marks += "（过期缓存）" if catalog.stale else "（缓存）"
        self.images_status.setText(marks)
        for warning in catalog.warnings:
            self._log(f"[镜像列表] {warning}")
        self._render_images()

    def _render_images(self) -> None:
        query = self.image_search.text().strip().casefold()
        keep = self._selected_image()
        self.image_table.setRowCount(0)
        rows = [
            entry
            for entry in self.image_entries
            if not query or query in entry.name.casefold() or query in entry.friendly_name.casefold()
        ]
        self.image_table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            for column, text in enumerate(w.image_row(entry)):
                item = make_table_item(text)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry.name)
                self.image_table.setItem(row, column, item)
        if keep is not None:
            for row in range(self.image_table.rowCount()):
                item = self.image_table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == keep.name:
                    self.image_table.selectRow(row)
                    break
        self._show_image_details()

    def _schedule_image_filter(self) -> None:
        if self._filter_timer is not None:
            self._filter_timer.stop()
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._render_images)
        self._filter_timer.start()

    def _selected_image(self) -> ImageEntry | None:
        row = self.image_table.currentRow()
        if row < 0:
            return None
        item = self.image_table.item(row, 0)
        name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return next((entry for entry in self.image_entries if entry.name == name), None)

    def _show_image_details(self) -> None:
        entry = self._selected_image()
        values = dict(w.image_details(entry)) if entry is not None else {}
        for label, widget in self.image_detail_values.items():
            widget.setText(values.get(label, "—"))
        self.image_url.setPlainText(values.get("下载地址", "—"))

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
                download_dir=self.download_dir.text().strip(),
                mirror=self.mirror_ids[self.mirror.currentIndex()] if self.mirror.currentIndex() >= 0 else "auto",
                sidebar_collapsed=self.nav.is_collapsed(),
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

    def __init__(self, owner: WslmanQtWindow, entries: tuple[ImageEntry, ...]) -> None:
        super().__init__(owner)
        self.setWindowTitle("安装本地镜像")
        self.setMinimumSize(820, 620)
        self._entries = entries
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

    def _scan(self) -> None:
        self.table.setRowCount(0)
        self._archives.clear()
        paths = w.local_archives(Path(self.directory.text().strip() or "."))
        for path in paths:
            self._add(w.inspect_archive(path, self._entries))
        if not self._archives:
            self._info["校验状态"].setText("该目录没有 .tar / .tar.gz / .tgz / .wsl 镜像包，可用「选择文件…」指定")
        elif self.table.rowCount():
            self.table.selectRow(0)

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
        self.target.setText(str(archive.path.parent / f"{archive.distro}-wsl"))

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
