"""padtool —— 文本左/右/居中/两端对齐。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "padtool",
    subcommand="left",
    help="左对齐文本",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def left(text: str, width: int = 20) -> None:
    """左对齐文本并打印。"""
    try:
        print(textinfra.align_left(text, width))
    except ValueError as error:
        print(f"错误: {error}")


@fx.tool(
    "padtool",
    subcommand="right",
    help="右对齐文本",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def right(text: str, width: int = 20) -> None:
    """右对齐文本并打印。"""
    try:
        print(textinfra.align_right(text, width))
    except ValueError as error:
        print(f"错误: {error}")


@fx.tool(
    "padtool",
    subcommand="center",
    help="居中对齐文本",
    param_help={
        "text": "待对齐的文本",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def center(text: str, width: int = 20) -> None:
    """居中对齐文本并打印。"""
    try:
        print(textinfra.align_center(text, width))
    except ValueError as error:
        print(f"错误: {error}")


@fx.tool(
    "padtool",
    subcommand="justify",
    help="两端对齐文本",
    param_help={
        "text": "待对齐的文本（可多行）",
        "width": "对齐宽度（默认 20，非负）",
    },
)
def justify(text: str, width: int = 20) -> None:
    """两端对齐文本并打印。"""
    try:
        print(textinfra.align_justify(text, width))
    except ValueError as error:
        print(f"错误: {error}")
