"""paramesh —— 参数化模型到仿真网格的中间层。

公开模块：

- :mod:`fcmd.apps.paramesh.paramesh` —— CLI（``fcmd paramesh ...``）
- :mod:`fcmd.apps.paramesh.parameshw` —— Tk 网格预览器（``fcmd parameshw``）

输入只读取 ``dynacalc-model/1`` 中影响网格的几何与分布场（见
``docs/dynacalc-model-contract.md``），输出二维轴对称（r–z）三档网格、
LS-DYNA 关键字网格片段与质量报告，格式为 ``paramesh-mesh/1``。本工具不求解、
不生成材料参数、不写 dynacalc 的 deck 目录（交接契约未定稿前）。
"""

from __future__ import annotations

__all__: list[str] = []
