"""filesyncw 的 Qt 主窗口与入口。

左侧边栏分页（与 filescanw / wslmanw 同一套 ``SidebarNav``），比较与结果分开：

- **比较**：文件夹对 + 比较方式 + 包含 / 排除 / 跳过隐藏文件，底部执行比较；
- **结果**：差异表 + 同步方式 / 预演 / 执行同步（比较完成后自动切到本页）。

布局口径：文件夹对复用公共 ``StoragePairCard`` — 左右各一张磁盘卡（容量 / 更改 /
打开，与 filescanw 扫描目录同源），「交换左右」放在两张卡中间；结果页表格吃满
页面高度，「相对路径」列吸收剩余宽度（长路径省略、悬停看全路径）。日志统一走
「帮助 → 任务日志…」。
"""

from __future__ import annotations

import time
from dataclasses import replace
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
from fcmd.gui.app import run_window
from fcmd.gui.page import AppPage, PageSpec
from fcmd.gui.widgets import (
    Card,
    PageScroll,
    StoragePairCard,
    button,
    confirm,
    make_table,
)
from fcmd.gui.window import QtMainWindow
from fcmd.gui.workers import EventBridge, submit

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


PAGES: tuple[PageSpec, ...] = (
    PageSpec("compare", "比较", "左右目录、比较方式与过滤；执行比较", "search"),
    PageSpec("results", "结果", "差异表、同步方式与执行同步", "check-square"),
)
"""侧边栏页顺序：比较与结果分开，与 filescanw / wslmanw 同一套分页。"""


class _FilesyncPage(AppPage):  # pragma: no cover - Qt 控件
    """filesync 的页面骨架：滚动表单页 / 满高结果页 + 页面级状态栏。"""

    def __init__(
        self,
        state: object,
        key: str,
        *,
        scroll: bool = True,
        margins: tuple[int, int, int, int] = (0, 0, 4, 8),
        spacing: int = 8,
    ) -> None:
        super().__init__(state, spacing=0)
        self.PAGE_KEY = key
        if scroll:
            self.scroll: PageScroll | None = PageScroll(self, margins=margins)
            self.body.addWidget(self.scroll, 1)
            self.content = self.scroll.body
            self.content.setSpacing(spacing)
        else:
            self.scroll = None
            self.body.setContentsMargins(*margins)
            self.content = self.body


class FilesyncWindow(QtMainWindow):  # pragma: no cover - 需要 Qt 事件循环
    """文件夹比较与同步主窗口：侧边栏分页（比较 / 结果）+ 状态栏 + 任务日志。"""

    log_hint = "汇总比较结果、同步计划与逐项动作；复制、清空、导出见日志工具条。"

    def format_log_line(self, message: str) -> str:
        """任务日志前缀：``[HH:MM:SS]``。"""
        return f"[{time.strftime('%H:%M:%S')}] {message}"

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._busy = False
        self._result: CompareResult | None = None
        self._plan: SyncPlan | None = None
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"filesync - 文件夹比较与同步 v{__version__}",
            config_name="filesyncw",
            nav_title="文件夹同步",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist,
            sidebar_collapsed=self._settings.sidebar_collapsed,
            settings_path=settings_path,
        )
        # 两页都先建好：设置回填与比较完成后的表格渲染都要立即读写控件。
        self._compare_page = self._build_compare_page()
        self._results_page = self._build_results_page()
        self.add_pages(
            PAGES,
            {
                "compare": lambda: self._compare_page,
                "results": lambda: self._results_page,
            },
        )
        self.nav.collapsedChanged.connect(self._persist_sidebar_collapsed)
        self._install_menu_actions()
        self.set_page_status("compare", "就绪")
        self.set_page_status("results", "还没有比较结果")
        self.log("选择左右目录后点击「比较」，确认结果与计划再执行「同步」。")

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_compare_page(self) -> _FilesyncPage:
        """比较页：文件夹对 + 比较方式与过滤 + 执行比较。"""
        page = _FilesyncPage(self, "compare", scroll=True)
        body = page.content

        # 文件夹对沿用 filescanw 的磁盘卡外观；交换按钮放在左右两卡中间。
        self._pair_card = StoragePairCard(
            self._settings.left,
            self._settings.right,
            title="文件夹对",
            change_text="更改",
            dialog_title="选择文件夹",
            parent=page,
        )
        body.addWidget(self._pair_card)

        options = Card("比较方式", page)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._compare = QComboBox(options)
        self._compare.addItems(list(COMPARE_CHOICES))
        self._compare.setCurrentText(mode_label(self._settings.compare_mode, COMPARE_MODE_TEXT))
        self._skip_hidden = QCheckBox("跳过隐藏文件", options)
        self._skip_hidden.setChecked(self._settings.skip_hidden)
        self._include = QLineEdit(self._settings.include, options)
        self._include.setPlaceholderText("包含模式（分号分隔，如 *.py;*.md）")
        self._exclude = QLineEdit(self._settings.exclude, options)
        self._exclude.setPlaceholderText("排除模式（分号分隔）")

        grid.addWidget(QLabel("比较：", options), 0, 0)
        grid.addWidget(self._compare, 0, 1)
        grid.addWidget(self._skip_hidden, 0, 2)
        grid.addWidget(QLabel("包含：", options), 1, 0)
        grid.addWidget(self._include, 1, 1)
        grid.addWidget(QLabel("排除：", options), 1, 2)
        grid.addWidget(self._exclude, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        options.body.addLayout(grid)
        body.addWidget(options)

        actions_card = Card("执行比较", page)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self._compare_button = button("比较", kind="primary", icon="search")
        self._compare_button.clicked.connect(self._start_compare)
        actions.addWidget(self._compare_button)
        actions.addStretch(1)
        actions_card.body.addLayout(actions)
        body.addWidget(actions_card)
        body.addStretch(1)
        return page

    def _build_results_page(self) -> _FilesyncPage:
        """结果页：差异表吃满高度，同步方式 / 预演 / 执行同步在表格上方。"""
        page = _FilesyncPage(self, "results", scroll=False, spacing=6)
        body = page.content
        results = Card("比较结果", page)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        self._show_equal = QCheckBox("显示相同项", results)
        self._show_equal.setChecked(self._settings.show_equal)
        toolbar.addWidget(self._show_equal)
        toolbar.addStretch(1)
        toolbar.addWidget(QLabel("同步：", results))
        self._sync = QComboBox(results)
        self._sync.addItems(list(SYNC_CHOICES))
        self._sync.setCurrentText(mode_label(self._settings.sync_mode, SYNC_MODE_TEXT))
        self._sync.currentTextChanged.connect(self._on_sync_mode_change)
        toolbar.addWidget(self._sync)
        self._dry_run = QCheckBox("预演（不写入）", results)
        self._dry_run.setChecked(True)
        toolbar.addWidget(self._dry_run)
        self._sync_button = button("同步", icon="transfer")
        self._sync_button.clicked.connect(self._start_sync)
        toolbar.addWidget(self._sync_button)
        results.body.addLayout(toolbar)

        self._table = make_table(
            ("状态", "相对路径", "计划动作", "左侧大小", "右侧大小"),
            widths=(90, 460, 130, 110, 110),
            stretch_columns=(1,),
            parent=results,
        )
        self._table.setMinimumHeight(200)
        results.body.addWidget(self._table, 1)
        self._show_equal.toggled.connect(self._render_result)
        body.addWidget(results, 1)
        return page

    def _install_menu_actions(self) -> None:
        """在帮助菜单「关于」之前插入「载入配置… / 保存配置…」。"""
        actions = self.help_menu.actions()
        # 应用自己的帮助项排在公共「任务日志…」之前，最后仍以「关于」收尾。
        anchor = self.log_action or (actions[-1] if actions else None)
        load_action = self._make_action("载入配置…", self._load_profile)
        save_action = self._make_action("保存配置…", self._save_profile)
        if anchor is not None:
            self.help_menu.insertAction(anchor, load_action)
            self.help_menu.insertAction(anchor, save_action)
        else:
            self.help_menu.addAction(load_action)
            self.help_menu.addAction(save_action)

    def _menu(self, title: str) -> QMenu:
        """按标题取出骨架菜单（目前只有帮助菜单需要补项）。"""
        if title == "帮助":
            return self.help_menu
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
        """交换左右目录（组件内完成，中间按钮直接调 ``swap()``）。"""
        self._pair_card.swap()

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
        left_text, right_text = self._pair_card.sides()
        left = Path(left_text).expanduser()
        right = Path(right_text).expanduser()
        if not left.is_dir() or not right.is_dir():
            QMessageBox.critical(self, "路径无效", "请选择存在的左侧和右侧文件夹。")
            return None
        if left.resolve() == right.resolve():
            QMessageBox.critical(self, "路径无效", "左右目录不能相同。")
            return None
        return left, right, self._collect_options()

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
        self.clear_log()
        self._set_busy(True)
        self.set_page_status("compare", "正在比较…")
        self.set_page_status("results", "正在比较…")
        self.log("正在比较…")
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
            if not confirm(
                self,
                "确认同步",
                f"将按「{label}」模式同步：\n{left}\n↔ {right}\n\n可能覆盖或删除文件，是否继续？",
            ):
                return
        self.clear_log()
        self._set_busy(True)
        self.set_page_status("results", "正在同步…")
        self.set_page_status("compare", "正在同步…")
        self.log("正在同步…")
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
            self.log(f"[{done}/{total}] {action.action_text} {action.relative}")
        elif kind == "compare_done" and isinstance(payload, tuple):
            result, plan = payload
            self._apply_result(result, plan)
            self.log(f"比较完成：{self._summary(result, plan)}")
            self.set_page_status("compare", "比较完成")
            self.set_page_status("results", "比较完成")
            self.show_page("results")
            self._finish()
        elif kind == "sync_done" and isinstance(payload, tuple):
            result, plan, report = payload
            self._apply_result(result, plan)
            self.log(format_sync_report(report))
            text = "同步完成（有失败）" if report.errors else "同步完成"
            self.set_page_status("results", text)
            self.set_page_status("compare", text)
            self._finish()
        elif kind == "error" and payload is not None:
            message = str(payload)
            self.log(f"错误：{message}")
            self.set_page_status("results", "操作失败")
            self.set_page_status("compare", "操作失败")
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
        self._pair_card.set_sides(loaded.left, loaded.right)
        self._compare.setCurrentText(mode_label(loaded.compare_mode, COMPARE_MODE_TEXT))
        self._sync.setCurrentText(mode_label(loaded.sync_mode, SYNC_MODE_TEXT))
        self._include.setText(loaded.include)
        self._exclude.setText(loaded.exclude)
        self._skip_hidden.setChecked(loaded.skip_hidden)
        self._show_equal.setChecked(loaded.show_equal)
        self.log(f"已载入配置：{selected}")

    def _save_profile(self) -> None:
        """把当前左右目录与选项保存到 JSON 文件。"""
        selected, _ = QFileDialog.getSaveFileName(self, "保存配置", "filesync.json", _JSON_FILTER)
        if not selected:
            return
        if not Path(selected).suffix:
            selected = f"{selected}.json"
        save_settings(self._current_settings(self._settings.geometry), Path(selected))
        self.log(f"已保存配置：{selected}")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _current_settings(self, geometry: str | None) -> FilesyncSettings:
        """收集当前界面状态。"""
        left, right = self._pair_card.sides()
        return FilesyncSettings(
            left=left,
            right=right,
            compare_mode=self._compare_mode(),
            sync_mode=self._sync_mode(),
            include=self._include.text(),
            exclude=self._exclude.text(),
            skip_hidden=self._skip_hidden.isChecked(),
            show_equal=self._show_equal.isChecked(),
            geometry=geometry,
            sidebar_collapsed=self._settings.sidebar_collapsed,
        )

    def _confirm_close(self) -> bool:
        """运行中关闭时先确认。"""
        if not self._busy:
            return True
        return confirm(self, "退出", "任务正在运行，确定退出？")

    def _persist(self, geometry: str) -> None:
        """关闭时写回目录、模式、过滤、侧边栏折叠状态与窗口 geometry。"""
        save_settings(self._current_settings(geometry), self._own_settings_path)

    def _persist_sidebar_collapsed(self, collapsed: bool) -> None:
        """侧边栏折叠状态随窗口设置落盘（与 filescanw / wslmanw 同一口径）。"""
        self._settings = replace(self._settings, sidebar_collapsed=collapsed)
        self._persist(self.geometry_text())


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: FilesyncWindow(settings_path), app_name="filesyncw")
