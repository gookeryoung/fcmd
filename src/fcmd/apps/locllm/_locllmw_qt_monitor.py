"""locllmw（Qt）监测页：实时性能、系统负载、GPU、性能测试与日志。

采样沿用既有口径：服务运行时每秒抓一次 ``/metrics`` 并按时间窗口平滑
token 速率（:class:`fcmd.apps.locllm._locllmw_monitor.RateWindow`），同时用
``psutil`` / ``nvidia-smi`` 采样系统负载；服务未运行时只采系统负载。

Qt 侧把「一次采样」封装成纯函数 :func:`sample_once`（可在无 Qt 环境单测），
GUI 用 ``QTimer`` 每秒投递一个后台任务，任务未完成时跳过下一次，避免
``nvidia-smi`` 偶发变慢时堆叠线程。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import llmenv, llmserve
from fcmd.apps.locllm._locllmw_bench import (
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
from fcmd.apps.locllm._locllmw_monitor import (
    RATE_WINDOW_SECONDS,
    GpuSample,
    LoadSample,
    RateWindow,
    _format_gpu,
    _format_load,
    _sample_load,
)
from fcmd.apps.locllm.llmbase import Endpoint, ServeError, ServeState
from fcmd.gui.qt.widgets import (
    Card,
    Collapsible,
    LogView,
    MetricGrid,
    PageScroll,
    button,
    hint_label,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.workers import periodic, submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["MonitorPage", "sample_once"]

METRIC_KEYS = (
    ("生成速率", "gen_rate"),
    ("提示速率", "prompt_rate"),
    ("处理中请求", "processing"),
    ("排队请求", "deferred"),
    ("KV 使用率", "kv"),
    ("累计生成 tokens", "predicted"),
    ("累计提示 tokens", "prompt"),
    ("采样状态", "monitor"),
)
LOAD_KEYS = (
    ("CPU", "cpu"),
    ("频率", "cpu_freq"),
    ("内存", "memory"),
    ("交换", "swap"),
    ("服务 CPU", "proc_cpu"),
    ("服务内存", "proc_mem"),
    ("线程", "proc_threads"),
    ("运行时长", "uptime"),
)
GPU_KEYS = (
    ("使用率", "gpu_util"),
    ("显存", "gpu_vram"),
    ("服务显存", "gpu_proc_vram"),
    ("温度", "gpu_temp"),
    ("功耗", "gpu_power"),
    ("风扇", "gpu_fan"),
    ("核心频率", "gpu_clock"),
)


def sample_once(state: ServeState | None) -> tuple[LoadSample, llmserve.ServeMetrics | None, str | None]:
    """采样一次系统负载与服务指标（阻塞，供线程池调用）。

    ``state`` 为 ``None`` 时只采系统负载，返回 ``(负载, None, None)``；服务指标
    抓取失败时把原因放在第三个返回值里，由界面显示「采样失败」而不是抛异常。
    """
    process = _attach_process(state.pid) if state is not None else None
    load = _sample_load(state.pid if state is not None else 0, process)
    if state is None:
        return load, None, None
    try:
        metrics = llmserve.fetch_metrics(state.host, state.port, state.api_key)
    except ServeError as exc:
        return load, None, str(exc)
    return load, metrics, None


def _attach_process(pid: int) -> Any | None:
    """绑定服务进程用于 CPU / 内存 / 运行时长采样；进程已退出时返回 ``None``。"""
    import psutil

    try:
        return psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


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


class MonitorPage(QWidget):  # pragma: no cover - Qt 控件
    """监测页：指标 / 负载 / GPU / 性能测试 / 日志。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._target_name = ""
        self._window = RATE_WINDOW_SECONDS
        self._rates = RateWindow()
        self._inflight = False
        self._timer: QTimer | None = None
        self._bench_cancel: Any = None
        self._bench_running = False
        self._bench_rows: list[tuple[str, ...]] = []
        self._build()
        self.state.servicesChanged.connect(lambda _states: self._sync_target())
        self._timer = periodic(self, 1000, self._tick)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(self)
        layout = scroll.body
        outer.addWidget(scroll)

        metrics_card = Card(f"实时性能（服务运行中每 1s 采样 /metrics，速率按 {RATE_WINDOW_SECONDS:.0f}s 窗口平滑）")
        self._metrics = MetricGrid(METRIC_KEYS, columns=4, parent=metrics_card)
        metrics_card.body.addWidget(self._metrics)
        layout.addWidget(metrics_card)

        load_card = Card("系统负载（每 1s 采样；服务运行时额外显示服务进程指标）")
        self._load = MetricGrid(LOAD_KEYS, columns=4, parent=load_card)
        load_card.body.addWidget(self._load)
        layout.addWidget(load_card)

        gpu_card = Card("GPU 负载（nvidia-smi，每 1s 采样；服务运行时额外显示服务显存）")
        self._gpu_name = QLabel("检测中…", gpu_card)
        self._gpu_name.setObjectName("Value")
        self._gpu_name.setWordWrap(True)
        gpu_card.body.addWidget(self._gpu_name)
        self._gpu = MetricGrid(GPU_KEYS, columns=4, parent=gpu_card)
        gpu_card.body.addWidget(self._gpu)
        layout.addWidget(gpu_card)

        self._bench = Collapsible("性能测试（短对话 / 长输出 / 代码补全 / 长上下文 / 并发）", expanded=False)
        self._build_bench(self._bench.content)
        layout.addWidget(self._bench)

        log_card = Card("服务日志")
        self.log_view = LogView(height=240, parent=log_card)
        log_card.body.addWidget(self.log_view)
        layout.addWidget(log_card, 1)

    def _build_bench(self, parent: QWidget) -> None:
        body = self._bench.body

        self._scenario_table = QTableWidget(len(SCENARIOS), 5, parent)
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
        self._scenario_table.setMaximumHeight(190)
        body.addWidget(self._scenario_table)

        params = Card("测试参数", parent)
        grid = QGridLayout()
        self._repeat_spin = QSpinBox(params)
        self._repeat_spin.setRange(1, 20)
        self._repeat_spin.setValue(1)
        self._timeout_edit = QLineEdit("180", params)
        self._tokens_edit = QLineEdit("", params)
        self._tokens_edit.setPlaceholderText("留空按场景")
        grid.addWidget(hint_label("重复次数", wrap=False), 0, 0)
        grid.addWidget(self._repeat_spin, 0, 1)
        grid.addWidget(hint_label("请求超时（秒）", wrap=False), 0, 2)
        grid.addWidget(self._timeout_edit, 0, 3)
        grid.addWidget(hint_label("输出上限", wrap=False), 1, 0)
        grid.addWidget(self._tokens_edit, 1, 1)
        params.body.addLayout(grid)
        body.addWidget(params)

        actions = QHBoxLayout()
        self._bench_start = button("开始测试", kind="primary")
        self._bench_stop = button("停止", kind="danger")
        self._bench_stop.setEnabled(False)
        self._bench_clear = button("清空结果", kind="ghost")
        self._bench_copy = button("复制 CSV", kind="ghost")
        self._bench_export = button("导出 CSV", kind="ghost")
        for widget in (self._bench_start, self._bench_stop, self._bench_clear, self._bench_copy, self._bench_export):
            actions.addWidget(widget)
        actions.addStretch(1)
        self._bench_status = hint_label("选择场景后点击「开始测试」")
        actions.addWidget(self._bench_status)
        body.addLayout(actions)

        self._bench_table = make_table(
            RESULT_COLUMNS,
            widths=(120, 50, 80, 80, 80, 90, 90, 200),
            stretch_columns=(0, 7),
            parent=parent,
        )
        self._bench_table.setMinimumHeight(140)
        body.addWidget(self._bench_table)
        body.addWidget(
            hint_label(
                "聚合 tok/s = 输出 tokens ÷ 墙钟（并发场景体现总吞吐）；单请求 tok/s 取服务端 decode 速率（不含 prefill）"
            )
        )

        self._bench_start.clicked.connect(self._start_bench)
        self._bench_stop.clicked.connect(self._stop_bench)
        self._bench_clear.clicked.connect(self._clear_bench)
        self._bench_copy.clicked.connect(self._copy_bench_csv)
        self._bench_export.clicked.connect(self._export_bench_csv)

    # ------------------------------------------------------------------ #
    # 采样
    # ------------------------------------------------------------------ #
    def set_target_name(self, name: str) -> None:
        """指定要监控的服务名（运行页切服务时同步）。"""
        self._target_name = name
        self._sync_target()

    def _sync_target(self) -> None:
        """服务列表变化后重置速率窗口，避免新旧服务 token 计数相减成负数。"""
        self._rates = RateWindow(self._window)
        if not self.state.alive_states:
            self._metrics.set_value("monitor", "服务未运行（仅采样系统负载）")

    def _current_state(self) -> ServeState | None:
        alive = self.state.alive_states
        if not alive:
            return None
        for state in alive:
            if state.name == self._target_name:
                return state
        return alive[0]

    def _tick(self) -> None:
        """每秒投递一次采样；上一轮未完成时跳过，避免任务堆叠。"""
        if self._inflight or self.state.closing:
            return
        self._inflight = True
        target = self._current_state()
        submit(sample_once, target, on_done=self._apply_sample, on_finished=self._sample_finished)

    def _sample_finished(self) -> None:
        self._inflight = False

    def _apply_sample(self, payload: tuple[LoadSample, Any, str | None]) -> None:
        load, metrics, error = payload
        self._load.set_values(_format_load(load))
        self._gpu.set_values(_format_gpu(load.gpu))
        self._gpu_name.setText(_format_gpu(load.gpu)["gpu_name"])
        self.state.sampleUpdated.emit(load)
        self._sync_free_vram(load.gpu)
        if metrics is None:
            if error is not None:
                self._metrics.set_value("monitor", "采样失败: " + error[:60])
            else:
                for key in ("gen_rate", "prompt_rate", "processing", "deferred", "kv", "predicted", "prompt"):
                    self._metrics.set_value(key, "-")
                self._metrics.set_value("monitor", "服务未运行（仅采样系统负载）")
            return
        gen_rate, prompt_rate = self._rates.update(time.monotonic(), metrics.predicted_tokens, metrics.prompt_tokens)
        self._metrics.set_values(
            {
                "gen_rate": f"{gen_rate:.1f} tok/s",
                "prompt_rate": f"{prompt_rate:.1f} tok/s",
                "processing": f"{metrics.processing:.0f}",
                "deferred": f"{metrics.deferred:.0f}",
                "kv": f"{metrics.kv_usage * 100:.1f} %",
                "predicted": f"{metrics.predicted_tokens:.0f}",
                "prompt": f"{metrics.prompt_tokens:.0f}",
                "monitor": "监控中",
            }
        )

    def _sync_free_vram(self, gpu: GpuSample | None) -> None:
        """用实时 GPU 采样更新可用显存（服务停止释放显存后自动恢复「可用」）。"""
        if gpu is None or gpu.memory_used is None or gpu.memory_total is None:
            return
        free = int(max(gpu.memory_total - gpu.memory_used, 0))
        if self.state.gpu_free_mib is not None and abs(free - self.state.gpu_free_mib) < 256:
            return
        self.state.gpu_free_mib = free
        if self.state.gpu_mib is None:
            self.state.gpu_mib = int(gpu.memory_total)
        self.state.sampleCapacityChanged.emit()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def stop(self) -> None:
        """窗口关闭时停止采样定时器。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._stop_bench()

    # ------------------------------------------------------------------ #
    # 性能测试
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
        name = self._target_name or ""
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
        import threading

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
        path, _ = QFileDialog.getSaveFileName(self, "导出测试结果", "locllmw-bench.csv", "CSV (*.csv)")
        if not path:
            return
        Path(path).write_text(format_result_csv(self._bench_rows), encoding="utf-8")
        self._bench_status.setText(f"已导出: {path}")
