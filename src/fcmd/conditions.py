"""条件表达式与矩阵展开工具。

为 YAML 任务编排提供条件判断（``if`` 表达式）与矩阵扇出（``matrix`` 展开）
能力，作为 :data:`fcmd.task.Condition` 的辅助构造器。

if 表达式
----------
基于 :mod:`ast` 模块安全求值（禁用 :func:`eval`，仅允许白名单节点），
支持：

- 状态检查：``success()`` / ``failure()`` / ``always()``
- 上下文访问：``ctx.NAME``（依赖任务的返回值）
- 环境变量：``vars.NAME``
- 字面量：字符串 / 数字 / 布尔 / ``None``
- 比较：``==`` / ``!=``
- 逻辑：``not`` / ``and`` / ``or`` / 括号

状态检查依赖执行器在上下文中注入的 ``__status__`` 键，值为
``{task_name: status_value}`` 映射（仅含本任务的硬依赖状态）。

示例
-----
    >>> from fcmd.conditions import parse_if
    >>> cond = parse_if("ctx.build == 'ok' and vars.CI == 'true'")
    >>> cond({"build": "ok", "__status__": {"build": "success"}})  # 需 CI 环境变量
    False

matrix 展开
------------
笛卡尔积展开：``matrix: {py: ["3.8","3.9"], os: ["linux"]}`` → 2 个组合。
每个组合通过 ``${{ matrix.NAME }}`` 占位符注入到 ``cmd``/``run``/``env``
等字段；任务名追加后缀 ``"(py-3.8)(os-linux)"`` 以保证展开后唯一。

示例
-----
    >>> from fcmd.conditions import expand_matrix, substitute_matrix_vars, matrix_suffix
    >>> combos = expand_matrix({"py": ["3.8", "3.9"], "os": ["linux"]})
    >>> len(combos)
    2
    >>> substitute_matrix_vars("echo ${{ matrix.py }}", combos[0])
    'echo 3.8'
    >>> matrix_suffix(combos[0])
    '(py-3.8)(os-linux)'
"""

from __future__ import annotations

__all__ = [
    "ConditionError",
    "expand_matrix",
    "matrix_suffix",
    "parse_if",
    "substitute_matrix_vars",
]

import ast
import os
import re
from collections.abc import Mapping
from typing import Any

from fcmd.task import Condition, Context

# matrix 变量占位符正则：${{ matrix.NAME }}（允许中间空白）
_MATRIX_VAR_PATTERN = re.compile(r"\$\{\{\s*matrix\.(\w+)\s*\}\}")


class ConditionError(ValueError):
    """条件表达式解析或求值错误。"""


# ---------------------------------------------------------------------- #
# 状态检查辅助
# ---------------------------------------------------------------------- #
def _get_upstream_status(context: Context) -> Mapping[str, str]:
    """从上下文获取上游任务状态映射。

    执行器在构建上下文时注入 ``__status__`` 键，值为
    ``{task_name: status_value}``。若未注入（编程式使用场景），返回空映射。
    """
    status = context.get("__status__") if context else None
    if isinstance(status, Mapping):
        return status
    return {}


def _check_success(context: Context) -> bool:
    """所有上游任务状态为 success（或无上游）。"""
    statuses = _get_upstream_status(context)
    if not statuses:
        return True
    return all(s == "success" for s in statuses.values())


def _check_failure(context: Context) -> bool:
    """任一上游任务状态为 failed。"""
    statuses = _get_upstream_status(context)
    return any(s == "failed" for s in statuses.values())


def _check_always() -> bool:
    """总是返回 True。

    需配合 ``allow_upstream_skip=True`` 才能真正在上游失败时执行；
    否则上游失败时本任务在状态检查前已被跳过。
    """
    return True


# 状态检查函数映射（白名单）；_check_always 无 context 参数，用 lambda 适配签名
_STATUS_FUNCS: dict[str, Any] = {
    "success": _check_success,
    "failure": _check_failure,
    "always": lambda _ctx: _check_always(),
}


# ---------------------------------------------------------------------- #
# if 表达式解析（基于 ast 安全求值）
# ---------------------------------------------------------------------- #
def parse_if(expr: str) -> Condition:
    """解析 if 表达式为 :data:`fcmd.task.Condition` 函数。

    Parameters
    ----------
    expr:
        if 表达式字符串，如 ``"failure()"`` 或 ``"ctx.build == 'ok'"``。

    Returns
    -------
    Condition
        接收 :data:`fcmd.task.Context` 返回 ``bool`` 的函数。
        函数附带 ``_reason`` 属性供 :meth:`TaskSpec.should_execute` 展示跳过原因。

    Raises
    ------
    ConditionError
        表达式语法错误或包含不支持的节点时。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ConditionError(f"if 表达式语法错误: {expr!r}: {exc.msg}") from exc

    def condition(context: Context) -> bool:
        """if 表达式求值条件函数。"""
        return bool(_eval_node(tree.body, context))

    condition.__name__ = f"if({expr})"
    # _reason 属性供 TaskSpec.should_execute 展示跳过原因
    condition._reason = f"if: {expr}"  # type: ignore[attr-defined]
    return condition


def _eval_node(node: ast.AST, context: Context) -> Any:
    """递归求值 AST 节点（白名单分发）。"""
    handler = _NODE_HANDLERS.get(type(node))
    if handler is None:
        raise ConditionError(f"不支持的表达式节点: {type(node).__name__}")
    return handler(node, context)


def _eval_boolop(node: ast.BoolOp, context: Context) -> Any:
    """求值布尔运算 ``and`` / ``or``（短路求值）。

    ``ast.BoolOp.op`` 在 AST 中仅可能为 ``And`` 或 ``Or``，无需额外分支。
    """
    if isinstance(node.op, ast.And):
        result: Any = True
        for value_node in node.values:
            result = _eval_node(value_node, context)
            if not result:
                return result
        return result
    # ast.BoolOp.op 仅可能为 And 或 Or，此处为 Or
    result: Any = False
    for value_node in node.values:
        result = _eval_node(value_node, context)
        if result:
            return result
    return result


def _eval_unaryop(node: ast.UnaryOp, context: Context) -> Any:
    """求值一元运算 ``not``。"""
    if isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, context)
    raise ConditionError(f"不支持的一元运算: {type(node.op).__name__}")


def _eval_compare(node: ast.Compare, context: Context) -> Any:
    """求值比较运算 ``==`` / ``!=``（支持链式比较 ``a == b == c``）。"""
    left = _eval_node(node.left, context)
    for op, comparator in zip(node.ops, node.comparators):
        right = _eval_node(comparator, context)
        if isinstance(op, ast.Eq):
            if left != right:
                return False
        elif isinstance(op, ast.NotEq):
            if left == right:
                return False
        else:
            raise ConditionError(f"不支持的比较运算: {type(op).__name__}")
        left = right
    return True


def _eval_call(node: ast.Call, context: Context) -> Any:
    """求值函数调用 ``success()`` / ``failure()`` / ``always()``。"""
    if not isinstance(node.func, ast.Name):
        raise ConditionError("仅支持 success()/failure()/always() 函数调用")
    func_name = node.func.id
    if func_name not in _STATUS_FUNCS:
        raise ConditionError(f"不支持的函数: {func_name}()")
    if node.args or node.keywords:
        raise ConditionError(f"{func_name}() 不接受参数")
    return _STATUS_FUNCS[func_name](context)


def _eval_attribute(node: ast.Attribute, context: Context) -> Any:
    """求值属性访问 ``ctx.NAME`` / ``vars.NAME``。"""
    if not isinstance(node.value, ast.Name):
        raise ConditionError("仅支持 ctx.NAME / vars.NAME 属性访问")
    obj_name = node.value.id
    attr = node.attr
    if obj_name == "ctx":
        return context.get(attr) if context else None
    if obj_name == "vars":
        return os.environ.get(attr)
    raise ConditionError(f"不支持的变量: {obj_name}")


def _eval_name(node: ast.Name) -> Any:
    """求值裸名。

    Python 3.8+ 中 ``True``/``False``/``None`` 均为 ``ast.Constant`` 而非
    ``ast.Name``，故此函数仅处理未知标识符（错误场景）。
    """
    raise ConditionError(f"不支持的标识符: {node.id}")


# AST 节点处理器映射（白名单）；Constant/Name 无需 context，用 lambda 适配签名
_NODE_HANDLERS: dict[type[ast.AST], Any] = {
    ast.BoolOp: _eval_boolop,
    ast.UnaryOp: _eval_unaryop,
    ast.Compare: _eval_compare,
    ast.Call: _eval_call,
    ast.Attribute: _eval_attribute,
    ast.Name: lambda n, _: _eval_name(n),
    ast.Constant: lambda n, _: n.value,
}


# ---------------------------------------------------------------------- #
# matrix 展开
# ---------------------------------------------------------------------- #
def expand_matrix(matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    """笛卡尔积展开 matrix 配置。

    Parameters
    ----------
    matrix:
        矩阵配置映射，每个键对应值列表。如 ``{"py": ["3.8", "3.9"], "os": ["linux"]}``。

    Returns
    -------
    list[dict[str, Any]]
        组合列表，每个组合是一个 ``{key: value}`` 字典。
        空矩阵返回 ``[{}]``（单个空组合，等价于无 matrix）。

    Raises
    ------
    ConditionError
        matrix 值非列表时。
    """
    if not matrix:
        return [{}]
    keys = list(matrix.keys())
    result: list[dict[str, Any]] = [{}]
    for key in keys:
        values = matrix[key]
        if not isinstance(values, list):
            raise ConditionError(f"matrix.{key} 必须是列表，收到: {type(values).__name__}")
        new_result: list[dict[str, Any]] = []
        for existing in result:
            for value in values:
                combo = dict(existing)
                combo[key] = value
                new_result.append(combo)
        result = new_result
    return result


def substitute_matrix_vars(text: str, combo: Mapping[str, Any]) -> str:
    """替换 ``${{ matrix.NAME }}`` 占位符。

    未在 ``combo`` 中出现的 key 保持原样（不替换），便于多层展开场景。

    Parameters
    ----------
    text:
        待替换的字符串。
    combo:
        matrix 组合字典。

    Returns
    -------
    str
        替换后的字符串。
    """

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in combo:
            return str(combo[key])
        return match.group(0)

    return _MATRIX_VAR_PATTERN.sub(replace, text)


def matrix_suffix(combo: Mapping[str, Any]) -> str:
    """生成 matrix 任务名后缀 ``(key1-value1)(key2-value2)``。

    键按插入顺序排列（Python 3.7+ dict 保序）。空组合返回空字符串。

    Parameters
    ----------
    combo:
        matrix 组合字典。

    Returns
    -------
    str
        后缀字符串，如 ``(py-3.8)(os-linux)``；空组合返回 ``""``。
    """
    if not combo:
        return ""
    parts = [f"{k}-{v}" for k, v in combo.items()]
    return "(" + ")(".join(parts) + ")"
