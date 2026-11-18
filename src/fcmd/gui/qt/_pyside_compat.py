"""PySide2 / PySide6 运行时兼容层。

项目在 Python ≥3.11 使用 PySide6，在 Python 3.10 使用 PySide2（PySide2 最后
支持到 cp310）。业务代码继续写 ``from PySide6...``；本模块在 3.10 上安装一个
轻量 meta path finder，把 ``PySide6.*`` 延迟映射到 ``PySide2.*``，并补齐 Qt6
相对 Qt5 的少量 API 差异（``QAction`` 所在模块、``exec`` / ``position`` 命名）。

不在这里预先导入 Qt：安装 finder 只是往 ``sys.meta_path`` 插一项，CLI 冷启动
仍不会加载图形库；只有真正导入 ``PySide6`` 时才触发 PySide2 导入与补丁。
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from types import ModuleType

__all__ = ["install"]

_FINDER: _PySide2Finder | None = None


class _PySide2Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """把 ``PySide6[.submodule]`` 透明地加载为 ``PySide2[.submodule]``。"""

    def find_spec(
        self,
        fullname: str,
        _path: object = None,
        _target: object = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            return importlib.util.spec_from_loader(fullname, self, is_package=fullname == "PySide6")
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        if spec.name == "PySide6":
            module = importlib.import_module("PySide2")
            _patch_binding()
            return module
        return importlib.import_module("PySide2" + spec.name[len("PySide6") :])

    def exec_module(self, module: ModuleType) -> None:  # noqa: ARG002 - 已初始化的真实模块
        return None


_FINDERS: list[_PySide2Finder] = []
_MENU_REFS: list[object] = []
_ACTION_REFS: list[object] = []


def install() -> None:
    """Python 3.10 上启用 PySide2 兼容映射（幂等；其它版本直接返回）。"""
    if sys.version_info >= (3, 11) or _FINDERS:
        return
    if importlib.util.find_spec("PySide2") is None:
        return
    finder = _PySide2Finder()
    sys.meta_path.insert(0, finder)
    _FINDERS.append(finder)


def _patch_binding() -> None:
    """补齐 Qt6 → Qt5 的少类别名；重复调用安全。"""
    gui = importlib.import_module("PySide2.QtGui")
    widgets = importlib.import_module("PySide2.QtWidgets")

    for name in ("QAction", "QActionGroup", "QShortcut", "QShortcutEvent"):
        if not hasattr(gui, name) and hasattr(widgets, name):
            setattr(gui, name, getattr(widgets, name))

    for cls_name in ("QApplication", "QDialog", "QMenu"):
        cls = getattr(widgets, cls_name, None)
        if cls is not None and not hasattr(cls, "exec") and hasattr(cls, "exec_"):
            cls.exec = cls.exec_  # type: ignore[attr-defined]

    _keep_added_menus(widgets.QMenuBar)
    _keep_added_menus(widgets.QMenu)
    _keep_action_menus(widgets.QAction)

    wheel = getattr(gui, "QWheelEvent", None)
    if wheel is not None and not hasattr(wheel, "position") and hasattr(wheel, "posF"):
        wheel.position = wheel.posF  # type: ignore[attr-defined]
    mouse = getattr(gui, "QMouseEvent", None)
    if mouse is not None:
        if not hasattr(mouse, "position") and hasattr(mouse, "localPos"):
            mouse.position = mouse.localPos  # type: ignore[attr-defined]
        if not hasattr(mouse, "globalPosition") and hasattr(mouse, "globalPos"):
            mouse.globalPosition = mouse.globalPos  # type: ignore[attr-defined]


def _keep_added_menus(owner: type) -> None:
    """PySide2 下 ``addMenu(str)`` 的返回值若不持引用会被 Shiboken 回收。"""
    if getattr(owner, "_fcmd_add_menu_patched", False):
        return
    original = owner.addMenu

    def add_menu(self: object, *args: object) -> object:
        menu = original(self, *args)
        if menu is not None:
            menu.setParent(self)  # type: ignore[attr-defined]
            _MENU_REFS.append(menu)
        return menu

    owner.addMenu = add_menu  # type: ignore[method-assign]
    owner._fcmd_add_menu_patched = True  # type: ignore[attr-defined]


def _keep_action_menus(owner: type) -> None:
    """PySide2 下 ``QAction.menu()`` 返回的包装可能随 action 临时对象失效。"""
    if getattr(owner, "_fcmd_action_menu_patched", False):
        return
    original = owner.menu

    def menu(self: object) -> object:
        current = original(self)
        if current is not None:
            _ACTION_REFS.append(self)
            _MENU_REFS.append(current)
        return current

    owner.menu = menu  # type: ignore[method-assign]
    owner._fcmd_action_menu_patched = True  # type: ignore[attr-defined]
