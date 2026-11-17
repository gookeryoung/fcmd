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
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.locllm import _llmtune, llmenv, llmserve
from fcmd.apps.locllm._locllmw_hardware import DiskInfo, HardwareSnapshot, disk_for_path
from fcmd.apps.locllm._locllmw_monitor import LoadSample
from fcmd.apps.locllm._locllmw_recommend import Recommendation, recommend
from fcmd.apps.locllm.llmbase import ServeState, human_size
from fcmd.apps.locllm.llmserve import read_log
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import (
    Card,
    ElidedLabel,
    PageScroll,
    button,
    hint_label,
    link_button,
    make_table,
    make_table_item,
)
from fcmd.gui.qt.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["OverviewPage", "loaded_row_values", "usage_gauge_texts"]

RECOMMEND_COLUMNS = ("推荐模型", "启动配置", "性能", "依据")
LOADED_COLUMNS = ("服务", "已加载模型", "档位 / ctx", "地址")


def loaded_row_values(state: ServeState) -> tuple[str, str, str, str]:
    """已加载服务表的一行（与 :data:`LOADED_COLUMNS` 对应）。"""
    return (state.name, Path(state.model).name, f"{state.profile} / ctx {state.context}", state.base_url)


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


def model_disk_text(models_dir: Path, disk: DiskInfo | None) -> str:
    """概览「模型目录」一行：设置里登记的模型目录 + 所在盘剩余空间。

    只显示模型目录本身（不随选中模型变成具体文件路径），盘符信息来自硬件
    快照的分区列表；取不到分区时剩余空间显示「未知」。
    """
    free = human_size(disk.free) if disk is not None else "未知"
    return f"{models_dir} · 剩余 {free}"


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

        disk_value = ElidedLabel("检测中…", parent=card)
        disk_value.setFixedWidth(420)
        disk_value.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        modify = link_button("修改", tooltip="到设置页修改模型目录")
        modify.clicked.connect(lambda: self.state.navigate("settings"))
        self._modify_link = modify
        grid.addWidget(row_label("模型目录"), 3, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        disk_row = QHBoxLayout()
        disk_row.setContentsMargins(0, 0, 0, 0)
        disk_row.setSpacing(8)
        disk_row.addWidget(disk_value, 0, Qt.AlignmentFlag.AlignVCenter)
        disk_row.addWidget(modify, 0, Qt.AlignmentFlag.AlignVCenter)
        disk_row.addStretch(1)
        grid.addLayout(disk_row, 3, 1)
        self._hardware_values["model_disk"] = disk_value

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
        models_dir = llmenv.models_dir()
        disk = disk_for_path(models_dir, snapshot.disks)
        disk_text = model_disk_text(models_dir, disk)
        self._hardware_values["model_disk"].setText(disk_text)
        self._hardware_values["model_disk"].setToolTip(disk_text)
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
