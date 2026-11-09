"""fcmd.core.shared —— dsl 与 tools 共用的中立能力层（不注册工具）。

本包只放不依赖上层（``cli`` / ``dsl`` / ``tools``）的通用实现，
供 DSL 动作与 Python 工具共同向下依赖，避免 tools → dsl 的反向导入。
"""

from __future__ import annotations

__all__: list[str] = []
