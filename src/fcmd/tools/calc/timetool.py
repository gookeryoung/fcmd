"""timetool 工具：当前时间、解析、Unix 时间戳与时区转换。

计算逻辑在 :mod:`fcmd.infras.calc`，本模块只做参数适配与输出。
"""

from __future__ import annotations

from datetime import datetime

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import calc


def _emit(subcommand: str, value: str | int, json_output: bool) -> None:
    """按输出模式呈现时间结果。"""
    if json_output:
        emit_result("timetool", subcommand, {"result": value})
        return
    print(value)


def _parse_or_report(time: str, format: str, subcommand: str, json_output: bool) -> datetime | None:
    """解析时间字符串；失败时呈现错误并返回 ``None``。"""
    try:
        return calc.parse_time(time, format)
    except ValueError as exc:
        report_error(
            "timetool",
            subcommand,
            code="INVALID_ARGUMENT",
            message=f"解析失败: {exc}",
            field="time",
            expected=format,
            hint="确认时间字符串与 --format 的格式一致",
            json_output=json_output,
        )
        return None


@fx.tool(
    "timetool",
    subcommand="now",
    help="显示当前时间",
    param_help={
        "utc": "使用 UTC 时间（默认 False=本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_now(utc: bool = False, format: str = calc._DEFAULT_TIME_FORMAT, json_output: bool = False) -> None:
    """显示当前时间（now）子命令。"""
    dt = calc.now_utc() if utc else calc.now_local()
    _emit("now", calc.format_time(dt, format), json_output)


@fx.tool(
    "timetool",
    subcommand="parse",
    help="解析时间字符串",
    param_help={
        "time": "时间字符串",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_parse(time: str, format: str = calc._DEFAULT_TIME_FORMAT, json_output: bool = False) -> None:
    """解析时间字符串（parse）子命令。"""
    dt = _parse_or_report(time, format, "parse", json_output)
    if dt is None:
        return
    _emit("parse", calc.format_time(dt, format), json_output)


@fx.tool(
    "timetool",
    subcommand="unix",
    help="转 Unix 时间戳",
    param_help={
        "time": "时间字符串（视为本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_unix(time: str, format: str = calc._DEFAULT_TIME_FORMAT, json_output: bool = False) -> None:
    """转 Unix 时间戳（unix）子命令。"""
    dt = _parse_or_report(time, format, "unix", json_output)
    if dt is None:
        return
    _emit("unix", calc.to_unix(dt), json_output)


@fx.tool(
    "timetool",
    subcommand="fromunix",
    help="Unix 时间戳转时间",
    param_help={
        "ts": "Unix 时间戳（秒）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_fromunix(ts: int, format: str = calc._DEFAULT_TIME_FORMAT, json_output: bool = False) -> None:
    """Unix 时间戳转时间（fromunix）子命令。"""
    dt = calc.from_unix(ts)
    _emit("fromunix", calc.format_time(dt, format), json_output)


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
    json_output: bool = False,
) -> None:
    """时区转换（convert）子命令。"""
    dt = _parse_or_report(time, format, "convert", json_output)
    if dt is None:
        return
    if from_tz:
        try:
            src_tz = calc.resolve_tz(from_tz)
        except ValueError as exc:
            report_error(
                "timetool",
                "convert",
                code="INVALID_ARGUMENT",
                message=str(exc),
                field="from_tz",
                expected="IANA 时区名（如 Asia/Shanghai）或 UTC",
                json_output=json_output,
            )
            return
        dt = dt.replace(tzinfo=src_tz)
    try:
        converted = calc.convert_timezone(dt, target_tz)
    except ValueError as exc:
        report_error(
            "timetool",
            "convert",
            code="INVALID_ARGUMENT",
            message=str(exc),
            field="target_tz",
            expected="IANA 时区名（如 Asia/Shanghai）或 UTC",
            json_output=json_output,
        )
        return
    _emit("convert", calc.format_time(converted, format), json_output)
