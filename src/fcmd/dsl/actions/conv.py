"""转换与计算动作：命名风格（casetool）、颜色换算（colortool）、数学（mathtool）。

colortool 的颜色空间换算由 ``coloraide`` 完成（需 ``pip install fcmd[color]``），
本模块只保留参数校验与输出格式。
"""

from __future__ import annotations

import ast
import math
import operator as op
import re
from typing import Any

from fcmd.dsl.actions import action, run_and_print

__all__: list[str] = []


# ======================================================================== #
# casetool — 命名风格转换
# ======================================================================== #
_WORD_PATTERN = re.compile(
    r"[^A-Za-z0-9]+"  # 非字母数字序列
    r"|(?<=[a-z0-9])(?=[A-Z])"  # 小写/数字→大写 边界
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # 大写(连续)→大写+小写 边界（HTTP|Server）
)


def _split_words(text: str) -> list[str]:
    """将输入字符串拆分为小写单词列表。"""
    if not text:
        return []
    parts = _WORD_PATTERN.split(text)
    return [p.lower() for p in parts if p]


def _to_snake(text: str) -> str:
    return "_".join(_split_words(text))


def _to_camel(text: str) -> str:
    words = _split_words(text)
    if not words:
        return ""
    return words[0] + "".join(w.capitalize() for w in words[1:])


def _to_pascal(text: str) -> str:
    return "".join(w.capitalize() for w in _split_words(text))


def _to_kebab(text: str) -> str:
    return "-".join(_split_words(text))


@action("casetool_snake", param_help={"text": "待转换的字符串"})
def _casetool_snake(text: str) -> None:
    """转换为 snake_case。"""
    print(_to_snake(text))


@action("casetool_camel", param_help={"text": "待转换的字符串"})
def _casetool_camel(text: str) -> None:
    """转换为 camelCase。"""
    print(_to_camel(text))


@action("casetool_pascal", param_help={"text": "待转换的字符串"})
def _casetool_pascal(text: str) -> None:
    """转换为 PascalCase。"""
    print(_to_pascal(text))


@action("casetool_kebab", param_help={"text": "待转换的字符串"})
def _casetool_kebab(text: str) -> None:
    """转换为 kebab-case。"""
    print(_to_kebab(text))


# ======================================================================== #
# colortool — 颜色换算（coloraide）
# ======================================================================== #
# 惰性导入的 coloraide.Color 类（首次颜色换算时填充）
_coloraide: Any = None


def _require_coloraide() -> Any | None:
    """惰性导入 ``coloraide.Color``；缺失时返回 ``None``（由调用方提示安装）。

    coloraide 的色空间注册表较大，``import fcmd`` 与 DSL 启动路径不应为其付费。
    """
    global _coloraide  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if _coloraide is not None:
        return _coloraide
    try:
        from coloraide import Color
    except ImportError:  # pragma: no cover - 由 extra 保证，缺库时给出安装提示
        return None
    _coloraide = Color
    return Color


def _color(*args: Any) -> Any:
    """构造 coloraide ``Color``；缺少依赖时抛 ``ValueError``。"""
    color_cls = _require_coloraide()
    if color_cls is None:
        raise ValueError("未安装 coloraide 库，请安装: pip install fcmd[color]")
    return color_cls(*args)


def _srgb_255(color: Any) -> tuple[int, int, int]:
    """取 sRGB 分量（0-1）并量化为 0-255 整数。"""
    r, g, b = color.convert("srgb").coords()[:3]
    return (round(r * 255), round(g * 255), round(b * 255))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """十六进制颜色转 RGB 分量（0-255）。"""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"HEX 颜色须为 6 位十六进制（#RRGGBB），收到: {hex_color!r}")
    try:
        int(s, 16)  # 仅用于校验字符集，交给 coloraide 解析会抛无上下文的错误
    except ValueError as exc:
        raise ValueError(f"HEX 颜色含非十六进制字符: {hex_color!r}") from exc
    return _srgb_255(_color(f"#{s}"))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """RGB 分量转 ``#RRGGBB``。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    return _color("srgb", [r / 255, g / 255, b / 255]).to_string(hex=True)


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """RGB 转 HSL（h: 0-360, s/l: 0-100）。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    h, s, light = _color("srgb", [r / 255, g / 255, b / 255]).convert("hsl").coords()[:3]
    # 灰阶（无彩色）的色相无定义，coloraide 返回 nan；归零保证输出可读
    hue = 0.0 if math.isnan(h) else h
    return (round(hue, 2), round(s * 100, 2), round(light * 100, 2))


def _hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    """HSL 转 RGB。"""
    if not 0 <= h <= 360:
        raise ValueError(f"h 须在 0-360 范围: {h}")
    if not 0 <= s <= 100:
        raise ValueError(f"s 须在 0-100 范围: {s}")
    if not 0 <= light <= 100:
        raise ValueError(f"l 须在 0-100 范围: {light}")
    return _srgb_255(_color("hsl", [h, s / 100, light / 100]))


def _print_result(result: tuple[float, ...] | str) -> None:
    """格式化打印转换结果：元组以空格拼接，字符串原样输出。"""
    if isinstance(result, tuple):
        print(" ".join(str(v) for v in result))
    else:
        print(result)


def _color_action(fn_name: str, *args: object) -> None:
    """颜色转换动作执行器，捕获 ValueError 并打印结果或错误。"""
    fn = globals()[fn_name]
    try:
        result = fn(*args)
    except ValueError as exc:
        from rich.markup import escape

        from fcmd.console import get_console  # 延迟导入

        # 错误文本可能含 ``[color]`` 等字样，必须转义后交给 markup 渲染
        get_console().print(f"[red]错误:[/red] {escape(str(exc))}")
        return
    _print_result(result)


@action("colortool_hex2rgb", param_help={"hex_color": "十六进制颜色（#RRGGBB）"})
def _colortool_hex2rgb(hex_color: str) -> None:
    """HEX 转 RGB。"""
    _color_action("_hex_to_rgb", hex_color)


@action("colortool_rgb2hex", param_help={"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"})
def _colortool_rgb2hex(r: int, g: int, b: int) -> None:
    """RGB 转 HEX。"""
    _color_action("_rgb_to_hex", r, g, b)


@action("colortool_rgb2hsl", param_help={"r": "红色分量（0-255）", "g": "绿色分量（0-255）", "b": "蓝色分量（0-255）"})
def _colortool_rgb2hsl(r: int, g: int, b: int) -> None:
    """RGB 转 HSL。"""
    _color_action("_rgb_to_hsl", r, g, b)


@action("colortool_hsl2rgb", param_help={"h": "色相（0-360）", "s": "饱和度（0-100）", "light": "亮度（0-100）"})
def _colortool_hsl2rgb(h: float, s: float, light: float) -> None:
    """HSL 转 RGB。"""
    _color_action("_hsl_to_rgb", h, s, light)


# ======================================================================== #
# mathtool — 数学计算
# ======================================================================== #
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


def _eval_expr(expr: str) -> float | int:
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


@action("mathtool_eval", param_help={"expr": "数学表达式字符串（如 1 + 2 * 3）"})
def _mathtool_eval(expr: str) -> None:
    """安全求值数学表达式。"""
    run_and_print(_eval_expr, expr, exc=(ValueError, ZeroDivisionError))


@action("mathtool_sqrt", param_help={"x": "待开方的数（须非负）"})
def _mathtool_sqrt(x: float) -> None:
    """计算平方根。"""

    def _sqrt(xv: float) -> float:
        if xv < 0:
            raise ValueError(f"sqrt 要求非负数，当前: {xv}")
        return math.sqrt(xv)

    run_and_print(_sqrt, x, exc=(ValueError, ZeroDivisionError))


@action("mathtool_pow", param_help={"base": "底数", "exp": "指数"})
def _mathtool_pow(base: float, exp: float) -> None:
    """计算 base ** exp。"""
    run_and_print(lambda b, e: b**e, base, exp, exc=(ValueError, ZeroDivisionError))


@action("mathtool_factorial", param_help={"n": "非负整数"})
def _mathtool_factorial(n: int) -> None:
    """计算 n!。"""

    def _factorial(nv: int) -> int:
        if not isinstance(nv, int) or isinstance(nv, bool):
            raise ValueError(f"factorial 要求非负整数，当前类型: {type(nv).__name__}")
        if nv < 0:
            raise ValueError(f"factorial 要求非负整数，当前: {nv}")
        return math.factorial(nv)

    run_and_print(_factorial, n, exc=(ValueError, ZeroDivisionError))
