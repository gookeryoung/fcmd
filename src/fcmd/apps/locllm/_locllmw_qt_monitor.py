"""locllmw（Qt）监测页：服务实时性能（系统占用展示在概览页硬件卡片）。

采样沿用既有口径：服务运行时每秒抓一次 ``/metrics`` 并按时间窗口平滑
token 速率（:class:`fcmd.apps.locllm._locllmw_monitor.RateWindow`），同时用
``psutil`` / ``nvidia-smi`` 采样系统负载；服务未运行时只采系统负载。

Qt 侧把「一次采样」封装成纯函数 :func:`sample_once`（可在无 Qt 环境单测），
GUI 用 ``QTimer`` 每秒投递一个后台任务，任务未完成时跳过下一次，避免
``nvidia-smi`` 偶发变慢时堆叠线程。采样结果通过 ``sampleUpdated`` 交给概览页
硬件卡片的 GPU / CPU / 内存负载块，监测页本身只展示服务侧 tok/s 指标。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
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
    _sample_load,
)
from fcmd.apps.locllm.llmbase import ServeError, ServeState
from fcmd.gui.qt.widgets import (
    Card,
    PageScroll,
    StatusTag,
    hint_label,
    link_button,
    value_label,
)
from fcmd.gui.qt.workers import periodic, submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["MonitorPage", "sample_once"]


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
        self._monitor_badge = StatusTag("等待采样…", tone="muted", parent=card)
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
            self._set_monitor_badge("仅系统负载", "warning")
            self.state.performanceChanged.emit(self._empty_performance("服务未运行（仅系统负载）", "warning"))
            return
        self._target_label.setText(f"服务：{target.name} · {target.base_url}")
        self._set_monitor_badge("等待采样…", "muted")
        self.state.performanceChanged.emit(self._empty_performance("等待采样…", "muted"))

    def _set_monitor_badge(self, text: str, tone: str) -> None:
        """状态徽标：用胶囊标签显示监控 / 仅系统负载 / 采样失败。"""
        self._monitor_badge.set_status(text, tone)

    @staticmethod
    def _empty_performance(status: str, tone: str) -> dict[str, str]:
        """无服务 / 未采样时的实时性能占位负载（概览系统信息右侧使用）。"""
        payload = {
            "gen_rate": "-",
            "prompt_rate": "-",
            "processing": "-",
            "deferred": "-",
            "kv": "-",
            "predicted": "-",
            "prompt": "-",
        }
        payload["monitor"] = status
        payload["tone"] = tone
        return payload

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
        self._last_sample_label.setText(f"最近采样：{time.strftime('%H:%M:%S')}")
        self.state.sampleUpdated.emit(load)
        self._sync_free_vram(load.gpu)
        if metrics is None:
            if error is not None:
                self._set_monitor_badge("采样失败", "danger")
                self.state.performanceChanged.emit(self._empty_performance("采样失败: " + error[:60], "danger"))
            else:
                self._set_monitor_badge("仅系统负载", "warning")
                self.state.performanceChanged.emit(self._empty_performance("服务未运行（仅系统负载）", "warning"))
            return
        gen_rate, prompt_rate = self._rates.update(time.monotonic(), metrics.predicted_tokens, metrics.prompt_tokens)
        self.state.performanceChanged.emit(
            {
                "gen_rate": f"{gen_rate:.1f}",
                "prompt_rate": f"{prompt_rate:.1f} tok/s",
                "processing": f"{metrics.processing:.0f}",
                "deferred": f"{metrics.deferred:.0f}",
                "kv": f"{metrics.kv_usage * 100:.1f} %",
                "predicted": f"{metrics.predicted_tokens:.0f}",
                "prompt": f"{metrics.prompt_tokens:.0f}",
                "monitor": "监控中",
                "tone": "success",
            }
        )
        self._set_monitor_badge("监控中", "success")

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
