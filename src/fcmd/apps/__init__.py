"""fcmd.apps —— 独立桌面应用（Tk 图形界面）。

与 ``fcmd.tools`` 同为工具发现根：每个子包即一个应用。包内默认逐公开模块
注册，模块名即工具名（如 ``fcmd.apps.reshack`` 的 ``reshack`` /
``reshackw``）；包级声明 ``__tool_name__`` 时整包注册为单个工具（如
``cmder`` 包对外命令为 ``cmderw``）。
``_`` 前缀模块只作实现拆分，不参与工具发现。本包门面不 re-export 任何
应用符号。
"""

from __future__ import annotations

__all__: list[str] = []
