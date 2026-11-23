"""``fcmd.infras.text.math``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

import ast
import math
import operator as op

__all__ = ["eval_expr", "factorial_value", "power", "sqrt_value"]


_BIN_OPS: dict[type, object] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}


_UNARY_OPS: dict[type, object] = {
    ast.UAdd: op.pos,
    ast.USub: op.neg,
}


def eval_expr(expr: str) -> float | int:
    """安全求值数学表达式。"""
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误: {expr!r}（{exc.msg}）") from exc
    return _eval_node(tree.body)  # type: ignore[no-any-return]


def _eval_node(node: ast.AST) -> float | int:
    """递归求值 AST 节点。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不支持的常量类型: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        binop_func = _BIN_OPS.get(type(node.op))
        if binop_func is None:
            raise ValueError(f"不支持的二元运算符: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return binop_func(left, right)  # type: ignore[operator]
    if isinstance(node, ast.UnaryOp):
        unaryop_func = _UNARY_OPS.get(type(node.op))
        if unaryop_func is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        operand = _eval_node(node.operand)
        return unaryop_func(operand)  # type: ignore[operator]
    raise ValueError(f"不支持的语法元素: {type(node).__name__}")


def sqrt_value(x: float) -> float:
    """计算平方根（负数抛 ``ValueError``）。"""
    if x < 0:
        raise ValueError(f"sqrt 要求非负数，当前: {x}")
    return math.sqrt(x)


def power(base: float, exp: float) -> float:
    """计算 ``base ** exp``。"""
    return base**exp


def factorial_value(n: int) -> int:
    """计算 ``n!``（非负整数）。"""
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"factorial 要求非负整数，当前类型: {type(n).__name__}")
    if n < 0:
        raise ValueError(f"factorial 要求非负整数，当前: {n}")
    return math.factorial(n)
