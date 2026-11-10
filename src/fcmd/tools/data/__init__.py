"""fcmd.tools.data —— 数据域：CSV / INI / JSON / TOML / XML / YAML 结构化数据转换。

领域子包：实现统一放在 :mod:`fcmd.tools.data.database`，供
``fcmd.dsl.actions.toolcmd.data`` 的 ``@action`` 声明与外部代码复用；本包门面
不 re-export 任何实现符号，避免导入单个工具时连带触发同域其他工具的导入。
"""

from __future__ import annotations

__all__: list[str] = []
