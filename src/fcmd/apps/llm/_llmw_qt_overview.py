"""llmw（Qt）概览页：系统信息、实时占用格图、运行中的模型与智能推荐。

硬件快照首次检测后写入 ``llmw.json``，之后读缓存展示并支持重新检测；
推荐行由 :mod:`fcmd.apps.llm._llmw_recommend` 纯逻辑计算，性能默认标注
「预测」，同一模型跑过性能测试后改用实测值。系统信息卡片右侧是 GPU / CPU / 内存
实时格图，底部「运行中的模型」逐个列出运行中服务并绑定各自的 `/metrics` 实时性能；
服务未启动时也能看到显存 / 内存占用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
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

from fcmd.apps.llm import _llmpaint_server, llmserve
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
from fcmd.gui import style as _qt_theme
from fcmd.gui.page import AppPage
from fcmd.gui.widgets import (
    Card,
    ElidedLabel,
    RowListWidget,
    SegmentBar,
    SelectionRow,
    StatusTag,
    button,
    hint_label,
    link_button,
    section_header,
    segment_band,
)
from fcmd.gui.workers import periodic, submit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = [
    "OverviewPage",
    "PaintSample",
    "RecommendationList",
    "RecommendationRow",
    "RunningModelRow",
    "ScoreBadge",
    "ServiceSample",
    "paint_running_subtitle",
    "paint_running_title",
    "paint_sample_text",
    "performance_metrics_text",
    "performance_rate_text",
    "recommendation_params_html",
    "recommendation_perf_html",
    "running_model_subtitle",
    "running_model_title",
    "sample_overview",
    "score_band_label",
]

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


def running_model_title(state: ServeState) -> str:
    """运行中模型行的标题：模型文件名，缺路径时回退服务名。"""
    return Path(state.model).name if state.model else state.name


def running_model_subtitle(state: ServeState) -> str:
    """运行中模型行的副标题：服务名 · 接入地址 · 档位。"""
    return f"{state.name} · {state.base_url} · {state.profile}"


def performance_rate_text(rate: float | None) -> str:
    """解码速率的展示文本（tok/s 大字旁边的数值）。"""
    return "-" if rate is None else f"{rate:.1f}"


def performance_metrics_text(
    metrics: llmserve.ServeMetrics | None,
    prompt_rate: float | None,
) -> str:
    """运行中模型的一行性能摘要：预填充 / 请求队列 / KV / 累计 tokens。"""
    if metrics is None:
        return "等待采样…"
    prompt = "-" if prompt_rate is None else f"{prompt_rate:.1f}"
    return (
        f"预填充 {prompt} tok/s · 处理中 {metrics.processing:.0f} · 排队 {metrics.deferred:.0f}"
        f" · KV {metrics.kv_usage * 100:.1f}%"
        f" · tokens {metrics.predicted_tokens:.0f} / {metrics.prompt_tokens:.0f}"
    )


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


class RecommendationRow(SelectionRow, QFrame):  # pragma: no cover - Qt 控件
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


class RecommendationList(RowListWidget):  # pragma: no cover - Qt 控件
    """推荐列表：复用自定义行列表的行宽同步，右侧性能摘要不会被挤出视口。"""


class RunningModelRow(QFrame):  # pragma: no cover - Qt 控件
    """运行中的模型：模型 / 服务信息 + 该模型自己的实时性能（每秒刷新）。

    实时性能不挂在全局面板上，而是跟随每个运行中的模型：左侧模型名与服务地址，
    右侧解码 tok/s 大字；下方一行小字是预填充 / 请求队列 / KV / tokens。
    画图（sd.cpp）服务没有 token 指标：``show_rate=False`` 时只保留状态与小字，
    用小字显示生成进度。
    """

    def __init__(
        self,
        title: str,
        subtitle: str,
        *,
        show_rate: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RunningModelRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(12)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        self.name_label = ElidedLabel(title, parent=self)
        self.name_label.setObjectName("RecommendationName")
        self.name_label.setToolTip(title)
        self.status_tag = StatusTag("等待采样…", tone="muted", parent=self)
        name_row.addWidget(self.name_label, 1)
        name_row.addWidget(self.status_tag, 0)
        left.addLayout(name_row)
        self.subtitle_label = ElidedLabel(subtitle, parent=self)
        self.subtitle_label.setObjectName("Hint")
        left.addWidget(self.subtitle_label)
        outer.addLayout(left, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(2)
        rate_row = QHBoxLayout()
        rate_row.setContentsMargins(0, 0, 0, 0)
        rate_row.setSpacing(4)
        self.rate_label = QLabel("-", self)
        self.rate_label.setObjectName("RateValue")
        self.rate_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rate_row.addStretch(1)
        self.rate_unit = QLabel("tok/s", self)
        rate_row.addWidget(self.rate_label, 0, Qt.AlignmentFlag.AlignBottom)
        rate_row.addWidget(self.rate_unit, 0, Qt.AlignmentFlag.AlignBottom)
        right.addLayout(rate_row)
        if not show_rate:
            self.rate_label.hide()
            self.rate_unit.hide()
        self.metrics_label = ElidedLabel(performance_metrics_text(None, None), parent=self)
        self.metrics_label.setObjectName("Hint")
        self.metrics_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.addWidget(self.metrics_label)
        outer.addLayout(right, 1)

    def set_performance(  # noqa: PLR0913 - 与全局性能面板同一组指标
        self,
        gen_rate: float | None,
        prompt_rate: float | None,
        metrics: llmserve.ServeMetrics | None,
        status: str,
        tone: str,
        *,
        detail: str = "",
    ) -> None:
        """刷新本行的 tok/s、性能摘要与状态胶囊。"""
        self.rate_label.setText(performance_rate_text(gen_rate))
        self.metrics_label.setText(detail or performance_metrics_text(metrics, prompt_rate))
        self.status_tag.set_status(status, tone)


@dataclass(frozen=True, slots=True)
class ServiceSample:
    """一个运行中服务的采样结果（指标或失败原因）。"""

    name: str
    metrics: llmserve.ServeMetrics | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PaintSample:
    """画图（sd.cpp）服务的采样结果：只要登记状态与生成进度。"""

    state: _llmpaint_server.PaintState | None
    alive: bool
    progress: float | None = None


def paint_sample_text(progress: float | None) -> str:
    """画图服务行的小字：引擎 + 生成进度（没进度时说明空闲 / 等待采样）。"""
    if progress is None:
        return "sd.cpp 画图服务 · 暂无生成进度"
    if progress <= 0:
        return "sd.cpp 画图服务 · 空闲"
    return f"sd.cpp 画图服务 · 生成进度 {progress * 100:.0f}%"


def paint_running_title(state: _llmpaint_server.PaintState) -> str:
    """画图模型行标题：主模型文件名，未记录时回退「画图服务」。"""
    return Path(state.model).name if state.model else "画图服务"


def paint_running_subtitle(state: _llmpaint_server.PaintState) -> str:
    """画图模型行副标题：画图 · 地址 · 引擎。"""
    return f"画图 · {state.base_url} · {state.engine_tag or 'sd.cpp'}"


def sample_paint() -> PaintSample:
    """读取画图服务登记状态与生成进度（阻塞；不可达时进度为 ``None``）。"""
    state, alive = _llmpaint_server.status()
    progress = None
    if state is not None and alive:
        progress = _llmpaint_server.progress_value(_llmpaint_server.fetch_progress(state.host, state.port))
    return PaintSample(state=state, alive=alive, progress=progress)


def sample_overview(
    states: Sequence[ServeState],
) -> tuple[LoadSample, tuple[ServiceSample, ...], PaintSample]:
    """采样一次系统负载 + 每个运行中服务 + 画图服务的指标（阻塞，供线程池调用）。

    系统负载绑定第一个运行中服务（用于 CPU 副条的服务进程占用 / GPU 进程显存）；
    每个服务的指标单独抓取，失败时把原因放在 :class:`ServiceSample` 里，
    由界面显示「采样失败」而不是抛异常。
    """
    first = states[0] if states else None
    process = _attach_process(first.pid) if first is not None else None
    load = _sample_load(first.pid if first is not None else 0, process)
    rows: list[ServiceSample] = []
    for state in states:
        try:
            metrics = llmserve.fetch_metrics(state.host, state.port, state.api_key)
        except ServeError as exc:
            rows.append(ServiceSample(state.name, None, str(exc)))
        else:
            rows.append(ServiceSample(state.name, metrics, None))
    return load, tuple(rows), sample_paint()


def _attach_process(pid: int) -> Any | None:
    """绑定服务进程用于 CPU / 内存 / 运行时长采样；进程已退出时返回 ``None``。"""
    import psutil

    try:
        return psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


class OverviewPage(AppPage):  # pragma: no cover - Qt 控件
    """概览页：系统信息（规格 + 实时格图 + 运行中的模型）→ 智能推荐。"""

    PAGE_KEY = "overview"

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(state, parent, margins=(0, 0, 0, 8), spacing=8)
        self._recommendations: tuple[Recommendation, ...] = ()
        self._window = RATE_WINDOW_SECONDS
        self._rates: dict[str, RateWindow] = {}
        self._paint_sample: PaintSample | None = None
        self._paint_row: RunningModelRow | None = None
        self._paint_key = ""
        self._inflight = False
        self._timer: QTimer | None = None
        layout = self.body
        layout.addWidget(self._build_hardware())
        layout.addWidget(self._build_recommendations(), 1)
        layout.addLayout(self._build_recommend_actions())

        state.hardwareChanged.connect(lambda _snapshot: self.refresh_hardware())
        state.hardwareFailed.connect(self._on_hardware_failed)
        state.modelsChanged.connect(lambda _outcome: self.refresh_recommendations())
        state.servicesChanged.connect(self._on_services_changed)
        self.refresh_hardware()
        self.refresh_recommendations()
        self._on_services_changed(self.state.alive_states)
        self._timer = periodic(self, 1000, self._tick)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_hardware(self) -> Card:
        """规格与实时占用：上方硬件规格，下方 GPU / CPU / 内存三块负载条。

        占用来自概览页每秒的系统采样：分段格图 + 百分比；
        三块的排版统一：主格图是设备占用，下方副条是该设备上另一个维度
        （GPU 显存 / 服务进程 CPU / 内存交换），副条上方用小字标出数值。
        标题后只留功耗、频率等其它信息，顶部只保留「重新检测」文字链接与
        检测状态，不放标题 / 说明文案。
        """
        card = Card()
        header, self._hardware_title = section_header("系统信息", card)
        self._detect_button = link_button("重新检测", tooltip="重新检测 CPU / GPU / 内存")
        self._detect_button.clicked.connect(self._detect)
        self._detected_label = hint_label("", wrap=False)
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
        top.addLayout(grid, 2)
        top.addLayout(self._build_loads(card), 3)
        card.body.addLayout(top)
        card.body.addWidget(self._build_running_models(card))
        return card

    def _build_loads(self, parent: QWidget) -> QHBoxLayout:
        """GPU / CPU / 内存三块实时负载：放在原「实时性能」所在位置（规格右侧）。"""
        loads = QHBoxLayout()
        loads.setContentsMargins(0, 0, 0, 0)
        loads.setSpacing(8)
        (
            gpu_block,
            self._gpu_percent,
            self._gpu_bar,
            self._gpu_hint,
            self._vram_hint,
            self._vram_bar,
        ) = self._build_load_block("GPU", parent)
        loads.addWidget(gpu_block, 1)
        (
            cpu_block,
            self._cpu_percent,
            self._cpu_bar,
            self._cpu_hint,
            self._cpu_proc_hint,
            self._cpu_proc_bar,
        ) = self._build_load_block("CPU", parent)
        loads.addWidget(cpu_block, 1)
        (
            mem_block,
            self._mem_percent,
            self._mem_bar,
            self._mem_hint,
            self._swap_hint,
            self._swap_bar,
        ) = self._build_load_block("内存", parent)
        loads.addWidget(mem_block, 1)
        return loads

    def _build_running_models(self, parent: QWidget) -> QWidget:
        """系统信息底部「运行中的模型」：每个运行中服务一行，绑定各自的实时性能。"""
        container = QWidget(parent)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("运行中的模型", container)
        title.setObjectName("SectionTitle")
        self._running_badge = StatusTag("未运行", tone="muted", parent=container)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._running_badge)
        layout.addLayout(header)
        self._running_empty = hint_label(
            "当前没有运行中的服务：在运行页启动模型后，这里显示各自的解码 / 预填充速率与请求指标。",
            wrap=True,
        )
        layout.addWidget(self._running_empty)
        self._running_host = QVBoxLayout()
        self._running_host.setContentsMargins(0, 0, 0, 0)
        self._running_host.setSpacing(4)
        layout.addLayout(self._running_host)
        self._running_rows: dict[str, RunningModelRow] = {}
        self._running_states: tuple[ServeState, ...] = ()
        return container

    def _build_load_block(
        self,
        title: str,
        parent: QWidget,
    ) -> tuple[QFrame, QLabel, SegmentBar, ElidedLabel, ElidedLabel, SegmentBar]:
        """单个负载块：标题 + 小字关键信息 + 占用率 + 主格图 + 副条（小字 + 格图）。

        三块用同一套排版：主格图对应标题右侧的大字占用率；副条对应「另一个
        维度」的占用（GPU 显存 / 服务进程 CPU / 内存交换），条上方用小字标出
        具体数值，不必再从标题后的小字里换算。
        """
        block, layout, hint, percent = self._load_block_shell(title, parent)
        bar = SegmentBar(parent=block)
        layout.addWidget(bar)
        extra_hint = ElidedLabel("等待采样…", parent=block)
        extra_hint.setObjectName("Hint")
        layout.addWidget(extra_hint)
        extra_bar = SegmentBar(parent=block)
        layout.addWidget(extra_bar)
        layout.addStretch(1)
        return block, percent, bar, hint, extra_hint, extra_bar

    @staticmethod
    def _load_block_shell(
        title: str,
        parent: QWidget,
    ) -> tuple[QFrame, QVBoxLayout, ElidedLabel, QLabel]:
        """负载块外壳：底板 + 标题行（标题 / 小字提示 / 占用率），格图由调用方追加。"""
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
        return block, layout, hint, percent

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
        # 与运行页顶部启停按钮同一尺寸：只比常规按钮略高，左右内边距更宽。
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
        self._recommend_list.sync_selection(current)

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
    def _on_services_changed(self, states: Sequence[ServeState] | None = None) -> None:
        """服务列表变化：重建「运行中的模型」行，并丢掉已退出服务的速率窗口。"""
        current = tuple(self.state.alive_states if states is None else states)
        self._running_states = current
        alive_names = {state.name for state in current}
        self._rates = {name: window for name, window in self._rates.items() if name in alive_names}
        self._rebuild_running_models()

    def _rebuild_running_models(self) -> None:
        """按当前运行中服务 + 画图服务重建行（行内数值由每秒采样刷新）。"""
        while self._running_host.count():
            item = self._running_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._running_rows = {}
        for state in self._running_states:
            row = RunningModelRow(running_model_title(state), running_model_subtitle(state), parent=self)
            row.set_performance(None, None, None, "等待采样…", "muted")
            self._running_host.addWidget(row)
            self._running_rows[state.name] = row
        self._paint_row = None
        self._paint_key = ""
        paint = self._paint_sample
        if paint is not None and paint.alive and paint.state is not None:
            self._paint_key = self._paint_identity(paint.state)
            self._paint_row = RunningModelRow(
                paint_running_title(paint.state),
                paint_running_subtitle(paint.state),
                show_rate=False,
                parent=self,
            )
            self._paint_row.set_performance(
                None,
                None,
                None,
                "运行中",
                "success",
                detail=paint_sample_text(paint.progress),
            )
            self._running_host.addWidget(self._paint_row)
        self._refresh_running_badge()

    def _refresh_running_badge(self) -> None:
        total = len(self._running_rows) + (1 if self._paint_row is not None else 0)
        self._running_empty.setVisible(total == 0)
        self._running_badge.set_status(f"{total} 个运行中" if total else "未运行", "success" if total else "muted")

    @staticmethod
    def _paint_identity(state: _llmpaint_server.PaintState) -> str:
        """画图行身份：模型 / 地址 / 引擎任一变化都要重建行。"""
        return f"{state.model}|{state.base_url}|{state.engine_tag}"

    def _sync_paint_row(self, paint: PaintSample) -> None:
        """画图服务状态：运行中显示成一行（重启应用后按 server.json 恢复），否则移除。"""
        self._paint_sample = paint
        running = paint.alive and paint.state is not None
        identity = self._paint_identity(paint.state) if running and paint.state is not None else ""
        if identity != self._paint_key or (not running and self._paint_row is not None):
            self._rebuild_running_models()
            return
        if self._paint_row is not None:
            self._paint_row.set_performance(
                None,
                None,
                None,
                "运行中" if running else "服务未运行",
                "success" if running else "warning",
                detail=paint_sample_text(paint.progress) if running else "",
            )

    def _tick(self) -> None:
        """每秒投递一次采样；上一轮未完成时跳过，避免任务堆叠。"""
        if self._inflight or self.state.closing:
            return
        self._inflight = True
        submit(
            sample_overview,
            tuple(self._running_states),
            on_done=self._apply_sample,
            on_finished=self._sample_finished,
        )

    def _sample_finished(self) -> None:
        self._inflight = False

    def _apply_sample(self, payload: tuple[LoadSample, tuple[ServiceSample, ...], PaintSample]) -> None:
        """系统负载刷格图，每个服务指标刷自己那一行的实时性能。"""
        load, rows, paint = payload
        self._apply_system_sample(load)
        self._sync_free_vram(load.gpu)
        self._sync_paint_row(paint)
        now = time.monotonic()
        seen: set[str] = set()
        for sample in rows:
            seen.add(sample.name)
            row = self._running_rows.get(sample.name)
            if row is None:
                continue
            if sample.metrics is None:
                row.set_performance(
                    None,
                    None,
                    None,
                    "采样失败" if sample.error else "服务未运行",
                    "danger" if sample.error else "warning",
                    detail="" if not sample.error else f"采样失败：{sample.error[:80]}",
                )
                continue
            window = self._rates.setdefault(sample.name, RateWindow(self._window))
            gen_rate, prompt_rate = window.update(
                now,
                sample.metrics.predicted_tokens,
                sample.metrics.prompt_tokens,
            )
            row.set_performance(gen_rate, prompt_rate, sample.metrics, "监控中", "success")
        for name, row in self._running_rows.items():
            if name not in seen:
                row.set_performance(None, None, None, "服务未运行", "warning")

    def _apply_system_sample(self, sample: LoadSample) -> None:
        """用系统采样刷新 GPU / CPU / 内存负载块（主格图 + 副条 + 小字）。"""
        gpu = sample.gpu
        gpu_values = _format_gpu(gpu)
        gpu_vram = gpu_memory_percent(gpu)
        self._set_hint(self._gpu_hint, [gpu_values["gpu_power"]])
        self._set_hint(self._vram_hint, [self._vram_text(gpu, gpu_vram)])
        self._apply_load_block(self._gpu_percent, self._gpu_bar, None if gpu is None else gpu.utilization)
        self._vram_bar.set_percent(gpu_vram)

        load_values = _format_load(sample)
        self._set_hint(self._cpu_hint, [] if load_values["cpu_freq"] == "-" else [f"频率 {load_values['cpu_freq']}"])
        self._set_hint(self._cpu_proc_hint, [self._proc_cpu_text(sample)])
        self._apply_load_block(self._cpu_percent, self._cpu_bar, sample.cpu)
        self._cpu_proc_bar.set_percent(None if sample.process_cpu is None else max(sample.process_cpu, 0.0))

        self._set_hint(
            self._mem_hint,
            [
                f"{human_size(sample.memory_used)} / {human_size(sample.memory_total)}"
                if sample.memory_total
                else "已用 -"
            ],
        )
        swap = swap_percent(sample)
        self._set_hint(self._swap_hint, [self._swap_text(sample, swap)])
        self._apply_load_block(self._mem_percent, self._mem_bar, sample.memory_percent if sample.memory_total else None)
        self._swap_bar.set_percent(swap)

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

    @staticmethod
    def _vram_text(gpu: GpuSample | None, percent: float | None) -> str:
        """显存格图上方的说明文字：已用 / 总量 + 百分比，无 GPU 时给出提示。"""
        if gpu is None:
            return "未检测到 GPU（CPU 推理）"
        if gpu.memory_used is None or not gpu.memory_total or percent is None:
            return "显存 -"
        used = human_size(int(gpu.memory_used * 1024 * 1024))
        total = human_size(int(gpu.memory_total * 1024 * 1024))
        return f"显存 {used} / {total}（{percent:.0f}%）"

    @staticmethod
    def _proc_cpu_text(sample: LoadSample) -> str:
        """副条说明：服务进程 CPU（单核基准，与 psutil 口径一致）。"""
        if sample.process_cpu is None:
            return "服务 -（未运行）"
        return f"服务 {sample.process_cpu:.1f} %"

    @staticmethod
    def _swap_text(sample: LoadSample, percent: float | None) -> str:
        """副条说明：交换分区已用 / 总量 + 百分比，未配置时给出提示。"""
        if not sample.swap_total or percent is None:
            return "未配置交换分区"
        used = human_size(sample.swap_used)
        total = human_size(sample.swap_total)
        return f"交换 {used} / {total}（{percent:.0f}%）"

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
            self.status(
                "未检测到可用模型：先在「模型」页刷新，或在「设置 → 存储」中登记模型目录。"
                if not self.state.models
                else "当前模型都放不进整卡显存：建议换更小量化，或释放显存后重新检测。"
            )

    def apply_recommendation(self) -> None:
        """把推荐行的模型与启动配置带到运行页。"""
        row = self._recommend_list.currentRow()
        if row < 0 or row >= len(self._recommendations):
            self.status("先在推荐列表中选择一行，再点「启动服务」。")
            return
        recommendation = self._recommendations[row]
        self.state.recommendationApplied.emit(recommendation)
        self.state.navigate("run")
