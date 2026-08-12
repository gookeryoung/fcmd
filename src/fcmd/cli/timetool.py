"""timetool - 时间工具。

基于标准库 ``datetime`` 提供时间获取、格式化、Unix 时间戳转换与时区转换。
Python 3.9+ 自动启用 ``zoneinfo`` 支持命名时区；3.8 仅支持 UTC 与本地时区。

示例
----
    fcmd timetool now                              # 当前本地时间
    fcmd timetool now --utc                        # 当前 UTC 时间
    fcmd timetool now --format "%Y-%m-%d %H:%M"   # 自定义格式
    fcmd timetool parse "2026-07-29 12:30:00"     # 解析时间
    fcmd timetool unix "2026-07-29T12:30:00"      # 转 Unix 时间戳
    fcmd timetool fromunix 1753785000             # Unix 时间戳转时间
    fcmd timetool convert "12:30:00" --to Asia/Shanghai --format "%H:%M:%S"
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

import fcmd
from fcmd.console import get_console

__all__ = [
    "convert_timezone",
    "format_time",
    "from_unix",
    "now_local",
    "now_utc",
    "parse_time",
    "to_unix",
]

# 默认时间格式
_DEFAULT_FORMAT = "%Y-%m-%d %H:%M:%S"

# zoneinfo 可用性：Python 3.9+ 标准库自带，3.8 需 backports.zoneinfo
if sys.version_info >= (3, 9):  # pragma: no cover (测试环境单版本)
    from zoneinfo import ZoneInfo

    def _resolve_tz(name: str) -> Any:
        """3.9+ 通过 zoneinfo 解析时区名；UTC 走捷径避免 zoneinfo 数据查询。"""
        if name.upper() == "UTC":
            return timezone.utc
        try:
            return ZoneInfo(name)
        except KeyError as exc:
            # ZoneInfoNotFoundError 是 KeyError 子类；统一转 ValueError 便于 CLI 捕获
            raise ValueError(f"无效或不可用的时区: {name}") from exc

else:  # pragma: no cover (测试环境单版本)

    def _resolve_tz(name: str) -> Any:
        """3.8 不支持命名时区，仅接受 ``UTC``。"""
        if name.upper() == "UTC":
            return timezone.utc
        raise ValueError(f"无效或不可用的时区（命名时区需要 Python 3.9+ 或 backports.zoneinfo）: {name}")


# ============================================================================
# 公共函数
# ============================================================================


def now_utc() -> datetime:
    """返回当前 UTC 时间（带 tzinfo）。"""
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """返回当前本地时间（无 tzinfo）。"""
    return datetime.now()


def parse_time(time_str: str, fmt: str = _DEFAULT_FORMAT) -> datetime:
    """按格式解析时间字符串。

    Parameters
    ----------
    time_str:
        时间字符串
    fmt:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）

    Returns
    -------
    datetime.datetime
        解析后的 datetime 对象

    Raises
    ------
    ValueError
        格式不匹配时
    """
    return datetime.strptime(time_str, fmt)


def format_time(dt: datetime, fmt: str = _DEFAULT_FORMAT) -> str:
    """格式化 datetime 为字符串。

    Parameters
    ----------
    dt:
        待格式化的 datetime
    fmt:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）

    Returns
    -------
    str
        格式化后的时间字符串
    """
    return dt.strftime(fmt)


def to_unix(dt: datetime) -> int:
    """将 datetime 转为 Unix 时间戳（秒）。

    Parameters
    ----------
    dt:
        待转换的 datetime（naive datetime 视为本地时间）

    Returns
    -------
    int
        Unix 时间戳（秒）
    """
    return int(dt.timestamp())


def from_unix(ts: int) -> datetime:
    """将 Unix 时间戳转为本地 datetime。

    Parameters
    ----------
    ts:
        Unix 时间戳（秒）

    Returns
    -------
    datetime.datetime
        本地 naive datetime
    """
    return datetime.fromtimestamp(ts)


def convert_timezone(dt: datetime, target_tz: str) -> datetime:
    """将 datetime 转换到目标时区。

    naive datetime 视为本地时间；aware datetime 保留其时刻，仅切换时区显示。

    Parameters
    ----------
    dt:
        待转换的 datetime
    target_tz:
        目标时区名（如 ``Asia/Shanghai``/``UTC``/``America/New_York``）

    Returns
    -------
    datetime.datetime
        目标时区的 aware datetime

    Raises
    ------
    ValueError
        时区名无效或不支持时
    """
    tz = _resolve_tz(target_tz)
    if dt.tzinfo is None:
        # naive datetime 视为本地时间，先附加本地时区
        dt = dt.astimezone()
    return dt.astimezone(tz)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("timetool", subcommand="now", help="显示当前时间")
def time_now_cmd(utc: bool = False, format: str = _DEFAULT_FORMAT) -> None:
    """显示当前时间。

    Parameters
    ----------
    utc:
        使用 UTC 时间（默认 ``False``，使用本地时间）
    format:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）
    """
    dt = now_utc() if utc else now_local()
    print(format_time(dt, format))


@fcmd.tool("timetool", subcommand="parse", help="解析时间字符串")
def time_parse_cmd(time: str, format: str = _DEFAULT_FORMAT) -> None:
    """解析时间字符串并打印。

    Parameters
    ----------
    time:
        时间字符串
    format:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）
    """
    try:
        dt = parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    # 输出 ISO 8601 与用户格式
    print(format_time(dt, format))


@fcmd.tool("timetool", subcommand="unix", help="转 Unix 时间戳")
def time_unix_cmd(time: str, format: str = _DEFAULT_FORMAT) -> None:
    """将时间字符串转为 Unix 时间戳。

    Parameters
    ----------
    time:
        时间字符串（视为本地时间）
    format:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）
    """
    try:
        dt = parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(to_unix(dt))


@fcmd.tool("timetool", subcommand="fromunix", help="Unix 时间戳转时间")
def time_fromunix_cmd(ts: int, format: str = _DEFAULT_FORMAT) -> None:
    """将 Unix 时间戳转为本地时间字符串。

    Parameters
    ----------
    ts:
        Unix 时间戳（秒）
    format:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）
    """
    dt = from_unix(ts)
    print(format_time(dt, format))


@fcmd.tool("timetool", subcommand="convert", help="时区转换")
def time_convert_cmd(
    time: str,
    target_tz: str,
    format: str = _DEFAULT_FORMAT,
    from_tz: str = "",
) -> None:
    """将时间字符串从源时区转换到目标时区。

    用法：``fcmd timetool convert <time> <target-tz> [--from-tz ...]``

    Parameters
    ----------
    time:
        时间字符串
    target_tz:
        目标时区名（如 ``Asia/Shanghai``/``UTC``）
    format:
        ``strftime`` 格式（默认 ``%Y-%m-%d %H:%M:%S``）
    from_tz:
        源时区名（默认空串，表示视为本地时间）
    """
    try:
        dt = parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    if from_tz:
        try:
            src_tz = _resolve_tz(from_tz)
        except ValueError as exc:
            get_console().print(f"[red]错误:[/red] {exc}")
            return
        dt = dt.replace(tzinfo=src_tz)
    try:
        converted = convert_timezone(dt, target_tz)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    print(format_time(converted, format))


@fcmd.main("timetool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
