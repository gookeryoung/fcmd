"""colortool —— HEX/RGB/HSL 颜色换算（依赖可选 coloraide）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import text as textinfra

__all__: list[str] = []


def _print_result(result: tuple[float, ...] | str) -> None:
    """格式化打印转换结果：元组以空格拼接，字符串原样输出。"""
    if isinstance(result, tuple):
        print(" ".join(str(v) for v in result))
    else:
        print(result)


def _run(subcommand: str, fn: Callable[..., tuple[float, ...] | str], *args: Any, json_output: bool = False) -> None:
    """执行颜色换算并输出；``ValueError`` 转为错误行（输入非法 3 / 缺依赖 1）。"""
    try:
        result = fn(*args)
    except ValueError as exc:
        message = str(exc)
        if "未安装 coloraide" in message:
            code, hint = "RUNTIME_ERROR", "安装可选依赖后重试: pip install fcmd[color]"
        else:
            code, hint = "INVALID_ARGUMENT", "确认颜色分量范围与 #RRGGBB 格式"
        if not json_output:
            # 错误文本可能含 ``[color]`` 等字样，必须转义后交给 markup 渲染
            from rich.markup import escape

            message = escape(message)
        report_error("colortool", subcommand, code=code, message=message, hint=hint, json_output=json_output)
        return
    if json_output:
        emit_result("colortool", subcommand, {"result": list(result) if isinstance(result, tuple) else result})
        return
    _print_result(result)


@fx.tool(
    "colortool",
    subcommand="hex2rgb",
    help="HEX 转 RGB",
    param_help={"hex_color": "十六进制颜色（#RRGGBB）"},
)
def hex2rgb(hex_color: str, json_output: bool = False) -> None:
    """HEX 转 RGB。"""
    _run("hex2rgb", textinfra.hex_to_rgb, hex_color, json_output=json_output)


@fx.tool(
    "colortool",
    subcommand="rgb2hex",
    help="RGB 转 HEX",
    param_help={"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"},
)
def rgb2hex(r: int, g: int, b: int, json_output: bool = False) -> None:
    """RGB 转 HEX。"""
    _run("rgb2hex", textinfra.rgb_to_hex, r, g, b, json_output=json_output)


@fx.tool(
    "colortool",
    subcommand="rgb2hsl",
    help="RGB 转 HSL",
    param_help={"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"},
)
def rgb2hsl(r: int, g: int, b: int, json_output: bool = False) -> None:
    """RGB 转 HSL。"""
    _run("rgb2hsl", textinfra.rgb_to_hsl, r, g, b, json_output=json_output)


@fx.tool(
    "colortool",
    subcommand="hsl2rgb",
    help="HSL 转 RGB",
    param_help={"h": "色相（0-360）", "s": "饱和度（0-100）", "light": "亮度（0-100）"},
)
def hsl2rgb(h: float, s: float, light: float, json_output: bool = False) -> None:
    """HSL 转 RGB。"""
    _run("hsl2rgb", textinfra.hsl_to_rgb, h, s, light, json_output=json_output)
