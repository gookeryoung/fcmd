"""fcmd.tools —— Python 实现的工具模块集合。

领域子包（``archive``/``calc``/``dev``/``media``/``net``）内的模块即工具，
由 :mod:`fcmd.cli._discovery` 用 ``pkgutil.iter_modules`` 扫描并按
``@fx.tool`` 注册为顶层命令（工具名 = 模块名，调用方式 ``fcmd <工具名>``）。

本包不 re-export 任何工具符号：导入单个工具时不应连带触发同域其他工具的导入。
"""

from __future__ import annotations

__all__: list[str] = []
