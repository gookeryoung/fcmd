"""转换与计算动作：命名风格（casetool）、颜色换算（colortool）、数学（mathtool）。"""

from __future__ import annotations

import ast
import math
import operator as op
import re
from collections.abc import Callable

from fcmd.dsl.actions import action

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
# colortool — 颜色换算
# ======================================================================== #
def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """十六进制颜色转 RGB 分量（0-255）。"""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"HEX 颜色须为 6 位十六进制（#RRGGBB），收到: {hex_color!r}")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError as exc:
        raise ValueError(f"HEX 颜色含非十六进制字符: {hex_color!r}") from exc


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    """RGB 分量转 ``#RRGGBB``。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """RGB 转 HSL（h: 0-360, s/l: 0-100）。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    rn, gn, bn = r / 255, g / 255, b / 255
    cmax = max(rn, gn, bn)
    cmin = min(rn, gn, bn)
    delta = cmax - cmin
    light = (cmax + cmin) / 2
    if delta == 0:
        s = 0.0
    else:
        denom = 1 - abs(2 * light - 1)
        s = delta / denom if denom != 0 else 0.0
    if delta == 0:
        h = 0.0
    elif cmax == rn:
        h = 60 * (((gn - bn) / delta) % 6)
    elif cmax == gn:
        h = 60 * (((bn - rn) / delta) + 2)
    else:
        h = 60 * (((rn - gn) / delta) + 4)
    if h < 0:
        h += 360
    return (round(h, 2), round(s * 100, 2), round(light * 100, 2))


def _hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    """HSL 转 RGB。"""
    if not 0 <= h <= 360:
        raise ValueError(f"h 须在 0-360 范围: {h}")
    if not 0 <= s <= 100:
        raise ValueError(f"s 须在 0-100 范围: {s}")
    if not 0 <= light <= 100:
        raise ValueError(f"l 须在 0-100 范围: {light}")
    hn = h / 360
    sn = s / 100
    ln = light / 100
    c = (1 - abs(2 * ln - 1)) * sn
    x = c * (1 - abs((hn * 6) % 2 - 1))
    m = ln - c / 2
    if hn < 1 / 6:
        r1, g1, b1 = c, x, 0
    elif hn < 2 / 6:
        r1, g1, b1 = x, c, 0
    elif hn < 3 / 6:
        r1, g1, b1 = 0, c, x
    elif hn < 4 / 6:
        r1, g1, b1 = 0, x, c
    elif hn < 5 / 6:
        r1, g1, b1 = x, 0, c
    else:
        r1, g1, b1 = c, 0, x
    return (round((r1 + m) * 255), round((g1 + m) * 255), round((b1 + m) * 255))


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
        from fcmd.console import get_console  # 延迟导入

        get_console().print(f"[red]错误:[/red] {exc}")
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


def _math_action(fn: Callable[..., object], *args: object) -> None:
    """数学动作执行器，捕获异常并打印结果或错误。"""
    try:
        result = fn(*args)
    except (ValueError, ZeroDivisionError) as exc:
        from fcmd.console import get_console  # 延迟导入

        get_console().print(f"[red]错误:[/red] {exc}")
        return
    print(result)


@action("mathtool_eval", param_help={"expr": "数学表达式字符串（如 1 + 2 * 3）"})
def _mathtool_eval(expr: str) -> None:
    """安全求值数学表达式。"""
    _math_action(_eval_expr, expr)


@action("mathtool_sqrt", param_help={"x": "待开方的数（须非负）"})
def _mathtool_sqrt(x: float) -> None:
    """计算平方根。"""

    def _sqrt(xv: float) -> float:
        if xv < 0:
            raise ValueError(f"sqrt 要求非负数，当前: {xv}")
        return math.sqrt(xv)

    _math_action(_sqrt, x)


@action("mathtool_pow", param_help={"base": "底数", "exp": "指数"})
def _mathtool_pow(base: float, exp: float) -> None:
    """计算 base ** exp。"""
    _math_action(lambda b, e: b**e, base, exp)


@action("mathtool_factorial", param_help={"n": "非负整数"})
def _mathtool_factorial(n: int) -> None:
    """计算 n!。"""

    def _factorial(nv: int) -> int:
        if not isinstance(nv, int) or isinstance(nv, bool):
            raise ValueError(f"factorial 要求非负整数，当前类型: {type(nv).__name__}")
        if nv < 0:
            raise ValueError(f"factorial 要求非负整数，当前: {nv}")
        return math.factorial(nv)

    _math_action(_factorial, n)
