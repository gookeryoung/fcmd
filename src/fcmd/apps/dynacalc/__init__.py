"""dynacalc —— 装药密度 / 尺寸 / 工艺缺陷 → 爆轰传递的参数化算例工具。

只做参数化、模板化、调度与结果汇总，不做物理求解、三维前处理或网格生成：

- :mod:`fcmd.apps.dynacalc.dynacalc` —— CLI（``fcmd dynacalc ...``）；
- :mod:`fcmd.apps.dynacalc.dynacalcw` —— Tk GUI（``fcmd dynacalcw``）。

纯逻辑模块（下划线开头，不注册为工具）：
``_params``（参数表与矩阵）、``_model``（``dynacalc-model/1`` 模型映射）、
``_template``（deck 渲染与清单）、``_solver``（调度）、``_fakesolver``（假求解器）、
``_results``（结果回收）、``_gates``（门控检查）。
"""

from __future__ import annotations

__all__: list[str] = []
