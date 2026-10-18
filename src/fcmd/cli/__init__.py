"""fcmd.cli —— CLI 入口层。

本包只保留入口与基础设施：

- ``main``：命令行路由（内建命令 / 工具名 / DSL 命令）
- ``_discovery``：扫描 :mod:`fcmd.tools` 发现工具模块并注册
- ``_builtins``：内建命令（graph/info/completion/yaml/env/doctor/profiler）
- ``_common`` / ``_env_*`` / ``_doctor_helpers`` / ``_profiler_helpers`` /
  ``_completion_scripts``：共享辅助模块

工具实现位于 :mod:`fcmd.tools`，声明式 exec 型命令位于
``fcmd/commands/*.toml`` + :mod:`fcmd.dsl`。

本门面不 re-export 任何工具符号，避免导入单个工具时连带触发其他工具
的导入（保 ``import fcmd`` 冷启动 < 100ms）。
"""

from __future__ import annotations

__all__: list[str] = []
