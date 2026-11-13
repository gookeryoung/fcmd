"""paramcad —— 参数化建模与几何预览（dynacalc 的上游模型来源）。

公开模块：

- :mod:`fcmd.apps.paramcad.paramcad` —— CLI（``fcmd paramcad ...``）
- :mod:`fcmd.apps.paramcad.paramcadw` —— Tk 预览器（``fcmd paramcadw``）

几何 / 工艺推导、分布场、缺陷与导出契约分别位于 ``_model`` / ``_fields`` /
``_defects`` / ``_export``，预览采样位于 ``_preview``，均不依赖 Tk。导出格式
为 ``dynacalc-model/1``（见 ``docs/dynacalc-model-contract.md``），本工具不求解、
不生成网格、不输出 LS-DYNA 关键字。
"""

from __future__ import annotations

__all__: list[str] = []
