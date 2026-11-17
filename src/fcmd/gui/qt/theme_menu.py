"""Qt 主题菜单：一处实现「视图 → 主题」子菜单与即时切换。

:class:`QtMainWindow`（分页窗口）与 :class:`fcmd.gui.qt.toolwindow.QtToolWindow`
（单页工具窗口）共用本控制器，避免每个窗口各复制一份主题动作与持久化逻辑。
主题注册表仍是 :mod:`fcmd.gui.theme`，Qt 侧只负责翻译成 QSS / QPalette。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

__all__ = ["ThemeMenu"]


class ThemeMenu(QObject):  # pragma: no cover - Qt 菜单胶水
    """主题子菜单控制器：登记动作、切换并持久化当前主题。"""

    def __init__(self, owner: QObject | None = None, theme: Theme | None = None) -> None:
        super().__init__(owner)
        self._owner = owner
        self._theme: Theme = theme if theme is not None else _qt_theme.load_theme()
        self._actions: dict[str, QAction] = {}
        self._menu: QMenu | None = None

    def attach(self, menu: QMenu) -> None:
        """把四套主题登记为可勾选动作；重复调用会重新生成动作。"""
        self._menu = menu
        self._actions.clear()
        for name in _guitheme.theme_names():
            theme = _guitheme.resolve_theme(name)
            action = QAction(theme.label, self._owner)
            action.setCheckable(True)
            action.setChecked(theme.name == self._theme.name)
            action.setToolTip(theme.description)
            action.triggered.connect(lambda _checked=False, chosen=theme: self.set_theme(chosen))
            menu.addAction(action)
            self._actions[theme.name] = action

    def set_theme(self, theme: Theme) -> None:
        """立即应用并持久化主题，同时同步勾选状态。"""
        self._theme = _guitheme.save_theme(theme)
        app = QApplication.instance()
        if app is not None:
            _qt_theme.apply_theme(app, self._theme)
        for name, action in self._actions.items():
            action.setChecked(name == self._theme.name)

    def theme(self) -> Theme:
        """当前主题。"""
        return self._theme

    def action(self, name: str) -> QAction | None:
        """返回指定主题的动作（测试与快捷键扩展用）。"""
        return self._actions.get(name)
