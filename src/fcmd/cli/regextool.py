"""regextool - 正则表达式工具。

基于标准库 ``re`` 提供正则匹配、查找、替换与分割。

示例
----
    fcmd regextool match "\\d+" "123abc"             # 匹配开头的数字
    fcmd regextool find "\\d+" "a1b22c333"            # 查找所有数字串
    fcmd regextool replace "\\d+" "#" "a1b22"         # 数字替换为 #
    fcmd regextool split "," "a,b,c"                  # 按逗号分割
"""

from __future__ import annotations

import re

import fcmd

__all__ = [
    "find_all",
    "match_pattern",
    "replace_pattern",
    "split_pattern",
]


# ============================================================================
# 公共函数
# ============================================================================


def match_pattern(pattern: str, text: str) -> dict[str, str] | None:
    """在文本开头匹配正则表达式。

    Parameters
    ----------
    pattern:
        正则表达式字符串
    text:
        待匹配的文本

    Returns
    -------
    dict[str, str] | None
        匹配成功返回包含以下键的字典：
        - ``match``: 匹配到的完整字符串
        - ``start``: 起始位置
        - ``end``: 结束位置
        - ``groups``: 捕获组（逗号分隔，无组时为空串）
        匹配失败返回 ``None``

    Raises
    ------
    ValueError
        正则表达式无效时
    """
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
    """查找文本中所有非重叠匹配。

    Parameters
    ----------
    pattern:
        正则表达式字符串
    text:
        待查找的文本

    Returns
    -------
    list[str]
        所有匹配字符串列表（无组时为完整匹配；有组时为组元组转字符串）

    Raises
    ------
    ValueError
        正则表达式无效时
    """
    try:
        matches = re.findall(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc
    return [str(m) if not isinstance(m, str) else m for m in matches]


def replace_pattern(pattern: str, replacement: str, text: str) -> str:
    """替换文本中所有匹配。

    Parameters
    ----------
    pattern:
        正则表达式字符串
    replacement:
        替换字符串（支持 ``\\1``/``\\g<name>`` 反向引用）
    text:
        待替换的文本

    Returns
    -------
    str
        替换后的文本

    Raises
    ------
    ValueError
        正则表达式无效时
    """
    try:
        return re.sub(pattern, replacement, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc


def split_pattern(pattern: str, text: str) -> list[str]:
    """按正则分割文本。

    Parameters
    ----------
    pattern:
        正则表达式字符串（分隔符模式）
    text:
        待分割的文本

    Returns
    -------
    list[str]
        分割后的片段列表

    Raises
    ------
    ValueError
        正则表达式无效时
    """
    try:
        return re.split(pattern, text)
    except re.error as exc:
        raise ValueError(f"无效的正则表达式: {pattern!r}（{exc}）") from exc


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("regextool", subcommand="match", help="匹配正则表达式")
def match_cmd(pattern: str, text: str) -> None:
    """在文本开头匹配正则表达式并打印结果。

    Parameters
    ----------
    pattern:
        正则表达式字符串
    text:
        待匹配的文本
    """
    try:
        result = match_pattern(pattern, text)
    except ValueError as exc:
        print(str(exc))
        return
    if result is None:
        print("未匹配")
        return
    for key, value in result.items():
        print(f"{key}: {value}")


@fcmd.tool("regextool", subcommand="find", help="查找所有匹配")
def find_cmd(pattern: str, text: str) -> None:
    """查找文本中所有匹配并逐行打印。

    Parameters
    ----------
    pattern:
        正则表达式字符串
    text:
        待查找的文本
    """
    try:
        matches = find_all(pattern, text)
    except ValueError as exc:
        print(str(exc))
        return
    if not matches:
        print("未匹配")
        return
    for m in matches:
        print(m)


@fcmd.tool("regextool", subcommand="replace", help="替换匹配")
def replace_cmd(pattern: str, replacement: str, text: str) -> None:
    """替换文本中所有匹配并打印结果。

    Parameters
    ----------
    pattern:
        正则表达式字符串
    replacement:
        替换字符串（支持 ``\\1``/``\\g<name>`` 反向引用）
    text:
        待替换的文本
    """
    try:
        print(replace_pattern(pattern, replacement, text))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("regextool", subcommand="split", help="按正则分割")
def split_cmd(pattern: str, text: str) -> None:
    """按正则分割文本并逐行打印片段。

    Parameters
    ----------
    pattern:
        正则表达式字符串（分隔符模式）
    text:
        待分割的文本
    """
    try:
        parts = split_pattern(pattern, text)
    except ValueError as exc:
        print(str(exc))
        return
    for part in parts:
        print(part)


def main() -> None:
    """``regextool`` 入口：等价于 ``fcmd regextool <args>``。"""
    from fcmd.cli._common import run_tool_main

    run_tool_main("regextool")


if __name__ == "__main__":
    main()
