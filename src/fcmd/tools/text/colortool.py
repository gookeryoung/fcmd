"""colortool —— HEX/RGB/HSL 颜色换算（依赖可选 coloraide）。"""

from __future__ import annotations

from collections.abc import Callable

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


def _print_result(result: tuple[float, ...] | str) -> None:
    """格式化打印转换结果：元组以空格拼接，字符串原样输出。"""
    if isinstance(result, tuple):
        print(" ".join(str(v) for v in result))
    else:
        print(result)


def _run(fn: Callable[..., tuple[float, ...] | str], *args: object) -> None:
    """执行颜色换算并打印；``ValueError`` 转为错误行（错误信息即结果，退出码 0）。"""
    try:
        result = fn(*args)
    except ValueError as exc:
        from rich.markup import escape

        from fcmd.console import print_error  # 延迟导入

        # 错误文本可能含 ``[color]`` 等字样，必须转义后交给 markup 渲染
        print_error(escape(str(exc)))
        return
    _print_result(result)


@fx.tool(
    "colortool",
    subcommand="hex2rgb",
    help="HEX 转 RGB",
    param_help={"hex_color": "十六进制颜色（#RRGGBB）"},
)
def hex2rgb(hex_color: str) -> None:
    """HEX 转 RGB。"""
    _run(textinfra.hex_to_rgb, hex_color)


@fx.tool(
    "colortool",
    subcommand="rgb2hex",
    help="RGB 转 HEX",
    param_help={"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"},
)
def rgb2hex(r: int, g: int, b: int) -> None:
    """RGB 转 HEX。"""
    _run(textinfra.rgb_to_hex, r, g, b)


@fx.tool(
    "colortool",
    subcommand="rgb2hsl",
    help="RGB 转 HSL",
    param_help={"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"},
)
def rgb2hsl(r: int, g: int, b: int) -> None:
    """RGB 转 HSL。"""
    _run(textinfra.rgb_to_hsl, r, g, b)


@fx.tool(
    "colortool",
    subcommand="hsl2rgb",
    help="HSL 转 RGB",
    param_help={"h": "色相（0-360）", "s": "饱和度（0-100）", "light": "亮度（0-100）"},
)
def hsl2rgb(h: float, s: float, light: float) -> None:
    """HSL 转 RGB。"""
    _run(textinfra.hsl_to_rgb, h, s, light)
