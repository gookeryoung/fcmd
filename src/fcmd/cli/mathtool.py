"""mathtool - 数学计算工具。

提供安全的数学表达式求值与常见数学函数。

示例
----
    fcmd mathtool eval "1 + 2 * 3"              # 求值表达式
    fcmd mathtool eval "2 ** 10"                # 幂运算
    fcmd mathtool sqrt 16                        # 平方根
    fcmd mathtool pow 2 10                       # 2 的 10 次方
    fcmd mathtool factorial 5                    # 5 的阶乘
"""

from __future__ import annotations

import ast
import math
import operator as op

import fcmd

__all__ = [
    "eval_expr",
    "factorial",
    "pow_",
    "sqrt",
]

# 安全 eval 支持的二元运算符映射
_BIN_OPS: dict[type, object] = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: op.pow,
}

# 安全 eval 支持的一元运算符映射
_UNARY_OPS: dict[type, object] = {
    ast.UAdd: op.pos,
    ast.USub: op.neg,
}


# ============================================================================
# 公共函数
# ============================================================================


def eval_expr(expr: str) -> float | int:
    """安全求值数学表达式。

    仅支持数字、四则运算、幂运算、取模、括号与正负号，
    不允许调用函数、访问变量或属性，杜绝代码注入风险。

    Parameters
    ----------
    expr:
        数学表达式字符串（如 ``1 + 2 * 3``、``2 ** 10``）

    Returns
    -------
    float | int
        求值结果

    Raises
    ------
    ValueError
        表达式语法错误或包含不支持的语法元素时
    ZeroDivisionError
        除零时
    """
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误: {expr!r}（{exc.msg}）") from exc
    return _eval_node(tree.body)  # type: ignore[no-any-return]


def _eval_node(node: ast.AST) -> float | int:
    """递归求值 AST 节点。"""
    # 数字字面量
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不支持的常量类型: {type(node.value).__name__}")
    # 二元运算
    if isinstance(node, ast.BinOp):
        binop_func = _BIN_OPS.get(type(node.op))
        if binop_func is None:
            raise ValueError(f"不支持的二元运算符: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return binop_func(left, right)  # type: ignore[operator]
    # 一元运算（正负号）
    if isinstance(node, ast.UnaryOp):
        unaryop_func = _UNARY_OPS.get(type(node.op))
        if unaryop_func is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        operand = _eval_node(node.operand)
        return unaryop_func(operand)  # type: ignore[operator]
    raise ValueError(f"不支持的语法元素: {type(node).__name__}")


def sqrt(x: float) -> float:
    """计算平方根。

    Parameters
    ----------
    x:
        待开方的数（必须非负）

    Returns
    -------
    float
        平方根

    Raises
    ------
    ValueError
        输入为负数时
    """
    if x < 0:
        raise ValueError(f"sqrt 要求非负数，当前: {x}")
    return math.sqrt(x)


def pow_(base: float, exp: float) -> float | int:
    """计算幂运算 ``base ** exp``。

    Parameters
    ----------
    base:
        底数
    exp:
        指数

    Returns
    -------
    float | int
        ``base`` 的 ``exp`` 次方
    """
    return base**exp


def factorial(n: int) -> int:
    """计算阶乘 ``n!``。

    Parameters
    ----------
    n:
        非负整数

    Returns
    -------
    int
        ``n`` 的阶乘

    Raises
    ------
    ValueError
        ``n`` 为负数或非整数时
    """
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError(f"factorial 要求非负整数，当前类型: {type(n).__name__}")
    if n < 0:
        raise ValueError(f"factorial 要求非负整数，当前: {n}")
    return math.factorial(n)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("mathtool", subcommand="eval", help="求值数学表达式")
def eval_cmd(expr: str) -> None:
    """安全求值数学表达式。

    Parameters
    ----------
    expr:
        数学表达式字符串（如 ``1 + 2 * 3``）
    """
    try:
        result = eval_expr(expr)
    except (ValueError, ZeroDivisionError) as exc:
        print(str(exc))
        return
    print(result)


@fcmd.tool("mathtool", subcommand="sqrt", help="计算平方根")
def sqrt_cmd(x: float) -> None:
    """计算平方根。

    Parameters
    ----------
    x:
        待开方的数（必须非负）
    """
    try:
        print(sqrt(x))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("mathtool", subcommand="pow", help="幂运算")
def pow_cmd(base: float, exp: float) -> None:
    """计算 ``base ** exp``。

    Parameters
    ----------
    base:
        底数
    exp:
        指数
    """
    print(pow_(base, exp))


@fcmd.tool("mathtool", subcommand="factorial", help="计算阶乘")
def factorial_cmd(n: int) -> None:
    """计算 ``n!``。

    Parameters
    ----------
    n:
        非负整数
    """
    try:
        print(factorial(n))
    except ValueError as exc:
        print(str(exc))


@fcmd.main("mathtool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
