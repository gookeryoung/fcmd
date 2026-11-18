"""locllmw（Qt）监测页：实时性能与系统负载（GPU / CPU / 内存）。

采样沿用既有口径：服务运行时每秒抓一次 ``/metrics`` 并按时间窗口平滑
token 速率（:class:`fcmd.apps.locllm._locllmw_monitor.RateWindow`），同时用
``psutil`` / ``nvidia-smi`` 采样系统负载；服务未运行时只采系统负载。

Qt 侧把「一次采样」封装成纯函数 :func:`sample_once`（可在无 Qt 环境单测），
GUI 用 ``QTimer`` 每秒投递一个后台任务，任务未完成时跳过下一次，避免
``nvidia-smi`` 偶发变慢时堆叠线程。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import llmserve
from fcmd.apps.locllm._locllmw_monitor import (
    RATE_WINDOW_SECONDS,
    GpuSample,
    LoadSample,
    RateWindow,
    _format_gpu,
    _format_load,
    _sample_load,
)
from fcmd.apps.locllm.llmbase import ServeError, ServeState
from fcmd.gui.qt.widgets import (
    Card,
    MetricGrid,
    PageScroll,
    SegmentBar,
    hint_label,
    link_button,
    segment_band,
    value_label,
)
from fcmd.gui.qt.workers import periodic, submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["MonitorPage", "sample_once"]

#: 实时性能的次要指标；生成 tok/s 单独用大字号展示，不放进网格
SECONDARY_METRIC_KEYS = (
    ("提示速率", "prompt_rate"),
    ("处理中请求", "processing"),
    ("排队请求", "deferred"),
    ("KV 使用率", "kv"),
    ("累计生成 tokens", "predicted"),
    ("累计提示 tokens", "prompt"),
    ("采样状态", "monitor"),
)
#: GPU 卡片：主格图显示利用率，其余参数放次要文字行
GPU_DETAIL_KEYS = (
    ("显存", "gpu_vram"),
    ("服务显存", "gpu_proc_vram"),
    ("温度", "gpu_temp"),
    ("功耗", "gpu_power"),
    ("风扇", "gpu_fan"),
    ("核心频率", "gpu_clock"),
)
#: CPU 卡片：主格图显示总利用率，其余为频率与服务进程占用
CPU_DETAIL_KEYS = (
    ("频率", "cpu_freq"),
    ("服务 CPU", "proc_cpu"),
    ("线程", "proc_threads"),
)
#: 内存卡片：主格图显示内存占用率，其余为已用 / 交换 / 服务驻留与运行时长
MEMORY_DETAIL_KEYS = (
    ("已用", "memory"),
    ("交换", "swap"),
    ("服务内存", "proc_mem"),
    ("运行时长", "uptime"),
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

        layout.addWidget(self._build_target())
        layout.addWidget(self._build_metrics())
        layout.addLayout(self._build_load_gpu())
        layout.addStretch(1)

    def _build_target(self) -> Card:
        """监测目标卡片：标题行放状态徽标与最近采样，正文只留目标与刷新入口。

        与概览页硬件卡片一致：``SectionTitle`` + 右侧轻量状态，状态不再单独占行；
        页面变窄时目标地址换行，徽标 / 时间贴右保持一眼可见。
        """
        card = Card(parent=self)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("监测目标", card)
        title.setObjectName("SectionTitle")
        self._monitor_badge = QLabel("等待采样…", card)
        self._monitor_badge.setObjectName("Hint")
        self._last_sample_label = hint_label("最近采样：-", wrap=False)
        header.addWidget(title)
        header.addWidget(self._monitor_badge)
        header.addStretch(1)
        header.addWidget(self._last_sample_label)
        card.body.addLayout(header)

        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(8)
        self._target_label = value_label("未选择运行中的服务：仅采样系统负载")
        self._target_label.setWordWrap(True)
        self._refresh_services_button = link_button("刷新服务", tooltip="重新读取已登记服务")
        self._refresh_services_button.clicked.connect(self.state.refresh_services)
        target_row.addWidget(self._target_label, 1)
        target_row.addWidget(self._refresh_services_button)
        card.body.addLayout(target_row)
        card.body.addWidget(
            hint_label(
                f"每 1s 采样服务 /metrics 与系统负载；速率按 {RATE_WINDOW_SECONDS:.0f}s 窗口平滑。服务未运行时仅采样系统负载。",
                wrap=True,
            )
        )
        return card

    def _build_metrics(self) -> Card:
        """实时性能：生成 tok/s 做主视觉，其余服务指标用次要小字网格。"""
        card = Card("实时性能")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)

        primary = QVBoxLayout()
        primary.setContentsMargins(0, 0, 0, 0)
        primary.setSpacing(0)
        primary.addWidget(hint_label(f"生成速率（{RATE_WINDOW_SECONDS:.0f}s 平滑）", wrap=False))
        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        self._gen_rate_label = QLabel("-", card)
        self._gen_rate_label.setObjectName("RateValue")
        self._gen_rate_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        unit = QLabel("tok/s", card)
        unit.setObjectName("RateUnit")
        value_row.addWidget(self._gen_rate_label, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        primary.addLayout(value_row)
        row.addLayout(primary, 0)

        divider = QFrame(card)
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        row.addWidget(divider)

        self._metrics = MetricGrid(SECONDARY_METRIC_KEYS, columns=3, parent=card)
        row.addWidget(self._metrics, 1)
        card.body.addLayout(row)
        return card

    def _build_load_gpu(self) -> QHBoxLayout:
        """GPU / CPU / 内存三张卡片：分段格图 + 占用率 + 次要参数。"""
        pair = QHBoxLayout()
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(8)

        self._gpu_name = QLabel("检测中…")
        self._gpu_name.setObjectName("Hint")
        self._gpu_name.setWordWrap(True)
        gpu_card, self._gpu_percent, self._gpu_bar, self._gpu_metrics = self._build_load_card(
            "GPU", GPU_DETAIL_KEYS, note=self._gpu_name
        )
        pair.addWidget(gpu_card, 1)
        cpu_card, self._cpu_percent, self._cpu_bar, self._cpu_metrics = self._build_load_card("CPU", CPU_DETAIL_KEYS)
        pair.addWidget(cpu_card, 1)
        mem_card, self._mem_percent, self._mem_bar, self._mem_metrics = self._build_load_card(
            "内存", MEMORY_DETAIL_KEYS
        )
        pair.addWidget(mem_card, 1)
        return pair

    def _build_load_card(
        self,
        title: str,
        keys: tuple[tuple[str, str], ...],
        *,
        note: QWidget | None = None,
    ) -> tuple[Card, QLabel, SegmentBar, MetricGrid]:
        """单张负载卡：标题 + 右侧占用率，下面是格图与次要参数。"""
        card = Card(parent=self)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        heading = QLabel(title, card)
        heading.setObjectName("SectionTitle")
        percent = QLabel("-", card)
        percent.setObjectName("LoadValue")
        percent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(percent)
        card.body.addLayout(header)

        bar = SegmentBar(parent=card)
        card.body.addWidget(bar)
        if note is not None:
            card.body.addWidget(note)
        details = MetricGrid(keys, columns=1, parent=card)
        card.body.addWidget(details)
        card.body.addStretch(1)
        return card, percent, bar, details

    # ------------------------------------------------------------------ #
    # 采样
    # ------------------------------------------------------------------ #
    def set_target_name(self, name: str) -> None:
        """指定要监控的服务名（运行页切服务时同步）。"""
        self._target_name = name
        self._sync_target()

    def _sync_target(self) -> None:
        """服务列表变化后重置速率窗口，并同步监测目标 / 状态标签。"""
        self._rates = RateWindow(self._window)
        target = self._current_state()
        if target is None:
            self._target_label.setText("未选择运行中的服务：仅采样系统负载")
            self._gen_rate_label.setText("-")
            self._metrics.set_value("monitor", "服务未运行（仅采样系统负载）")
            self._set_monitor_badge("仅系统负载", "warning")
            return
        self._target_label.setText(f"服务：{target.name} · {target.base_url}")
        self._set_monitor_badge("等待采样…", "hint")

    def _set_monitor_badge(self, text: str, tone: str) -> None:
        """状态徽标：沿用主题的 Success / Warning / Danger 语义色。"""
        self._monitor_badge.setText(text)
        names = {"success": "Success", "warning": "Warning", "danger": "Danger"}
        name = names.get(tone, "Hint")
        if self._monitor_badge.objectName() != name:
            self._monitor_badge.setObjectName(name)
            self._monitor_badge.style().unpolish(self._monitor_badge)
            self._monitor_badge.style().polish(self._monitor_badge)

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
        load_values = _format_load(load)
        gpu_values = _format_gpu(load.gpu)
        gpu = load.gpu
        self._gpu_name.setText(gpu_values["gpu_name"])
        self._apply_load_card(
            self._gpu_percent,
            self._gpu_bar,
            self._gpu_metrics,
            None if gpu is None else gpu.utilization,
            {key: gpu_values[key] for _label, key in GPU_DETAIL_KEYS},
        )
        self._apply_load_card(
            self._cpu_percent,
            self._cpu_bar,
            self._cpu_metrics,
            load.cpu,
            {key: load_values[key] for _label, key in CPU_DETAIL_KEYS},
        )
        self._apply_load_card(
            self._mem_percent,
            self._mem_bar,
            self._mem_metrics,
            load.memory_percent if load.memory_total else None,
            {key: load_values[key] for _label, key in MEMORY_DETAIL_KEYS},
        )
        self._last_sample_label.setText(f"最近采样：{time.strftime('%H:%M:%S')}")
        self.state.sampleUpdated.emit(load)
        self._sync_free_vram(load.gpu)
        if metrics is None:
            self._gen_rate_label.setText("-")
            for _label, key in SECONDARY_METRIC_KEYS:
                if key != "monitor":
                    self._metrics.set_value(key, "-")
            if error is not None:
                self._metrics.set_value("monitor", "采样失败: " + error[:60])
                self._set_monitor_badge("采样失败", "danger")
            else:
                self._metrics.set_value("monitor", "服务未运行（仅采样系统负载）")
                self._set_monitor_badge("仅系统负载", "warning")
            return
        gen_rate, prompt_rate = self._rates.update(time.monotonic(), metrics.predicted_tokens, metrics.prompt_tokens)
        self._gen_rate_label.setText(f"{gen_rate:.1f}")
        self._metrics.set_values(
            {
                "prompt_rate": f"{prompt_rate:.1f} tok/s",
                "processing": f"{metrics.processing:.0f}",
                "deferred": f"{metrics.deferred:.0f}",
                "kv": f"{metrics.kv_usage * 100:.1f} %",
                "predicted": f"{metrics.predicted_tokens:.0f}",
                "prompt": f"{metrics.prompt_tokens:.0f}",
                "monitor": "监控中",
            }
        )
        self._set_monitor_badge("监控中", "success")

    def _apply_load_card(
        self,
        percent_label: QLabel,
        bar: SegmentBar,
        details: MetricGrid,
        percent: float | None,
        values: dict[str, str],
    ) -> None:
        """刷新一张负载卡：占用率文字 / 格图 / 次要参数一起更新。"""
        percent_label.setText("-" if percent is None else f"{percent:.0f} %")
        self._set_load_band(percent_label, segment_band(percent))
        bar.set_percent(percent)
        details.set_values(values)

    def _set_load_band(self, label: QLabel, band: str) -> None:
        """按档位切换占用率文字颜色（QSS 按 ``band`` 属性上色）。"""
        if label.property("band") == band:
            return
        label.setProperty("band", band)
        label.style().unpolish(label)
        label.style().polish(label)

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
