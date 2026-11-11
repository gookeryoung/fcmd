"""mathtool —— 安全表达式求值、平方根、幂与阶乘。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


def _run(fn: object, *args: object) -> None:
    """执行计算并打印；``ValueError``/``ZeroDivisionError`` 转为错误行（退出码 0）。"""
    from fcmd.console import print_error  # 延迟导入

    try:
        result = fn(*args)  # type: ignore[operator]
    except (ValueError, ZeroDivisionError) as error:
        print_error(str(error))
        return
    print(result)


@fx.tool(
    "mathtool",
    subcommand="eval",
    help="求值数学表达式",
    param_help={"expr": "数学表达式字符串（如 1 + 2 * 3）"},
)
def eval(expr: str) -> None:
    """安全求值数学表达式。"""
    _run(textinfra.eval_expr, expr)


@fx.tool(
    "mathtool",
    subcommand="sqrt",
    help="计算平方根",
    param_help={"x": "待开方的数（须非负）"},
)
def sqrt(x: float) -> None:
    """计算平方根。"""
    _run(textinfra.sqrt_value, x)


@fx.tool(
    "mathtool",
    subcommand="pow",
    help="幂运算",
    param_help={"base": "底数", "exp": "指数"},
)
def pow(base: float, exp: float) -> None:
    """计算 base ** exp。"""
    _run(textinfra.power, base, exp)


@fx.tool(
    "mathtool",
    subcommand="factorial",
    help="计算阶乘",
    param_help={"n": "非负整数"},
)
def factorial(n: int) -> None:
    """计算 n!。"""
    _run(textinfra.factorial_value, n)
