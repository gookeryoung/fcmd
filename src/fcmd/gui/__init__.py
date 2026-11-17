"""展示基座：Tk 窗口骨架 / 主题 / UI 设置持久化，以及 Qt（PySide6）基座。

Tk 部分只依赖标准库 tkinter；Qt 部分在 :mod:`fcmd.gui.qt`，依赖基础依赖
``PySide6-Essentials``，且只在创建窗口时导入 ``PySide6``。本包不属于任何业务域，也不在
:mod:`fcmd.tools` 的工具发现命名空间内。各工具的窗口仍与业务同域
（``tools/<域>/<工具>w.py``），复用这里的 :mod:`fcmd.gui.window` /
:mod:`fcmd.gui.theme` / :mod:`fcmd.gui.config` / :mod:`fcmd.gui.sidebar`；
Qt 窗口用 :mod:`fcmd.gui.qt.window` / :mod:`fcmd.gui.qt.theme` /
:mod:`fcmd.gui.qt.sidebar` / :mod:`fcmd.gui.qt.widgets` /
:mod:`fcmd.gui.qt.workers`，主题与设置文件与 Tk 侧共用。
"""

from __future__ import annotations

__all__: list[str] = []
