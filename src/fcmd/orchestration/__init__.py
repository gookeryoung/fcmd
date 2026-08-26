"""fcmd.orchestration —— YAML 任务编排域。

GitHub Actions 风格的 YAML 编排功能：

- ``yaml_loader``：YAML → :class:`~fcmd.apis.dag.Graph` 加载器
  （``jobs``/``needs``/``cmd``/``run``/``env``/``matrix`` 等字段映射）
- ``conditions``：``if`` 条件表达式安全解析（AST 白名单）与
  ``matrix`` 矩阵展开（笛卡尔积扇出）
"""

from __future__ import annotations

from fcmd.orchestration.conditions import (
    ConditionError,
    expand_matrix,
    matrix_suffix,
    parse_if,
    substitute_matrix_vars,
)
from fcmd.orchestration.yaml_loader import load_yaml, parse_yaml_string

__all__ = [
    "ConditionError",
    "expand_matrix",
    "load_yaml",
    "matrix_suffix",
    "parse_if",
    "parse_yaml_string",
    "substitute_matrix_vars",
]
