"""fcmd.cli —— CLI 工具层。

两层结构：

- 领域子包（``archive``/``calc``/``dev``/``media``/``net``）：每个内含工具模块，
  工具名 = 模块名，经 ``@fx.tool`` 注册后以 ``fcmd <工具名>`` 调用。
  声明式 exec 型工具已全部迁入 DSL（``fcmd/commands/*.toml`` + ``fcmd/dsl/``），
  本包仅保留含 Python 逻辑的工具模块；原先的 ``conv``/``crypto``/``data``/
  ``text``/``fileops`` 空壳子包已随迁移删除。
- 基础设施模块（``_`` 前缀）：``main`` 路由、``_discovery`` 工具发现、
  ``_builtins`` 内建命令、各共享辅助模块。

本门面不 re-export 任何工具符号，避免导入单个工具时连带触发其他工具
的导入（保 ``import fcmd`` 冷启动 < 100ms）。
"""

from __future__ import annotations

__all__: list[str] = []
