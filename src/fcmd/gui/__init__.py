"""展示基座：Qt（PySide6）GUI 共享层与主题注册表。

- :mod:`fcmd.gui.qt` 提供窗口骨架（`QtMainWindow` / `QtToolWindow`）、主题、
  侧边栏导航、通用控件、后台任务与统一启动入口 `run_window`；
- :mod:`fcmd.gui.theme` 保存主题注册表（四套内置主题、别名、`ui/theme.json` 与
  调色板），`fcmd.gui.qt.theme` 只做 `Palette → QSS/QPalette` 翻译；
- :mod:`fcmd.gui.geometry` 是不导入 GUI 库的 `WxH+X+Y` 解析 / 夹紧 / 居中纯函数；
- :mod:`fcmd.gui.config` 负责 `${FCMD_HOME:-~/.fcmd}/ui/*.json` 的原子读写。

`PySide6` 只在创建窗口的路径上导入；本包不属于任何业务域，也不在
:mod:`fcmd.tools` 的工具发现命名空间内。各工具的窗口与业务同域
（``tools/<域>/<工具>w.py``、``apps/<应用>/_<工具>w_qt.py``），复用这里的基座。
"""

from __future__ import annotations

__all__: list[str] = []
