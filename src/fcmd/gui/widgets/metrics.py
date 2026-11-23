"""指标与进度：指标网格、键值表、分段负载条、加载条与进度行。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui import style as _qt_theme
from fcmd.gui.widgets.tables import make_table, make_table_item
from fcmd.gui.widgets.text import hint_label, value_label

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "KeyValueGrid",
    "LoadingBar",
    "MetricGrid",
    "ProgressBar",
    "ProgressRow",
    "SegmentBar",
    "segment_band",
    "segment_fill",
]


_LOAD_BAND_THRESHOLDS = ((85.0, "danger"), (60.0, "warning"), (0.0, "success"))


def segment_band(percent: float | None) -> str:
    """占用率档位：≥85% 告急（danger）、≥60% 偏紧（warning）、其余充足（success）。

    未采样时返回 ``idle``，由调用方保留默认文字色，不误导为「充足」。
    """
    if percent is None:
        return "idle"
    for threshold, band in _LOAD_BAND_THRESHOLDS:
        if percent >= threshold:
            return band
    return "success"


def segment_fill(percent: float | None, segments: int) -> int:
    """把 0–100 占用率换算为点亮的格数：非零至少点亮 1 格，上限 ``segments``。"""
    if percent is None or segments <= 0:
        return 0
    clamped = min(max(float(percent), 0.0), 100.0)
    if clamped <= 0:
        return 0
    return min(max(round(clamped * segments / 100), 1), segments)


class SegmentBar(QWidget):  # pragma: no cover - Qt 控件
    """分段负载条（格图）：按占用率点亮格子，颜色随档位（充足 / 偏紧 / 告急）变化。

    用固定格数而不是连续进度条：低负载时也能一眼看出「用了几成」，格与格之间
    留缝，和概览页硬件卡负载块的余量分档配色（绿 / 琥珀 / 红）保持一致。
    """

    def __init__(
        self,
        *,
        segments: int = 24,
        height: int = 12,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._segments = max(int(segments), 1)
        self._percent: float | None = None
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("负载条")
        self.setToolTip("等待采样")

    def set_percent(self, percent: float | None) -> None:
        """写入 0–100 占用率；``None`` 表示尚未采样（全部格子保持空）。"""
        self._percent = None if percent is None else min(max(float(percent), 0.0), 100.0)
        self.setToolTip("等待采样" if self._percent is None else f"占用率 {self._percent:.1f}%")
        self.update()

    def percent(self) -> float | None:
        """当前占用率（供测试与调用方读取）。"""
        return self._percent

    def paintEvent(self, event: object) -> None:  # noqa: ARG002 - Qt 事件签名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        track = palette.color(QPalette.ColorRole.Mid)
        theme_palette = _qt_theme.load_theme().palette
        band = segment_band(self._percent)
        fill = QColor(
            {
                "danger": theme_palette.danger,
                "warning": theme_palette.warning,
                "success": theme_palette.success,
                # 尚未采样时全格为空，颜色只作占位，避免首帧 KeyError。
                "idle": theme_palette.success,
            }[band]
        )
        gap = 2.0
        segment_width = (self.width() - gap * (self._segments - 1)) / self._segments
        if segment_width <= 0:
            return
        lit = segment_fill(self._percent, self._segments)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(self._segments):
            painter.setBrush(fill if index < lit else track)
            painter.drawRoundedRect(
                QRectF(index * (segment_width + gap), 0.0, segment_width, float(self.height())),
                2.0,
                2.0,
            )


class MetricGrid(QWidget):  # pragma: no cover - Qt 控件
    """指标网格：按键写入文本，每行 ``columns`` 个「标签: 数值」单元。"""

    def __init__(self, keys: Sequence[tuple[str, str]], *, columns: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: dict[str, QLabel] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for index, (label, key) in enumerate(keys):
            row, column = divmod(index, columns)
            cell = QWidget(self)
            cell.setMinimumWidth(150)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(4)
            cell_layout.addWidget(hint_label(f"{label}:", wrap=False), 0, Qt.AlignmentFlag.AlignTop)
            value = value_label("-")
            value.setWordWrap(True)
            cell_layout.addWidget(value, 1, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(cell, row, column)
            self._values[key] = value
        for column in range(columns):
            grid.setColumnStretch(column, 1)

    def set_value(self, key: str, text: str) -> None:
        """写入单个指标；未知 key 静默忽略，避免面板改版导致崩溃。"""
        label = self._values.get(key)
        if label is not None:
            label.setText(text)

    def set_values(self, values: dict[str, str]) -> None:
        """批量写入指标。"""
        for key, text in values.items():
            self.set_value(key, text)

    def reset(self, text: str = "-") -> None:
        """把所有指标重置为占位文本。"""
        for label in self._values.values():
            label.setText(text)

    def value(self, key: str) -> str:
        """读取当前显示文本（测试用）。"""
        label = self._values.get(key)
        return "" if label is None else label.text()


class KeyValueGrid(QWidget):  # pragma: no cover - Qt 控件
    """只读「项目 / 路径 / 来源」三列表格，供设置页与硬件信息使用。"""

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = make_table(headers, parent=self)
        layout.addWidget(self.table)

    def set_rows(self, rows: Sequence[Sequence[str]]) -> None:
        """整体替换行内容。"""
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                self.table.setItem(row, column, make_table_item(str(text)))


_PROGRESS_STEPS = 1000
"""确定进度条的内部刻度：与字节数解耦，避免几十 GB 的商品文件撑爆 QProgressBar 的 int 范围。"""


class ProgressBar(QProgressBar):  # pragma: no cover - Qt 控件
    """统一进度条：隐藏百分比、可切忙碌态、按比例写入且不溢出。

    ``QProgressBar`` 的取值是 32 位 ``int``，几十 GB 的字节数会溢出；需要按字节
    上报的调用方用 :meth:`set_progress`（按当前 ``maximum`` 折算），或直接使用
    :data:`_PROGRESS_STEPS` 刻度。默认 ``0..100`` 与多数窗口的百分比口径一致。
    """

    def __init__(
        self,
        *,
        maximum: int = 100,
        height: int | None = None,
        busy: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._maximum = max(int(maximum), 1)
        self.setTextVisible(False)
        if height is not None:
            self.setFixedHeight(height)
        if busy:
            self.set_busy()
        else:
            self.setRange(0, self._maximum)
            self.setValue(0)

    def set_busy(self) -> None:
        """切到忙碌动画（总量未知）；不显示百分比。"""
        self.setRange(0, 0)

    def set_progress(self, done: float, total: float | None) -> None:
        """按 ``done / total`` 写入比例；``total`` 未知或非正数时回到忙碌态。"""
        if not total or total <= 0:
            self.set_busy()
            return
        ratio = max(0.0, min(1.0, float(done) / float(total)))
        if self.maximum() != self._maximum:
            self.setRange(0, self._maximum)
        self.setValue(int(ratio * self._maximum))

    def set_percent(self, percent: float | None) -> None:
        """写入 0–100 百分比；``None`` 切忙碌态。"""
        if percent is None:
            self.set_busy()
            return
        self.setRange(0, 100)
        clamped = max(0.0, min(100.0, float(percent)))
        self.setValue(round(clamped))


class LoadingBar(QWidget):  # pragma: no cover - Qt 控件
    """不定进度加载条：说明文字 + 忙碌动画 + 已用时（秒）。

    用于无法预估总量的后台加载（在线搜索、本地目录扫描、硬件检测等）：
    ``start()`` 后每 100ms 刷新已用时，``stop()`` 收起；同一页面同时只有
    一项任务时直接 ``start`` / ``stop``，多项任务由调用方合并且只保留
    最后开始的那条说明。
    """

    def __init__(self, text: str = "加载中…", parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingBar")
        row = QHBoxLayout(self)
        # 紧凑模式给状态栏用：不要上下留白、进度条压细，整条底栏保持一行高。
        if compact:
            row.setContentsMargins(8, 0, 8, 0)
            row.setSpacing(8)
        else:
            row.setContentsMargins(10, 6, 10, 6)
            row.setSpacing(10)
        self.text_label = QLabel(text, self)
        self.text_label.setObjectName("LoadingText")
        self.elapsed_label = QLabel("0.0s", self)
        self.elapsed_label.setObjectName("Hint")
        self.elapsed_label.setToolTip("本次加载已用时")
        self.bar = ProgressBar(maximum=_PROGRESS_STEPS, height=6 if compact else None, busy=True, parent=self)
        self.bar.setMinimumWidth(120 if compact else 160)
        row.addWidget(self.text_label, 0)
        row.addWidget(self.bar, 1)
        row.addWidget(self.elapsed_label, 0)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self.setVisible(False)

    def start(self, text: str | None = None) -> None:
        """显示加载条并重新计时；``text`` 为 ``None`` 时沿用上次说明。"""
        if text:
            self.text_label.setText(text)
        self._clock.start()
        self.elapsed_label.setText("0.0s")
        self._timer.start()
        self.setVisible(True)

    def set_text(self, text: str) -> None:
        """更新说明文字（已用时不清零）。"""
        self.text_label.setText(text)

    def set_progress(self, done: int, total: int | None) -> None:
        """切到确定进度（下载等能预估总量的任务）；``total`` 未知时回到忙碌动画。

        ``QProgressBar`` 的取值是 32 位 ``int``，几十 GB 的镜像字节数会溢出，所以
        内部统一折算成 ``0.._PROGRESS_STEPS`` 的刻度，只用于画填充比例。
        """
        self.bar.set_progress(done, total)

    def stop(self) -> None:
        """停止计时并收起加载条（进度条回到忙碌态，下次复用时不会残留百分比）。"""
        self._timer.stop()
        self.bar.set_busy()
        self.setVisible(False)

    def is_running(self) -> bool:
        """是否正在计时（供测试与调用方判断）。"""
        return self._timer.isActive()

    def elapsed_text(self) -> str:
        """当前已用时文本（形如 ``1.2s``）。"""
        return self.elapsed_label.text()

    def _tick(self) -> None:
        seconds = self._clock.elapsed() / 1000
        self.elapsed_label.setText(f"{seconds:.1f}s")


class ProgressRow(QWidget):  # pragma: no cover - Qt 控件
    """进度条 + 可选状态文字 + 右侧动作区。

    下载 / 转换 / 批处理窗口的通用底栏：``bar`` 由调用方控制确定 / 不定进度，
    ``add_action`` 依次追加按钮；``show_bar=False`` 时只保留状态文字，供只需要
    一行状态提示（如性能测试）的窗口复用同一布局。
    """

    def __init__(
        self,
        text: str = "",
        *,
        show_bar: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.label = hint_label(text, wrap=False)
        self.label.setVisible(bool(text))
        self.bar = ProgressBar(parent=self)
        self.bar.setMinimumWidth(120)
        self.bar.setVisible(show_bar)
        self._actions = QHBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        row.addWidget(self.label, 0)
        row.addWidget(self.bar, 1)
        row.addLayout(self._actions, 0)

    def set_text(self, text: str) -> None:
        """更新状态文字；空文本时收起标签。"""
        self.label.setText(text)
        self.label.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> QWidget:
        """把按钮追加到右侧动作区，返回该控件便于链式使用。"""
        self._actions.addWidget(widget)
        return widget
