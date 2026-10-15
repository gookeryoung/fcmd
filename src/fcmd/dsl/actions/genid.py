"""ID 生成动作：UUID/时间戳/随机字符串（idtool）。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from fcmd.dsl.actions import action

__all__: list[str] = []


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
    from fcmd.dsl.actions.calc import generate_string  # 延迟导入：复用 randtool 原语

    try:
        print(generate_string(length))
    except ValueError as exc:
        from fcmd.console import get_console  # 延迟导入

        get_console().print(f"[red]错误:[/red] {exc}")
