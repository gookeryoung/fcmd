"""``fcmd.infras.text.ascii``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

__all__ = ["PRINTABLE_END", "PRINTABLE_START", "build_ascii_table", "char_to_code", "code_to_char"]


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
