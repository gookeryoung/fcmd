"""计算类 DSL 动作：randtool / stattool / timetool。

业务实现统一在 :mod:`fcmd.tools.calc.calcbase`；本模块只保留 ``@action``
声明与参数转接，避免 DSL 层出现第二套计算实现。TOML 声明通过
``action = "<名>"`` 引用这些名字。CLI 参数 schema 从动作签名自动推导
（:mod:`fcmd.dsl.synth` 层拷贝）。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.tools.calc import calcbase

from .. import action

# ============================================================================
# randtool DSL 动作
# ============================================================================


@action(
    "randtool_password",
    param_help={
        "length": "密码长度（默认 16，>= 4）",
        "symbols": "是否包含符号字符",
    },
    errors=(ValueError,),
)
def randtool_password(length: int = calcbase._DEFAULT_PASSWORD_LENGTH, symbols: bool = True) -> None:
    """生成强密码（password）子命令。"""
    print(calcbase.generate_password(length, symbols))


@action(
    "randtool_number",
    param_help={
        "min_val": "最小值（含）",
        "max_val": "最大值（含）",
    },
    errors=(ValueError,),
)
def randtool_number(min_val: int, max_val: int) -> None:
    """生成随机整数（number）子命令。"""
    print(calcbase.generate_number(min_val, max_val))


@action(
    "randtool_string",
    param_help={
        "length": "字符串长度（默认 16，> 0）",
        "chars": "自定义字符集（空串=字母+数字）",
    },
    errors=(ValueError,),
)
def randtool_string(length: int = calcbase._DEFAULT_STRING_LENGTH, chars: str = "") -> None:
    """生成自定义字符集随机字符串（string）子命令。"""
    print(calcbase.generate_string(length, chars))


@action(
    "randtool_bytes",
    param_help={
        "length": "字节长度（> 0）",
        "encoding": "输出编码：hex 或 base64",
    },
    errors=(ValueError,),
)
def randtool_bytes(length: int, encoding: str = "hex") -> None:
    """生成随机字节（bytes）子命令。"""
    print(calcbase.generate_bytes(length, encoding))


# ============================================================================
# stattool DSL 动作
# ============================================================================


@action("stattool_mean", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_mean(file: Path) -> None:
    """计算算术平均值（mean）子命令。"""
    calcbase._run_stat_action(calcbase.stat_mean, "平均值", file)


@action("stattool_median", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_median(file: Path) -> None:
    """计算中位数（median）子命令。"""
    calcbase._run_stat_action(calcbase.stat_median, "中位数", file)


@action("stattool_stddev", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_stddev(file: Path) -> None:
    """计算样本标准差（stddev）子命令。"""
    calcbase._run_stat_action(calcbase.stat_stddev, "标准差", file)


@action("stattool_variance", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_variance(file: Path) -> None:
    """计算样本方差（variance）子命令。"""
    calcbase._run_stat_action(calcbase.stat_variance, "方差", file)


@action("stattool_summarize", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_summarize(file: Path) -> None:
    """输出完整统计摘要（summarize）子命令。"""
    calcbase._run_stat_action(calcbase.stat_summarize, "统计摘要", file)


# ============================================================================
# timetool DSL 动作
# ============================================================================


@action(
    "timetool_now",
    param_help={
        "utc": "使用 UTC 时间（默认 False=本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_now(utc: bool = False, format: str = calcbase._DEFAULT_TIME_FORMAT) -> None:
    """显示当前时间（now）子命令。"""
    dt = calcbase.now_utc() if utc else calcbase.now_local()
    print(calcbase.format_time(dt, format))


@action(
    "timetool_parse",
    param_help={
        "time": "时间字符串",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_parse(time: str, format: str = calcbase._DEFAULT_TIME_FORMAT) -> None:
    """解析时间字符串（parse）子命令。"""
    try:
        dt = calcbase.parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(calcbase.format_time(dt, format))


@action(
    "timetool_unix",
    param_help={
        "time": "时间字符串（视为本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_unix(time: str, format: str = calcbase._DEFAULT_TIME_FORMAT) -> None:
    """转 Unix 时间戳（unix）子命令。"""
    try:
        dt = calcbase.parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(calcbase.to_unix(dt))


@action(
    "timetool_fromunix",
    param_help={
        "ts": "Unix 时间戳（秒）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_fromunix(ts: int, format: str = calcbase._DEFAULT_TIME_FORMAT) -> None:
    """Unix 时间戳转时间（fromunix）子命令。"""
    dt = calcbase.from_unix(ts)
    print(calcbase.format_time(dt, format))


@action(
    "timetool_convert",
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
    format: str = calcbase._DEFAULT_TIME_FORMAT,
    from_tz: str = "",
) -> None:
    """时区转换（convert）子命令。"""
    try:
        dt = calcbase.parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    if from_tz:
        try:
            src_tz = calcbase.resolve_tz(from_tz)
        except ValueError as exc:
            print(f"错误: {exc}")
            return
        dt = dt.replace(tzinfo=src_tz)
    try:
        converted = calcbase.convert_timezone(dt, target_tz)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(calcbase.format_time(converted, format))
