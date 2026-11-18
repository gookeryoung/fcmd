"""mathtool —— 安全表达式求值、平方根、幂与阶乘。"""

from __future__ import annotations

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import text as textinfra

__all__: list[str] = []


def _run(subcommand: str, fn: object, *args: object, json_output: bool = False) -> None:
    """执行计算并输出；``ValueError``/``ZeroDivisionError`` 转为错误行（退出码 3）。"""
    try:
        result = fn(*args)  # type: ignore[operator]
    except (ValueError, ZeroDivisionError) as error:
        report_error(
            "mathtool",
            subcommand,
            code="INVALID_ARGUMENT",
            message=str(error),
            hint="检查表达式语法、除数是否为 0、以及 sqrt/factorial 的取值范围",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("mathtool", subcommand, {"result": result})
        return
    print(result)


@fx.tool(
    "mathtool",
    subcommand="eval",
    help="求值数学表达式",
    param_help={"expr": "数学表达式字符串（如 1 + 2 * 3）"},
)
def eval(expr: str, json_output: bool = False) -> None:
    """安全求值数学表达式。"""
    _run("eval", textinfra.eval_expr, expr, json_output=json_output)


@fx.tool(
    "mathtool",
    subcommand="sqrt",
    help="计算平方根",
    param_help={"x": "待开方的数（须非负）"},
)
def sqrt(x: float, json_output: bool = False) -> None:
    """计算平方根。"""
    _run("sqrt", textinfra.sqrt_value, x, json_output=json_output)


@fx.tool(
    "mathtool",
    subcommand="pow",
    help="幂运算",
    param_help={"base": "底数", "exp": "指数"},
)
def pow(base: float, exp: float, json_output: bool = False) -> None:
    """计算 base ** exp。"""
    _run("pow", textinfra.power, base, exp, json_output=json_output)


@fx.tool(
    "mathtool",
    subcommand="factorial",
    help="计算阶乘",
    param_help={"n": "非负整数"},
)
def factorial(n: int, json_output: bool = False) -> None:
    """计算 n!。"""
    _run("factorial", textinfra.factorial_value, n, json_output=json_output)
