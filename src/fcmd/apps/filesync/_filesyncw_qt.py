"""filesyncw 的 Qt 主窗口与入口。

界面分三段：文件夹对（左右目录 + 交换）、比较与同步选项（模式、过滤、预演）、
结果区（比较结果表 + 日志，垂直分栏可拖动）。比较 / 同步仍在后台线程执行，
进度与结果经信号桥回投 GUI 线程，与后台队列事件语义一致。

布局口径：目录行是「输入框 + 浏览… + 整行省略路径提示」，结果表「相对路径」
列吸收剩余宽度（长路径省略、悬停看全路径），日志与结果表按 3:1 分栏并各自
保留最小高度，窗口变矮时优先压缩日志。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidgetItem,
    QWidget,
)

from fcmd import __version__
from fcmd.apps.filesync._core import (
    ACTION_NONE,
    ACTION_TEXT,
    CATEGORY_CONFLICT,
    CATEGORY_DIFFERENT,
    CATEGORY_EQUAL,
    CATEGORY_LEFT_ONLY,
    CATEGORY_RIGHT_ONLY,
    CATEGORY_TEXT,
    COMPARE_MODE_TEXT,
    COMPARE_MODES,
    SYNC_MODE_TEXT,
    SYNC_TWO_WAY,
    CompareOptions,
    CompareResult,
    SyncPlan,
    build_sync_plan,
    compare_trees,
    execute_plan,
    format_size,
    format_sync_report,
    parse_filter_text,
)
from fcmd.apps.filesync.filesyncw import (
    COMPARE_CHOICES,
    MIN_HEIGHT,
    MIN_WIDTH,
    SYNC_CHOICES,
    FilesyncSettings,
    load_settings,
    mode_label,
    mode_value,
    save_settings,
    settings_from_mapping,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    LogView,
    button,
    make_splitter,
    make_table,
)
from fcmd.gui.qt.workers import EventBridge, submit

__all__ = ["FilesyncWindow", "run_qt"]

DEFAULT_SIZE = (1120, 780)

_JSON_FILTER = "JSON 配置 (*.json);;所有文件 (*.*)"

_TAG_COLORS: dict[str, str] = {
    CATEGORY_LEFT_ONLY: "accent",
    CATEGORY_RIGHT_ONLY: "warning",
    CATEGORY_DIFFERENT: "danger",
    CATEGORY_EQUAL: "muted",
    CATEGORY_CONFLICT: "danger",
}
"""结果行的语义色：取调色板同名字段，切换主题后重新渲染即跟随。"""


class _FolderRow(QWidget):  # pragma: no cover - Qt 控件
    """一行目录输入：输入框 + 浏览按钮 + 整行省略路径提示。"""

    def __init__(self, *, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(2)
        self.edit = QLineEdit(value, self)
        self.edit.setPlaceholderText("选择文件夹")
        layout.addWidget(self.edit, 0, 0)
        self.browse = button("浏览…")
        self.browse.clicked.connect(self._pick)
        layout.addWidget(self.browse, 0, 1)
        self.hint = ElidedLabel("", parent=self)
        self.hint.setObjectName("Mono")
        layout.addWidget(self.hint, 1, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        self.edit.textChanged.connect(self._refresh_hint)
        self._refresh_hint(self.edit.text())

    def value(self) -> str:
        """当前输入文本（去首尾空白）。"""
        return self.edit.text().strip()

    def set_value(self, text: str) -> None:
        """写入输入文本。"""
        self.edit.setText(text)

    def _refresh_hint(self, text: str) -> None:
        self.hint.setText(str(Path(text).expanduser()) if text.strip() else "未选择")

    def _pick(self) -> None:
        """选择目录并写回输入框。"""
        directory = QFileDialog.getExistingDirectory(self, "选择文件夹", self.value())
        if directory:
            self.edit.setText(directory)


def _work_compare(
    left: Path,
    right: Path,
    options: CompareOptions,
    mode: str,
    bridge: EventBridge,
) -> None:
    """线程池里执行比较并生成计划。"""
    try:
        result = compare_trees(left, right, options)
        plan = build_sync_plan(result, mode)
    except (OSError, ValueError) as exc:
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("compare_done", (result, plan)))


def _work_sync(  # noqa: PLR0913, PLR0917 - 参数与界面选项一一对应
    left: Path,
    right: Path,
    options: CompareOptions,
    mode: str,
    dry_run: bool,
    bridge: EventBridge,
) -> None:
    """线程池里执行比较 + 同步，逐项进度投回界面。"""
    try:
        result = compare_trees(left, right, options)
        plan = build_sync_plan(result, mode)
        report = execute_plan(
            plan,
            dry_run=dry_run,
            progress=lambda done, total, action: bridge.event.emit(("progress", (done, total, action))),
        )
    except (OSError, ValueError) as exc:
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("sync_done", (result, plan, report)))


class FilesyncWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """文件夹比较与同步主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._settings_override = settings_path
        self._busy = False
        self._result: CompareResult | None = None
        self._plan: SyncPlan | None = None
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"filesync - 文件夹比较与同步 v{__version__}",
            config_name="filesyncw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist,
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._build()
        self._install_menu_actions()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """文件夹对 / 选项 / 结果与日志。"""
        self.add(self._build_folders_card())
        self.add(self._build_options_card())
        self.add(self._build_results_splitter(), 1)
        self._append_log("选择左右目录后点击「比较」，确认结果与计划再执行「同步」。")

    def _build_folders_card(self) -> Card:
        """左右目录两行，右侧「交换左右」纵向居中。"""
        card = Card("文件夹对", self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self._left_row = _FolderRow(value=self._settings.left, parent=card)
        self._right_row = _FolderRow(value=self._settings.right, parent=card)
        grid.addWidget(QLabel("左侧：", card), 0, 0)
        grid.addWidget(self._left_row, 0, 1)
        grid.addWidget(QLabel("右侧：", card), 1, 0)
        grid.addWidget(self._right_row, 1, 1)
        self._swap_button = button("交换左右")
        self._swap_button.clicked.connect(self._swap)
        grid.addWidget(self._swap_button, 0, 2, 2, 1, Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(1, 1)
        card.body.addLayout(grid)
        return card

    def _build_options_card(self) -> Card:
        """比较 / 同步模式、过滤与三个动作。"""
        card = Card("比较与同步", self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._compare = QComboBox(card)
        self._compare.addItems(list(COMPARE_CHOICES))
        self._compare.setCurrentText(mode_label(self._settings.compare_mode, COMPARE_MODE_TEXT))
        self._sync = QComboBox(card)
        self._sync.addItems(list(SYNC_CHOICES))
        self._sync.setCurrentText(mode_label(self._settings.sync_mode, SYNC_MODE_TEXT))
        self._sync.currentTextChanged.connect(self._on_sync_mode_change)
        self._skip_hidden = QCheckBox("跳过隐藏文件", card)
        self._skip_hidden.setChecked(self._settings.skip_hidden)
        self._show_equal = QCheckBox("显示相同项", card)
        self._show_equal.setChecked(self._settings.show_equal)
        self._show_equal.toggled.connect(self._render_result)
        self._include = QLineEdit(self._settings.include, card)
        self._include.setPlaceholderText("包含模式（分号分隔，如 *.py;*.md）")
        self._exclude = QLineEdit(self._settings.exclude, card)
        self._exclude.setPlaceholderText("排除模式（分号分隔）")

        grid.addWidget(QLabel("比较：", card), 0, 0)
        grid.addWidget(self._compare, 0, 1)
        grid.addWidget(QLabel("同步：", card), 0, 2)
        grid.addWidget(self._sync, 0, 3)
        grid.addWidget(self._skip_hidden, 0, 4)
        grid.addWidget(self._show_equal, 0, 5)
        grid.addWidget(QLabel("包含：", card), 1, 0)
        grid.addWidget(self._include, 1, 1, 1, 3)
        grid.addWidget(QLabel("排除：", card), 1, 4)
        grid.addWidget(self._exclude, 1, 5)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(5, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._compare_button = button("比较", kind="primary")
        self._compare_button.clicked.connect(self._start_compare)
        self._sync_button = button("同步")
        self._sync_button.clicked.connect(self._start_sync)
        self._dry_run = QCheckBox("预演（不写入）", card)
        self._dry_run.setChecked(True)
        actions.addWidget(self._compare_button)
        actions.addWidget(self._sync_button)
        actions.addWidget(self._dry_run)
        actions.addStretch(1)
        grid.addLayout(actions, 2, 0, 1, 6)
        card.body.addLayout(grid)
        return card

    def _build_results_splitter(self) -> QWidget:
        """结果表与日志的垂直分栏（结果占大头，日志保底高度）。"""
        results = Card("比较结果", self)
        self._table = make_table(
            ("状态", "相对路径", "计划动作", "左侧大小", "右侧大小"),
            widths=(90, 460, 130, 110, 110),
            stretch_columns=(1,),
            parent=results,
        )
        self._table.setMinimumHeight(200)
        results.body.addWidget(self._table)

        log_card = Card("日志", self)
        self._log = LogView(height=140, parent=log_card)
        self._log.setMinimumHeight(110)
        log_card.body.addWidget(self._log)

        splitter = make_splitter(Qt.Orientation.Vertical, sizes=(520, 200), parent=self)
        splitter.addWidget(results)
        splitter.addWidget(log_card)
        return splitter

    def _install_menu_actions(self) -> None:
        """在帮助菜单「关于」之前插入「载入配置… / 保存配置…」。"""
        help_menu = self._menu("帮助")
        actions = help_menu.actions()
        about = actions[-1] if actions else None
        load_action = self._make_action("载入配置…", self._load_profile)
        save_action = self._make_action("保存配置…", self._save_profile)
        if about is not None:
            help_menu.insertAction(about, load_action)
            help_menu.insertAction(about, save_action)
            help_menu.insertSeparator(about)
        else:
            help_menu.addAction(load_action)
            help_menu.addAction(save_action)

    def _menu(self, title: str) -> QMenu:
        """按标题取出骨架菜单。"""
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is not None and action.text() == title:
                return menu
        raise LookupError(f"缺少菜单：{title}")

    def _make_action(self, text: str, handler: Any) -> QAction:
        """创建菜单动作并连接处理函数。"""
        action = QAction(text, self)
        action.triggered.connect(handler)
        return action

    def set_theme(self, theme: Any) -> None:
        """切换主题后重渲染结果表，让语义行色跟随新调色板。"""
        super().set_theme(theme)
        if hasattr(self, "_table"):
            self._render_result()

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _swap(self) -> None:
        """交换左右目录。"""
        left = self._left_row.value()
        self._left_row.set_value(self._right_row.value())
        self._right_row.set_value(left)

    def _on_sync_mode_change(self, *_args: str) -> None:
        """切换同步模式后，用缓存结果即时重算计划动作列。"""
        if self._result is None:
            return
        self._plan = build_sync_plan(self._result, self._sync_mode())
        self._render_result()

    def _compare_mode(self) -> str:
        return mode_value(self._compare.currentText(), COMPARE_MODE_TEXT, COMPARE_MODES[0])

    def _sync_mode(self) -> str:
        return mode_value(self._sync.currentText(), SYNC_MODE_TEXT, SYNC_TWO_WAY)

    def _collect_options(self) -> CompareOptions:
        """把界面选项转为比较选项。"""
        return CompareOptions(
            mode=self._compare_mode(),
            include=parse_filter_text(self._include.text()),
            exclude=parse_filter_text(self._exclude.text()),
            skip_hidden=self._skip_hidden.isChecked(),
        )

    def _validate(self) -> tuple[Path, Path, CompareOptions] | None:
        """校验左右目录；无效时弹窗并返回 ``None``。"""
        left = Path(self._left_row.value()).expanduser()
        right = Path(self._right_row.value()).expanduser()
        if not left.is_dir() or not right.is_dir():
            QMessageBox.critical(self, "路径无效", "请选择存在的左侧和右侧文件夹。")
            return None
        if left.resolve() == right.resolve():
            QMessageBox.critical(self, "路径无效", "左右目录不能相同。")
            return None
        return left, right, self._collect_options()

    def _append_log(self, message: str) -> None:
        """追加一行带时间戳的日志。"""
        from datetime import datetime

        self._log.append(f"{datetime.now():%H:%M:%S} {message}")

    def _set_busy(self, busy: bool) -> None:
        """运行期间禁用动作按钮，避免重复提交。"""
        self._busy = busy
        for widget in (self._compare_button, self._sync_button):
            widget.setEnabled(not busy)

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _start_compare(self) -> None:
        if self._busy:
            return
        target = self._validate()
        if target is None:
            return
        left, right, options = target
        self._log.clear()
        self._set_busy(True)
        self.set_status("正在比较…")
        self._append_log("正在比较…")
        bridge = self._new_bridge()
        submit(_work_compare, left, right, options, self._sync_mode(), bridge, on_error=self._on_worker_error)

    def _start_sync(self) -> None:
        if self._busy:
            return
        target = self._validate()
        if target is None:
            return
        left, right, options = target
        mode = self._sync_mode()
        dry_run = self._dry_run.isChecked()
        if not dry_run:
            label = mode_label(mode, SYNC_MODE_TEXT)
            answer = QMessageBox.question(
                self,
                "确认同步",
                f"将按「{label}」模式同步：\n{left}\n↔ {right}\n\n可能覆盖或删除文件，是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._log.clear()
        self._set_busy(True)
        self.set_status("正在同步…")
        self._append_log("正在同步…")
        bridge = self._new_bridge()
        submit(
            _work_sync,
            left,
            right,
            options,
            mode,
            dry_run,
            bridge,
            on_error=self._on_worker_error,
        )

    def _new_bridge(self) -> EventBridge:
        """创建并连接后台事件桥（任务结束前保持引用）。"""
        bridge = EventBridge()
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        return bridge

    def _on_worker_error(self, message: str) -> None:
        """线程池兜底异常：走同一条错误收尾路径（错误信息含异常类型）。"""
        self._on_event(("error", message))

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：进度、完成或失败。"""
        kind = event[0]
        payload = event[1] if len(event) > 1 else None
        if kind == "progress" and isinstance(payload, tuple):
            done, total, action = payload
            self._append_log(f"[{done}/{total}] {action.action_text} {action.relative}")
        elif kind == "compare_done" and isinstance(payload, tuple):
            result, plan = payload
            self._apply_result(result, plan)
            self._append_log(f"比较完成：{self._summary(result, plan)}")
            self.set_status("比较完成")
            self._finish()
        elif kind == "sync_done" and isinstance(payload, tuple):
            result, plan, report = payload
            self._apply_result(result, plan)
            self._append_log(format_sync_report(report))
            self.set_status("同步完成（有失败）" if report.errors else "同步完成")
            self._finish()
        elif kind == "error" and payload is not None:
            message = str(payload)
            self._append_log(f"错误：{message}")
            self.set_status("操作失败")
            self._finish()
            QMessageBox.critical(self, "操作失败", message)

    def _apply_result(self, result: CompareResult, plan: SyncPlan) -> None:
        """缓存比较结果与计划并刷新表格。"""
        self._result = result
        self._plan = plan
        self._render_result()

    def _finish(self) -> None:
        """任务结束：恢复按钮并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 结果渲染
    # ------------------------------------------------------------------ #
    def _render_result(self, *_args: Any) -> None:
        """按缓存结果刷新表格（「显示相同项」切换与主题切换时复用）。"""
        self._table.setRowCount(0)
        if self._result is None:
            return
        actions = {action.relative: action for action in self._plan.actions} if self._plan else {}
        conflicts = {item.relative for item in self._plan.conflicts} if self._plan else set()
        palette = self.theme().palette
        show_equal = self._show_equal.isChecked()
        for item in self._result.items:
            if item.category == CATEGORY_EQUAL and not show_equal:
                continue
            action = actions.get(item.relative)
            if action is not None:
                action_text = action.action_text
            elif item.relative in conflicts:
                action_text = "冲突（跳过）"
            else:
                action_text = ACTION_TEXT[ACTION_NONE]
            tag = CATEGORY_CONFLICT if item.relative in conflicts else item.category
            color_name = _TAG_COLORS.get(tag, "text")
            color = QColor(getattr(palette, color_name))
            values = (
                CATEGORY_TEXT[item.category],
                item.relative,
                action_text,
                format_size(item.left.size) if item.left is not None else "-",
                format_size(item.right.size) if item.right is not None else "-",
            )
            row = self._table.rowCount()
            self._table.insertRow(row)
            for column, text in enumerate(values):
                cell = QTableWidgetItem(text)
                cell.setForeground(color)
                if column >= 3:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if column == 1:
                    cell.setToolTip(item.relative)
                self._table.setItem(row, column, cell)

    @staticmethod
    def _summary(result: CompareResult, plan: SyncPlan) -> str:
        """生成一行概要：差异数量 + 计划动作数。"""
        counts = result.counts()
        return (
            f"共 {len(result.items)} 项：仅左 {counts[CATEGORY_LEFT_ONLY]}，"
            f"仅右 {counts[CATEGORY_RIGHT_ONLY]}，不同 {counts[CATEGORY_DIFFERENT]}，"
            f"相同 {counts[CATEGORY_EQUAL]}；计划复制 {plan.copy_count}，"
            f"删除 {plan.delete_count}，冲突 {len(plan.conflicts)}"
        )

    # ------------------------------------------------------------------ #
    # 配置文件
    # ------------------------------------------------------------------ #
    def _load_profile(self) -> None:
        """从 JSON 文件载入左右目录与选项。"""
        selected, _ = QFileDialog.getOpenFileName(self, "载入配置", "", _JSON_FILTER)
        if not selected:
            return
        raw = _guiconfig.read_settings(Path(selected))
        if raw is None:
            QMessageBox.critical(self, "载入配置", "配置文件无法解析。")
            return
        loaded = settings_from_mapping(raw)
        self._left_row.set_value(loaded.left)
        self._right_row.set_value(loaded.right)
        self._compare.setCurrentText(mode_label(loaded.compare_mode, COMPARE_MODE_TEXT))
        self._sync.setCurrentText(mode_label(loaded.sync_mode, SYNC_MODE_TEXT))
        self._include.setText(loaded.include)
        self._exclude.setText(loaded.exclude)
        self._skip_hidden.setChecked(loaded.skip_hidden)
        self._show_equal.setChecked(loaded.show_equal)
        self._append_log(f"已载入配置：{selected}")

    def _save_profile(self) -> None:
        """把当前左右目录与选项保存到 JSON 文件。"""
        selected, _ = QFileDialog.getSaveFileName(self, "保存配置", "filesync.json", _JSON_FILTER)
        if not selected:
            return
        if not Path(selected).suffix:
            selected = f"{selected}.json"
        save_settings(self._current_settings(self._settings.geometry), Path(selected))
        self._append_log(f"已保存配置：{selected}")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _current_settings(self, geometry: str | None) -> FilesyncSettings:
        """收集当前界面状态。"""
        return FilesyncSettings(
            left=self._left_row.edit.text(),
            right=self._right_row.edit.text(),
            compare_mode=self._compare_mode(),
            sync_mode=self._sync_mode(),
            include=self._include.text(),
            exclude=self._exclude.text(),
            skip_hidden=self._skip_hidden.isChecked(),
            show_equal=self._show_equal.isChecked(),
            geometry=geometry,
        )

    def _confirm_close(self) -> bool:
        """运行中关闭时先确认。"""
        if not self._busy:
            return True
        answer = QMessageBox.question(
            self,
            "退出",
            "任务正在运行，确定退出？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _persist(self, geometry: str) -> None:
        """关闭时写回目录、模式、过滤与窗口 geometry。"""
        save_settings(self._current_settings(geometry), self._settings_path)


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: FilesyncWindow(settings_path), app_name="filesyncw")
