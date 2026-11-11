"""ID 生成基础件：UUID、时间戳与随机字符串（纯函数）。

由 ``fcmd.tools.crypto.idtool`` 做参数适配与输出。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from fcmd.infras import calc

__all__ = ["make_timestamp", "make_uuid", "random_string"]


def make_uuid(version: int) -> str:
    """生成 UUID 字符串（支持版本 1 / 4）。"""
    if version == 1:
        return str(uuid.uuid1())
    if version == 4:
        return str(uuid.uuid4())
    raise ValueError(f"不支持的 UUID 版本: {version}，支持: 1, 4")


def make_timestamp(fmt: str) -> str:
    """生成当前时间戳（``iso`` 或 ``unix``）。"""
    if fmt == "iso":
        return datetime.now().isoformat()
    if fmt == "unix":
        return str(int(time.time()))
    raise ValueError(f"不支持的格式: {fmt}，支持: iso, unix")


def random_string(length: int = 16) -> str:
    """生成随机字母数字字符串。"""
    return calc.generate_string(length)
