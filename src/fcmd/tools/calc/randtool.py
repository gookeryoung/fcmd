"""randtool 工具：强密码、随机整数、随机字符串与随机字节。

计算逻辑在 :mod:`fcmd.infras.calc`，本模块只做参数适配与输出。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import calc


@fx.tool(
    "randtool",
    subcommand="password",
    help="生成强密码",
    param_help={
        "length": "密码长度（默认 16，>= 4）",
        "symbols": "是否包含符号字符",
    },
)
def randtool_password(length: int = 16, symbols: bool = True) -> None:
    """生成强密码（password）子命令。"""
    try:
        print(calc.generate_password(length, symbols))
    except ValueError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "randtool",
    subcommand="number",
    help="生成随机整数",
    param_help={
        "min_val": "最小值（含）",
        "max_val": "最大值（含）",
    },
)
def randtool_number(min_val: int, max_val: int) -> None:
    """生成随机整数（number）子命令。"""
    try:
        print(calc.generate_number(min_val, max_val))
    except ValueError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "randtool",
    subcommand="string",
    help="生成自定义字符集随机字符串",
    param_help={
        "length": "字符串长度（默认 16，> 0）",
        "chars": "自定义字符集（空串=字母+数字）",
    },
)
def randtool_string(length: int = 16, chars: str = "") -> None:
    """生成自定义字符集随机字符串（string）子命令。"""
    try:
        print(calc.generate_string(length, chars))
    except ValueError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "randtool",
    subcommand="bytes",
    help="生成随机字节",
    param_help={
        "length": "字节长度（> 0）",
        "encoding": "输出编码：hex 或 base64",
    },
)
def randtool_bytes(length: int, encoding: str = "hex") -> None:
    """生成随机字节（bytes）子命令。"""
    try:
        print(calc.generate_bytes(length, encoding))
    except ValueError as exc:
        print(f"错误: {exc}")
