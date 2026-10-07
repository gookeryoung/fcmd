"""文本类 DSL 动作：asciitool / padtool / regextool / txttool / textdiff。

五个工具原属 ``fcmd.cli.text`` 包，全部为纯 Python 实现（标准库
re/difflib/filecmp/pathlib），无子进程调用。迁移至本模块后由
``@action`` 装饰器注册，TOML 声明 ``action = "<名>"`` 直接引用；CLI
参数 schema 从动作签名自动推导（:mod:`fcmd.dsl.synth` 层拷贝）。

公共函数（无下划线前缀）保持原模块对外 API 不变，供测试 import：
char_to_code / code_to_char / build_ascii_table（asciitool）、align_left /
align_right / align_center / align_justify（padtool）、match_pattern /
find_all / replace_pattern / split_pattern（regextool）、count_text /
sort_lines / unique_lines / convert_case（txttool）、colorize_diff /
compare_files / compare_directories（textdiff）。
"""

from __future__ import annotations

import difflib
import filecmp
import re
from collections.abc import Callable
from pathlib import Path

from ..actions import action

# ============================================================================
# asciitool 常量
# ============================================================================

# 可打印 ASCII 字符范围（0x20..0x7E，共 95 个）
_PRINTABLE_START: int = 0x20
_PRINTABLE_END: int = 0x7E


# ============================================================================
# asciitool 公共函数
# ============================================================================


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


def build_ascii_table(start: int = _PRINTABLE_START, end: int = _PRINTABLE_END) -> list[dict[str, str]]:
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
# asciitool DSL 动作
# ============================================================================


@action(
    "asciitool_char",
    param_help={"char": "单个字符（多字符时报错）"},
)
def asciitool_char(char: str) -> None:
    """查询字符的 ASCII 码并打印。"""
    try:
        code = char_to_code(char)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(f"char: {char}")
    print(f"code: {code}")
    print(f"hex: 0x{code:02X}")


@action(
    "asciitool_code",
    param_help={"code": "ASCII 码值（0..0x10FFFF）"},
)
def asciitool_code(code: int) -> None:
    """查询 ASCII 码对应的字符并打印。"""
    try:
        char = code_to_char(code)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(f"code: {code}")
    print(f"hex: 0x{code:02X}")
    print(f"char: {char}")


@action(
    "asciitool_table",
    param_help={
        "start": "起始码值（默认 32）",
        "end": "结束码值（默认 126，包含）",
    },
)
def asciitool_table(start: int = _PRINTABLE_START, end: int = _PRINTABLE_END) -> None:
    """打印 ASCII 表（每行一条目）。"""
    try:
        entries = build_ascii_table(start, end)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    for entry in entries:
        print(f"{entry['code']:>3}  {entry['hex']}  {entry['char']}")


# ============================================================================
# padtool 公共函数
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
# padtool DSL 动作
# ============================================================================


@action(
    "padtool_left",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def padtool_left(text: str, width: int = 20) -> None:
    """左对齐文本并打印。"""
    try:
        print(align_left(text, width))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "padtool_right",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def padtool_right(text: str, width: int = 20) -> None:
    """右对齐文本并打印。"""
    try:
        print(align_right(text, width))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "padtool_center",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def padtool_center(text: str, width: int = 20) -> None:
    """居中对齐文本并打印。"""
    try:
        print(align_center(text, width))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "padtool_justify",
    param_help={
        "text": "待对齐的文本（可多行）",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def padtool_justify(text: str, width: int = 20) -> None:
    """两端对齐文本并打印。"""
    try:
        print(align_justify(text, width))
    except ValueError as exc:
        print(f"错误: {exc}")


# ============================================================================
# regextool 公共函数
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
# regextool DSL 动作
# ============================================================================


@action(
    "regextool_match",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待匹配的文本",
    },
)
def regextool_match(pattern: str, text: str) -> None:
    """在文本开头匹配正则表达式并打印结果。"""
    try:
        result = match_pattern(pattern, text)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    if result is None:
        print("未匹配")
        return
    for key, value in result.items():
        print(f"{key}: {value}")


@action(
    "regextool_find",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待查找的文本",
    },
)
def regextool_find(pattern: str, text: str) -> None:
    """查找文本中所有匹配并逐行打印。"""
    try:
        matches = find_all(pattern, text)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    if not matches:
        print("未匹配")
        return
    for m in matches:
        print(m)


@action(
    "regextool_replace",
    param_help={
        "pattern": "正则表达式字符串",
        "replacement": "替换字符串（支持反向引用）",
        "text": "待替换的文本",
    },
)
def regextool_replace(pattern: str, replacement: str, text: str) -> None:
    """替换文本中所有匹配并打印结果。"""
    try:
        print(replace_pattern(pattern, replacement, text))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "regextool_split",
    param_help={
        "pattern": "正则表达式字符串（分隔符模式）",
        "text": "待分割的文本",
    },
)
def regextool_split(pattern: str, text: str) -> None:
    """按正则分割文本并逐行打印片段。"""
    try:
        parts = split_pattern(pattern, text)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    for part in parts:
        print(part)


# ============================================================================
# txttool 常量与公共函数
# ============================================================================

# 支持的大小写转换模式
_CASE_MODES: dict[str, Callable[[str], str]] = {
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
    "capitalize": str.capitalize,
    "swapcase": str.swapcase,
}


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


def _read_text_file(path: str) -> str | None:
    """读取文件文本（内部 helper），文件不存在时打印提示并返回 None。"""
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return None
    return file_path.read_text(encoding="utf-8")


# ============================================================================
# txttool DSL 动作
# ============================================================================


@action(
    "txttool_count",
    param_help={"path": "目标文件路径"},
)
def txttool_count(path: str) -> None:
    """统计文本文件的行数、单词数、字符数。"""
    text = _read_text_file(path)
    if text is None:
        return
    stats = count_text(text)
    print(f"行数: {stats['lines']}")
    print(f"词数: {stats['words']}")
    print(f"字符数: {stats['chars']}")


@action(
    "txttool_sort",
    param_help={
        "path": "目标文件路径",
        "reverse": "是否逆序排序（默认 False）",
    },
)
def txttool_sort(path: str, reverse: bool = False) -> None:
    """对文本文件的行排序。"""
    text = _read_text_file(path)
    if text is None:
        return
    print(sort_lines(text, reverse=reverse))


@action(
    "txttool_unique",
    param_help={"path": "目标文件路径"},
)
def txttool_unique(path: str) -> None:
    """去重文本文件的行（保持首次出现的顺序）。"""
    text = _read_text_file(path)
    if text is None:
        return
    print(unique_lines(text))


@action(
    "txttool_case",
    param_help={
        "path": "目标文件路径",
        "mode": "转换模式（upper/lower/title/capitalize/swapcase，默认 upper）",
    },
)
def txttool_case(path: str, mode: str = "upper") -> None:
    """转换文本文件的大小写。"""
    text = _read_text_file(path)
    if text is None:
        return
    try:
        print(convert_case(text, mode))
    except ValueError as exc:
        print(f"错误: {exc}")


# ============================================================================
# textdiff 公共函数
# ============================================================================

# ANSI 颜色码
_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def _read_lines(filepath: Path) -> list[str]:
    """读取文本文件行列表（内部 helper）。"""
    with filepath.open("rb") as f:
        if b"\x00" in f.read(1024):
            raise ValueError(f"二进制文件不支持比较: {filepath}")
    try:
        with filepath.open("r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with filepath.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()


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
    lines1 = _read_lines(file1)
    lines2 = _read_lines(file2)
    diff = difflib.unified_diff(lines1, lines2, fromfile=str(file1), tofile=str(file2), n=context)
    return "".join(diff)


def compare_directories(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> str:
    """比较两个目录，返回差异报告字符串。"""
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

    lines: list[str] = []
    if only_left:
        lines.append(f"仅在 {dir1}:")
        lines.extend(f"  {name}" for name in only_left)
    if only_right:
        lines.append(f"仅在 {dir2}:")
        lines.extend(f"  {name}" for name in only_right)
    if diffs:
        lines.append("内容不同:")
        lines.extend(f"  {name}" for name in diffs)
    if errors:
        lines.append("无法比较:")
        lines.extend(f"  {name}" for name in errors)
    if not lines:
        return "目录内容相同"
    return "\n".join(lines)


# ============================================================================
# textdiff DSL 动作
# ============================================================================


@action(
    "textdiff_file",
    param_help={
        "file1": "待比较文件路径",
        "file2": "待比较文件路径",
        "context": "上下文行数（默认 3）",
        "color": "启用 ANSI 彩色输出（默认关闭）",
    },
)
def textdiff_file(file1: Path, file2: Path, context: int = 3, color: bool = False) -> None:
    """输出两个文本文件的 unified diff。"""
    if not file1.exists():
        print(f"文件不存在: {file1}")
        return
    if not file2.exists():
        print(f"文件不存在: {file2}")
        return
    try:
        result = compare_files(file1, file2, context)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    if not result:
        print("文件内容相同")
        return
    if color:
        result = colorize_diff(result)
    print(result, end="")


@action(
    "textdiff_dir",
    param_help={
        "dir1": "待比较目录路径",
        "dir2": "待比较目录路径",
        "pattern": "文件名 glob 模式（默认所有文件）",
        "recursive": "是否递归比较子目录（默认 True）",
    },
)
def textdiff_dir(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> None:
    """列出两个目录中差异文件。"""
    if not dir1.is_dir():
        print(f"目录不存在: {dir1}")
        return
    if not dir2.is_dir():
        print(f"目录不存在: {dir2}")
        return
    print(compare_directories(dir1, dir2, pattern, recursive))
