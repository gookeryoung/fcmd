"""展示基座：Qt（PySide6）GUI 共享层与主题注册表。

所有 Qt 窗口工具（``llmw`` / ``cmderw`` / 各 ``*w`` 应用）共用本包，模块全部平铺在
``fcmd/gui/`` 下（不再有 ``gui/qt/`` 子层），纯数据与 Qt 翻译分开放：

- :mod:`fcmd.gui.theme`：主题注册表（四套内置主题、别名、`ui/theme.json` 与调色板 / 度量），
  纯数据与纯函数，不导入 GUI 库；
- :mod:`fcmd.gui.style`：把 `Palette` / `Metrics` 翻译成 QSS / `QPalette`，只在
  `apply_theme` 内延迟导入 Qt；
- :mod:`fcmd.gui.geometry`：`WxH+X+Y` 解析 / 夹紧 / 居中纯函数；
- :mod:`fcmd.gui.config`：`${FCMD_HOME:-~/.fcmd}/ui/*.json` 的原子读写；
- :mod:`fcmd.gui.nav`：分页 key 解析与循环切换纯函数；
- :mod:`fcmd.gui.app`：`QApplication` 生命周期、高 DPI 策略与 `run_window`；
- :mod:`fcmd.gui.window` / :mod:`fcmd.gui.toolwindow`：分页窗口与单页工具窗口骨架；
- :mod:`fcmd.gui.shell` / :mod:`fcmd.gui.page` / :mod:`fcmd.gui.theme_menu`：窗口外壳
  mixin、页面骨架（`PageBody` / `AppPage` / `PageSpec`）与主题菜单；
- :mod:`fcmd.gui.sidebar` / :mod:`fcmd.gui.statusbar` / :mod:`fcmd.gui.logdialog`：
  侧边栏导航、底部状态栏与独立日志对话框；
- :mod:`fcmd.gui.icons` / :mod:`fcmd.gui.ime` / :mod:`fcmd.gui.layout` /
  :mod:`fcmd.gui.compat`：内置图标、输入法占位守卫、省略 / 列宽纯函数与 PySide2 映射；
- :mod:`fcmd.gui.workers`：后台任务与定时器，结果回投 GUI 线程；
- :mod:`fcmd.gui.widgets`：常用展示控件包，按职责拆成 ``text`` / ``tables`` /
  ``buttons`` / ``tabs`` / ``rows`` / ``cards`` / ``forms`` / ``metrics`` / ``log`` /
  ``splitter`` 子模块，``from fcmd.gui.widgets import Card`` 一次拿全。

`PySide6` 只在创建窗口的路径上导入；本包不属于任何业务域，也不在
:mod:`fcmd.tools` 的工具发现命名空间内。各工具的窗口与业务同域
（``tools/<域>/<工具>w.py``、``apps/<应用>/_<工具>w_qt.py``），复用这里的基座。
"""

from __future__ import annotations

__all__: list[str] = []
