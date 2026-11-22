"""mirrortoolw 的 Qt 主窗口与入口。

界面分四段：测速目标（模式 / 服务 / 超时 / 来源 / 搜索 / 配置位置）、动作行
（应用选中 / 应用推荐 / 开始测速）、结果表（排名 / 镜像 / 地址 / 状态 / 延迟 /
吞吐）、日志。探测与应用都在后台线程执行，结果经信号桥回投 GUI 线程。

布局口径：结果表「地址」列吸收剩余宽度，长地址右省略并悬停看全路径；「状态」
列文本较长时同样省略并带 tooltip；站点模式隐藏「吞吐」列并把「镜像」表头换成
「站点」；结果表与日志按 3:2 分配高度，窗口变矮时优先压缩日志。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QWidget,
)

from fcmd import __version__
from fcmd.gui import config as _guiconfig
from fcmd.gui.app import run_window
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.widgets import Card, ElidedLabel, ProgressRow, button, make_table, make_table_item
from fcmd.gui.workers import EventBridge, submit
from fcmd.tools.dev.mirrortoolw import (
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    MIN_HEIGHT,
    MIN_TIMEOUT,
    MIN_WIDTH,
    MODE_SERVICE,
    MODE_SITES,
    SERVICE_LABELS,
    SOURCE_LABELS,
    MirrorResult,
    MirrorToolSettings,
    apply_service_mirror,
    config_location,
    filter_results,
    load_settings,
    normalize_timeout,
    probe_service,
    probe_sites,
    recommend_mirror,
    save_settings,
)

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

__all__ = ["MirrorToolWindow", "run_qt"]

DEFAULT_SIZE = (1040, 720)

_TABLE_HEADERS = ("排名", "镜像", "地址", "状态", "延迟", "吞吐")
_TABLE_WIDTHS = (50, 120, 420, 150, 90, 110)
_THROUGHPUT_COLUMN = 5


def _work_probe(
    mode: str,
    service: str,
    timeout: float,
    source: str,
    bridge: EventBridge,
) -> None:
    """线程池里执行服务测速或站点探测。"""
    try:
        if mode == MODE_SERVICE:
            rows = probe_service(service, timeout)
            bridge.event.emit(("service", service, rows))
        else:
            rows, origin = probe_sites(timeout, source)
            bridge.event.emit(("sites", origin, rows))
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))


def _work_apply(service: str, mirror: str, bridge: EventBridge) -> None:
    """线程池里写入服务镜像配置。"""
    try:
        text = apply_service_mirror(service, mirror)
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("applied", service, mirror, text))


class MirrorToolWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """镜像搜索与配置主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._settings_override = settings_path
        self._rows: list[MirrorResult] = []
        self._last_service = ""
        self._busy = False
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"镜像搜索与配置 v{__version__}",
            config_name="mirrortoolw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._build()
        self._sync_mode()
        self._sync_config_hint()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """目标控件 / 动作行 / 结果表 / 进度条 / 日志。"""
        self.add(self._build_controls_card())
        self.add(self._build_action_row())
        self.add(self._build_results_card(), 3)
        self.add(self._build_progress())
        self.add(self._build_log_card(), 2)
        self._log.append_timestamped("选择服务镜像或教育网站点，点击「开始测速」；测速完成后可应用推荐镜像。")

    def _build_controls_card(self) -> Card:
        card = Card("测速目标", self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self._mode_service = QRadioButton("服务镜像", card)
        self._mode_sites = QRadioButton("教育网站点", card)
        self._mode_service.setChecked(self._settings.mode == MODE_SERVICE)
        self._mode_sites.setChecked(self._settings.mode == MODE_SITES)
        self._mode_service.toggled.connect(self._on_mode_change)
        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(12)
        mode_row.addWidget(self._mode_service)
        mode_row.addWidget(self._mode_sites)
        mode_row.addStretch(1)

        self._service_combo = QComboBox(card)
        self._service_combo.addItems(list(SERVICE_LABELS.values()))
        self._service_combo.setCurrentText(SERVICE_LABELS[self._settings.service])
        self._service_combo.currentTextChanged.connect(self._on_service_change)

        self._timeout_spin = QDoubleSpinBox(card)
        self._timeout_spin.setRange(MIN_TIMEOUT, MAX_TIMEOUT)
        self._timeout_spin.setSingleStep(0.5)
        self._timeout_spin.setDecimals(1)
        self._timeout_spin.setValue(self._settings.timeout)

        self._source_combo = QComboBox(card)
        self._source_combo.addItems(list(SOURCE_LABELS.values()))
        self._source_combo.setCurrentText(SOURCE_LABELS[self._settings.source])

        self._search = QLineEdit(self._settings.search, card)
        self._search.setPlaceholderText("按镜像名或地址过滤")
        self._search.textChanged.connect(self._render_rows)

        self._config_hint = ElidedLabel("", parent=card)
        self._config_hint.setObjectName("Hint")

        grid.addWidget(QLabel("模式：", card), 0, 0)
        grid.addLayout(mode_row, 0, 1)
        grid.addWidget(QLabel("服务：", card), 0, 2)
        grid.addWidget(self._service_combo, 0, 3)
        grid.addWidget(QLabel("超时(秒)：", card), 0, 4)
        grid.addWidget(self._timeout_spin, 0, 5)
        grid.addWidget(QLabel("来源：", card), 1, 0)
        grid.addWidget(self._source_combo, 1, 1)
        grid.addWidget(QLabel("搜索：", card), 1, 2)
        grid.addWidget(self._search, 1, 3, 1, 3)
        grid.addWidget(self._config_hint, 2, 0, 1, 6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 2)
        card.body.addLayout(grid)
        return card

    def _build_action_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._apply_selected_button = button("应用选中")
        self._apply_selected_button.clicked.connect(self._apply_selected)
        self._apply_recommended_button = button("应用推荐")
        self._apply_recommended_button.clicked.connect(self._apply_recommended)
        self._probe_button = button("开始测速", kind="primary")
        self._probe_button.clicked.connect(self._start_probe)
        layout.addStretch(1)
        layout.addWidget(self._apply_selected_button)
        layout.addWidget(self._apply_recommended_button)
        layout.addWidget(self._probe_button)
        return row

    def _build_results_card(self) -> Card:
        card = Card("探测结果", self)
        self._table = make_table(
            _TABLE_HEADERS,
            widths=_TABLE_WIDTHS,
            stretch_columns=(2,),
            parent=card,
        )
        self._table.setMinimumHeight(220)
        self._table.itemSelectionChanged.connect(self._update_apply_state)
        card.body.addWidget(self._table)
        return card

    def _build_progress(self) -> ProgressRow:
        self._progress_row = ProgressRow(parent=self)
        self._progress = self._progress_row.bar
        return self._progress_row

    def _build_log_card(self) -> Card:
        card = Card("日志", self)
        self._log = card.add_log(height=120, minimum_height=90)
        return card

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _selected_service(self) -> str:
        """把服务下拉框的显示名映射回服务 key；无匹配时返回空串。"""
        label = self._service_combo.currentText()
        return next((name for name, text in SERVICE_LABELS.items() if text == label), "")

    def _selected_source(self) -> str:
        """把来源下拉框的显示名映射回 ``auto`` / ``builtin``。"""
        label = self._source_combo.currentText()
        return next((name for name, text in SOURCE_LABELS.items() if text == label), "auto")

    def _on_mode_change(self, *_args: bool) -> None:
        """模式切换：重置上次服务、同步控件与结果表列。"""
        self._last_service = ""
        self._sync_mode()
        self._sync_config_hint()

    def _on_service_change(self, *_args: str) -> None:
        """切换服务后清空上次探测归属，并刷新配置位置。"""
        self._last_service = ""
        self._sync_config_hint()

    def _sync_mode(self) -> None:
        """按模式启用/禁用服务与来源控件，切换结果表列与表头。"""
        service_mode = self._mode_service.isChecked()
        self._service_combo.setEnabled(service_mode)
        self._source_combo.setEnabled(not service_mode)
        self._apply_selected_button.setEnabled(service_mode)
        self._apply_recommended_button.setEnabled(service_mode)
        self._table.setColumnHidden(_THROUGHPUT_COLUMN, not service_mode)
        header = self._table.horizontalHeaderItem(1)
        if header is not None:
            header.setText("镜像" if service_mode else "站点")

    def _sync_config_hint(self) -> None:
        """更新「配置位置」提示：站点模式只读，服务模式显示落盘位置。"""
        if not self._mode_service.isChecked():
            text = "配置位置：教育网站点探测为只读，不修改任何配置"
        else:
            service = self._selected_service()
            text = f"配置位置：{config_location(service)}" if service else ""
        self._config_hint.setText(text)
        self._config_hint.setToolTip(text)

    def _set_busy(self, busy: bool) -> None:
        """探测 / 应用期间禁用操作与目标控件，避免重复提交或中途切换目标。"""
        self._busy = busy
        for widget in (
            self._probe_button,
            self._apply_selected_button,
            self._apply_recommended_button,
            self._mode_service,
            self._mode_sites,
            self._service_combo,
            self._timeout_spin,
            self._source_combo,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self._sync_mode()

    def _start_progress(self) -> None:
        """进度条切到不确定态（测速 / 应用都可能持续数秒）。"""
        self._progress.setRange(0, 0)

    def _stop_progress(self) -> None:
        """进度条回到静止态。"""
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

    # ------------------------------------------------------------------ #
    # 探测
    # ------------------------------------------------------------------ #
    def _start_probe(self) -> None:
        """校验参数并启动后台测速。"""
        if self._busy:
            return
        try:
            timeout = normalize_timeout(self._timeout_spin.value())
        except ValueError as exc:
            self._log.append_timestamped(f"错误：{exc}")
            return
        mode = MODE_SERVICE if self._mode_service.isChecked() else MODE_SITES
        service = self._selected_service()
        if mode == MODE_SERVICE and not service:
            self._log.append_timestamped("错误：请选择服务")
            return
        source = self._selected_source()

        self._log.clear()
        self._rows = []
        self._render_rows()
        self._set_busy(True)
        self._start_progress()
        if mode == MODE_SERVICE:
            self.set_status(f"正在测速 {SERVICE_LABELS[service]}…")
            self._log.append_timestamped(f"测速服务：{SERVICE_LABELS[service]}；超时 {timeout:g} 秒")
            self._log.append_timestamped(f"配置位置：{config_location(service)}")
        else:
            self.set_status("正在探测教育网镜像站点…")
            self._log.append_timestamped(
                f"探测教育网镜像站点；超时 {timeout:g} 秒；来源 {self._source_combo.currentText()}"
            )

        self._submit(_work_probe, mode, service, timeout, source)

    def _submit(self, worker: Any, *args: Any) -> None:
        """创建信号桥并提交后台任务。"""
        bridge = EventBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(worker, *args, bridge, on_error=self._on_worker_error)

    def _on_worker_error(self, message: str) -> None:
        """线程池兜底异常：走统一失败收尾。"""
        self._finish_error(message)

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：服务结果、站点结果、应用完成或失败。"""
        kind = event[0]
        if kind == "service":
            _, service, rows = event
            self._last_service = service
            self._finish_busy(rows, f"{SERVICE_LABELS[service]} 共 {len(rows)} 个候选镜像")
        elif kind == "sites":
            _, origin, rows = event
            self._last_service = ""
            self._finish_busy(rows, f"教育网站点（{origin}）共 {len(rows)} 个")
        elif kind == "applied":
            _, service, mirror, text = event
            self._stop_progress()
            self._set_busy(False)
            self._bridge = None
            for line in str(text).splitlines():
                if line.strip():
                    self._log.append_timestamped(line.strip())
            self._log.append_timestamped(f"已应用 {SERVICE_LABELS[service]} 镜像: {mirror}")
            self.set_status(f"已应用 {mirror}")
        elif kind == "error":
            self._finish_error(str(event[1]))

    def _finish_busy(self, rows: list[MirrorResult], summary: str) -> None:
        """探测完成：停进度、恢复控件、渲染结果并输出推荐。"""
        self._rows = list(rows)
        self._stop_progress()
        self._set_busy(False)
        self._bridge = None
        self._render_rows()
        reachable = sum(1 for row in rows if row.ok)
        self._log.append_timestamped(f"{summary}，可达 {reachable} 个")
        best = recommend_mirror(rows)
        if best is None:
            self._log.append_timestamped("全部不可达，请检查网络或更换来源后重试")
            self.set_status("全部不可达")
            return
        self._log.append_timestamped(f"推荐镜像: {best}（按实测吞吐与延迟排序）")
        self.set_status(f"完成：推荐 {best}")

    def _finish_error(self, message: str) -> None:
        """探测 / 应用失败：停进度、恢复控件并提示。"""
        self._stop_progress()
        self._set_busy(False)
        self._bridge = None
        self._log.append_timestamped(f"错误：{message}")
        self.set_status("操作失败")
        QMessageBox.critical(self, "操作失败", message)

    # ------------------------------------------------------------------ #
    # 结果渲染
    # ------------------------------------------------------------------ #
    def _render_rows(self, *_args: Any) -> None:
        """按搜索词过滤并重绘结果表，标记推荐与不可达行。"""
        self._table.setRowCount(0)
        rows = filter_results(self._rows, self._search.text())
        recommended = recommend_mirror(self._rows)
        palette = self.theme().palette
        for index, row in enumerate(rows, start=1):
            values = (
                str(index),
                row.name,
                row.url,
                row.status_text,
                row.latency_text,
                row.throughput_text,
            )
            table_row = self._table.rowCount()
            self._table.insertRow(table_row)
            for column, text in enumerate(values):
                cell = make_table_item(text)
                if column == 2:
                    cell.setToolTip(row.url)
                elif column == 3:
                    cell.setToolTip(row.status_text)
                if not row.ok:
                    cell.setForeground(QColor(palette.muted))
                elif row.name == recommended:
                    cell.setForeground(QColor(palette.success))
                self._table.setItem(table_row, column, cell)
            first = self._table.item(table_row, 0)
            if first is not None:
                first.setData(Qt.ItemDataRole.UserRole, row.name)
        self._update_apply_state()

    def _update_apply_state(self) -> None:
        """无选中行时禁用「应用选中」。"""
        service_mode = self._mode_service.isChecked()
        has_selection = self._table.currentRow() >= 0
        self._apply_selected_button.setEnabled(service_mode and has_selection and not self._busy)

    def set_theme(self, theme: Theme) -> None:
        """切换主题后重渲染结果表，让推荐 / 不可达行色跟随调色板。"""
        super().set_theme(theme)
        if hasattr(self, "_table"):
            self._render_rows()

    # ------------------------------------------------------------------ #
    # 应用配置
    # ------------------------------------------------------------------ #
    def _apply_selected(self) -> None:
        """把结果表中选中的镜像写入当前服务配置。"""
        if not self._check_applicable():
            return
        table_row = self._table.currentRow()
        cell = self._table.item(table_row, 0) if table_row >= 0 else None
        mirror = cell.data(Qt.ItemDataRole.UserRole) if cell is not None else None
        if not mirror:
            self._log.append_timestamped("请先在结果表中选择一个镜像")
            return
        self._start_apply(self._last_service, str(mirror))

    def _apply_recommended(self) -> None:
        """把本次测速推荐的镜像写入当前服务配置。"""
        if not self._check_applicable():
            return
        best = recommend_mirror(self._rows)
        if best is None:
            self._log.append_timestamped("没有可达镜像可应用，请先测速或更换来源")
            return
        self._start_apply(self._last_service, best)

    def _check_applicable(self) -> bool:
        """检查当前状态是否允许应用配置（服务模式 + 已有测速结果）。"""
        if not self._mode_service.isChecked():
            self._log.append_timestamped("教育网站点探测为只读，不支持应用配置")
            return False
        if not self._last_service:
            self._log.append_timestamped("请先完成一次服务镜像测速")
            return False
        return True

    def _start_apply(self, service: str, mirror: str) -> None:
        """启动后台应用任务（写环境变量 / 配置文件可能较慢）。"""
        if self._busy:
            return
        self._set_busy(True)
        self._start_progress()
        self.set_status(f"正在应用 {mirror}…")
        self._log.append_timestamped(f"开始应用 {SERVICE_LABELS[service]} 镜像: {mirror}")
        self._submit(_work_apply, service, mirror)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回模式、服务、超时、来源、搜索词与窗口 geometry。"""
        try:
            timeout = normalize_timeout(self._timeout_spin.value())
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        save_settings(
            MirrorToolSettings(
                mode=MODE_SERVICE if self._mode_service.isChecked() else MODE_SITES,
                service=self._selected_service() or "pip",
                timeout=timeout,
                source=self._selected_source(),
                search=self._search.text(),
                geometry=geometry,
            ),
            self._settings_path,
        )


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: MirrorToolWindow(settings_path), app_name="mirrortoolw")
