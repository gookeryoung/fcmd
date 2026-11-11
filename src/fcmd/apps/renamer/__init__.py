"""renamer —— 批量重命名应用（规则流水线 + 正则重排 / 搬移）。

包内 ``renamer`` 为命令行入口、``renamerw`` 为 Tk 窗口入口；规则求值、
预览、冲突检测、两阶段执行与撤销全部在 :mod:`fcmd.apps.renamer._core`，
两个入口共用同一套口径。下划线前缀模块不参与 :mod:`fcmd` 的工具发现。
"""

from __future__ import annotations

__all__: list[str] = []
