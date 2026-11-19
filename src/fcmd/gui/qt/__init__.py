"""Qt 展示基座：主题、侧边栏导航、窗口骨架、后台任务与常用控件。

服务全部 GUI 窗口工具（``llmw`` / ``cmderw`` / 各 ``*w`` 应用）。
本包只在真正创建窗口时才导入 ``PySide6``（基础依赖，安装 fcmd 即具备），
``import fcmd.gui.qt.theme`` 这类纯逻辑模块可在无 Qt 环境（CI）下单测。

模块划分：

- :mod:`fcmd.gui.qt.theme`：调色板 → QSS / QPalette / 字体，纯函数可单测；
- :mod:`fcmd.gui.qt.sidebar`：左侧分页导航（``SidebarNav`` 的 Qt 版本）；
- :mod:`fcmd.gui.qt.window`：QMainWindow 骨架（导航 + 分页栈 + 状态栏 + 主题菜单）；
- :mod:`fcmd.gui.qt.toolwindow`：单页工具窗口骨架（菜单 + 状态栏 + 几何记忆）；
- :mod:`fcmd.gui.qt.theme_menu`：主题子菜单与即时切换（分页 / 单页窗口共用）；
- :mod:`fcmd.gui.qt.app`：QApplication 生命周期、高 DPI 策略与 ``run_window``；
- :mod:`fcmd.gui.qt.layout`：省略与列宽 / 分栏尺寸的纯函数；
- :mod:`fcmd.gui.qt.widgets`：卡片、折叠区、表单行、指标网格、日志区、表格、长文本标签；
- :mod:`fcmd.gui.qt.workers`：后台任务与定时器，结果回投 GUI 线程。

本包位于 :mod:`fcmd.gui`，不参与 :mod:`fcmd.tools` 的工具发现。
"""

from __future__ import annotations

__all__: list[str] = []
