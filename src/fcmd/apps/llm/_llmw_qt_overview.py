"""llmw（Qt）概览页：系统信息、实时占用格图、智能推荐与运行中的服务。

硬件快照首次检测后写入 ``llmw.json``，之后读缓存展示并支持重新检测；
推荐行由 :mod:`fcmd.apps.llm._llmw_recommend` 纯逻辑计算，性能默认标注
「预测」，同一模型跑过性能测试后改用实测值。页面自身每秒采样系统负载与服务
`/metrics`，服务未启动时也能看到显存 / 内存占用。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import llmserve
from fcmd.apps.llm._llmw_monitor import (
    RATE_WINDOW_SECONDS,
    GpuSample,
    LoadSample,
    RateWindow,
    _format_gpu,
    _format_load,
    _sample_load,
    gpu_memory_percent,
    swap_percent,
)
from fcmd.apps.llm._llmw_recommend import Recommendation, recommend
from fcmd.apps.llm.llmbase import ServeError, ServeState, human_size
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    MetricGrid,
    RowListWidget,
    SegmentBar,
    StatusTag,
    button,
    hint_label,
    link_button,
    segment_band,
)
from fcmd.gui.qt.workers import periodic, submit

if TYPE_CHECKING:
    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = [
    "OverviewPage",
    "RecommendationList",
    "RecommendationRow",
    "ScoreBadge",
    "recommendation_params_html",
    "recommendation_perf_html",
    "score_band_label",
]

#: 系统信息右侧的实时性能次要指标（生成 tok/s 单独大字展示）
_PERFORMANCE_METRICS = (
    ("提示速率", "prompt_rate"),
    ("处理中请求", "processing"),
    ("排队请求", "deferred"),
    ("KV 使用率", "kv"),
    ("累计生成 tokens", "predicted"),
    ("累计提示 tokens", "prompt"),
)

_SCORE_BAND_LABELS = {
    "excellent": "极佳",
    "good": "合适",
    "fair": "可用",
    "limited": "勉强",
}


def score_band_label(band: str) -> str:
    """评分档位的中文名（未知档位退化为原值）。"""
    return _SCORE_BAND_LABELS.get(band, band)


def recommendation_params_html(recommendation: Recommendation) -> str:
    """推荐行的参数摘要：小字正文，关键参数用 ``<b>`` 加粗。"""
    rec = recommendation
    common = (
        f"档位 <b>{rec.profile}</b>",
        f"上下文 <b>{rec.context // 1024}K</b>",
        f"KV <b>{rec.kv_type}</b>",
        f"并行 <b>{rec.parallel}</b>",
    )
    tuning = (
        f"batch <b>{rec.batch}</b>",
        f"ubatch <b>{rec.ubatch}</b>",
        f"调优 <b>{rec.tuning}</b>",
    )
    return " · ".join(common) + "<br>" + " · ".join(tuning)


def recommendation_perf_html(recommendation: Recommendation) -> str:
    """推荐行的性能摘要：小字，数值与来源加粗。"""
    rec = recommendation
    prompt = rec.prompt_tps / 1000
    prompt_text = f"{prompt:.1f}k" if prompt >= 1 else f"{rec.prompt_tps:.0f}"
    return f"解码 <b>{rec.decode_tps:.0f}</b> tok/s · 预填充 <b>{prompt_text}</b> tok/s"


class ScoreBadge(QLabel):  # pragma: no cover - Qt 控件
    """左侧圆形评分框：环框与数字颜色随评分档位变化（配色取自 QSS 主题）。

    只负责展示：档位 id 写到动态属性 ``band``，具体颜色由
    ``QLabel#ScoreBadge[band="…"]`` 样式规则决定，切主题时自动跟随。
    """

    _SIZE = 44

    def __init__(self, recommendation: Recommendation, parent: QWidget | None = None) -> None:
        super().__init__(str(recommendation.score), parent)
        band = recommendation.score_band
        self.setObjectName("ScoreBadge")
        self.setProperty("band", band)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(self._SIZE, self._SIZE)
        summary = f"{recommendation.score_text}（{score_band_label(band)}）"
        self.setToolTip(f"{summary}\n{recommendation.score_detail}")
        self.setAccessibleName(summary)


class RecommendationRow(QFrame):  # pragma: no cover - Qt 控件
    """单条推荐的自定义列表行：左侧评分圆框，模型名大字，配置 / 性能小字补充。

    行边框与底色按评分档位区分色系（QSS 按 ``band`` 属性上色），选中只加粗边框。
    """

    def __init__(self, recommendation: Recommendation, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.recommendation = recommendation
        self.setObjectName("RecommendationRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("band", recommendation.score_band)
        self.setToolTip(
            f"{recommendation.basis}\n{recommendation.score_text}（{score_band_label(recommendation.score_band)}）"
            f" · {recommendation.score_detail}"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(12)
        self.score_badge = ScoreBadge(recommendation, self)
        outer.addWidget(self.score_badge, 0)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name = ElidedLabel(recommendation.model.path.name, parent=self)
        name.setObjectName("RecommendationName")
        name.setMinimumWidth(0)
        name.setToolTip(str(recommendation.model.path))
        self.perf_tag = StatusTag(
            "实测" if recommendation.measured else "预测",
            tone="success" if recommendation.measured else "accent",
            parent=self,
        )
        self.perf_tag.setToolTip("性能来自本地实测" if recommendation.measured else "性能按参考机器外推")
        name_row.addWidget(name, 1)
        name_row.addWidget(self.perf_tag, 0)
        left.addLayout(name_row)
        params = QLabel(recommendation_params_html(recommendation), self)
        params.setObjectName("Hint")
        params.setTextFormat(Qt.TextFormat.RichText)
        params.setWordWrap(False)
        params.setMinimumWidth(0)
        params.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left.addWidget(params)
        outer.addLayout(left, 1)

        perf = QLabel(recommendation_perf_html(recommendation), self)
        perf.setObjectName("Hint")
        perf.setTextFormat(Qt.TextFormat.RichText)
        perf.setWordWrap(False)
        perf.setMinimumWidth(0)
        perf.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        perf.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(perf, 0)

    def set_selected(self, selected: bool) -> None:
        """切换选中态边框（列表当前行变化时由页面同步）。"""
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class RecommendationList(RowListWidget):  # pragma: no cover - Qt 控件
    """推荐列表：复用自定义行列表的行宽同步，右侧性能摘要不会被挤出视口。"""


def sample_once(state: ServeState | None) -> tuple[LoadSample, llmserve.ServeMetrics | None, str | None]:
    """采样一次系统负载与服务指标（阻塞，供线程池调用）。

    ``state`` 为 ``None`` 时只采系统负载；服务指标抓取失败时把原因放在第三个
    返回值里，由界面显示「采样失败」而不是抛异常。
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


class OverviewPage(QWidget):  # pragma: no cover - Qt 控件
    """概览页：硬件与模型目录 → 智能推荐。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._recommendations: tuple[Recommendation, ...] = ()
        self._target_name = ""
        self._window = RATE_WINDOW_SECONDS
        self._rates = RateWindow()
        self._inflight = False
        self._timer: QTimer | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(8)
        layout.addWidget(self._build_hardware())
        layout.addWidget(self._build_recommendations(), 1)
        layout.addLayout(self._build_recommend_actions())

        state.hardwareChanged.connect(lambda _snapshot: self.refresh_hardware())
        state.hardwareFailed.connect(self._on_hardware_failed)
        state.modelsChanged.connect(lambda _outcome: self.refresh_recommendations())
        state.servicesChanged.connect(lambda _states: self._sync_target())
        self.refresh_hardware()
        self.refresh_recommendations()
        self._timer = periodic(self, 1000, self._tick)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_hardware(self) -> Card:
        """规格与实时占用：上方硬件规格，下方 GPU / CPU / 内存三块负载条。

        占用来自概览页每秒的系统采样：分段格图 + 百分比；
        GPU 显存、内存交换作为副条并排显示。顶部只保留「重新检测」文字链接与
        检测状态，不放标题 / 说明文案。
        """
        card = Card()
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._hardware_title = QLabel("系统信息", card)
        self._hardware_title.setObjectName("SectionTitle")
        self._detect_button = link_button("重新检测", tooltip="重新检测 CPU / GPU / 内存")
        self._detect_button.clicked.connect(self._detect)
        self._detected_label = hint_label("", wrap=False)
        header.addWidget(self._hardware_title)
        header.addStretch(1)
        header.addWidget(self._detected_label)
        header.addWidget(self._detect_button)
        card.body.addLayout(header)

        self._hardware_values: dict[str, QLabel] = {}

        def row_label(text: str) -> QLabel:
            label = QLabel(text, card)
            label.setObjectName("Hint")
            return label

        def value_label() -> QLabel:
            value = QLabel("检测中…", card)
            value.setWordWrap(True)
            value.setMinimumWidth(0)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            return value

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 48)

        gpu_value = value_label()
        grid.addWidget(row_label("显卡"), 0, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(gpu_value, 0, 1)
        self._hardware_values["gpu"] = gpu_value

        cpu_value = value_label()
        grid.addWidget(row_label("处理器"), 1, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(cpu_value, 1, 1)
        self._hardware_values["cpu"] = cpu_value

        memory_value = value_label()
        grid.addWidget(row_label("内存"), 2, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(memory_value, 2, 1)
        self._hardware_values["memory"] = memory_value
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(16)
        top.addLayout(grid, 1)
        top.addWidget(self._build_performance(card), 1)
        card.body.addLayout(top)

        loads = QHBoxLayout()
        loads.setContentsMargins(0, 0, 0, 0)
        loads.setSpacing(8)
        gpu_block, self._gpu_percent, self._gpu_bar, self._gpu_hint = self._build_load_block("GPU", card)
        loads.addWidget(gpu_block, 1)
        cpu_block, self._cpu_percent, self._cpu_bar, self._cpu_hint = self._build_load_block("CPU", card)
        loads.addWidget(cpu_block, 1)
        mem_block, self._mem_percent, self._mem_bar, self._mem_hint = self._build_load_block("内存", card)
        loads.addWidget(mem_block, 1)
        card.body.addLayout(loads)
        return card

    def _build_performance(self, parent: QWidget) -> QWidget:
        """系统信息右侧的实时性能：生成 tok/s 主视觉 + 服务 / 请求次要指标。

        数据由概览页每秒采样计算；这里只负责展示，
        与左侧规格共用一行，避免系统信息卡片右侧留白。
        """
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("实时性能", panel)
        title.setObjectName("SectionTitle")
        self._performance_badge = StatusTag("等待采样…", tone="muted", parent=panel)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._performance_badge)
        layout.addLayout(header)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        self._gen_rate_label = QLabel("-", panel)
        self._gen_rate_label.setObjectName("RateValue")
        self._gen_rate_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        unit = QLabel("tok/s", panel)
        unit.setObjectName("RateUnit")
        value_row.addWidget(self._gen_rate_label, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addWidget(unit, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addWidget(hint_label("10s 平滑", wrap=False), 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        layout.addLayout(value_row)

        self._performance_metrics = MetricGrid(_PERFORMANCE_METRICS, columns=2, parent=panel)
        layout.addWidget(self._performance_metrics)
        layout.addStretch(1)
        return panel

    def _build_load_block(
        self,
        title: str,
        parent: QWidget,
    ) -> tuple[QFrame, QLabel, SegmentBar, ElidedLabel]:
        """单个负载块：标题 + 小字关键信息 + 占用率 + 主格图。"""
        block = QFrame(parent)
        block.setObjectName("LoadBlock")
        block.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(block)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        heading = QLabel(title, block)
        heading.setObjectName("SectionTitle")
        hint = ElidedLabel("等待采样…", parent=block)
        hint.setObjectName("Hint")
        percent = QLabel("-", block)
        percent.setObjectName("LoadValue")
        percent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(heading)
        header.addWidget(hint, 1)
        header.addWidget(percent)
        layout.addLayout(header)

        bar = SegmentBar(parent=block)
        layout.addWidget(bar)
        layout.addStretch(1)
        return block, percent, bar, hint

    def _build_recommendations(self) -> Card:
        card = Card("智能推荐")
        self._recommend_list = RecommendationList(card)
        self._recommend_list.setObjectName("RecommendationList")
        self._recommend_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._recommend_list.setSpacing(4)
        self._recommend_list.setMinimumHeight(120)
        self._recommend_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._recommend_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recommend_list.currentRowChanged.connect(self._on_recommendation_selected)
        self._recommend_list.itemDoubleClicked.connect(lambda _item: self.apply_recommendation())
        self._recommend_list.itemActivated.connect(lambda _item: self.apply_recommendation())
        card.body.addWidget(self._recommend_list, 1)
        return card

    def _build_recommend_actions(self) -> QHBoxLayout:
        """启动按钮放在推荐列表卡片之外，避免列表滚动区同时承载操作。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self._apply_button = button("启动服务", kind="primary")
        # 与运行页顶部启停按钮同一尺寸（40px 高、更大的左右内边距与字号）。
        self._apply_button.setObjectName("RunPrimary")
        self._apply_button.setToolTip("把选中推荐行的模型与参数带入运行页")
        self._apply_button.clicked.connect(self.apply_recommendation)
        row.addWidget(self._apply_button)
        return row

    def _render_recommendations(self) -> None:
        """按 ``self._recommendations`` 重建自定义列表行。"""
        self._recommend_list.clear()
        for recommendation in self._recommendations:
            item = QListWidgetItem(self._recommend_list)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            row = RecommendationRow(recommendation, self._recommend_list)
            # 宽度交给列表铺满视口（0 不会限制宽度），只固定行高，避免长模型名
            # 把行撑到视口之外导致右侧内容被裁。
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self._recommend_list.setItemWidget(item, row)
        self._recommend_list.sync_row_widths()
        if self._recommendations:
            self._recommend_list.setCurrentRow(0)

    def _on_recommendation_selected(self, current: int) -> None:
        for index in range(self._recommend_list.count()):
            item = self._recommend_list.item(index)
            row = self._recommend_list.itemWidget(item)
            if isinstance(row, RecommendationRow):
                row.set_selected(index == current)

    # ------------------------------------------------------------------ #
    # 硬件
    # ------------------------------------------------------------------ #
    def _detect(self) -> None:
        self._detect_button.setEnabled(False)
        self._detected_label.setText("正在检测硬件…")
        self.state.begin_loading("hardware", "正在检测硬件…")
        self.state.detect_hardware(on_done=lambda: self._detect_button.setEnabled(True))

    def _on_hardware_failed(self, message: str) -> None:
        self.state.end_loading("hardware")
        self._detect_button.setEnabled(True)
        for value in self._hardware_values.values():
            value.setText("检测失败")
        self._detected_label.setText(f"检测失败：{message}")

    def refresh_hardware(self) -> None:
        """按当前快照（缓存或新检测）刷新硬件卡片。"""
        snapshot = self.state.hardware
        if snapshot is None:
            if self.state.detecting:
                self.state.begin_loading("hardware", "正在检测硬件…")
            else:
                self.state.end_loading("hardware")
            for value in self._hardware_values.values():
                value.setText("尚未检测" if not self.state.detecting else "检测中…")
            self._detected_label.setText("尚未检测硬件" if not self.state.detecting else "正在检测硬件…")
            return
        self.state.end_loading("hardware")
        self._detect_button.setEnabled(True)
        theme = _qt_theme.load_theme()
        if snapshot.gpu_mib:
            gpu = _qt_theme.tiered_value_html(
                theme,
                snapshot.gpu,
                f"{snapshot.gpu_mib / 1024:.0f} GB 显存",
            )
        else:
            gpu = "未检测到 NVIDIA GPU（CPU 推理）"
        self._hardware_values["gpu"].setText(gpu)
        self._hardware_values["cpu"].setText(
            _qt_theme.tiered_value_html(theme, snapshot.cpu, f"{snapshot.cpu_cores} 核 / {snapshot.cpu_threads} 线程")
        )
        memory_detail = snapshot.memory_gen
        self._hardware_values["memory"].setText(
            _qt_theme.tiered_value_html(
                theme,
                human_size(snapshot.memory_total) if snapshot.memory_total else "-",
                memory_detail,
            )
        )
        self._detected_label.setText("")

    # ------------------------------------------------------------------ #
    # 采样
    # ------------------------------------------------------------------ #
    def set_target_name(self, name: str) -> None:
        """指定要采样的服务名（运行页切服务时同步）。"""
        self._target_name = name
        self._sync_target()

    def _sync_target(self) -> None:
        """服务列表变化后重置速率窗口，并给出等待 / 仅系统负载占位。"""
        self._rates = RateWindow(self._window)
        if self._current_state() is None:
            self._set_performance(None, None, None, "服务未运行（仅系统负载）", "warning")
            return
        self._set_performance(None, None, None, "等待采样…", "muted")

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
        submit(sample_once, self._current_state(), on_done=self._apply_sample, on_finished=self._sample_finished)

    def _sample_finished(self) -> None:
        self._inflight = False

    def _apply_sample(self, payload: tuple[LoadSample, Any, str | None]) -> None:
        load, metrics, error = payload
        self._apply_system_sample(load)
        self._sync_free_vram(load.gpu)
        if metrics is None:
            if error is not None:
                self._set_performance(None, None, None, "采样失败: " + error[:60], "danger")
            else:
                self._set_performance(None, None, None, "服务未运行（仅系统负载）", "warning")
            return
        gen_rate, prompt_rate = self._rates.update(time.monotonic(), metrics.predicted_tokens, metrics.prompt_tokens)
        self._set_performance(gen_rate, prompt_rate, metrics, "监控中", "success")

    def _apply_system_sample(self, sample: LoadSample) -> None:
        """用系统采样刷新 GPU / CPU / 内存负载块（关键信息小字 + 主格图）。"""
        gpu = sample.gpu
        gpu_values = _format_gpu(gpu)
        gpu_vram = gpu_memory_percent(gpu)
        gpu_parts = [
            "未检测到 GPU" if gpu is None else f"显存 {gpu_vram:.0f}%" if gpu_vram is not None else "显存 -",
            gpu_values["gpu_power"],
        ]
        self._set_hint(self._gpu_hint, gpu_parts)
        self._apply_load_block(self._gpu_percent, self._gpu_bar, None if gpu is None else gpu.utilization)

        load_values = _format_load(sample)
        cpu_parts = []
        if load_values["cpu_freq"] != "-":
            cpu_parts.append(f"频率 {load_values['cpu_freq']}")
        if load_values["proc_cpu"] != "-":
            cpu_parts.append(f"服务 {load_values['proc_cpu']}")
        self._set_hint(self._cpu_hint, cpu_parts)
        self._apply_load_block(self._cpu_percent, self._cpu_bar, sample.cpu)

        memory_parts = [
            f"{human_size(sample.memory_used)} / {human_size(sample.memory_total)}"
            if sample.memory_total
            else "已用 -",
        ]
        swap = swap_percent(sample)
        memory_parts.append("交换 -" if swap is None else f"交换 {swap:.0f}%")
        self._set_hint(self._mem_hint, memory_parts)
        self._apply_load_block(self._mem_percent, self._mem_bar, sample.memory_percent if sample.memory_total else None)

    def _set_performance(
        self,
        gen_rate: float | None,
        prompt_rate: float | None,
        metrics: llmserve.ServeMetrics | None,
        status: str,
        tone: str,
    ) -> None:
        """刷新右侧实时性能：tok/s 大字、次要指标与状态标签。"""
        self._gen_rate_label.setText("-" if gen_rate is None else f"{gen_rate:.1f}")
        if metrics is None:
            self._performance_metrics.set_values({key: "-" for _label, key in _PERFORMANCE_METRICS})
        else:
            self._performance_metrics.set_values(
                {
                    "prompt_rate": "-" if prompt_rate is None else f"{prompt_rate:.1f} tok/s",
                    "processing": f"{metrics.processing:.0f}",
                    "deferred": f"{metrics.deferred:.0f}",
                    "kv": f"{metrics.kv_usage * 100:.1f} %",
                    "predicted": f"{metrics.predicted_tokens:.0f}",
                    "prompt": f"{metrics.prompt_tokens:.0f}",
                }
            )
        self._performance_badge.set_status(status, tone)

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

    def stop(self) -> None:
        """窗口关闭时停止采样定时器。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    @staticmethod
    def _set_hint(label: QLabel, parts: list[str]) -> None:
        """拼接标题后的小字关键信息；占位 ``-`` 不参与拼接。"""
        label.setText(" · ".join(part for part in parts if part and part != "-"))

    def _apply_load_block(self, percent_label: QLabel, bar: SegmentBar, percent: float | None) -> None:
        """刷新一块负载：占用率文字与主格图同步。"""
        percent_label.setText("-" if percent is None else f"{percent:.0f} %")
        self._set_band(percent_label, segment_band(percent))
        bar.set_percent(percent)

    def _set_band(self, label: QLabel, band: str) -> None:
        """按档位切换数值文字颜色（QSS 按 ``band`` 属性上色）。"""
        if label.property("band") == band:
            return
        label.setProperty("band", band)
        label.style().unpolish(label)
        label.style().polish(label)

    # ------------------------------------------------------------------ #
    # 推荐
    # ------------------------------------------------------------------ #
    def refresh_recommendations(self) -> None:
        """按当前模型清单 + 硬件快照重算推荐行。"""
        rows = recommend(self.state.models, self.state.hardware, self.state.measured)
        self._recommendations = rows
        self._render_recommendations()
        if not rows:
            self.state.status(
                "未检测到可用模型：先在「模型」页刷新，或在「设置 → 存储」中登记模型目录。"
                if not self.state.models
                else "当前模型都放不进整卡显存：建议换更小量化，或释放显存后重新检测。"
            )

    def apply_recommendation(self) -> None:
        """把推荐行的模型与启动配置带到运行页。"""
        row = self._recommend_list.currentRow()
        if row < 0 or row >= len(self._recommendations):
            self.state.status("先在推荐列表中选择一行，再点「启动服务」。")
            return
        recommendation = self._recommendations[row]
        self.state.recommendationApplied.emit(recommendation)
        self.state.navigate("run")
