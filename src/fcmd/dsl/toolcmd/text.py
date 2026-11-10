"""文本类 DSL 动作：asciitool / padtool / regextool / txttool / textdiff。

五个工具的实现统一在 :mod:`fcmd.tools.text.textbase`；本模块只保留
``@action`` 声明与参数转接，避免 DSL 层出现第二套文本实现。TOML 声明
``action = "<名>"`` 直接引用，CLI 参数 schema 从动作签名自动推导
（:mod:`fcmd.dsl.synth` 层拷贝）。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.tools.text import textbase

from .. import action

# ============================================================================
# asciitool DSL 动作
# ============================================================================


@action(
    "asciitool_char",
    param_help={"char": "单个字符（多字符时报错）"},
    errors=(ValueError,),
)
def asciitool_char(char: str) -> None:
    """查询字符的 ASCII 码并打印。"""
    textbase.asciitool_char(char)


@action(
    "asciitool_code",
    param_help={"code": "ASCII 码值（0..0x10FFFF）"},
    errors=(ValueError,),
)
def asciitool_code(code: int) -> None:
    """查询 ASCII 码对应的字符并打印。"""
    textbase.asciitool_code(code)


@action(
    "asciitool_table",
    param_help={
        "start": "起始码值（默认 32）",
        "end": "结束码值（默认 126，包含）",
    },
    errors=(ValueError,),
)
def asciitool_table(
    start: int = textbase._PRINTABLE_START,
    end: int = textbase._PRINTABLE_END,
) -> None:
    """打印 ASCII 表（每行一条目）。"""
    textbase.asciitool_table(start, end)


# ============================================================================
# padtool DSL 动作
# ============================================================================


@action(
    "padtool_left",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
    errors=(ValueError,),
)
def padtool_left(text: str, width: int = 20) -> None:
    """左对齐文本并打印。"""
    textbase.padtool_left(text, width)


@action(
    "padtool_right",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
    errors=(ValueError,),
)
def padtool_right(text: str, width: int = 20) -> None:
    """右对齐文本并打印。"""
    textbase.padtool_right(text, width)


@action(
    "padtool_center",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
    errors=(ValueError,),
)
def padtool_center(text: str, width: int = 20) -> None:
    """居中对齐文本并打印。"""
    textbase.padtool_center(text, width)


@action(
    "padtool_justify",
    param_help={
        "text": "待对齐的文本（可多行）",
        "width": "对齐宽度（默认 20，非负）",
    },
    errors=(ValueError,),
)
def padtool_justify(text: str, width: int = 20) -> None:
    """两端对齐文本并打印。"""
    textbase.padtool_justify(text, width)


# ============================================================================
# regextool DSL 动作
# ============================================================================


@action(
    "regextool_match",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待匹配的文本",
    },
    errors=(ValueError,),
)
def regextool_match(pattern: str, text: str) -> None:
    """在文本开头匹配正则表达式并打印结果。"""
    textbase.regextool_match(pattern, text)


@action(
    "regextool_find",
    param_help={
        "pattern": "正则表达式字符串",
        "text": "待查找的文本",
    },
    errors=(ValueError,),
)
def regextool_find(pattern: str, text: str) -> None:
    """查找文本中所有匹配并逐行打印。"""
    textbase.regextool_find(pattern, text)


@action(
    "regextool_replace",
    param_help={
        "pattern": "正则表达式字符串",
        "replacement": "替换字符串（支持反向引用）",
        "text": "待替换的文本",
    },
    errors=(ValueError,),
)
def regextool_replace(pattern: str, replacement: str, text: str) -> None:
    """替换文本中所有匹配并打印结果。"""
    textbase.regextool_replace(pattern, replacement, text)


@action(
    "regextool_split",
    param_help={
        "pattern": "正则表达式字符串（分隔符模式）",
        "text": "待分割的文本",
    },
    errors=(ValueError,),
)
def regextool_split(pattern: str, text: str) -> None:
    """按正则分割文本并逐行打印片段。"""
    textbase.regextool_split(pattern, text)


# ============================================================================
# txttool DSL 动作
# ============================================================================


@action(
    "txttool_count",
    param_help={"path": "目标文件路径"},
)
def txttool_count(path: str) -> None:
    """统计文本文件的行数、单词数、字符数。"""
    textbase.txttool_count(path)


@action(
    "txttool_sort",
    param_help={
        "path": "目标文件路径",
        "reverse": "是否逆序排序（默认 False）",
    },
)
def txttool_sort(path: str, reverse: bool = False) -> None:
    """对文本文件的行排序。"""
    textbase.txttool_sort(path, reverse=reverse)


@action(
    "txttool_unique",
    param_help={"path": "目标文件路径"},
)
def txttool_unique(path: str) -> None:
    """去重文本文件的行（保持首次出现的顺序）。"""
    textbase.txttool_unique(path)


@action(
    "txttool_case",
    param_help={
        "path": "目标文件路径",
        "mode": "转换模式（upper/lower/title/capitalize/swapcase，默认 upper）",
    },
    errors=(ValueError,),
)
def txttool_case(path: str, mode: str = "upper") -> None:
    """转换文本文件的大小写。"""
    textbase.txttool_case(path, mode)


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
    textbase.textdiff_file(file1, file2, context, color)


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
    textbase.textdiff_dir(dir1, dir2, pattern, recursive)
