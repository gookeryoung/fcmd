"""fcmd.apps —— 独立桌面应用（Tk 图形界面）。

与 ``fcmd.tools`` 同为工具发现根：每个子包即一个应用（如
``fcmd.apps.cmderw``），包名即工具名（调用方式 ``fcmd <应用名>``）；
``_`` 前缀模块只作实现拆分，不参与工具发现。本包门面不 re-export 任何
应用符号。
"""

from __future__ import annotations

__all__: list[str] = []
