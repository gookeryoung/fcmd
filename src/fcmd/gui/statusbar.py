"""窗口底部状态栏组件：固定单行高度 + 文字按分页隔离 + 右侧备注 / 加载条。

各 GUI 窗口的底栏诉求一致，但历史上每个窗口各自往 ``QStatusBar`` 里塞控件：
换行标签（长文案）会把状态栏撑成两三行，选中项摘要之类的页内信息也不会随
分页切换消失。这里收成一份共享组件，窗口只调用方法：

- **单行高度**：主文字与页内补充都是 ``ElidedLabel``（垂直策略 Fixed、不换行），
  超长文案中间省略、完整内容进 tooltip；加载条用紧凑模式（细进度条 + 无上下留白），
  所以状态栏高度恒为一行，不随文案长度 / 是否加载变化。
- **按分页隔离**：``set_page_text(key, text)`` / ``set_page_detail(key, text)``
  记录每页自己的文案，``set_page(key)`` 切页时恢复；切走的页面不会把提示留在底部。
- **左文字、右补充**：主状态文字靠左，页内补充（如选中项摘要）靠右、紧挨右侧备注 /
  加载条，读起来是「这页在做什么 … 选中的是哪条」。
- **右侧常驻**：``set_note`` 写「更新于 12:00」这类长期备注，``show_loading``
  控制加载条；两者都不挤占左侧文字（左侧文字始终可见，给用户看的是「这一页在做什么」）。

组件不依赖 :class:`~fcmd.gui.window.QtMainWindow`，可单独用在任何
``QMainWindow`` 上；``QtMainWindow`` 只是把它接进骨架并暴露便捷方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSizePolicy, QStatusBar

from fcmd.gui.widgets import ElidedLabel, LoadingBar

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

__all__ = ["DEFAULT_STATUS", "WindowStatusBar"]

DEFAULT_STATUS = "就绪"
"""页面还没有任何状态时的兜底文字。"""


class WindowStatusBar(QStatusBar):  # pragma: no cover - Qt 控件
    """单行、按分页隔离文字的窗口状态栏。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WindowStatusBar")
        # 右下角的尺寸手柄会占位并抬高 / 加宽底栏，窗口本身可自由缩放，不需要它。
        self.setSizeGripEnabled(False)
        self._page = ""
        self._texts: dict[str, str] = {}
        self._details: dict[str, str] = {}

        self.text_label = ElidedLabel(DEFAULT_STATUS, parent=self)
        self.text_label.setObjectName("StatusText")
        self.text_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.detail_label = ElidedLabel("", mode=Qt.TextElideMode.ElideRight, parent=self)
        self.detail_label.setObjectName("Hint")
        self.detail_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        # 页内补充靠右：它表示「当前页选中的那条」，与右侧备注 / 加载条同一侧读起来更整齐。
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.detail_label.setVisible(False)
        self.note_label = QLabel("", self)
        self.note_label.setObjectName("Hint")
        self.note_label.setWordWrap(False)
        self.note_label.setVisible(False)
        self.loading_bar = LoadingBar("加载中…", parent=self, compact=True)
        self.loading_bar.setVisible(False)

        # 主文字靠左、页内补充靠右（同一行右半段，紧挨右侧备注 / 加载条）。
        self.addWidget(self.text_label, 3)
        self.addWidget(self.detail_label, 2)
        # 右侧常驻：备注 + 加载条。
        self.addPermanentWidget(self.note_label, 0)
        self.addPermanentWidget(self.loading_bar, 0)

    # ------------------------------------------------------------------ #
    # 文字（按页隔离）
    # ------------------------------------------------------------------ #
    def set_page_text(self, key: str, text: str) -> None:
        """记录某页的主状态文字；仅当它就是当前页时立即显示。"""
        self._texts[key] = text
        if key == self._page:
            self._sync()

    def page_text(self, key: str) -> str:
        """某页记录的主状态文字（未记录时为 :data:`DEFAULT_STATUS`）。"""
        return self._texts.get(key, DEFAULT_STATUS)

    def set_page_detail(self, key: str, text: str) -> None:
        """记录某页的页内补充（如选中项摘要）；空串时收起该段。"""
        self._details[key] = text
        if key == self._page:
            self._sync()

    def page_detail(self, key: str) -> str:
        """某页记录的页内补充（未记录时为空串）。"""
        return self._details.get(key, "")

    def set_page(self, key: str) -> None:
        """切到某页：恢复该页自己的主文字与补充信息。"""
        self._page = key
        self._sync()

    def current_page(self) -> str:
        """当前状态栏对应的分页 key。"""
        return self._page

    def text(self) -> str:
        """当前主状态文字（完整文本，不受省略影响）。"""
        return self.text_label.fullText()

    def detail_text(self) -> str:
        """当前页内补充的完整文本。"""
        return self.detail_label.fullText()

    def _sync(self) -> None:
        text = self.page_text(self._page)
        self.text_label.setText(text)
        self.text_label.setToolTip(text)
        detail = self.page_detail(self._page)
        self.detail_label.setText(detail)
        self.detail_label.setToolTip(detail)
        self.detail_label.setVisible(bool(detail))

    # ------------------------------------------------------------------ #
    # 右侧常驻区
    # ------------------------------------------------------------------ #
    def set_note(self, text: str) -> None:
        """右侧长期备注（如模型列表更新时间）；空串收起。"""
        self.note_label.setText(text)
        self.note_label.setVisible(bool(text))

    def show_loading(self, text: str) -> None:
        """右侧加载条：空串收起，非空时启动 / 更新说明（左侧文字保持可见）。"""
        if not text:
            self.loading_bar.stop()
            return
        if self.loading_bar.is_running():
            self.loading_bar.set_text(text)
        else:
            self.loading_bar.start(text)

    def show_progress(self, text: str, done: int, total: int | None) -> None:
        """右侧加载条切到确定进度（下载等可预估总量的任务）；空 ``text`` 收起。

        说明文字通常写成「下载 Ubuntu · 42% · 3.2 MB/s」，进度条按 ``done / total``
        填充；``total`` 未知时退回忙碌动画。
        """
        if not text:
            self.loading_bar.stop()
            return
        if self.loading_bar.is_running():
            self.loading_bar.set_text(text)
        else:
            self.loading_bar.start(text)
        self.loading_bar.set_progress(done, total)
