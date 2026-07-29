"""padtool - 文本对齐工具。

提供四种文本对齐方式：左对齐、右对齐、居中、两端对齐。

示例
----
    fcmd padtool left "hello" --width 10            # 左对齐
    fcmd padtool right "hello" --width 10            # 右对齐
    fcmd padtool center "hello" --width 10           # 居中
    fcmd padtool justify "the quick brown" --width 20  # 两端对齐
"""

from __future__ import annotations

import fcmd

__all__ = [
    "align_center",
    "align_justify",
    "align_left",
    "align_right",
]


# ============================================================================
# 公共函数
# ============================================================================


def align_left(text: str, width: int) -> str:
    """左对齐文本（右侧填充空格）。

    Parameters
    ----------
    text:
        待对齐的文本
    width:
        对齐宽度（必须非负）

    Returns
    -------
    str
        对齐后的字符串（长度 ``max(width, len(text))``）

    Raises
    ------
    ValueError
        ``width`` 为负数时
    """
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.ljust(width)


def align_right(text: str, width: int) -> str:
    """右对齐文本（左侧填充空格）。

    Parameters
    ----------
    text:
        待对齐的文本
    width:
        对齐宽度（必须非负）

    Returns
    -------
    str
        对齐后的字符串

    Raises
    ------
    ValueError
        ``width`` 为负数时
    """
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.rjust(width)


def align_center(text: str, width: int) -> str:
    """居中对齐文本（两侧填充空格）。

    Parameters
    ----------
    text:
        待对齐的文本
    width:
        对齐宽度（必须非负）

    Returns
    -------
    str
        对齐后的字符串

    Raises
    ------
    ValueError
        ``width`` 为负数时
    """
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    return text.center(width)


def align_justify(text: str, width: int) -> str:
    """两端对齐文本（多行段落，最后一行左对齐）。

    对输入文本按行处理：每个非末行通过在词间插入额外空格使长度达到 ``width``。
    末行（段落最后一行）保持左对齐。空行原样保留。

    Parameters
    ----------
    text:
        待对齐的文本（可多行）
    width:
        对齐宽度（必须非负）

    Returns
    -------
    str
        对齐后的文本

    Raises
    ------
    ValueError
        ``width`` 为负数时
    """
    if width < 0:
        raise ValueError(f"width 要求非负数，当前: {width}")
    lines = text.splitlines()
    if not lines:
        # 空输入视为单个空行，左对齐到目标宽度
        return "".ljust(width)
    result: list[str] = []
    multi_line = len(lines) > 1
    for i, line in enumerate(lines):
        # 仅多行输入的末行保持左对齐；单行输入应被两端对齐
        is_last = multi_line and i == len(lines) - 1
        result.append(_justify_line(line, width, is_last))
    return "\n".join(result)


def _justify_line(line: str, width: int, is_last: bool) -> str:
    """对单行执行两端对齐。

    末行或单行不进行两端对齐，仅左对齐。行内单词数 <= 1 时也无法两端对齐。
    """
    # 空行或末行：左对齐
    if not line.strip() or is_last:
        return line.ljust(width)
    words = line.split()
    # 单词数 <= 1：左对齐
    if len(words) <= 1:
        return line.ljust(width)
    # 计算需要填充的总空格数
    total_chars = sum(len(w) for w in words)
    total_gaps = len(words) - 1
    total_spaces = width - total_chars
    # 宽度不足以容纳原文 + 单空格分隔，原样返回（左对齐）
    if total_spaces < total_gaps:
        return line.ljust(width)
    # 每个间隙的基础空格数 + 额外分配（左侧优先）
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
# CLI 子命令
# ============================================================================


@fcmd.tool("padtool", subcommand="left", help="左对齐文本")
def left_cmd(text: str, width: int = 20) -> None:
    """左对齐文本并打印。

    Parameters
    ----------
    text:
        待对齐的文本
    width:
        对齐宽度（默认 ``20``）
    """
    try:
        print(align_left(text, width))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("padtool", subcommand="right", help="右对齐文本")
def right_cmd(text: str, width: int = 20) -> None:
    """右对齐文本并打印。

    Parameters
    ----------
    text:
        待对齐的文本
    width:
        对齐宽度（默认 ``20``）
    """
    try:
        print(align_right(text, width))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("padtool", subcommand="center", help="居中对齐文本")
def center_cmd(text: str, width: int = 20) -> None:
    """居中对齐文本并打印。

    Parameters
    ----------
    text:
        待对齐的文本
    width:
        对齐宽度（默认 ``20``）
    """
    try:
        print(align_center(text, width))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("padtool", subcommand="justify", help="两端对齐文本")
def justify_cmd(text: str, width: int = 20) -> None:
    """两端对齐文本并打印。

    Parameters
    ----------
    text:
        待对齐的文本（可多行）
    width:
        对齐宽度（默认 ``20``）
    """
    try:
        print(align_justify(text, width))
    except ValueError as exc:
        print(str(exc))


def main() -> None:
    """``padtool`` 入口：等价于 ``fcmd padtool <args>``。"""
    from fcmd.cli._common import run_tool_main

    run_tool_main("padtool")


if __name__ == "__main__":
    main()
