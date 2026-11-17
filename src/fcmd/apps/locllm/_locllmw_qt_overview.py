"""locllmw（Qt）概览页：硬件信息、占用条、智能推荐与运行中的服务。

硬件快照首次检测后写入 ``locllmw.json``，之后读缓存展示并支持重新检测；
推荐行由 :mod:`fcmd.apps.locllm._locllmw_recommend` 纯逻辑计算，性能默认标注
「预测」，同一模型跑过性能测试后改用实测值。占用条跟随监测页的系统采样刷新，
服务未启动时也能看到显存 / 内存占用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
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

from fcmd.apps.locllm._locllmw_monitor import LoadSample
from fcmd.apps.locllm._locllmw_recommend import Recommendation, recommend
from fcmd.apps.locllm.llmbase import human_size
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    PageScroll,
    button,
    hint_label,
    link_button,
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
    "usage_gauge_texts",
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

    行边框颜色跟评分档位一致（QSS 按 ``band`` 属性上色），选中时边框改为主色。
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


def usage_gauge_texts(sample: LoadSample) -> tuple[float, str, float, str]:
    """把采样换算成（显存百分比, 显存简报, 内存百分比, 内存简报），供环形占用表显示。

    简报只保留「已用 / 总量」，百分比画在环心，完整细节由环下小字说明。
    """
    gpu = sample.gpu
    if gpu is not None and gpu.memory_used is not None and gpu.memory_total:
        percent = min(max(gpu.memory_used / gpu.memory_total * 100, 0.0), 100.0)
        gpu_brief = f"{gpu.memory_used / 1024:.1f} / {gpu.memory_total / 1024:.1f} GB"
    else:
        percent = 0.0
        gpu_brief = "未检测到 NVIDIA GPU" if gpu is None else "暂不可用"
    if sample.memory_total:
        memory_percent = min(max(sample.memory_percent, 0.0), 100.0)
        memory_brief = f"{human_size(sample.memory_used)} / {human_size(sample.memory_total)}"
    else:
        memory_percent = 0.0
        memory_brief = "暂不可用"
    return percent, gpu_brief, memory_percent, memory_brief


class UsageGauge(QWidget):  # pragma: no cover - Qt 控件
    """环形占用表：环心画百分比，环随占用变色（>=90% 告警色），环下标注说明。

    颜色取自应用 QPalette（主题切换自动跟随）：主色用 ``Highlight``，
    底环用 ``Mid``，告警用 ``BrightText``，说明文字用 ``PlaceholderText``。
    """

    _DIAMETER = 76
    _PEN_WIDTH = 7

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._percent = 0.0
        self._detail = "等待采样…"
        self.setFixedSize(128, 136)

    def set_usage(self, percent: float, detail: str) -> None:
        self._percent = max(0.0, min(percent, 100.0))
        self._detail = detail
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: ARG002 - Qt 事件签名
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QFont, QPainter, QPalette, QPen

        palette = self.palette()
        text_color = palette.color(QPalette.ColorRole.WindowText)
        muted_color = palette.color(QPalette.ColorRole.PlaceholderText)
        ring_color = palette.color(QPalette.ColorRole.Mid)
        value_color = (
            palette.color(QPalette.ColorRole.BrightText)
            if self._percent >= 90.0
            else palette.color(QPalette.ColorRole.Highlight)
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        ring_rect = QRectF((self.width() - self._DIAMETER) / 2, 4, self._DIAMETER, self._DIAMETER)
        pen = QPen(ring_color, self._PEN_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(ring_rect, 0, 360 * 16)
        span = int(360 * 16 * self._percent / 100)
        if span > 0:
            pen.setColor(value_color)
            painter.setPen(pen)
            painter.drawArc(ring_rect, 90 * 16, -span)

        percent_font = QFont()
        percent_font.setPointSize(11)
        percent_font.setBold(True)
        painter.setFont(percent_font)
        painter.setPen(text_color)
        painter.drawText(ring_rect, Qt.AlignmentFlag.AlignCenter, f"{self._percent:.0f}%")

        caption_font = QFont()
        caption_font.setBold(True)
        painter.setFont(caption_font)
        caption_rect = QRectF(0, ring_rect.bottom() + 4, self.width(), 18)
        painter.drawText(caption_rect, Qt.AlignmentFlag.AlignCenter, self._caption)

        detail_font = QFont()
        detail_font.setPointSize(8)
        painter.setFont(detail_font)
        metrics = painter.fontMetrics()
        elided = metrics.elidedText(self._detail, Qt.TextElideMode.ElideRight, self.width() - 4)
        detail_rect = QRectF(0, caption_rect.bottom(), self.width(), 16)
        painter.setPen(muted_color)
        painter.drawText(detail_rect, Qt.AlignmentFlag.AlignCenter, elided)


class RecommendationList(QListWidget):  # pragma: no cover - Qt 控件
    """推荐列表：自定义行宽度始终跟随视口，避免反复拖宽 / 拖窄后行宽度残留。

    ``QListWidget.setItemWidget`` 挂上去的行只在 item rect 变大时被 Qt 重新
    摆正；窗口反复拖拉后行会保留上一次的更大宽度，右半部分（性能摘要）被
    推到视口之外，看起来就是「右侧显示不正常」。这里在每次尺寸变化后按
    当前可视区域重新摆正每行几何。
    """

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.sync_row_widths()

    def sync_row_widths(self) -> None:
        """把每行自定义控件的几何对齐到列表当前可视区域。"""
        for index in range(self.count()):
            item = self.item(index)
            widget = self.itemWidget(item)
            if widget is not None:
                widget.setGeometry(self.visualRect(self.indexFromItem(item)))


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
        """硬件卡片：左侧「标签 / 主值」信息行，右侧显存 / 内存环形占用表。

        左右分栏：主值（型号 / 容量，大字加粗）独占一行保持层次；占用可视化
        不再混排在文字行里，改为右侧两个环形仪表，环心百分比、环下简报。
        「修改」与「重新检测」都是文字链接（`QPushButton#Link`），不占按钮
        高度；模型目录用单行省略标签（完整路径在 tooltip）。
        """
        card = Card()
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel("硬件信息", card)
        title.setObjectName("SectionTitle")
        self._detect_button = link_button("重新检测", tooltip="重新检测 CPU / GPU / 内存")
        self._detect_button.clicked.connect(self._detect)
        self._detected_label = hint_label("", wrap=False)
        header.addWidget(title)
        header.addWidget(self._detect_button)
        header.addWidget(self._detected_label, 1)
        card.body.addLayout(header)

        self._hardware_values: dict[str, QLabel] = {}
        self._gpu_gauge = UsageGauge("显存占用", card)
        self._mem_gauge = UsageGauge("内存占用", card)

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
        grid.setVerticalSpacing(8)
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

        gauges = QHBoxLayout()
        gauges.setContentsMargins(0, 0, 0, 0)
        gauges.setSpacing(8)
        gauges.addWidget(self._gpu_gauge, 0, Qt.AlignmentFlag.AlignVCenter)
        gauges.addWidget(self._mem_gauge, 0, Qt.AlignmentFlag.AlignVCenter)

        content = QHBoxLayout()
        content.setContentsMargins(0, 4, 0, 0)
        content.setSpacing(16)
        content.addLayout(grid, 1)
        content.addLayout(gauges)
        card.body.addLayout(content)
        return card

    def _build_recommendations(self) -> Card:
        card = Card("智能推荐（按显存筛选；性能测试后优先用实测值）")
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
        self._recommend_status = hint_label("等待硬件检测与模型扫描…")
        self._apply_button = button("用此配置启动")
        self._apply_button.clicked.connect(self.apply_recommendation)
        row.addWidget(self._recommend_status, 1)
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
        """用系统采样刷新右侧显存 / 内存环形占用表。"""
        if not isinstance(sample, LoadSample):
            return
        gpu_percent, gpu_brief, memory_percent, memory_brief = usage_gauge_texts(sample)
        self._gpu_gauge.set_usage(gpu_percent, gpu_brief)
        self._mem_gauge.set_usage(memory_percent, memory_brief)

    # ------------------------------------------------------------------ #
    # 推荐
    # ------------------------------------------------------------------ #
    def refresh_recommendations(self) -> None:
        """按当前模型清单 + 硬件快照重算推荐行。"""
        rows = recommend(self.state.models, self.state.hardware, self.state.measured)
        self._recommendations = rows
        self._render_recommendations()
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
        row = self._recommend_list.currentRow()
        if row < 0 or row >= len(self._recommendations):
            self._recommend_status.setText("先在推荐列表中选择一行，再点「用此配置启动」。")
            return
        recommendation = self._recommendations[row]
        self.state.recommendationApplied.emit(recommendation)
        self.state.navigate("run")
