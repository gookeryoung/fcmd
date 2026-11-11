"""casetool —— 命名风格转换（snake/camel/pascal/kebab）。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "casetool",
    subcommand="snake",
    help="转 snake_case",
    param_help={"text": "待转换的字符串"},
)
def snake(text: str) -> None:
    """转换为 snake_case。"""
    print(textinfra.to_snake(text))


@fx.tool(
    "casetool",
    subcommand="camel",
    help="转 camelCase",
    param_help={"text": "待转换的字符串"},
)
def camel(text: str) -> None:
    """转换为 camelCase。"""
    print(textinfra.to_camel(text))


@fx.tool(
    "casetool",
    subcommand="pascal",
    help="转 PascalCase",
    param_help={"text": "待转换的字符串"},
)
def pascal(text: str) -> None:
    """转换为 PascalCase。"""
    print(textinfra.to_pascal(text))


@fx.tool(
    "casetool",
    subcommand="kebab",
    help="转 kebab-case",
    param_help={"text": "待转换的字符串"},
)
def kebab(text: str) -> None:
    """转换为 kebab-case。"""
    print(textinfra.to_kebab(text))
