"""llmw（Qt）性能测试窗口：从顶部「帮助 → 性能测试…」打开。

场景定义 / 执行 / 结果格式化仍由 :mod:`fcmd.apps.llm._llmw_bench`
（无 Qt 依赖）提供；本模块只负责被测服务选择、参数表单、后台任务与结果展示。

被测服务默认自动取唯一运行中的服务；多个服务时用顶部下拉框选择，服务列表
变化（启动 / 停止）会即时刷新。关窗只隐藏，测试继续；主窗口关闭时取消任务。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import llmenv, llmserve
from fcmd.apps.llm._llmw_bench import (
    BENCH_CHECKED,
    BENCH_UNCHECKED,
    RESULT_COLUMNS,
    SCENARIOS,
    ScenarioResult,
    checked_scenarios,
    endpoint_from_states,
    format_result_csv,
    format_result_row,
    run_scenario,
)
from fcmd.apps.llm.llmbase import Endpoint, ServeError
from fcmd.gui.qt.widgets import (
    Card,
    FormGrid,
    PageScroll,
    ProgressRow,
    button,
    hint_label,
    link_button,
    make_table,
    make_table_item,
    section_header,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["BenchDialog"]


class _BenchBridge(QObject):  # pragma: no cover - Qt 信号桥
    """性能测试后台线程 → GUI 的信号桥。"""

    result = Signal(object)
    progress = Signal(int, int, str)
    finished = Signal(bool)


def _bench_worker(  # noqa: PLR0913, PLR0917 - 参数即测试口径，显式汇总成一次投递
    endpoint: Endpoint,
    scenarios: list[Any],
    repeat: int,
    timeout: float,
    max_tokens: int | None,
    cancel: Any,
    bridge: _BenchBridge,
) -> None:  # pragma: no cover - 线程体
    """后台逐个执行场景，把结果与进度投回 GUI 线程。"""
    model_path = ""
    for state in llmenv.list_states():
        if state.endpoint.base_url == endpoint.base_url and llmserve.process_alive(state.pid, state.create_time):
            model_path = state.model
            break
    total = len(scenarios)
    for index, scenario in enumerate(scenarios, start=1):
        if cancel.is_set():
            break
        bridge.progress.emit(index, total, scenario.label)
        result = run_scenario(
            endpoint,
            scenario,
            repeat=repeat,
            max_tokens=max_tokens,
            timeout=timeout,
            cancel=cancel,
        )
        bridge.result.emit((result, model_path))
    bridge.finished.emit(cancel.is_set())


class BenchDialog(QDialog):  # pragma: no cover - Qt 控件
    """性能测试窗口：目标服务 + 场景 / 参数 + 结果表。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._bench_cancel: Any = None
        self._bench_running = False
        self._bench_rows: list[tuple[str, ...]] = []

        self.setWindowTitle("性能测试")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.resize(1040, 760)
        self.setMinimumSize(900, 620)

        self._build()
        self.state.servicesChanged.connect(lambda _states: self._refresh_targets())
        self._refresh_targets()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        scroll = PageScroll(self, margins=(0, 0, 4, 8))
        layout = scroll.body
        outer.addWidget(scroll)

        layout.addWidget(self._build_target())
        layout.addWidget(self._build_scenarios())
        layout.addWidget(self._build_results())
        layout.addStretch(1)

    def _build_target(self) -> Card:
        """被测服务：与页面卡片一致，标题行右侧放选择框与刷新入口。"""
        card = Card(parent=self)
        header, self._target_title = section_header("测试目标", card)
        self._target_combo = QComboBox(card)
        self._target_combo.setMinimumWidth(320)
        self._refresh_services_button = link_button("刷新服务", tooltip="重新读取已登记服务")
        self._refresh_services_button.clicked.connect(self.state.refresh_services)
        header.addWidget(self._target_combo, 1)
        header.addWidget(self._refresh_services_button)
        card.body.addLayout(header)
        self._target_hint = hint_label("", wrap=True)
        card.body.addWidget(self._target_hint)
        return card

    def _build_scenarios(self) -> Card:
        """场景表与参数左右分栏；开始 / 停止与结果工具按钮分组两行。"""
        card = Card("测试场景与参数")
        body = card.body

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)

        self._scenario_table = QTableWidget(len(SCENARIOS), 5, card)
        self._scenario_table.setHorizontalHeaderLabels(["选", "场景", "输入", "输出上限", "说明"])
        self._scenario_table.verticalHeader().setVisible(False)
        self._scenario_table.setAlternatingRowColors(True)
        self._scenario_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._scenario_items: list[QCheckBox] = []
        for row, scenario in enumerate(SCENARIOS):
            checkbox = QCheckBox(self._scenario_table)
            checkbox.setChecked(True)
            holder = QWidget(self._scenario_table)
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(checkbox)
            holder_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._scenario_table.setCellWidget(row, 0, holder)
            self._scenario_items.append(checkbox)
            values = (
                scenario.label,
                "默认短提示" if not scenario.prompt_tokens else f"约 {scenario.prompt_tokens} tok",
                f"{scenario.max_tokens} tok",
                scenario.detail,
            )
            for column, text in enumerate(values, start=1):
                self._scenario_table.setItem(row, column, make_table_item(text))
        self._scenario_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((44, 108, 96, 92)):
            self._scenario_table.setColumnWidth(column, width)
        self._scenario_table.setMinimumWidth(380)
        self._scenario_table.setMaximumHeight(190)
        top.addWidget(self._scenario_table, 3)

        params = FormGrid(card)
        params.setMinimumWidth(280)
        self._repeat_spin = QSpinBox(params)
        self._repeat_spin.setRange(1, 20)
        self._repeat_spin.setValue(1)
        self._timeout_edit = QLineEdit("180", params)
        self._tokens_edit = QLineEdit("", params)
        self._tokens_edit.setPlaceholderText("留空按场景")
        params.add_pair("重复次数", self._repeat_spin, "请求超时（秒）", self._timeout_edit)
        params.add_row("输出上限", self._tokens_edit)
        top.addWidget(params, 2, Qt.AlignmentFlag.AlignTop)
        body.addLayout(top)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self._bench_start = button("开始测试", kind="primary")
        self._bench_stop = button("停止", kind="danger")
        self._bench_stop.setEnabled(False)
        self._bench_clear = button("清空结果", kind="ghost")
        self._bench_copy = button("复制 CSV", kind="ghost")
        self._bench_export = button("导出 CSV", kind="ghost")
        actions.addWidget(self._bench_start)
        actions.addWidget(self._bench_stop)
        actions.addStretch(1)
        for widget in (self._bench_clear, self._bench_copy, self._bench_export):
            actions.addWidget(widget)
        body.addLayout(actions)

        self._bench_start.clicked.connect(self._start_bench)
        self._bench_stop.clicked.connect(self._stop_bench)
        self._bench_clear.clicked.connect(self._clear_bench)
        self._bench_copy.clicked.connect(self._copy_bench_csv)
        self._bench_export.clicked.connect(self._export_bench_csv)
        return card

    def _build_results(self) -> Card:
        card = Card(parent=self)
        header, self._results_title = section_header("测试结果", card)
        self._bench_progress = ProgressRow("选择场景后点击「开始测试」", show_bar=False, parent=card)
        self._bench_status = self._bench_progress.label
        header.addWidget(self._bench_progress)
        card.body.addLayout(header)

        self._bench_table = make_table(
            RESULT_COLUMNS,
            widths=(120, 50, 80, 80, 80, 90, 90, 200),
            stretch_columns=(0, 7),
            parent=card,
        )
        self._bench_table.setMinimumHeight(220)
        card.body.addWidget(self._bench_table)
        card.body.addWidget(
            hint_label(
                "聚合 tok/s = 输出 tokens ÷ 墙钟（并发场景体现总吞吐）；单请求 tok/s 取服务端 decode 速率（不含 prefill）"
            )
        )
        return card

    # ------------------------------------------------------------------ #
    # 目标服务
    # ------------------------------------------------------------------ #
    def _refresh_targets(self) -> None:
        """按运行中服务重建下拉项；保留原选择，服务全部退出时回落自动模式。"""
        current = self._target_combo.currentData()
        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        self._target_combo.addItem("（自动选择唯一运行中的服务）", "")
        for state in self.state.alive_states:
            self._target_combo.addItem(f"{state.name} · {state.base_url}", state.name)
        if current:
            index = self._target_combo.findData(current)
            if index >= 0:
                self._target_combo.setCurrentIndex(index)
        self._target_combo.blockSignals(False)
        self._target_hint.setText(
            "未检测到运行中的服务：先到「运行」页启动服务。"
            if not self.state.alive_states
            else f"运行中的服务 {len(self.state.alive_states)} 个；留空自动选择唯一服务，多个服务请手动选择。"
        )

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _selected_scenarios(self) -> list[Any]:
        rows: list[tuple[str, ...]] = []
        for row, scenario in enumerate(SCENARIOS):
            checked = self._scenario_items[row].isChecked()
            rows.append((BENCH_CHECKED if checked else BENCH_UNCHECKED, scenario.label))
        selected = checked_scenarios(rows)
        if not selected:
            raise ServeError("请至少勾选一个测试场景")
        return list(selected)

    def _bench_settings(self) -> tuple[int, float, int | None]:
        repeat = int(self._repeat_spin.value())
        try:
            timeout = float(self._timeout_edit.text().strip() or "180")
            raw = self._tokens_edit.text().strip()
            max_tokens = int(raw) if raw else None
        except ValueError as exc:
            raise ServeError("超时必须是数字，输出上限必须是整数") from exc
        return repeat, timeout, max_tokens

    def _bench_endpoint(self) -> Endpoint:
        name = str(self._target_combo.currentData() or "")
        return endpoint_from_states(llmenv.list_states(), name)

    def _start_bench(self) -> None:
        if self._bench_running:
            return
        try:
            scenarios = self._selected_scenarios()
            endpoint = self._bench_endpoint()
            repeat, timeout, max_tokens = self._bench_settings()
        except ServeError as exc:
            QMessageBox.critical(self, "无法开始测试", str(exc))
            return
        cancel = threading.Event()
        self._bench_cancel = cancel
        self._bench_running = True
        self._bench_start.setEnabled(False)
        self._bench_stop.setEnabled(True)
        self._bench_status.setText("正在测试…")
        bridge = _BenchBridge(self)
        bridge.result.connect(self._on_bench_result)
        bridge.progress.connect(self._on_bench_progress)
        bridge.finished.connect(self._on_bench_finished)
        submit(
            _bench_worker,
            endpoint,
            scenarios,
            repeat,
            timeout,
            max_tokens,
            cancel,
            bridge,
        )

    def _stop_bench(self) -> None:
        if self._bench_cancel is not None:
            self._bench_cancel.set()
            self._bench_status.setText("正在停止…")

    def stop(self) -> None:
        """主窗口关闭时取消后台测试（结果窗口随后销毁）。"""
        if self._bench_cancel is not None:
            self._bench_cancel.set()

    def _on_bench_progress(self, index: int, total: int, label: str) -> None:
        self._bench_status.setText(f"正在测试 {index}/{total}：{label}")

    def _on_bench_result(self, payload: tuple[ScenarioResult, str]) -> None:
        result, model_path = payload
        row = format_result_row(result)
        self._bench_rows.append(row)
        table_row = self._bench_table.rowCount()
        self._bench_table.insertRow(table_row)
        for column, text in enumerate(row):
            self._bench_table.setItem(table_row, column, make_table_item(text))
        self.state.record_benchmark(model_path, result)

    def _on_bench_finished(self, cancelled: bool) -> None:
        self._bench_running = False
        self._bench_start.setEnabled(True)
        self._bench_stop.setEnabled(False)
        self._bench_status.setText("已停止" if cancelled else "测试完成")
        self._bench_cancel = None

    def _clear_bench(self) -> None:
        self._bench_table.setRowCount(0)
        self._bench_rows.clear()
        self._bench_status.setText("选择场景后点击「开始测试」")

    def _copy_bench_csv(self) -> None:
        if not self._bench_rows:
            self._bench_status.setText("暂无结果可复制")
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(format_result_csv(self._bench_rows))
        self._bench_status.setText("已复制 CSV 到剪贴板")

    def _export_bench_csv(self) -> None:
        if not self._bench_rows:
            self._bench_status.setText("暂无结果可导出")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出测试结果", "llmw-bench.csv", "CSV (*.csv)")
        if not path:
            return
        Path(path).write_text(format_result_csv(self._bench_rows), encoding="utf-8")
        self._bench_status.setText(f"已导出: {path}")

    def showEvent(self, event: QShowEvent) -> None:
        """打开时刷新服务列表并置顶（重复点菜单只聚焦已有窗口）。"""
        super().showEvent(event)
        self._refresh_targets()
        self.raise_()
        self.activateWindow()
