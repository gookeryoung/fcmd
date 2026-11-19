"""llmw（Qt）服务日志窗口：从顶部「帮助 → 服务日志…」打开。

日志由 :class:`~fcmd.apps.llm._llmw_qt_state.LlmState` 统一缓冲
（``log_buffer`` + ``logLine`` 信号）：窗口首次创建时回放缓冲，之后逐行追加。
关窗只隐藏、不销毁，历史保留，便于多次对照；工具条沿用统一日志组件
（复制全部 / 清空 / 导出）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from fcmd.gui.qt.widgets import Card, LogView, hint_label

if TYPE_CHECKING:
    from fcmd.apps.llm._llmw_qt_state import LlmState

__all__ = ["ServiceLogDialog"]


class ServiceLogDialog(QDialog):  # pragma: no cover - Qt 控件
    """服务日志对话框：非模态，主窗口仍可继续操作。"""

    def __init__(self, state: LlmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("服务日志")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.resize(920, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        card = Card("服务日志", self)
        self.log_view = LogView(height=420, parent=card)
        card.body.addWidget(self.log_view, 1)
        card.body.addWidget(hint_label("汇总服务启动 / 停止、模型扫描与下载等操作；复制 / 清空 / 导出在日志工具条。"))
        layout.addWidget(card, 1)

        # 首次打开回放缓冲，之后由信号实时追加；清空时同步清掉缓冲。
        for line in state.log_text().splitlines():
            self.log_view.append(line)
        state.logLine.connect(self.log_view.append)
        self.log_view.clear_button.clicked.connect(state.clear_log)

    def showEvent(self, event: QShowEvent) -> None:
        """打开时置顶并聚焦（重复点菜单只聚焦已有窗口）。"""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
