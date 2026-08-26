"""txttool - 文本处理工具。

对文本文件执行常见处理：统计、排序、去重、大小写转换。

示例
----
    fcmd txttool count README.md              # 统计行/词/字符数
    fcmd txttool sort list.txt --reverse       # 逆序排序
    fcmd txttool unique dup.txt                # 去重行
    fcmd txttool case name.txt --mode title    # 标题大小写
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import fcmd
from fcmd.console import get_console

__all__ = [
    "convert_case",
    "count_text",
    "sort_lines",
    "unique_lines",
]

# 支持的大小写转换模式
_CASE_MODES: dict[str, Callable[[str], str]] = {
    "upper": str.upper,
    "lower": str.lower,
    "title": str.title,
    "capitalize": str.capitalize,
    "swapcase": str.swapcase,
}


# ============================================================================
# 公共函数
# ============================================================================


def count_text(text: str) -> dict[str, int]:
    """统计文本的行数、单词数、字符数。

    Parameters
    ----------
    text:
        待统计的文本

    Returns
    -------
    dict[str, int]
        包含 ``lines``/``words``/``chars`` 三个键的字典
    """
    return {
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "chars": len(text),
    }


def sort_lines(text: str, reverse: bool = False) -> str:
    """对文本行排序。

    Parameters
    ----------
    text:
        待排序的文本
    reverse:
        是否逆序排序（默认 ``False``）

    Returns
    -------
    str
        排序后的文本（行间以 ``\\n`` 连接）
    """
    return "\n".join(sorted(text.splitlines(), reverse=reverse))


def unique_lines(text: str) -> str:
    """去重文本行（保持首次出现的顺序）。

    Parameters
    ----------
    text:
        待去重的文本

    Returns
    -------
    str
        去重后的文本（行间以 ``\\n`` 连接）
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        if line not in seen:
            seen.add(line)
            result.append(line)
    return "\n".join(result)


def convert_case(text: str, mode: str = "upper") -> str:
    """转换文本大小写。

    Parameters
    ----------
    text:
        待转换的文本
    mode:
        转换模式（``upper``/``lower``/``title``/``capitalize``/``swapcase``，默认 ``upper``）

    Returns
    -------
    str
        转换后的文本

    Raises
    ------
    ValueError
        ``mode`` 不在支持列表中时
    """
    if mode not in _CASE_MODES:
        raise ValueError(f"不支持的模式: {mode}，支持: {', '.join(_CASE_MODES)}")
    return _CASE_MODES[mode](text)


# ============================================================================
# CLI 子命令
# ============================================================================


def _read_text(path: str) -> str | None:
    """读取文件文本，文件不存在时打印提示并返回 ``None``。"""
    file_path = Path(path)
    if not file_path.is_file():
        print(f"文件不存在: {file_path}")
        return None
    return file_path.read_text(encoding="utf-8")


@fcmd.tool("txttool", subcommand="count", help="统计行/词/字符数")
def txt_count_cmd(path: str) -> None:
    """统计文本文件的行数、单词数、字符数。

    Parameters
    ----------
    path:
        目标文件路径
    """
    text = _read_text(path)
    if text is None:
        return
    stats = count_text(text)
    print(f"行数: {stats['lines']}")
    print(f"词数: {stats['words']}")
    print(f"字符数: {stats['chars']}")


@fcmd.tool("txttool", subcommand="sort", help="排序文本行")
def txt_sort_cmd(path: str, reverse: bool = False) -> None:
    """对文本文件的行排序。

    Parameters
    ----------
    path:
        目标文件路径
    reverse:
        是否逆序排序（默认 ``False``）
    """
    text = _read_text(path)
    if text is None:
        return
    print(sort_lines(text, reverse=reverse))


@fcmd.tool("txttool", subcommand="unique", help="去重文本行")
def txt_unique_cmd(path: str) -> None:
    """去重文本文件的行（保持首次出现的顺序）。

    Parameters
    ----------
    path:
        目标文件路径
    """
    text = _read_text(path)
    if text is None:
        return
    print(unique_lines(text))


@fcmd.tool("txttool", subcommand="case", help="大小写转换")
def txt_case_cmd(path: str, mode: str = "upper") -> None:
    """转换文本文件的大小写。

    Parameters
    ----------
    path:
        目标文件路径
    mode:
        转换模式（``upper``/``lower``/``title``/``capitalize``/``swapcase``，默认 ``upper``）
    """
    text = _read_text(path)
    if text is None:
        return
    try:
        print(convert_case(text, mode))
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")


@fcmd.main("txttool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
