"""ID 生成动作：UUID/时间戳/随机字符串（idtool）。"""

from __future__ import annotations

import secrets
import string
import time
import uuid
from datetime import datetime

from fcmd.dsl.actions import action

__all__: list[str] = []

_RANDOM_CHARS: str = string.ascii_letters + string.digits


def _make_uuid(version: int) -> str:
    """生成 UUID 字符串。"""
    if version == 1:
        return str(uuid.uuid1())
    if version == 4:
        return str(uuid.uuid4())
    raise ValueError(f"不支持的 UUID 版本: {version}，支持: 1, 4")


def _make_timestamp(fmt: str) -> str:
    """生成当前时间戳。"""
    if fmt == "iso":
        return datetime.now().isoformat()
    if fmt == "unix":
        return str(int(time.time()))
    raise ValueError(f"不支持的格式: {fmt}，支持: iso, unix")


def _make_random(length: int) -> str:
    """生成密码学安全的随机字母数字字符串。"""
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    return "".join(secrets.choice(_RANDOM_CHARS) for _ in range(length))


@action("idtool_uuid", param_help={"version": "UUID 版本（1 或 4，默认 4）"})
def _idtool_uuid(version: int = 4) -> None:
    """生成 UUID 字符串。"""
    try:
        print(_make_uuid(version))
    except ValueError as exc:
        from fcmd.console import get_console  # 延迟导入

        get_console().print(f"[red]错误:[/red] {exc}")


@action("idtool_timestamp", param_help={"fmt": "时间戳格式（iso 或 unix，默认 iso）"})
def _idtool_timestamp(fmt: str = "iso") -> None:
    """生成当前时间戳。"""
    try:
        print(_make_timestamp(fmt))
    except ValueError as exc:
        from fcmd.console import get_console  # 延迟导入

        get_console().print(f"[red]错误:[/red] {exc}")


@action("idtool_random", param_help={"length": "随机字符串长度（默认 16）"})
def _idtool_random(length: int = 16) -> None:
    """生成随机字母数字字符串。"""
    try:
        print(_make_random(length))
    except ValueError as exc:
        from fcmd.console import get_console  # 延迟导入

        get_console().print(f"[red]错误:[/red] {exc}")
