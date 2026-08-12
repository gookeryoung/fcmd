"""asciitool - ASCII 表工具。

提供字符与 ASCII 码互转，以及 ASCII 表打印。

示例
----
    fcmd asciitool char A                        # 查询字符的 ASCII 码
    fcmd asciitool code 65                       # 查询 ASCII 码对应的字符
    fcmd asciitool table                         # 打印可打印 ASCII 表
    fcmd asciitool table --start 48 --end 57     # 打印数字字符表
"""

from __future__ import annotations

import fcmd

__all__ = [
    "build_ascii_table",
    "char_to_code",
    "code_to_char",
]

# 可打印 ASCII 字符范围（0x20..0x7E，共 95 个）
_PRINTABLE_START: int = 0x20
_PRINTABLE_END: int = 0x7E


# ============================================================================
# 公共函数
# ============================================================================


def char_to_code(char: str) -> int:
    """查询单字符的 ASCII 码。

    Parameters
    ----------
    char:
        单个字符（多字符时报错）

    Returns
    -------
    int
        ASCII 码值

    Raises
    ------
    ValueError
        输入不是单个字符时
    """
    if len(char) != 1:
        raise ValueError(f"char 要求单个字符，当前长度: {len(char)}（{char!r}）")
    return ord(char)


def code_to_char(code: int) -> str:
    """查询 ASCII 码对应的字符。

    Parameters
    ----------
    code:
        ASCII 码值（0..0x10FFFF）

    Returns
    -------
    str
        对应字符

    Raises
    ------
    ValueError
        码值超出合法范围时
    """
    if not isinstance(code, int) or isinstance(code, bool):
        raise ValueError(f"code 要求整数，当前类型: {type(code).__name__}")
    if code < 0 or code > 0x10FFFF:
        raise ValueError(f"code 超出合法范围 [0, 1114111]，当前: {code}")
    return chr(code)


def build_ascii_table(start: int = _PRINTABLE_START, end: int = _PRINTABLE_END) -> list[dict[str, str]]:
    """构建 ASCII 表条目列表。

    Parameters
    ----------
    start:
        起始码值（默认 ``0x20``）
    end:
        结束码值（默认 ``0x7E``，包含）

    Returns
    -------
    list[dict[str, str]]
        每项包含 ``code``（十进制）、``hex``（十六进制）、``char``（字符）三个键

    Raises
    ------
    ValueError
        范围无效（``start > end`` 或超出 ``[0, 0x10FFFF]``）时
    """
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
            "char": chr(c) if c != 0x7F else "\\x7f",  # DEL 字符显示为转义
        }
        for c in range(start, end + 1)
    ]


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("asciitool", subcommand="char", help="查询字符的 ASCII 码")
def char_cmd(char: str) -> None:
    """查询单字符的 ASCII 码并打印。

    Parameters
    ----------
    char:
        单个字符
    """
    try:
        code = char_to_code(char)
    except ValueError as exc:
        print(str(exc))
        return
    print(f"char: {char}")
    print(f"code: {code}")
    print(f"hex: 0x{code:02X}")


@fcmd.tool("asciitool", subcommand="code", help="查询 ASCII 码对应的字符")
def code_cmd(code: int) -> None:
    """查询 ASCII 码对应的字符并打印。

    Parameters
    ----------
    code:
        ASCII 码值
    """
    try:
        char = code_to_char(code)
    except ValueError as exc:
        print(str(exc))
        return
    print(f"code: {code}")
    print(f"hex: 0x{code:02X}")
    print(f"char: {char}")


@fcmd.tool("asciitool", subcommand="table", help="打印 ASCII 表")
def table_cmd(start: int = _PRINTABLE_START, end: int = _PRINTABLE_END) -> None:
    """打印 ASCII 表（每行一条目）。

    Parameters
    ----------
    start:
        起始码值（默认 ``32``）
    end:
        结束码值（默认 ``126``，包含）
    """
    try:
        entries = build_ascii_table(start, end)
    except ValueError as exc:
        print(str(exc))
        return
    for entry in entries:
        print(f"{entry['code']:>3}  {entry['hex']}  {entry['char']}")


@fcmd.main("asciitool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
