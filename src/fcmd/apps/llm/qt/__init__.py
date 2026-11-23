"""llmw Qt 页面（ADR 0046 P0 骨架）。

后续按 D5 只迁入本次触达的页面（先 ModelCenterPage / RunPage），
未触达模块保留原 ``apps/llm/_llmw_qt_*.py`` 路径与兼容 shim；
本目录内不再嵌套子包。
"""

from __future__ import annotations

__all__: list[str] = []
