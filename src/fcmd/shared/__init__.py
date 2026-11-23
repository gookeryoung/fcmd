"""跨层共享基础件：纯标准库、无 ``fcmd`` 业务依赖的叶子层。

本包被 ``core`` / ``infras`` / ``gui`` / ``tools`` / ``apps`` 共同依赖，
因此只允许标准库 import，不得反向依赖任何其他 ``fcmd`` 包。
"""

from __future__ import annotations

__all__: list[str] = []
