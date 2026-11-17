"""devmanw 的 Qt 主窗口与入口。

窗口只做编排：安装 / 卸载 / 切换 / 升级 / PATH 集成全部调用
:mod:`fcmd.apps.devman._ops`（与 ``fcmd devman`` 共用的机读契约），GUI 不重写
任何安装逻辑；耗时操作都在后台线程执行，事件经信号桥回投 GUI 线程。

布局口径：左列「已安装」占 3 份高、「可用版本」占 2 份，右侧详情 / PATH 面板
固定较窄宽度并允许值文本换行；工具栏按刷新 / 版本操作 / PATH 集成三组排布，
任务进行中整组禁用并以不确定进度条提示；长路径在详情值里换行显示，底部只留
root 提示与状态结论。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.devman import _env, _ops
from fcmd.apps.devman._paths import TOOLS
from fcmd.apps.devman.devmanw import (
    MIN_HEIGHT,
    MIN_WIDTH,
    DevmanwSettings,
    _error_text,
    action_states,
    available_rows,
    detail_values,
    installed_rows,
    load_settings,
    path_lines,
    path_snapshot,
    save_settings,
    tool_iid,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, LogView, button, hint_label, make_splitter
from fcmd.gui.qt.workers import submit

__all__ = ["DevmanWindow", "run_qt"]

DEFAULT_SIZE = (1080, 720)

_DETAIL_FIELDS: tuple[str, ...] = ("工具", "版本", "状态", "活动", "版本目录", "可执行文件", "来源", "安装时间")
_PATH_FIELDS: tuple[str, ...] = ("用户 PATH", "当前终端", "条目", "写入记录", "会遮蔽")
_INSTALLED_HEADERS = ("工具 / 版本", "状态", "活动", "来源")
_INSTALLED_WIDTHS = (260, 84, 90, 120)
_AVAILABLE_HEADERS = ("可用版本", "已安装", "活动", "备注")
_AVAILABLE_WIDTHS = (200, 84, 90, 70)

_Emit = Callable[[tuple[Any, ...]], None]
_Work = Callable[[Callable[[str], None], Callable[[int, int], None], _Emit], str]


class _TaskBridge(QObject):  # pragma: no cover - Qt 信号桥
    """后台任务 → GUI 的事件桥。"""

    event = Signal(object)


def _work_task(work: _Work, bridge: _TaskBridge) -> None:
    """线程池里执行编排函数，把日志、进度、终态逐条投回界面。"""

    def emit_log(text: str) -> None:
        bridge.event.emit(("log", text))

    def emit_progress(done: int, total: int) -> None:
        bridge.event.emit(("progress", done, total))

    try:
        message = work(emit_log, emit_progress, bridge.event.emit)
    except Exception as exc:  # 后台线程不能把异常抛给界面
        bridge.event.emit(("error", _error_text(exc)))
        return
    bridge.event.emit(("done", message or ""))


class DevmanWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """工具栏 + 已安装/可用双列表 + 详情与 PATH 面板 + 日志 + 状态栏。"""

    def __init__(self, settings_path: Path | None = None, root_dir: str = "") -> None:
        self._settings = load_settings(settings_path)
        self._root_dir = root_dir
        self.current_tool = self._settings.tool
        self.snapshot: dict[str, Any] = {}
        self.path_state: dict[str, Any] = {}
        self.available: dict[str, dict[str, Any]] = {}
        self._rows: dict[str, dict[str, Any]] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._active_tree = "installed"
        self._busy = False
        self._label = ""
        self._syncing = False
        self._last_warnings: tuple[str, ...] = ()
        self._bridge: _TaskBridge | None = None
        super().__init__(
            title="开发环境管理 - devman",
            config_name="devmanw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist,
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._build()
        self._update_action_states()
        # 绑定 self 作为上下文：窗口在定时器触发前被销毁时自动取消，
        # 避免测试里跨用例触发已删除的 C++ 对象（Qt 会直接 abort）
        QTimer.singleShot(0, self, self.refresh)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self.add(self._build_toolbar())
        self.add(self._build_middle(), 1)
        self.add(self._build_footer())
        self.add(self._build_log_card())

    def _build_toolbar(self) -> QWidget:
        """刷新 / 版本操作 / PATH 集成三组按钮。"""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._buttons: dict[str, Any] = {}

        def add(key: str, text: str, slot: Callable[[], None], kind: str = "") -> None:
            widget = button(text, kind=kind)
            widget.clicked.connect(slot)
            layout.addWidget(widget)
            self._buttons[key] = widget

        add("refresh", "刷新", self.refresh)
        add("refresh_versions", "刷新可用版本", self.refresh_available)
        layout.addSpacing(10)
        add("install", "安装选中", self.install_selected, "primary")
        add("use", "切换", self.use_selected)
        add("upgrade", "升级", self.upgrade_tool)
        add("uninstall", "卸载", self.uninstall_selected, "danger")
        layout.addSpacing(10)
        add("init", "写入 PATH", self.init_path)
        add("uninit", "移除 PATH 集成", self.uninit_path, "danger")
        layout.addStretch(1)
        return row

    def _build_middle(self) -> QWidget:
        """左侧双列表，右侧详情 / PATH 面板。"""
        splitter = make_splitter(Qt.Orientation.Horizontal, sizes=(640, 440), parent=self)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        installed_card = Card("已安装（按工具分组）", left)
        self._installed_tree = _make_tree(installed_card, _INSTALLED_HEADERS, _INSTALLED_WIDTHS)
        self._installed_tree.setMinimumHeight(180)
        self._installed_tree.currentItemChanged.connect(self._on_installed_select)
        installed_card.body.addWidget(self._installed_tree)
        left_layout.addWidget(installed_card, 3)

        self._available_card = Card("可用版本", left)
        self._available_tree = _make_tree(self._available_card, _AVAILABLE_HEADERS, _AVAILABLE_WIDTHS)
        self._available_tree.setMinimumHeight(120)
        self._available_tree.currentItemChanged.connect(self._on_available_select)
        self._available_card.body.addWidget(self._available_tree)
        left_layout.addWidget(self._available_card, 2)
        splitter.addWidget(left)

        right = self._build_detail_card(splitter)
        right.setMinimumWidth(380)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return splitter

    def _build_detail_card(self, parent: QWidget) -> Card:
        """详情与 PATH 状态：两段只读字段，长值自动换行。"""
        card = Card("详情 / PATH 状态", parent)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self._detail_labels: dict[str, QLabel] = {}
        self._path_labels: dict[str, QLabel] = {}

        row = 0
        for label in _DETAIL_FIELDS:
            self._detail_labels[label] = _add_field(grid, row, label, card)
            row += 1
        separator = QWidget(card)
        separator.setFixedHeight(1)
        separator.setObjectName("Separator")
        grid.addWidget(separator, row, 0, 1, 2)
        row += 1
        for label in _PATH_FIELDS:
            self._path_labels[label] = _add_field(grid, row, label, card)
            row += 1
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        card.body.addStretch(1)
        return card

    def _build_footer(self) -> QWidget:
        """不确定进度条 + root 提示。"""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._progress = QProgressBar(row)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedWidth(160)
        self._root_label = hint_label("", wrap=False)
        layout.addWidget(self._progress)
        layout.addWidget(self._root_label, 1)
        return row

    def _build_log_card(self) -> Card:
        card = Card("日志", self)
        self._log_view = LogView(height=110, parent=card)
        card.body.addWidget(self._log_view)
        return card

    # ------------------------------------------------------------------ #
    # 任务编排
    # ------------------------------------------------------------------ #
    def _start(self, label: str, work: _Work) -> None:
        """提交后台任务；同一时刻只允许一个任务。"""
        if self._busy:
            QMessageBox.information(self, "请稍候", f"「{self._label}」仍在执行")
            return
        self._busy = True
        self._label = label
        self.set_status(f"{label}…")
        self._progress.setRange(0, 0)
        self._set_buttons(False)
        # 不挂到窗口下：窗口可能在后台任务结束前销毁，桥对象由任务参数保活，
        # 避免 worker 线程向已删除的 QObject 发信号（Qt 会堆损坏 / abort）
        bridge = _TaskBridge()
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(_work_task, work, bridge, on_error=self._on_worker_error)

    def _on_event(self, event: tuple[Any, ...]) -> None:
        kind = event[0]
        if kind == "log":
            self._log(str(event[1]))
            return
        if kind == "progress":
            _, done, total = event
            if total:
                self._progress.setRange(0, total)
                self._progress.setValue(done)
                self.set_status(f"{self._label}… {done * 100 // total}%")
            else:
                self.set_status(f"{self._label}… {done // (1024 * 1024)} MB")
            return
        if kind == "snapshot":
            self._apply_snapshot(event[1])
            return
        if kind == "available":
            self.available = event[1]
            self._render_available()
            self._update_details()
            self._update_action_states()
            return
        self._finish(kind, str(event[1]))

    def _finish(self, kind: str, payload: str) -> None:
        """任务结束：恢复进度 / 按钮，按成功 / 失败收尾。"""
        label = self._label
        self._busy = False
        self._bridge = None
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._set_buttons(True)
        if kind == "done":
            if payload:
                self._log(f"[{label}] {payload}")
            self.set_status(f"{label}完成")
            if label != "刷新":
                self.refresh()
            return
        self._log(f"[{label}] 失败：{payload}")
        self.set_status(f"{label}失败")
        QMessageBox.critical(self, label, payload)

    def _on_worker_error(self, message: str) -> None:
        """线程池兜底异常：按失败收尾。"""
        if not self._busy:
            return
        self._finish("error", message)

    def _set_buttons(self, enabled: bool) -> None:
        for widget in self._buttons.values():
            widget.setEnabled(enabled)
        if enabled:
            self._update_action_states()

    def _update_action_states(self) -> None:
        if self._busy:
            return
        row = self._selection()
        installed = (self.snapshot.get("tools") or {}).get(self.current_tool, {}).get("installed") or []
        states = action_states(
            busy=False,
            has_installed=bool(row and row.get("kind") == "installed"),
            installed_is_active=bool(row and row.get("kind") == "installed" and row.get("active")),
            has_available=bool(row and row.get("kind") == "available"),
            has_tool_versions=bool(installed),
            path_registered=bool(self.path_state.get("registered")),
        )
        for key, enabled in states.items():
            widget = self._buttons.get(key)
            if widget is not None:
                widget.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        def work(
            _log: Callable[[str], None],
            _progress: Callable[[int, int], None],
            emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            store = _env.default_env_store()
            emit(
                (
                    "snapshot",
                    {
                        "list": _ops.list_payload(ctx),
                        "registry": store.get("Path"),
                        "shadowed": _env.shadowed_commands(ctx.layout),
                        "recorded": list(ctx.state.path_added),
                        "tool_roots": {tool: str(ctx.layout.tool_root(tool)) for tool in TOOLS},
                    },
                )
            )
            return ""

        self._start("刷新", work)

    def refresh_available(self) -> None:
        def work(
            log: Callable[[str], None],
            _progress: Callable[[int, int], None],
            emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            payloads: dict[str, Any] = {}
            for tool in TOOLS:
                log(f"读取 {tool} 可用版本…")
                payloads[tool] = _ops.versions_payload(ctx, tool, remote=True)
            emit(("available", payloads))
            counts = "，".join(f"{tool} {len(payloads[tool].get('versions') or [])} 个" for tool in TOOLS)
            return f"可用版本已更新（{counts}）"

        self._start("刷新可用版本", work)

    def _apply_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        listing = dict(snapshot["list"])
        listing["tool_roots"] = snapshot["tool_roots"]
        self.snapshot = listing
        self.path_state = path_snapshot(
            registry_path=snapshot["registry"],
            entry=(listing.get("shims") or {}).get("dir", ""),
            recorded=snapshot["recorded"],
            shadowed=snapshot["shadowed"],
            shims=listing.get("shims") or {},
        )
        self._root_label.setText(f"root: {self.snapshot.get('root') or ''}")
        self._rows = {}
        self._render_installed()
        self._render_available()
        self._render_path()
        self._update_details()
        self._update_action_states()
        warnings = tuple(str(item) for item in self.snapshot.get("warnings") or [])
        if warnings != self._last_warnings:
            self._last_warnings = warnings
            for item in warnings:
                self._log(f"[warn] {item}")

    def _render_installed(self) -> None:
        """按工具分组重建已安装树，并尽量恢复原选中项。"""
        selected = self._installed_selected_iid()
        self._syncing = True
        self._installed_tree.clear()
        self._items.clear()
        for row in installed_rows(self.snapshot):
            self._rows[row["iid"]] = row
            if row["kind"] == "tool":
                item = QTreeWidgetItem([row["text"], *row["values"]])
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setData(0, Qt.ItemDataRole.UserRole, row)
                self._installed_tree.addTopLevelItem(item)
                item.setExpanded(True)
                self._items[row["iid"]] = item
            else:
                parent = self._items.get(tool_iid(row["tool"]))
                if parent is None:
                    continue
                item = QTreeWidgetItem([row["text"], *row["values"]])
                item.setData(0, Qt.ItemDataRole.UserRole, row)
                parent.addChild(item)
                self._items[row["iid"]] = item
        if selected is not None and selected in self._items:
            self._installed_tree.setCurrentItem(self._items[selected])
        self._syncing = False

    def _render_available(self) -> None:
        """重建当前工具的可用版本列表。"""
        self._available_card.title_label.setText(f"可用版本（{self.current_tool}）")
        selected = self._available_selected_iid()
        self._syncing = True
        self._available_tree.clear()
        payload = self.available.get(self.current_tool)
        if payload:
            for row in available_rows(payload):
                self._rows[row["iid"]] = row
                item = QTreeWidgetItem([row["text"], *row["values"]])
                item.setData(0, Qt.ItemDataRole.UserRole, row)
                self._available_tree.addTopLevelItem(item)
                self._items[row["iid"]] = item
        if selected is not None and selected in self._items:
            self._available_tree.setCurrentItem(self._items[selected])
        self._syncing = False

    def _render_path(self) -> None:
        for label, value in path_lines(self.path_state):
            widget = self._path_labels.get(label)
            if widget is not None:
                widget.setText(value)

    def _update_details(self) -> None:
        row = self._selection()
        payload = self.available.get(self.current_tool) or {}
        values = detail_values(row, self.snapshot, source=payload.get("source"))
        for label, value in values.items():
            widget = self._detail_labels.get(label)
            if widget is not None:
                widget.setText(value)

    # ------------------------------------------------------------------ #
    # 选中项
    # ------------------------------------------------------------------ #
    def _installed_selected_iid(self) -> str | None:
        if self._active_tree != "installed":
            return None
        item = self._installed_tree.currentItem()
        return _row_iid(item)

    def _available_selected_iid(self) -> str | None:
        if self._active_tree != "available":
            return None
        item = self._available_tree.currentItem()
        return _row_iid(item)

    def _selection(self) -> dict[str, Any] | None:
        tree = self._available_tree if self._active_tree == "available" else self._installed_tree
        item = tree.currentItem()
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _on_installed_select(self, current: Any, _previous: Any) -> None:
        if self._syncing or current is None:
            return
        self._active_tree = "installed"
        self._syncing = True
        self._available_tree.clearSelection()
        self._syncing = False
        data = current.data(0, Qt.ItemDataRole.UserRole)
        tool = data.get("tool") if isinstance(data, dict) else None
        if tool in TOOLS and tool != self.current_tool:
            self.current_tool = tool
            self._render_available()
        self._update_details()
        self._update_action_states()

    def _on_available_select(self, current: Any, _previous: Any) -> None:
        if self._syncing or current is None:
            return
        self._active_tree = "available"
        self._syncing = True
        self._installed_tree.clearSelection()
        self._syncing = False
        self._update_details()
        self._update_action_states()

    # ------------------------------------------------------------------ #
    # 操作
    # ------------------------------------------------------------------ #
    def install_selected(self) -> None:
        row = self._selection()
        if row is None or row.get("kind") != "available":
            self.set_status("请先在上方可用版本列表中选择一个版本")
            return
        tool = row["tool"]
        spec = f"{tool}@{row.get('key') or row['version']}"
        allow_shadow = self._shadow_gate()
        if allow_shadow is None:
            return
        label = f"安装 {tool} {row['version']}"

        def work(
            log: Callable[[str], None],
            progress: Callable[[int, int], None],
            _emit: _Emit,
        ) -> str:
            log(f"开始安装 {spec}…")
            ctx = _ops.new_context(self._root_dir or None)
            payload = _ops.install(ctx, spec, allow_shadow=bool(allow_shadow), progress=progress)
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                position = "追加" if user_path.get("position") == "append" else "前置"
                log(f"用户 PATH 已{position} {user_path.get('entry')}")
            return f"{payload.get('message') or '安装完成'}（{payload.get('path')}）"

        self._start(label, work)

    def uninstall_selected(self) -> None:
        row = self._selection()
        if row is None or row.get("kind") != "installed":
            self.set_status("请先在已安装列表中选择一个版本")
            return
        tool, name = row["tool"], row["name"]
        total = sum(len((self.snapshot.get("tools") or {}).get(item, {}).get("installed") or []) for item in TOOLS)
        lines = [f"将删除 {tool} {row['version']} 的版本目录：", str(row.get("path") or "")]
        if row.get("active"):
            lines.append("该版本是当前活动版本，活动标记会被清空。")
        if total <= 1:
            lines.append("这是最后一个版本，卸载后会从用户 PATH 移除 devman 写入的条目。")
        lines.append("已打开的终端不受影响。继续？")
        if not _confirm(self, "卸载版本", "\n".join(lines)):
            return

        def work(
            log: Callable[[str], None],
            _progress: Callable[[int, int], None],
            _emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            payload = _ops.uninstall(ctx, f"{tool}@{name}", yes=True, force=bool(row.get("active")))
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                log(f"用户 PATH 已精确移除 {user_path.get('removed')} 个 devman 条目")
            removed = "、".join(item["version"] for item in payload.get("removed") or [])
            return f"已卸载 {tool} {removed or row['version']}"

        self._start("卸载", work)

    def use_selected(self) -> None:
        row = self._selection()
        if row is None or row.get("kind") != "installed":
            self.set_status("请先在已安装列表中选择一个版本")
            return
        tool, name = row["tool"], row["name"]

        def work(
            _log: Callable[[str], None],
            _progress: Callable[[int, int], None],
            _emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            payload = _ops.use(ctx, f"{tool}@{name}")
            return f"{tool} 活动版本：{payload.get('active') or '（已清空）'}"

        self._start("切换", work)

    def upgrade_tool(self) -> None:
        tool = self.current_tool
        allow_shadow = self._shadow_gate()
        if allow_shadow is None:
            return

        def work(
            log: Callable[[str], None],
            progress: Callable[[int, int], None],
            _emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            payload = _ops.upgrade(ctx, tool, allow_shadow=bool(allow_shadow), progress=progress)
            if not payload.get("changed"):
                return str(payload.get("message") or "已是最新")
            log(f"{tool} {payload.get('current')} → {payload.get('target')}")
            return f"{tool} 已升级到 {payload.get('target')}"

        self._start("升级", work)

    def init_path(self) -> None:
        entry = str(self.path_state.get("entry") or "")
        shadowed = self.path_state.get("shadowed") or {}
        lines = [f"将把\n{entry}\n写入用户级 PATH（HKCU，无 UAC、幂等），并生成 shim。"]
        if shadowed:
            detail = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items())
            lines.append(f"前置后会在新终端里遮蔽现有命令：\n{detail}")
        lines.append("写入后需要重开终端才生效。继续？")
        if not _confirm(self, "写入用户 PATH", "\n\n".join(lines)):
            return

        def work(
            log: Callable[[str], None],
            _progress: Callable[[int, int], None],
            _emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            payload = _ops.init(ctx, allow_shadow=True)
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                log(f"用户 PATH 已写入：{user_path.get('entry')}")
            return f"shim 已就绪（{len(payload.get('shims_written') or [])} 个更新）"

        self._start("写入 PATH", work)

    def uninit_path(self) -> None:
        recorded = self.path_state.get("recorded") or []
        entry = str(self.path_state.get("entry") or "")
        lines = [
            f"将删除 shim 目录 {entry}",
            f"并从用户 PATH 精确移除 devman 写入的 {len(recorded)} 个条目；其余 PATH 内容不受影响。",
            "已打开的终端不受影响。继续？",
        ]
        if not _confirm(self, "移除 PATH 集成", "\n\n".join(lines)):
            return

        def work(
            log: Callable[[str], None],
            _progress: Callable[[int, int], None],
            _emit: _Emit,
        ) -> str:
            ctx = _ops.new_context(self._root_dir or None)
            payload = _ops.uninit(ctx)
            user_path = payload.get("user_path") or {}
            if user_path.get("changed"):
                log(f"用户 PATH 已精确移除 {user_path.get('removed')} 个条目")
            return f"已删除 shim {len(payload.get('removed_shims') or [])} 个"

        self._start("移除 PATH 集成", work)

    def _shadow_gate(self) -> bool | None:
        """返回 ``allow_shadow``；需要用户确认时弹窗，取消返回 ``None``。"""
        shadowed = self.path_state.get("shadowed") or {}
        if not shadowed or self.path_state.get("registered"):
            return False
        detail = "；".join(f"{tool}: {', '.join(items)}" for tool, items in shadowed.items())
        message = (
            f"devman 的 shim 目录会前置到用户 PATH，在新终端里遮蔽现有命令：\n\n{detail}\n\n确认要接管这些命令吗？"
        )
        return True if _confirm(self, "会遮蔽现有命令", message) else None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _log(self, text: str) -> None:
        from datetime import datetime

        self._log_view.append(f"{datetime.now():%H:%M:%S} {text}")

    def _confirm_close(self) -> bool:
        """任务进行中关闭时先确认。"""
        if not self._busy:
            return True
        return _confirm(self, "任务进行中", f"「{self._label}」仍在执行，直接退出？")

    def _persist(self, geometry: str) -> None:
        save_settings(
            DevmanwSettings(geometry=geometry, tool=self.current_tool),
            self._settings_path,
        )


def _make_tree(parent: QWidget, headers: tuple[str, ...], widths: tuple[int, ...]) -> QTreeWidget:
    """只读资源树：首列自适应，其余列固定宽度。"""
    tree = QTreeWidget(parent)
    tree.setHeaderLabels(list(headers))
    tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setUniformRowHeights(True)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column, width in enumerate(widths[1:], start=1):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        tree.setColumnWidth(column, width)
    return tree


def _add_field(grid: QGridLayout, row: int, label: str, parent: QWidget) -> QLabel:
    """详情区一行：灰色标签 + 可换行、可复制的值。"""
    grid.addWidget(hint_label(label, wrap=False), row, 0, Qt.AlignmentFlag.AlignTop)
    value = QLabel("—", parent)
    value.setWordWrap(True)
    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    grid.addWidget(value, row, 1)
    return value


def _row_iid(item: QTreeWidgetItem | None) -> str | None:
    if item is None:
        return None
    data = item.data(0, Qt.ItemDataRole.UserRole)
    return str(data.get("iid")) if isinstance(data, dict) else None


def _confirm(parent: QWidget, title: str, text: str) -> bool:
    """是 / 否确认框，默认焦点在「否」。"""
    answer = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def run_qt(root_dir: str = "") -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: DevmanWindow(root_dir=root_dir), app_name="devmanw")
