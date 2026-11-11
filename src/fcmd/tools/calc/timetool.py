"""timetool 工具：当前时间、解析、Unix 时间戳与时区转换。

计算逻辑在 :mod:`fcmd.infras.calc`，本模块只做参数适配与输出。
"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import calc


@fx.tool(
    "timetool",
    subcommand="now",
    help="显示当前时间",
    param_help={
        "utc": "使用 UTC 时间（默认 False=本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_now(utc: bool = False, format: str = calc._DEFAULT_TIME_FORMAT) -> None:
    """显示当前时间（now）子命令。"""
    dt = calc.now_utc() if utc else calc.now_local()
    print(calc.format_time(dt, format))


@fx.tool(
    "timetool",
    subcommand="parse",
    help="解析时间字符串",
    param_help={
        "time": "时间字符串",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_parse(time: str, format: str = calc._DEFAULT_TIME_FORMAT) -> None:
    """解析时间字符串（parse）子命令。"""
    try:
        dt = calc.parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(calc.format_time(dt, format))


@fx.tool(
    "timetool",
    subcommand="unix",
    help="转 Unix 时间戳",
    param_help={
        "time": "时间字符串（视为本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_unix(time: str, format: str = calc._DEFAULT_TIME_FORMAT) -> None:
    """转 Unix 时间戳（unix）子命令。"""
    try:
        dt = calc.parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(calc.to_unix(dt))


@fx.tool(
    "timetool",
    subcommand="fromunix",
    help="Unix 时间戳转时间",
    param_help={
        "ts": "Unix 时间戳（秒）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_fromunix(ts: int, format: str = calc._DEFAULT_TIME_FORMAT) -> None:
    """Unix 时间戳转时间（fromunix）子命令。"""
    dt = calc.from_unix(ts)
    print(calc.format_time(dt, format))


@fx.tool(
    "timetool",
    subcommand="convert",
    help="时区转换",
    param_help={
        "time": "时间字符串",
        "target_tz": "目标时区名（如 Asia/Shanghai / UTC）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
        "from_tz": "源时区名（空串=视为本地时间）",
    },
)
def timetool_convert(
    time: str,
    target_tz: str,
    format: str = calc._DEFAULT_TIME_FORMAT,
    from_tz: str = "",
) -> None:
    """时区转换（convert）子命令。"""
    try:
        dt = calc.parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    if from_tz:
        try:
            src_tz = calc.resolve_tz(from_tz)
        except ValueError as exc:
            print(f"错误: {exc}")
            return
        dt = dt.replace(tzinfo=src_tz)
    try:
        converted = calc.convert_timezone(dt, target_tz)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(calc.format_time(converted, format))
