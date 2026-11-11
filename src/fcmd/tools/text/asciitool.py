"""asciitool —— ASCII 码与字符互查、ASCII 表打印。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "asciitool",
    subcommand="char",
    help="查询字符的 ASCII 码",
    param_help={"char": "单个字符（多字符时报错）"},
)
def char(char: str) -> None:
    """查询字符的 ASCII 码并打印。"""
    try:
        code = textinfra.char_to_code(char)
    except ValueError as error:
        print(f"错误: {error}")
        return
    print(f"char: {char}")
    print(f"code: {code}")
    print(f"hex: 0x{code:02X}")


@fx.tool(
    "asciitool",
    subcommand="code",
    help="查询 ASCII 码对应的字符",
    param_help={"code": "ASCII 码值（0..0x10FFFF）"},
)
def code(code: int) -> None:
    """查询 ASCII 码对应的字符并打印。"""
    try:
        char_value = textinfra.code_to_char(code)
    except ValueError as error:
        print(f"错误: {error}")
        return
    print(f"code: {code}")
    print(f"hex: 0x{code:02X}")
    print(f"char: {char_value}")


@fx.tool(
    "asciitool",
    subcommand="table",
    help="打印 ASCII 表",
    param_help={
        "start": "起始码值（默认 32）",
        "end": "结束码值（默认 126，包含）",
    },
)
def table(start: int = textinfra.PRINTABLE_START, end: int = textinfra.PRINTABLE_END) -> None:
    """打印 ASCII 表（每行一条目）。"""
    try:
        entries = textinfra.build_ascii_table(start, end)
    except ValueError as error:
        print(f"错误: {error}")
        return
    for entry in entries:
        print(f"{entry['code']:>3}  {entry['hex']}  {entry['char']}")
