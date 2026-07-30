"""idtool - ID 生成工具。

生成常见 ID：UUID（v1/v4）、时间戳（ISO/Unix）、随机字符串。

示例
----
    fcmd idtool uuid                           # 生成 UUID v4
    fcmd idtool uuid --version 1               # 生成 UUID v1
    fcmd idtool timestamp                      # 生成 ISO 时间戳
    fcmd idtool timestamp --fmt unix           # 生成 Unix 时间戳
    fcmd idtool random                         # 生成 16 位随机字符串
    fcmd idtool random --length 32             # 生成 32 位随机字符串
"""

from __future__ import annotations

import secrets
import string
import time
import uuid
from datetime import datetime

import fcmd

__all__ = [
    "generate_random_string",
    "generate_timestamp",
    "generate_uuid",
]

# 随机字符串字符集（字母 + 数字）
_RANDOM_CHARS: str = string.ascii_letters + string.digits


# ============================================================================
# 公共函数
# ============================================================================


def generate_uuid(version: int = 4) -> str:
    """生成 UUID 字符串。

    Parameters
    ----------
    version:
        UUID 版本（``1`` 或 ``4``，默认 ``4``）

    Returns
    -------
    str
        UUID 字符串（36 字符，含 4 个连字符）

    Raises
    ------
    ValueError
        ``version`` 不在支持列表中时
    """
    if version == 1:
        return str(uuid.uuid1())
    if version == 4:
        return str(uuid.uuid4())
    raise ValueError(f"不支持的 UUID 版本: {version}，支持: 1, 4")


def generate_timestamp(fmt: str = "iso") -> str:
    """生成当前时间戳。

    Parameters
    ----------
    fmt:
        时间戳格式（``iso`` 或 ``unix``，默认 ``iso``）

    Returns
    -------
    str
        ISO 8601 格式时间戳或 Unix 时间戳字符串

    Raises
    ------
    ValueError
        ``fmt`` 不在支持列表中时
    """
    if fmt == "iso":
        return datetime.now().isoformat()
    if fmt == "unix":
        return str(int(time.time()))
    raise ValueError(f"不支持的格式: {fmt}，支持: iso, unix")


def generate_random_string(length: int = 16) -> str:
    """生成随机字母数字字符串。

    使用 ``secrets.choice`` 保证密码学安全随机性。

    Parameters
    ----------
    length:
        字符串长度（默认 ``16``，必须大于 0）

    Returns
    -------
    str
        随机字母数字字符串

    Raises
    ------
    ValueError
        ``length`` 小于等于 0 时
    """
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    return "".join(secrets.choice(_RANDOM_CHARS) for _ in range(length))


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("idtool", subcommand="uuid", help="生成 UUID")
def uuid_cmd(version: int = 4) -> None:
    """生成 UUID 字符串。

    Parameters
    ----------
    version:
        UUID 版本（``1`` 或 ``4``，默认 ``4``）
    """
    try:
        print(generate_uuid(version))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("idtool", subcommand="timestamp", help="生成时间戳")
def timestamp_cmd(fmt: str = "iso") -> None:
    """生成当前时间戳。

    Parameters
    ----------
    fmt:
        时间戳格式（``iso`` 或 ``unix``，默认 ``iso``）
    """
    try:
        print(generate_timestamp(fmt))
    except ValueError as exc:
        print(str(exc))


@fcmd.tool("idtool", subcommand="random", help="生成随机字符串")
def random_cmd(length: int = 16) -> None:
    """生成随机字母数字字符串。

    Parameters
    ----------
    length:
        字符串长度（默认 ``16``，必须大于 0）
    """
    try:
        print(generate_random_string(length))
    except ValueError as exc:
        print(str(exc))


@fcmd.main("idtool")
def main() -> None:
    """``idtool`` 入口：等价于 ``fcmd idtool <args>``。"""


if __name__ == "__main__":
    main()
