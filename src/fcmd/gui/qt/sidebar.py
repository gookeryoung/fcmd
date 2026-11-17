"""Qt 侧边栏分页导航：左侧导航栏 + 右侧页面栈。

与 Tk 版 :class:`fcmd.gui.sidebar.SidebarNav` 同名的能力：标题、页按钮、
当前页提示与快捷键循环切换。窗口只负责提供页面内容，导航栏只管「选中哪一页」：

- ``add(key, label, hint)`` 登记页面（顺序即导航顺序）；
- ``select(key)`` 切换页面并发出 :attr:`SidebarNav.changed`；
- ``Alt+1..9`` 直达、``Alt+↓ / Alt+↑`` 循环，键位与 Tk 版一致；
- ``set_collapsed(True)`` 折叠为窄栏（只显示首字与 tooltip），再次调用展开。

页按钮用 ``QPushButton`` 而不是 ``QToolButton``：前者支持 QSS 的
``text-align: left`` 且默认横向可扩展，能铺满侧边栏宽度（``QToolButton``
会按文本收缩成小方块，看起来「宽度显示不全」）。折叠状态经
:attr:`SidebarNav.collapsedChanged` 交给窗口持久化。

纯逻辑（key 解析、循环切换）复用 :mod:`fcmd.gui.sidebar` 的模块级函数，
CI 无显示环境也能单测。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.sidebar import next_page, resolve_page_key

__all__ = ["COLLAPSED_WIDTH", "EXPANDED_WIDTH", "SidebarNav", "next_page", "resolve_page_key"]

EXPANDED_WIDTH = 184
"""展开态侧边栏宽度（像素）：容纳 3 个中文字的页名 + 内边距。"""

COLLAPSED_WIDTH = 52
"""折叠态侧边栏宽度：一个中文字 + 左右内边距。"""


class SidebarNav(QFrame):  # pragma: no cover - Qt 控件
    """左侧导航栏：标题 + 折叠按钮 + 页按钮 + 当前页提示。"""

    changed = Signal(str)
    collapsedChanged = Signal(bool)

    def __init__(
        self,
        *,
        title: str = "",
        parent: QWidget | None = None,
        width: int = EXPANDED_WIDTH,
        collapsed: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._expanded_width = width
        self._keys: list[str] = []
        self._labels: dict[str, str] = {}
        self._hints: dict[str, str] = {}
        self._buttons: dict[str, QPushButton] = {}
        self._collapsed = False
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._current: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 12)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 6)
        header.setSpacing(4)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("Title")
        self.title_label.setContentsMargins(6, 0, 0, 0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toggle_button = QToolButton(self)
        self.toggle_button.setObjectName("SidebarToggle")
        self.toggle_button.setText("«")
        self.toggle_button.setToolTip("折叠侧边栏（Ctrl+Shift+B）")
        self.toggle_button.setAutoRaise(True)
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.clicked.connect(self.toggle)
        header.addWidget(self.title_label, 1)
        header.addWidget(self.toggle_button)
        layout.addLayout(header)
        self._button_layout = QVBoxLayout()
        self._button_layout.setSpacing(3)
        self._button_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._button_layout)
        layout.addStretch(1)
        self.hint_label = QLabel("", self)
        self.hint_label.setObjectName("Hint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setContentsMargins(6, 0, 6, 0)
        layout.addWidget(self.hint_label)
        self.setFixedWidth(self._expanded_width)
        if collapsed:
            self.set_collapsed(True)
        else:
            self._apply_width()

    # ------------------------------------------------------------------ #
    # 页面登记与切换
    # ------------------------------------------------------------------ #
    def add(self, key: str, label: str, *, hint: str = "") -> None:
        """登记一页；``key`` 必须唯一，重复登记抛 ``KeyError``。"""
        if key in self._buttons:
            raise KeyError(f"页面已存在: {key}")
        button = QPushButton(label, self)
        button.setObjectName("NavButton")
        button.setCheckable(True)
        button.setAutoExclusive(True)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(hint)
        button.clicked.connect(lambda _checked=False, page_key=key: self.select(page_key))
        self._button_layout.addWidget(button)
        self._group.addButton(button)
        self._buttons[key] = button
        self._keys.append(key)
        self._labels[key] = label
        self._hints[key] = hint
        if self._collapsed:
            button.setText(label[:1])
        if self._current is None:
            self._current = key
            button.setChecked(True)
            self.hint_label.setText(hint)
        self._sync_tooltips()

    def keys(self) -> tuple[str, ...]:
        """按登记顺序返回页面 key。"""
        return tuple(self._keys)

    def selected(self) -> str:
        """当前页面 key；尚未登记任何页面时返回空串。"""
        return self._current or ""

    def label(self, key: str) -> str:
        """页面显示名。"""
        return self._labels.get(key, key)

    def hint(self, key: str) -> str:
        """页面提示文案。"""
        return self._hints.get(key, "")

    def button(self, key: str) -> QPushButton | None:
        """返回页按钮（测试与窗口同步用）。"""
        return self._buttons.get(key)

    def select(self, target: str | int) -> str:
        """切到指定页：``target`` 可以是 key 或索引；非法值抛 ``KeyError``。"""
        key = resolve_page_key(self._keys, target)
        self._activate(key)
        return key

    def select_relative(self, step: int) -> str:
        """按顺序循环切换到相邻页（``step`` 为负数时向前）。"""
        key = next_page(self._keys, self._current, step)
        if key:
            self._activate(key)
        return key

    def _activate(self, key: str) -> None:
        """同步按钮选中态与提示文字，并发出 ``changed``。"""
        if key == self._current and self._buttons[key].isChecked():
            return
        self._current = key
        self._buttons[key].setChecked(True)
        self.hint_label.setText(self._hints.get(key, ""))
        self.changed.emit(key)

    def set_hint(self, key: str, hint: str) -> None:
        """更新页面提示（例如健康横幅变化后同步说明）。"""
        self._hints[key] = hint
        if key == self._current:
            self.hint_label.setText(hint)
        self._sync_tooltips()

    # ------------------------------------------------------------------ #
    # 折叠 / 展开
    # ------------------------------------------------------------------ #
    def is_collapsed(self) -> bool:
        """当前是否处于折叠态。"""
        return self._collapsed

    def toggle(self) -> None:
        """在展开 / 折叠之间切换。"""
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """折叠成窄栏（只显示页名首字 + tooltip）或恢复展开态。

        折叠时隐藏标题与页提示，按钮文本退化为首字；展开时还原完整页名。
        重复设置同一状态只同步宽度，不重复发信号。
        """
        changed = collapsed != self._collapsed
        self._collapsed = collapsed
        self._apply_width()
        self.title_label.setVisible(not collapsed)
        self.hint_label.setVisible(not collapsed)
        self.toggle_button.setText("»" if collapsed else "«")
        self.toggle_button.setToolTip("展开侧边栏（Ctrl+Shift+B）" if collapsed else "折叠侧边栏（Ctrl+Shift+B）")
        for key, button in self._buttons.items():
            button.setText(self._labels[key][:1] if collapsed else self._labels[key])
        self._sync_tooltips()
        if changed:
            self.collapsedChanged.emit(collapsed)

    def _apply_width(self) -> None:
        """按折叠状态设置固定宽度（折叠 52px / 展开 184px，可构造时覆盖）。"""
        self.setFixedWidth(COLLAPSED_WIDTH if self._collapsed else self._expanded_width)

    def _sync_tooltips(self) -> None:
        """折叠态用 tooltip 补全「页名 + 提示」，展开态只有提示。"""
        for key, button in self._buttons.items():
            hint = self._hints.get(key, "")
            if self._collapsed:
                label = self._labels[key]
                button.setToolTip("\n".join(part for part in (label, hint) if part))
            else:
                button.setToolTip(hint)

    def enable_shortcuts(self, window: QWidget) -> None:
        """注册 ``Alt+1..9`` 直达与 ``Alt+↑/↓`` 循环切换。"""
        from PySide6.QtGui import QAction, QKeySequence

        for index, key in enumerate(self._keys[:9], start=1):
            action = QAction(window)
            action.setShortcut(QKeySequence(f"Alt+{index}"))
            action.triggered.connect(lambda _checked=False, page_key=key: self.select(page_key))
            window.addAction(action)
        for sequence, step in (("Alt+Down", 1), ("Alt+Up", -1)):
            action = QAction(window)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(lambda _checked=False, delta=step: self.select_relative(delta))
            window.addAction(action)
