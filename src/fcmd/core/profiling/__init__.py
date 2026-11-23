"""性能剖面：数据模型（``model``）与渲染（``render``）分层。

保留 ``from fcmd.core.profiling import ProfileReport`` 的旧导入路径。
"""

from __future__ import annotations

from .model import ProfileReport, TaskProfile

__all__ = ["ProfileReport", "TaskProfile"]
