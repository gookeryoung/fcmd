"""casetool - 命名风格转换工具。

将输入字符串识别为单词序列（支持空格、下划线、连字符、驼峰边界），
再转换为目标命名风格。

支持的输出风格：
- ``snake``: snake_case（如 ``hello_world``）
- ``camel``: camelCase（如 ``helloWorld``）
- ``pascal``: PascalCase（如 ``HelloWorld``）
- ``kebab``: kebab-case（如 ``hello-world``）

示例
----
    fcmd casetool snake "helloWorld"           # hello_world
    fcmd casetool camel "hello world"           # helloWorld
    fcmd casetool pascal "hello-world"          # HelloWorld
    fcmd casetool kebab "HelloWorld"            # hello-world
    fcmd casetool snake "HTTPServer"            # http_server
"""

from __future__ import annotations

import re

import fcmd

__all__ = [
    "to_camel",
    "to_kebab",
    "to_pascal",
    "to_snake",
]

# 单词拆分正则：匹配
# 1. 非字母数字字符序列（作为分隔符）
# 2. 小写或数字后跟大写字母（如 helloWorld / hello2World 的边界）
# 3. 连续大写字母后跟大写+小写（如 HTTPServer 的 S 边界）
_WORD_PATTERN = re.compile(
    r"[^A-Za-z0-9]+"  # 非字母数字序列
    r"|(?<=[a-z0-9])(?=[A-Z])"  # 小写/数字->大写 边界
    r"|(?<=[A-Z])(?=[A-Z][a-z])"  # 大写(连续)->大写+小写 边界（HTTP|Server）
)


def _split_words(text: str) -> list[str]:
    """将输入字符串拆分为单词列表。

    识别以下边界：
    - 空格、下划线、连字符等非字母数字字符
    - 小写到大写的转换处（``helloWorld`` → ``hello``/``World``）
    - 连续大写到大小写组合的边界（``HTTPServer`` → ``HTTP``/``Server``）

    Parameters
    ----------
    text:
        待拆分的字符串

    Returns
    -------
    list[str]
        小写单词列表（空输入或全分隔符时返回空列表）
    """
    if not text:
        return []
    parts = _WORD_PATTERN.split(text)
    return [p.lower() for p in parts if p]


def to_snake(text: str) -> str:
    """转换为 snake_case。

    Parameters
    ----------
    text:
        待转换的字符串

    Returns
    -------
    str
        snake_case 格式字符串
    """
    words = _split_words(text)
    return "_".join(words)


def to_camel(text: str) -> str:
    """转换为 camelCase。

    Parameters
    ----------
    text:
        待转换的字符串

    Returns
    -------
    str
        camelCase 格式字符串
    """
    words = _split_words(text)
    if not words:
        return ""
    # 第一个单词全小写，后续单词首字母大写
    return words[0] + "".join(w.capitalize() for w in words[1:])


def to_pascal(text: str) -> str:
    """转换为 PascalCase。

    Parameters
    ----------
    text:
        待转换的字符串

    Returns
    -------
    str
        PascalCase 格式字符串
    """
    words = _split_words(text)
    return "".join(w.capitalize() for w in words)


def to_kebab(text: str) -> str:
    """转换为 kebab-case。

    Parameters
    ----------
    text:
        待转换的字符串

    Returns
    -------
    str
        kebab-case 格式字符串
    """
    words = _split_words(text)
    return "-".join(words)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("casetool", subcommand="snake", help="转 snake_case")
def snake_cmd(text: str) -> None:
    """转换为 snake_case。

    Parameters
    ----------
    text:
        待转换的字符串
    """
    print(to_snake(text))


@fcmd.tool("casetool", subcommand="camel", help="转 camelCase")
def camel_cmd(text: str) -> None:
    """转换为 camelCase。

    Parameters
    ----------
    text:
        待转换的字符串
    """
    print(to_camel(text))


@fcmd.tool("casetool", subcommand="pascal", help="转 PascalCase")
def pascal_cmd(text: str) -> None:
    """转换为 PascalCase。

    Parameters
    ----------
    text:
        待转换的字符串
    """
    print(to_pascal(text))


@fcmd.tool("casetool", subcommand="kebab", help="转 kebab-case")
def kebab_cmd(text: str) -> None:
    """转换为 kebab-case。

    Parameters
    ----------
    text:
        待转换的字符串
    """
    print(to_kebab(text))


@fcmd.main("casetool")
def main() -> None:
    pass
