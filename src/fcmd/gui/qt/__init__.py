"""Qt 展示基座：主题、侧边栏导航、窗口骨架、后台任务与常用控件。

与 :mod:`fcmd.gui`（Tk 基座）并列，服务 ``locllmw`` 等迁移到 Qt 的窗口工具。
本包只在真正创建窗口时才导入 ``PySide6``（可选依赖 ``fcmd[qt]``），
``import fcmd.gui.qt.theme`` 这类纯逻辑模块可在无 Qt 环境（CI）下单测。

模块划分：

- :mod:`fcmd.gui.qt.theme`：调色板 → QSS / QPalette / 字体，纯函数可单测；
- :mod:`fcmd.gui.qt.sidebar`：左侧分页导航（``SidebarNav`` 的 Qt 版本）；
- :mod:`fcmd.gui.qt.window`：QMainWindow 骨架（导航 + 分页栈 + 状态栏 + 主题菜单）；
- :mod:`fcmd.gui.qt.widgets`：卡片、折叠区、表单行、指标网格、日志区、表格；
- :mod:`fcmd.gui.qt.workers`：后台任务与定时器，结果回投 GUI 线程。

本包位于 :mod:`fcmd.gui`，不参与 :mod:`fcmd.tools` 的工具发现。
"""

from __future__ import annotations

__all__: list[str] = []
