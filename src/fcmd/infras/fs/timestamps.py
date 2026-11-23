"""文件时间戳解析与展示（``filetime`` 工具复用）。

与 :mod:`fcmd.infras.fs.date` 的区别：本模块只处理文件系统元数据（mtime /
atime / ctime），不重命名文件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

__all__ = ["TimestampError", "file_stamps", "format_epoch", "parse_timestamp"]

#: Unix 秒前缀；与 ISO 8601 的纯数字年份区分，避免 ``2025`` 被当成 1970 年
_EPOCH_PREFIX = "@"


class TimestampError(ValueError):
    """时间戳文本无法解析（消息可直接展示给用户）。"""


def parse_timestamp(value: str) -> float:
    """解析时间戳文本，返回 Unix 秒。

    接受两种写法：

    * ``@<unix>``：Unix 秒（可带小数），如 ``@1735787045``
    * ISO 8601：``YYYY-MM-DD``、``YYYY-MM-DD HH:MM[:SS]``，可带 ``Z`` 或时区偏移

    无时区的 ISO 文本按本机时区解释，与终端 ``dir`` / ``ls`` 的显示一致。
    """
    text = value.strip()
    if not text:
        raise TimestampError("时间戳不能为空")
    if text.startswith(_EPOCH_PREFIX):
        raw = text[len(_EPOCH_PREFIX) :]
        try:
            return float(raw)
        except ValueError as exc:
            raise TimestampError(f"不是合法的 Unix 秒: {raw or '(空)'}") from exc
    normalised = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise TimestampError(f"无法解析为 ISO 8601 时间: {text}") from exc
    return moment.timestamp()


def format_epoch(epoch: float) -> dict[str, str | int]:
    """把 Unix 秒展开为整数秒、本地时间与 UTC 文本。"""
    local = datetime.fromtimestamp(epoch).astimezone()
    utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return {
        "unix": int(epoch),
        "local": local.strftime("%Y-%m-%d %H:%M:%S"),
        "utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def file_stamps(path: Path) -> dict[str, dict[str, str | int]]:
    """读取文件的 mtime / atime / ctime（Windows 上 ctime 为创建时间）。"""
    stat = path.stat()
    return {
        "mtime": format_epoch(stat.st_mtime),
        "atime": format_epoch(stat.st_atime),
        "ctime": format_epoch(stat.st_ctime),
    }
