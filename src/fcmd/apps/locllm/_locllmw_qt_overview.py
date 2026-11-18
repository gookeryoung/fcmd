"""locllmw（Qt）概览页：硬件信息、占用条、智能推荐与运行中的服务。

硬件快照首次检测后写入 ``locllmw.json``，之后读缓存展示并支持重新检测；
推荐行由 :mod:`fcmd.apps.locllm._locllmw_recommend` 纯逻辑计算，性能默认标注
「预测」，同一模型跑过性能测试后改用实测值。占用条跟随监测页的系统采样刷新，
服务未启动时也能看到显存 / 内存占用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
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

from fcmd.apps.locllm._locllmw_monitor import (
    LoadSample,
    _format_gpu,
    _format_load,
    gpu_memory_percent,
    swap_percent,
)
from fcmd.apps.locllm._locllmw_recommend import Recommendation, recommend
from fcmd.apps.locllm.llmbase import human_size
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    MetricGrid,
    PageScroll,
    RowListWidget,
    SegmentBar,
    button,
    hint_label,
    link_button,
    segment_band,
)

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = [
    "OverviewPage",
    "RecommendationList",
    "RecommendationRow",
    "ScoreBadge",
    "recommendation_params_html",
    "recommendation_perf_html",
    "score_band_label",
]

#: 硬件卡里的实时负载块：主条 + 副条（显存 / 交换）+ 次要参数
_GPU_DETAILS = (
    ("显存", "gpu_vram"),
    ("服务显存", "gpu_proc_vram"),
    ("温度", "gpu_temp"),
    ("功耗", "gpu_power"),
    ("风扇", "gpu_fan"),
    ("核心频率", "gpu_clock"),
)
_CPU_DETAILS = (
    ("频率", "cpu_freq"),
    ("服务 CPU", "proc_cpu"),
    ("线程", "proc_threads"),
)
_MEMORY_DETAILS = (
    ("已用", "memory"),
    ("服务内存", "proc_mem"),
    ("运行时长", "uptime"),
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
    source = "实测" if rec.measured else "预测"
    prompt = rec.prompt_tps / 1000
    prompt_text = f"{prompt:.1f}k" if prompt >= 1 else f"{rec.prompt_tps:.0f}"
    return f"解码 <b>{rec.decode_tps:.0f}</b> tok/s · 预填充 <b>{prompt_text}</b> tok/s · <b>{source}</b>"


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
        name = ElidedLabel(recommendation.model.path.name, parent=self)
        name.setObjectName("RecommendationName")
        name.setMinimumWidth(0)
        name.setToolTip(str(recommendation.model.path))
        left.addWidget(name)
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


class OverviewPage(QWidget):  # pragma: no cover - Qt 控件
    """概览页：硬件与模型目录 → 智能推荐。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self._recommendations: tuple[Recommendation, ...] = ()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(self)
        layout = scroll.body
        outer.addWidget(scroll)
        layout.addWidget(self._build_hardware())
        layout.addWidget(self._build_recommendations(), 1)

        state.hardwareChanged.connect(lambda _snapshot: self.refresh_hardware())
        state.hardwareFailed.connect(self._on_hardware_failed)
        state.modelsChanged.connect(lambda _outcome: self.refresh_recommendations())
        state.sampleUpdated.connect(self.apply_sample)
        self.refresh_hardware()
        self.refresh_recommendations()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_hardware(self) -> Card:
        """规格与实时占用：上方硬件规格，下方 GPU / CPU / 内存三块负载条。

        占用来自监测页每秒的系统采样（``sampleUpdated``）：分段格图 + 百分比；
        GPU 显存、内存交换作为副条并排显示。顶部只保留「重新检测」文字链接与
        检测状态，不放标题 / 说明文案。
        """
        card = Card()
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._detect_button = link_button("重新检测", tooltip="重新检测 CPU / GPU / 内存")
        self._detect_button.clicked.connect(self._detect)
        self._detected_label = hint_label("", wrap=False)
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
        card.body.addLayout(grid)

        loads = QHBoxLayout()
        loads.setContentsMargins(0, 0, 0, 0)
        loads.setSpacing(8)
        gpu_block, self._gpu_percent, self._gpu_bar, self._gpu_metrics, gpu_meters = self._build_load_block(
            "GPU", _GPU_DETAILS, card, extra=("显存",)
        )
        self._gpu_vram_bar, self._gpu_vram_value = gpu_meters["显存"]
        loads.addWidget(gpu_block, 1)
        cpu_block, self._cpu_percent, self._cpu_bar, self._cpu_metrics, _cpu_meters = self._build_load_block(
            "CPU", _CPU_DETAILS, card
        )
        loads.addWidget(cpu_block, 1)
        mem_block, self._mem_percent, self._mem_bar, self._mem_metrics, mem_meters = self._build_load_block(
            "内存", _MEMORY_DETAILS, card, extra=("交换",)
        )
        self._swap_bar, self._swap_value = mem_meters["交换"]
        loads.addWidget(mem_block, 1)
        card.body.addLayout(loads)
        return card

    def _build_load_block(
        self,
        title: str,
        keys: tuple[tuple[str, str], ...],
        parent: QWidget,
        *,
        extra: tuple[str, ...] = (),
    ) -> tuple[QFrame, QLabel, SegmentBar, MetricGrid, dict[str, tuple[SegmentBar, QLabel]]]:
        """单个负载块：标题 + 占用率，主格图后可按需追加副指标格图。"""
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
        percent = QLabel("-", block)
        percent.setObjectName("LoadValue")
        percent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(percent)
        layout.addLayout(header)

        bar = SegmentBar(parent=block)
        layout.addWidget(bar)
        meters: dict[str, tuple[SegmentBar, QLabel]] = {}
        for caption in extra:
            row, meter_bar, meter_value = self._build_meter_row(caption, block)
            layout.addWidget(row)
            meters[caption] = (meter_bar, meter_value)
        details = MetricGrid(keys, columns=1, parent=block)
        layout.addWidget(details)
        return block, percent, bar, details, meters

    def _build_meter_row(self, caption: str, parent: QWidget) -> tuple[QWidget, SegmentBar, QLabel]:
        """副指标行：标签 + 分段格图 + 百分比并排，例如显存 / 交换。"""
        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = hint_label(caption, wrap=False)
        label.setMinimumWidth(32)
        bar = SegmentBar(segments=20, height=10, parent=row)
        value = QLabel("-", row)
        value.setObjectName("MeterValue")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setMinimumWidth(56)
        layout.addWidget(label)
        layout.addWidget(bar, 1)
        layout.addWidget(value)
        return row, bar, value

    def _build_recommendations(self) -> Card:
        card = Card("智能推荐（按评分排序；性能测试后优先用实测值）")
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
        row = QHBoxLayout()
        row.addStretch(1)
        self._apply_button = button("启动服务", kind="primary")
        # 与运行页顶部启停按钮同一尺寸（40px 高、更大的左右内边距与字号）。
        self._apply_button.setObjectName("RunPrimary")
        self._apply_button.setToolTip("把选中推荐行的模型与参数带入运行页")
        self._apply_button.clicked.connect(self.apply_recommendation)
        row.addWidget(self._apply_button)
        card.body.addLayout(row)
        return card

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

    def apply_sample(self, sample: object) -> None:
        """用系统采样刷新 GPU / CPU / 内存负载块（主条 + 显存 / 交换副条）。"""
        if not isinstance(sample, LoadSample):
            return
        load_values = _format_load(sample)
        gpu_values = _format_gpu(sample.gpu)
        gpu = sample.gpu
        self._apply_load_block(
            self._gpu_percent,
            self._gpu_bar,
            self._gpu_metrics,
            None if gpu is None else gpu.utilization,
            {key: gpu_values[key] for _label, key in _GPU_DETAILS},
        )
        self._apply_meter(self._gpu_vram_bar, self._gpu_vram_value, gpu_memory_percent(gpu))
        self._apply_load_block(
            self._cpu_percent,
            self._cpu_bar,
            self._cpu_metrics,
            sample.cpu,
            {key: load_values[key] for _label, key in _CPU_DETAILS},
        )
        self._apply_load_block(
            self._mem_percent,
            self._mem_bar,
            self._mem_metrics,
            sample.memory_percent if sample.memory_total else None,
            {key: load_values[key] for _label, key in _MEMORY_DETAILS},
        )
        self._apply_meter(self._swap_bar, self._swap_value, swap_percent(sample))

    def _apply_load_block(
        self,
        percent_label: QLabel,
        bar: SegmentBar,
        details: MetricGrid,
        percent: float | None,
        values: dict[str, str],
    ) -> None:
        """刷新一块负载：占用率文字 / 主格图 / 次要参数一起更新。"""
        percent_label.setText("-" if percent is None else f"{percent:.0f} %")
        self._set_band(percent_label, segment_band(percent))
        bar.set_percent(percent)
        details.set_values(values)

    def _apply_meter(self, bar: SegmentBar, value: QLabel, percent: float | None) -> None:
        """副指标（显存 / 交换）：百分比文字与格图同步，缺失时显示 ``-`` 并清空格图。"""
        value.setText("-" if percent is None else f"{percent:.1f} %")
        self._set_band(value, segment_band(percent))
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
