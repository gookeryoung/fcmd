"""文本工具域纯函数：ASCII / 对齐 / 正则 / 文本统计 / diff / 命名风格 / 颜色 / 数学。

本模块只做计算，不打印、不解析 CLI、不依赖 ``argparse``。``fcmd.tools.text``
下的工具模块负责参数适配与输出，错误提示由调用方决定如何呈现。

颜色换算依赖可选的 ``coloraide``（``pip install fcmd[color]``），首次换算时
才惰性导入；缺失时抛 ``ValueError`` 由工具层提示安装。
"""

from __future__ import annotations

import ast
import difflib
import filecmp
import math
import operator as op
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fcmd.infras.textio import read_text_lines

__all__ = [
    "PRINTABLE_END",
    "PRINTABLE_START",
    "align_center",
    "align_justify",
    "align_left",
    "align_right",
    "build_ascii_table",
    "char_to_code",
    "code_to_char",
    "colorize_diff",
    "compare_directories",
    "compare_directories_result",
    "compare_files",
    "compare_files_result",
    "convert_case",
    "count_text",
    "diff_stats",
    "eval_expr",
    "factorial_value",
    "find_all",
    "format_directory_result",
    "hex_to_rgb",
    "hsl_to_rgb",
    "match_pattern",
    "power",
    "read_lines",
    "read_text_file",
    "replace_pattern",
    "rgb_to_hex",
    "rgb_to_hsl",
    "sort_lines",
    "split_pattern",
    "sqrt_value",
    "to_camel",
    "to_kebab",
    "to_pascal",
    "to_snake",
    "unique_lines",
]

# ============================================================================
# asciitool
# ============================================================================

# 可打印 ASCII 字符范围（0x20..0x7E，共 95 个）
PRINTABLE_START: int = 0x20
PRINTABLE_END: int = 0x7E


def char_to_code(char: str) -> int:
    """查询单字符的 ASCII 码。"""
    if len(char) != 1:
        raise ValueError(f"char 要求单个字符，当前长度: {len(char)}（{char!r}）")
    return ord(char)


def code_to_char(code: int) -> str:
    """查询 ASCII 码对应的字符。"""
    if not isinstance(code, int) or isinstance(code, bool):
        raise ValueError(f"code 要求整数，当前类型: {type(code).__name__}")
    if code < 0 or code > 0x10FFFF:
        raise ValueError(f"code 超出合法范围 [0, 1114111]，当前: {code}")
    return chr(code)


def build_ascii_table(start: int = PRINTABLE_START, end: int = PRINTABLE_END) -> list[dict[str, str]]:
    """构建 ASCII 表条目列表。"""
    if not isinstance(start, int) or isinstance(start, bool):
        raise ValueError(f"start 要求整数，当前类型: {type(start).__name__}")
    if not isinstance(end, int) or isinstance(end, bool):
        raise ValueError(f"end 要求整数，当前类型: {type(end).__name__}")
    if start < 0 or end > 0x10FFFF:
        raise ValueError(f"范围超出 [0, 1114111]，当前: [{start}, {end}]")
    if start > end:
        raise ValueError(f"start 不能大于 end，当前: start={start}, end={end}")
    return [
        {
            "code": str(c),
            "hex": f"0x{c:02X}",
            "char": chr(c) if c != 0x7F else "\\x7f",
        }
        for c in range(start, end + 1)
    ]


# ============================================================================
# padtool
# ============================================================================


def align_left(text: str, width: int) -> str:
    """左对齐文本（右侧填充空格）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.ljust(width)


def align_right(text: str, width: int) -> str:
    """右对齐文本（左侧填充空格）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.rjust(width)


def align_center(text: str, width: int) -> str:
    """居中对齐文本（两侧填充空格）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.center(width)


def align_justify(text: str, width: int) -> str:
    """两端对齐文本（多行段落，最后一行左对齐）。"""
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    lines = text.splitlines()
    if not lines:
        return "".ljust(width)
    result: list[str] = []
    multi_line = len(lines) > 1
    for i, line in enumerate(lines):
        is_last = multi_line and i == len(lines) - 1
        result.append(_justify_line(line, width, is_last))
    return "\n".join(result)


def _justify_line(line: str, width: int, is_last: bool) -> str:
    """对单行执行两端对齐（内部 helper）。"""
    if not line.strip() or is_last:
        return line.ljust(width)
    words = line.split()
    if len(words) <= 1:
        return line.ljust(width)
    total_chars = sum(len(w) for w in words)
    total_gaps = len(words) - 1
    total_spaces = width - total_chars
    if total_spaces < total_gaps:
        return line.ljust(width)
    base = total_spaces // total_gaps
    extra = total_spaces % total_gaps
    parts: list[str] = []
    for j, word in enumerate(words):
        parts.append(word)
        if j < total_gaps:
            spaces = base + (1 if j < extra else 0)
            parts.append(" " * spaces)
    return "".join(parts)


# ============================================================================
# regextool
# ============================================================================


def match_pattern(pattern: str, text: str) -> dict[str, str] | None:
    """在文本开头匹配正则表达式。"""
    try:
        m = re.match(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc
    if m is None:
        return None
    groups = ",".join(g if g is not None else "" for g in m.groups())
    return {
        "match": m.group(0),
        "start": str(m.start()),
        "end": str(m.end()),
        "groups": groups,
    }


def find_all(pattern: str, text: str) -> list[str]:
    """查找文本中所有非重叠匹配。"""
    try:
        matches = re.findall(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc
    return [str(m) if not isinstance(m, str) else m for m in matches]


def replace_pattern(pattern: str, replacement: str, text: str) -> str:
    """替换文本中所有匹配。"""
    try:
        return re.sub(pattern, replacement, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc


def split_pattern(pattern: str, text: str) -> list[str]:
    """按正则分割文本。"""
    try:
        return re.split(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc


# ============================================================================
# txttool
# ============================================================================

# 支持的大小写转换模式
_CASE_MODES: dict[str, Callable[[str], str]] = {
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
    "capitalize": str.capitalize,
    "swapcase": str.swapcase,
}


def read_text_file(path: str | Path) -> str:
    """读取文本文件内容（utf-8）。"""
    return Path(path).read_text(encoding="utf-8")


def count_text(text: str) -> dict[str, int]:
    """统计文本的行数、单词数、字符数。"""
    return {
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "chars": len(text),
    }


def sort_lines(text: str, reverse: bool = False) -> str:
    """对文本行排序。"""
    return "\n".join(sorted(text.splitlines(), reverse=reverse))


def unique_lines(text: str) -> str:
    """去重文本行（保持首次出现的顺序）。"""
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        if line not in seen:
            seen.add(line)
            result.append(line)
    return "\n".join(result)


def convert_case(text: str, mode: str = "upper") -> str:
    """转换文本大小写。"""
    if mode not in _CASE_MODES:
        raise ValueError(f"不支持的模式: {mode}，支持: {', '.join(_CASE_MODES)}")
    return _CASE_MODES[mode](text)


# ============================================================================
# textdiff
# ============================================================================

# ANSI 颜色码
_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def read_lines(filepath: Path) -> list[str]:
    """读取文本文件行列表（内部 helper，复用中立文本 IO）。"""
    return read_text_lines(filepath, binary_message="二进制文件不支持比较")


def colorize_diff(diff_text: str) -> str:
    """为 unified diff 文本添加 ANSI 颜色。"""
    lines = diff_text.splitlines(keepends=True)
    result: list[str] = []
    for line in lines:
        if line.startswith(("---", "+++")):
            result.append(line)
        elif line.startswith("-"):
            result.append(f"{_RED}{line}{_RESET}")
        elif line.startswith("+"):
            result.append(f"{_GREEN}{line}{_RESET}")
        elif line.startswith("@@"):
            result.append(f"{_CYAN}{line}{_RESET}")
        else:
            result.append(line)
    return "".join(result)


def compare_files(file1: Path, file2: Path, context: int = 3) -> str:
    """比较两个文本文件，返回 unified diff 字符串。"""
    lines1 = read_lines(file1)
    lines2 = read_lines(file2)
    diff = difflib.unified_diff(lines1, lines2, fromfile=str(file1), tofile=str(file2), n=context)
    return "".join(diff)


def diff_stats(diff_text: str) -> dict[str, int]:
    """统计 unified diff 的增删行数与 hunk 数（不改变 diff 原文）。"""
    added = 0
    removed = 0
    hunks = 0
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        elif line.startswith("@@"):
            hunks += 1
    return {"added": added, "removed": removed, "hunks": hunks}


def compare_files_result(file1: Path, file2: Path, context: int = 3) -> dict[str, Any]:
    """比较两个文本文件，返回结构化结果（diff 原文 + 增删统计）。"""
    diff = compare_files(file1, file2, context)
    return {"equal": not diff, "diff": diff, **diff_stats(diff)}


def compare_directories_result(
    dir1: Path,
    dir2: Path,
    pattern: str = "*",
    recursive: bool = True,
) -> dict[str, Any]:
    """比较两个目录，返回结构化差异（文本渲染与 JSON 输出共用）。"""
    if recursive:
        files1 = {p.relative_to(dir1).as_posix() for p in dir1.rglob(pattern) if p.is_file()}
        files2 = {p.relative_to(dir2).as_posix() for p in dir2.rglob(pattern) if p.is_file()}
    else:
        files1 = {p.name for p in dir1.glob(pattern) if p.is_file()}
        files2 = {p.name for p in dir2.glob(pattern) if p.is_file()}

    only_left = sorted(files1 - files2)
    only_right = sorted(files2 - files1)
    common = sorted(files1 & files2)

    diffs: list[str] = []
    errors: list[str] = []
    for rel in common:
        try:
            if not filecmp.cmp(dir1 / rel, dir2 / rel, shallow=False):
                diffs.append(rel)
        except OSError:
            errors.append(rel)

    return {
        "equal": not (only_left or only_right or diffs or errors),
        "only_left": only_left,
        "only_right": only_right,
        "different": diffs,
        "errors": errors,
    }


def format_directory_result(result: dict[str, Any], dir1: Path, dir2: Path) -> str:
    """把结构化目录差异渲染为人类可读文本（保持既有输出格式）。"""
    lines: list[str] = []
    if result["only_left"]:
        lines.append(f"仅在 {dir1}:")
        lines.extend(f"  {name}" for name in result["only_left"])
    if result["only_right"]:
        lines.append(f"仅在 {dir2}:")
        lines.extend(f"  {name}" for name in result["only_right"])
    if result["different"]:
        lines.append("内容不同:")
        lines.extend(f"  {name}" for name in result["different"])
    if result["errors"]:
        lines.append("无法比较:")
        lines.extend(f"  {name}" for name in result["errors"])
    if not lines:
        return "目录内容相同"
    return "\n".join(lines)


def compare_directories(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> str:
    """比较两个目录，返回差异报告字符串。"""
    result = compare_directories_result(dir1, dir2, pattern, recursive)
    return format_directory_result(result, dir1, dir2)


# ============================================================================
# casetool
# ============================================================================

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


def to_snake(text: str) -> str:
    """转换为 snake_case。"""
    return "_".join(_split_words(text))


def to_camel(text: str) -> str:
    """转换为 camelCase。"""
    words = _split_words(text)
    if not words:
        return ""
    return words[0] + "".join(w.capitalize() for w in words[1:])


def to_pascal(text: str) -> str:
    """转换为 PascalCase。"""
    return "".join(w.capitalize() for w in _split_words(text))


def to_kebab(text: str) -> str:
    """转换为 kebab-case。"""
    return "-".join(_split_words(text))


# ============================================================================
# colortool
# ============================================================================

# 惰性导入的 coloraide.Color 类（首次颜色换算时填充）
_coloraide: Any = None


def _require_coloraide() -> Any | None:
    """惰性导入 ``coloraide.Color``；缺失时返回 ``None``（由调用方提示安装）。

    coloraide 的色空间注册表较大，``import fcmd`` 与工具启动路径不应为其付费。
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


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """十六进制颜色转 RGB 分量（0-255）。"""
    s = hex_color.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"HEX 颜色须为 6 位十六进制（#RRGGBB），收到: {hex_color!r}")
    try:
        int(s, 16)  # 仅用于校验字符集，交给 coloraide 解析会抛无上下文的错误
    except ValueError as exc:
        raise ValueError(f"HEX 颜色含非十六进制字符: {hex_color!r}") from exc
    return _srgb_255(_color(f"#{s}"))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """RGB 分量转 ``#RRGGBB``。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    return _color("srgb", [r / 255, g / 255, b / 255]).to_string(hex=True)


def rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """RGB 转 HSL（h: 0-360, s/l: 0-100）。"""
    for name, val in (("r", r), ("g", g), ("b", b)):
        if not 0 <= val <= 255:
            raise ValueError(f"{name} 分量超出 0-255 范围: {val}")
    h, s, light = _color("srgb", [r / 255, g / 255, b / 255]).convert("hsl").coords()[:3]
    # 灰阶（无彩色）的色相无定义，coloraide 返回 nan；归零保证输出可读
    hue = 0.0 if math.isnan(h) else h
    return (round(hue, 2), round(s * 100, 2), round(light * 100, 2))


def hsl_to_rgb(h: float, s: float, light: float) -> tuple[int, int, int]:
    """HSL 转 RGB。"""
    if not 0 <= h <= 360:
        raise ValueError(f"h 须在 0-360 范围: {h}")
    if not 0 <= s <= 100:
        raise ValueError(f"s 须在 0-100 范围: {s}")
    if not 0 <= light <= 100:
        raise ValueError(f"l 须在 0-100 范围: {light}")
    return _srgb_255(_color("hsl", [h, s / 100, light / 100]))


# ============================================================================
# mathtool
# ============================================================================

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
