"""locllmw（Qt）实验室页：代理 / 画图 / 视频 / 音频四项规划中的能力。

这些能力尚未落地，页面上集中说明定位与当前可用的 CLI 入口，避免在侧边栏
堆四个空页；真正落地时按卡片位置替换为对应配置面板。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from fcmd.gui.qt.widgets import Card, HintLabel, PageScroll, hint_label, mono_label

if TYPE_CHECKING:
    from fcmd.apps.locllm._locllmw_qt_state import LocllmState

__all__ = ["LAB_CARDS", "LabPage"]

LAB_CARDS = (
    ("代理", "工具调用、文件编辑与多步任务编排；落地后在此配置并查看运行轨迹。", ()),
    ("画图", "接入本地图片生成模型后，在此配置提示词、尺寸、步数与批量任务。", ()),
    ("视频", "视频生成与视频理解复用同一套本地模型与任务队列。", ("fcmd videoconvw",)),
    ("音频", "语音识别与语音合成共用模型选择、设备与批处理设置。", ()),
)
"""实验室四张卡片：(标题, 说明, 当前可用的 CLI 入口)。"""


class LabPage(QWidget):  # pragma: no cover - Qt 控件
    """实验室页：四张规划卡片（两列网格）。"""

    def __init__(self, state: LocllmState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = PageScroll(self)
        layout = scroll.body
        outer.addWidget(scroll)
        layout.addWidget(hint_label("以下能力尚未落地，先集中说明定位；列出的 CLI 命令当前可用。"))
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (title, summary, commands) in enumerate(LAB_CARDS):
            card = Card(title)
            card.body.addWidget(hint_label("规划中 · 暂无入口"))
            card.body.addWidget(HintLabel(summary))
            for command in commands:
                card.body.addWidget(mono_label(command))
            card.body.addStretch(1)
            grid.addWidget(card, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
