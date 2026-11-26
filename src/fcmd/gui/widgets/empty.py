"""空态占位：列表 / 表格 / 树没有数据时给出下一步操作。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from fcmd.gui.widgets.buttons import button
from fcmd.gui.widgets.text import hint_label

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["EmptyState"]


class EmptyState(QWidget):  # pragma: no cover - Qt 控件
    """居中空态：一行标题（SectionTitle）+ 一句说明 + 可选动作按钮。

    用法：把实例 addWidget 进列表 / 表格所在布局，数据到位后 setVisible(False)
    收起；标题与说明可后改（set_text），省去各窗口自己拼 title + hint + 上下拉伸。
    """

    def __init__(
        self,
        title: str = "暂无数据",
        hint: str = "",
        *,
        action: str = "",
        on_action: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 16, 18)
        layout.setSpacing(4)
        layout.addStretch(1)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("SectionTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        self.hint = hint_label(hint)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setVisible(bool(hint))
        layout.addWidget(self.hint)
        self.action = None
        if action:
            self.action = button(action, kind="ghost")
            if on_action is not None:
                self.action.clicked.connect(lambda _checked=False: on_action())
            layout.addWidget(self.action, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_text(self, title: str = "", hint: str | None = None) -> None:
        """更新标题与说明；hint 为 None 时保持原说明不变。"""
        if title:
            self.title_label.setText(title)
        if hint is not None:
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))
