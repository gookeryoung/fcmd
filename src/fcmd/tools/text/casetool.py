"""casetool —— 命名风格转换（snake/camel/pascal/kebab）。"""

from __future__ import annotations

from collections.abc import Callable

import fcmd as fx
from fcmd.core.result import emit_result
from fcmd.infras import text as textinfra

__all__: list[str] = []


def _convert(subcommand: str, fn: Callable[[str], str], text: str, json_output: bool) -> None:
    """执行命名风格转换并输出。"""
    result = fn(text)
    if json_output:
        emit_result("casetool", subcommand, {"result": result})
        return
    print(result)


@fx.tool(
    "casetool",
    subcommand="snake",
    help="转 snake_case",
    param_help={"text": "待转换的字符串"},
)
def snake(text: str, json_output: bool = False) -> None:
    """转换为 snake_case。"""
    _convert("snake", textinfra.to_snake, text, json_output)


@fx.tool(
    "casetool",
    subcommand="camel",
    help="转 camelCase",
    param_help={"text": "待转换的字符串"},
)
def camel(text: str, json_output: bool = False) -> None:
    """转换为 camelCase。"""
    _convert("camel", textinfra.to_camel, text, json_output)


@fx.tool(
    "casetool",
    subcommand="pascal",
    help="转 PascalCase",
    param_help={"text": "待转换的字符串"},
)
def pascal(text: str, json_output: bool = False) -> None:
    """转换为 PascalCase。"""
    _convert("pascal", textinfra.to_pascal, text, json_output)


@fx.tool(
    "casetool",
    subcommand="kebab",
    help="转 kebab-case",
    param_help={"text": "待转换的字符串"},
)
def kebab(text: str, json_output: bool = False) -> None:
    """转换为 kebab-case。"""
    _convert("kebab", textinfra.to_kebab, text, json_output)
