"""fcmd.cli —— CLI 工具层。

两层结构：

- 领域子包（``fileops``/``archive``/``text``/``data``/``conv``/``calc``/
  ``crypto``/``dev``/``system``/``net``/``media``）：每个内含工具模块，
  工具名 = 模块名，经 ``@fx.tool`` 注册后以 ``fcmd <工具名>`` 调用。
- 基础设施模块（``_`` 前缀）：``main`` 路由、``_discovery`` 工具发现、
  ``_builtins`` 内建命令、各共享辅助模块。

本门面不 re-export 任何工具符号，避免导入单个工具时连带触发其他工具
的导入（保 ``import fcmd`` 冷启动 < 100ms）。
"""

from __future__ import annotations

__all__: list[str] = []
