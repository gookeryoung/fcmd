"""idtool 工具：UUID / 时间戳 / 随机字符串。

生成逻辑在 :mod:`fcmd.infras.ids`，本模块只做参数适配与输出。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import ids


@fx.tool(
    "idtool",
    subcommand="uuid",
    help="生成 UUID",
    param_help={"version": "UUID 版本（1 或 4，默认 4）"},
)
def idtool_uuid(version: int = 4) -> None:
    """生成 UUID 字符串。"""
    try:
        print(ids.make_uuid(version))
    except ValueError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "idtool",
    subcommand="timestamp",
    help="生成时间戳",
    param_help={"fmt": "时间戳格式（iso 或 unix，默认 iso）"},
)
def idtool_timestamp(fmt: str = "iso") -> None:
    """生成当前时间戳。"""
    try:
        print(ids.make_timestamp(fmt))
    except ValueError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "idtool",
    subcommand="random",
    help="生成随机字符串",
    param_help={"length": "随机字符串长度（默认 16）"},
)
def idtool_random(length: int = 16) -> None:
    """生成随机字母数字字符串。"""
    try:
        print(ids.random_string(length))
    except ValueError as exc:
        print(f"错误: {exc}")
