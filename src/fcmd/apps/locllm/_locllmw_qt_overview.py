"""locllmw（Qt）概览页：硬件信息、占用条、智能推荐与运行中的服务。

硬件快照首次检测后写入 ``locllmw.json``，之后读缓存展示并支持重新检测；
推荐行由 :mod:`fcmd.apps.locllm._locllmw_recommend` 纯逻辑计算，性能默认标注
「预测」，同一模型跑过性能测试后改用实测值。占用条跟随监测页的系统采样刷新，
服务未启动时也能看到显存 / 内存占用。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import _llmtune, llmenv, llmserve
from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot, disk_for_path, format_disk
from fcmd.apps.locllm._locllmw_monitor import LoadSample
from fcmd.apps.locllm._locllmw_recommend import Recommendation, recommend
from fcmd.apps.locllm.llmbase import ServeState, human_size
from fcmd.apps.locllm.llmserve import read_log
from fcmd.gui.qt.widgets import Card, PageScroll, button, hint_label, make_table, make_table_item
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["OverviewPage", "loaded_row_values", "usage_texts"]

RECOMMEND_COLUMNS = ("推荐模型", "启动配置", "性能", "依据")
LOADED_COLUMNS = ("服务", "已加载模型", "档位 / ctx", "地址")


def loaded_row_values(state: ServeState) -> tuple[str, str, str, str]:
    """已加载服务表的一行（与 :data:`LOADED_COLUMNS` 对应）。"""
    return (state.name, Path(state.model).name, f"{state.profile} / ctx {state.context}", state.base_url)


def usage_texts(sample: LoadSample) -> tuple[str, float, str, float]:
    """把采样换算成（显存文案, 显存百分比, 内存文案, 内存百分比）。"""
    gpu = sample.gpu
    if gpu is not None and gpu.memory_used is not None and gpu.memory_total:
        percent = min(max(gpu.memory_used / gpu.memory_total * 100, 0.0), 100.0)
        gpu_text = f"{gpu.memory_used / 1024:.1f} / {gpu.memory_total / 1024:.1f} GB（{percent:.1f} %）"
    else:
        percent = 0.0
        gpu_text = "未检测到 NVIDIA GPU" if gpu is None else "暂不可用"
    if sample.memory_total:
        memory_percent = min(max(sample.memory_percent, 0.0), 100.0)
        memory_text = f"{human_size(sample.memory_used)} / {human_size(sample.memory_total)}（{memory_percent:.1f} %）"
    else:
        memory_percent = 0.0
        memory_text = "暂不可用"
    return gpu_text, percent, memory_text, memory_percent


class OverviewPage(QWidget):  # pragma: no cover - Qt 控件
    """概览页：硬件与模型目录 → 智能推荐 → 已加载模型。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._recommendations: tuple[Recommendation, ...] = ()
        self._loaded_states: list[ServeState] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(self)
        layout = scroll.body
        outer.addWidget(scroll)
        layout.addWidget(self._build_hardware())
        layout.addWidget(self._build_recommendations())
        layout.addWidget(self._build_loaded(), 1)

        state.hardwareChanged.connect(lambda _snapshot: self.refresh_hardware())
        state.hardwareFailed.connect(self._on_hardware_failed)
        state.modelsChanged.connect(lambda _outcome: self.refresh_recommendations())
        state.servicesChanged.connect(self.set_services)
        state.sampleUpdated.connect(self.apply_sample)
        self.refresh_hardware()
        self.refresh_recommendations()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_hardware(self) -> Card:
        card = Card("硬件信息")
        self._hardware_values: dict[str, QLabel] = {}
        rows = (("显卡", "gpu"), ("处理器", "cpu"), ("内存", "memory"), ("模型目录", "model_disk"))
        for label, key in rows:
            row = QHBoxLayout()
            name = hint_label(label, wrap=False)
            name.setFixedWidth(64)
            value = QLabel("检测中…", card)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(value, 1)
            if key == "model_disk":
                settings_button = button("去设置修改…", kind="ghost")
                settings_button.clicked.connect(lambda: self.state.navigate("settings"))
                row.addWidget(settings_button, 0, Qt.AlignmentFlag.AlignTop)
            card.body.addLayout(row)
            self._hardware_values[key] = value

        self._usage_bars: dict[str, QProgressBar] = {}
        self._usage_labels: dict[str, QLabel] = {}
        for label, key in (("显存占用", "gpu"), ("内存占用", "memory")):
            row = QHBoxLayout()
            name = hint_label(label, wrap=False)
            name.setFixedWidth(64)
            bar = QProgressBar(card)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(False)
            text = hint_label("等待采样…", wrap=False)
            text.setMinimumWidth(220)
            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(text)
            card.body.addLayout(row)
            self._usage_bars[key] = bar
            self._usage_labels[key] = text

        footer = QHBoxLayout()
        self._detected_label = hint_label("尚未检测硬件")
        self._detect_button = button("重新检测硬件", kind="ghost")
        self._detect_button.clicked.connect(self._detect)
        footer.addWidget(self._detected_label, 1)
        footer.addWidget(self._detect_button)
        card.body.addLayout(footer)
        return card

    def _build_recommendations(self) -> Card:
        card = Card("智能推荐（按显存筛选；性能测试后优先用实测值）")
        self._recommend_table = make_table(RECOMMEND_COLUMNS, widths=(260, 320, 300, 160), stretch_columns=(0, 1, 2))
        self._recommend_table.setMinimumHeight(110)
        self._recommend_table.doubleClicked.connect(lambda _index: self.apply_recommendation())
        card.body.addWidget(self._recommend_table)
        row = QHBoxLayout()
        self._recommend_status = hint_label("等待硬件检测与模型扫描…")
        self._apply_button = button("用此配置启动")
        self._apply_button.clicked.connect(self.apply_recommendation)
        row.addWidget(self._recommend_status, 1)
        row.addWidget(self._apply_button)
        card.body.addLayout(row)
        return card

    def _build_loaded(self) -> Card:
        card = Card("已加载模型（运行中的服务）")
        self._loaded_table = make_table(LOADED_COLUMNS, widths=(90, 360, 140, 170), stretch_columns=(1,))
        self._loaded_table.setMinimumHeight(100)
        card.body.addWidget(self._loaded_table)
        self._loaded_hint = hint_label("无运行中的服务")
        card.body.addWidget(self._loaded_hint)
        actions = QHBoxLayout()
        for text, slot in (
            ("打开 Web UI", self._open_web_ui),
            ("查看日志", self._show_logs),
            ("复制地址", lambda: self._copy("address")),
            ("复制 API Key", lambda: self._copy("api_key")),
        ):
            widget = button(text, kind="ghost")
            widget.clicked.connect(slot)
            actions.addWidget(widget)
        actions.addStretch(1)
        stop_button = button("停止服务", kind="danger")
        stop_button.clicked.connect(self._stop_selected)
        actions.addWidget(stop_button)
        card.body.addLayout(actions)
        return card

    # ------------------------------------------------------------------ #
    # 硬件
    # ------------------------------------------------------------------ #
    def _detect(self) -> None:
        self._detect_button.setEnabled(False)
        self._detected_label.setText("正在检测硬件…")
        self.state.detect_hardware(on_done=lambda: self._detect_button.setEnabled(True))

    def _on_hardware_failed(self, message: str) -> None:
        self._detect_button.setEnabled(True)
        for value in self._hardware_values.values():
            value.setText("检测失败")
        self._detected_label.setText(f"检测失败：{message}")

    def refresh_hardware(self) -> None:
        """按当前快照（缓存或新检测）刷新硬件卡片。"""
        snapshot = self.state.hardware
        if snapshot is None:
            for value in self._hardware_values.values():
                value.setText("尚未检测" if not self.state.detecting else "检测中…")
            self._detected_label.setText("尚未检测硬件" if not self.state.detecting else "正在检测硬件…")
            return
        self._detect_button.setEnabled(True)
        gpu = (
            f"{snapshot.gpu} · {snapshot.gpu_mib / 1024:.0f} GB 显存"
            if snapshot.gpu_mib
            else "未检测到 NVIDIA GPU（CPU 推理）"
        )
        self._hardware_values["gpu"].setText(gpu)
        self._hardware_values["cpu"].setText(f"{snapshot.cpu}（{snapshot.cpu_cores} 核 / {snapshot.cpu_threads} 线程）")
        self._hardware_values["memory"].setText(human_size(snapshot.memory_total) if snapshot.memory_total else "-")
        model_path = self.state.selected_model or Path(snapshot.model_root or ".")
        disk = disk_for_path(model_path, snapshot.disks)
        root_text = str(model_path if self.state.selected_model else (snapshot.model_root or "未配置"))
        self._hardware_values["model_disk"].setText(f"{root_text}\n{format_disk(disk)}")
        self._detected_label.setText(f"检测时间 {snapshot.detected_at or '未知'}；模型目录随选中模型或主模型目录变化")

    def apply_sample(self, sample: object) -> None:
        """用系统采样刷新显存 / 内存占用条。"""
        if not isinstance(sample, LoadSample):
            return
        gpu_text, gpu_percent, memory_text, memory_percent = usage_texts(sample)
        self._usage_bars["gpu"].setValue(int(gpu_percent))
        self._usage_bars["memory"].setValue(int(memory_percent))
        self._usage_labels["gpu"].setText(gpu_text)
        self._usage_labels["memory"].setText(memory_text)

    # ------------------------------------------------------------------ #
    # 推荐
    # ------------------------------------------------------------------ #
    def refresh_recommendations(self) -> None:
        """按当前模型清单 + 硬件快照重算推荐行。"""
        rows = recommend(self.state.models, self.state.hardware, self.state.measured)
        self._recommendations = rows
        self._recommend_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (row.model.path.name, row.config_text, row.performance_text, row.basis)
            for column, text in enumerate(values):
                self._recommend_table.setItem(index, column, make_table_item(text))
        if not rows:
            self._recommend_status.setText(
                "未检测到可用模型：先在「模型」页刷新，或在「设置 → 存储」中登记模型目录。"
                if not self.state.models
                else "当前模型都放不进整卡显存：建议换更小量化，或释放显存后重新检测。"
            )
            return
        self._recommend_status.setText(
            "推荐按模型大小与显存适配排序；选中一行点「用此配置启动」把模型与参数带入运行页。"
            "解码速度为「预测」时来自参考机器外推，跑过性能测试后同一模型自动切换为「实测」。"
        )

    def apply_recommendation(self) -> None:
        """把推荐行的模型与启动配置带到运行页。"""
        row = self._recommend_table.currentRow()
        if row < 0 or row >= len(self._recommendations):
            self._recommend_status.setText("先在推荐表中选择一行，再点「用此配置启动」。")
            return
        recommendation = self._recommendations[row]
        self.state.recommendationApplied.emit(recommendation)
        self.state.navigate("run")

    # ------------------------------------------------------------------ #
    # 已加载服务
    # ------------------------------------------------------------------ #
    def set_services(self, states: list[ServeState]) -> None:
        """刷新「已加载模型」表。"""
        self._loaded_states = list(states)
        self._loaded_table.setRowCount(len(states))
        for index, state in enumerate(states):
            for column, text in enumerate(loaded_row_values(state)):
                self._loaded_table.setItem(index, column, make_table_item(text))
        self._loaded_hint.setText("" if states else "无运行中的服务")

    def _selected_state(self) -> ServeState | None:
        row = self._loaded_table.currentRow()
        if 0 <= row < len(self._loaded_states):
            return self._loaded_states[row]
        self._loaded_hint.setText("先在列表中选择一个运行中的服务")
        return None

    def _open_web_ui(self) -> None:
        state = self._selected_state()
        if state is None:
            return
        import webbrowser

        webbrowser.open(state.base_url)
        self.state.log(f"已在浏览器中打开: {state.base_url}")

    def _show_logs(self) -> None:
        state = self._selected_state()
        if state is None:
            return
        try:
            content = read_log(state.name, 50)
        except Exception as exc:
            QMessageBox.critical(self, "日志不可用", str(exc))
            return
        self.state.navigate("monitor")
        self.state.log(f"日志尾部（{state.name}）:")
        self.state.log(content)

    def _copy(self, field: str) -> None:
        state = self._selected_state()
        if state is None:
            return
        text = state.base_url if field == "address" else state.api_key
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        message = "已复制地址: " + text if field == "address" else "已复制 API Key"
        self.state.status(message)
        self.state.log(message)

    def _stop_selected(self) -> None:
        state = self._selected_state()
        if state is None:
            return
        answer = QMessageBox.question(self, "停止选中服务", f"停止服务：{state.name}？\n其他服务不受影响。")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.state.set_busy(True)
        submit(
            llmserve.stop_server,
            state.name,
            on_done=self._on_stopped,
            on_error=lambda message: QMessageBox.critical(self, "停止失败", message),
            on_finished=lambda: self.state.set_busy(False),
        )

    def _on_stopped(self, result: object) -> None:
        was_running = getattr(result, "was_running", False)
        name = getattr(getattr(result, "state", None), "name", "")
        if was_running:
            self.state.log(f"已停止服务 {name}")
            self.state.status(f"已停止服务 {name}")
        else:
            self.state.log(f"服务未在运行，已清理残留状态: {name}")
        self.state.refresh_services()
        self.state.refresh_models()


_ = HardwareSnapshot  # 类型引用：硬件快照由共享状态持有，页面只读
_ = _llmtune
_ = llmenv
