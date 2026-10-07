"""计算类 DSL 动作：randtool / stattool / timetool。

三个工具原属 ``fcmd.cli.calc`` 包，全部为纯 Python 实现（标准库
secrets / statistics / datetime / zoneinfo），无子进程调用。迁移至
本模块后由 ``@action`` 装饰器注册，TOML 声明 ``action = "<名>"`` 直接
引用；CLI 参数 schema 从动作签名自动推导（:mod:`fcmd.dsl.synth` 层拷贝）。
"""

from __future__ import annotations

import base64
import secrets
import statistics
import string
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..actions import action

# ============================================================================
# randtool 常量
# ============================================================================

# 默认密码字符集
_LOWERCASE = string.ascii_lowercase
_UPPERCASE = string.ascii_uppercase
_DIGITS = string.digits
# 安全符号集：排除易混淆字符（引号/反斜杠/反引号）
_SYMBOLS = "!@#$%^&*()-_=+[]{}<>?"

_DEFAULT_PASSWORD_LENGTH = 16
_DEFAULT_STRING_LENGTH = 16


# ============================================================================
# randtool 公共逻辑
# ============================================================================


def generate_password(length: int = 16, symbols: bool = True) -> str:
    if length < 4:
        raise ValueError(f"length 必须大于等于 4，当前: {length}")
    pools: list[str] = [_LOWERCASE, _UPPERCASE, _DIGITS]
    if symbols:
        pools.append(_SYMBOLS)
    full_pool = "".join(pools)
    result: list[str] = [secrets.choice(pool) for pool in pools]
    result.extend(secrets.choice(full_pool) for _ in range(length - len(result)))
    secrets.SystemRandom().shuffle(result)
    return "".join(result)


def generate_number(min_val: int, max_val: int) -> int:
    if min_val > max_val:
        raise ValueError(f"min_val({min_val}) 不能大于 max_val({max_val})")
    return min_val + secrets.randbelow(max_val - min_val + 1)


def generate_string(length: int = 16, chars: str = "") -> str:
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    pool = chars if chars else (string.ascii_letters + string.digits)
    return "".join(secrets.choice(pool) for _ in range(length))


def generate_bytes(length: int, encoding: str = "hex") -> str:
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    raw = secrets.token_bytes(length)
    if encoding == "hex":
        return raw.hex()
    if encoding == "base64":
        return base64.b64encode(raw).decode("ascii")
    raise ValueError(f"不支持的编码: {encoding}，支持: hex, base64")


# ============================================================================
# randtool DSL 动作
# ============================================================================


@action(
    "randtool_password",
    param_help={
        "length": "密码长度（默认 16，>= 4）",
        "symbols": "是否包含符号字符",
    },
)
def randtool_password(length: int = _DEFAULT_PASSWORD_LENGTH, symbols: bool = True) -> None:
    """生成强密码（password）子命令。"""
    try:
        print(generate_password(length, symbols))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "randtool_number",
    param_help={
        "min_val": "最小值（含）",
        "max_val": "最大值（含）",
    },
)
def randtool_number(min_val: int, max_val: int) -> None:
    """生成随机整数（number）子命令。"""
    try:
        print(generate_number(min_val, max_val))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "randtool_string",
    param_help={
        "length": "字符串长度（默认 16，> 0）",
        "chars": "自定义字符集（空串=字母+数字）",
    },
)
def randtool_string(length: int = _DEFAULT_STRING_LENGTH, chars: str = "") -> None:
    """生成自定义字符集随机字符串（string）子命令。"""
    try:
        print(generate_string(length, chars))
    except ValueError as exc:
        print(f"错误: {exc}")


@action(
    "randtool_bytes",
    param_help={
        "length": "字节长度（> 0）",
        "encoding": "输出编码：hex 或 base64",
    },
)
def randtool_bytes(length: int, encoding: str = "hex") -> None:
    """生成随机字节（bytes）子命令。"""
    try:
        print(generate_bytes(length, encoding))
    except ValueError as exc:
        print(f"错误: {exc}")


# ============================================================================
# stattool 公共逻辑
# ============================================================================


def load_numbers(filepath: Path) -> list[float]:
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    numbers: list[float] = []
    with filepath.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                numbers.append(float(line))
            except ValueError as exc:
                raise ValueError(f"第 {lineno} 行无法解析为数字: {line!r}") from exc
    return numbers


def stat_mean(numbers: list[float]) -> float:
    if not numbers:
        raise ValueError("数据列表为空，无法计算平均值")
    return statistics.mean(numbers)


def stat_median(numbers: list[float]) -> float:
    if not numbers:
        raise ValueError("数据列表为空，无法计算中位数")
    return statistics.median(numbers)


def stat_stddev(numbers: list[float]) -> float:
    if len(numbers) < 2:
        raise ValueError("样本标准差需要至少 2 个数据点")
    return statistics.stdev(numbers)


def stat_variance(numbers: list[float]) -> float:
    if len(numbers) < 2:
        raise ValueError("样本方差需要至少 2 个数据点")
    return statistics.variance(numbers)


def stat_summarize(numbers: list[float]) -> dict[str, float | int]:
    if not numbers:
        raise ValueError("数据列表为空，无法生成统计摘要")
    result: dict[str, float | int] = {
        "count": len(numbers),
        "sum": sum(numbers),
        "mean": statistics.mean(numbers),
        "median": statistics.median(numbers),
        "min": min(numbers),
        "max": max(numbers),
    }
    if len(numbers) >= 2:
        result["stddev"] = statistics.stdev(numbers)
        result["variance"] = statistics.variance(numbers)
    else:
        result["stddev"] = 0.0
        result["variance"] = 0.0
    return result


def _run_stat_action(
    func: Callable[[list[float]], float | dict[str, float | int]],
    label: str,
    file: Path,
) -> None:
    """stattool 五子命令共享的加载→计算→输出模板。"""
    try:
        numbers = load_numbers(file)
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
        return
    except ValueError as exc:
        print(f"数据解析失败: {exc}")
        return
    try:
        result = func(numbers)
    except ValueError as exc:
        print(f"{label} 计算失败: {exc}")
        return
    # summarize 返回字典，单独处理
    if isinstance(result, dict):
        for key, value in result.items():
            if isinstance(value, int):
                print(f"{key}: {value}")
            else:
                print(f"{key}: {value:.4f}")
    else:
        print(result)


# ============================================================================
# stattool DSL 动作
# ============================================================================


@action("stattool_mean", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_mean(file: Path) -> None:
    """计算算术平均值（mean）子命令。"""
    _run_stat_action(stat_mean, "平均值", file)


@action("stattool_median", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_median(file: Path) -> None:
    """计算中位数（median）子命令。"""
    _run_stat_action(stat_median, "中位数", file)


@action("stattool_stddev", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_stddev(file: Path) -> None:
    """计算样本标准差（stddev）子命令。"""
    _run_stat_action(stat_stddev, "标准差", file)


@action("stattool_variance", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_variance(file: Path) -> None:
    """计算样本方差（variance）子命令。"""
    _run_stat_action(stat_variance, "方差", file)


@action("stattool_summarize", param_help={"file": "数据文件路径（每行一个数字）"})
def stattool_summarize(file: Path) -> None:
    """输出完整统计摘要（summarize）子命令。"""
    _run_stat_action(stat_summarize, "统计摘要", file)


# ============================================================================
# timetool 公共逻辑
# ============================================================================

_DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def resolve_tz(name: str):
    if name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except KeyError as exc:
        raise ValueError(f"无效或不可用的时区: {name}") from exc


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return datetime.now()


def parse_time(time_str: str, fmt: str = _DEFAULT_TIME_FORMAT) -> datetime:
    return datetime.strptime(time_str, fmt)


def format_time(dt: datetime, fmt: str = _DEFAULT_TIME_FORMAT) -> str:
    return dt.strftime(fmt)


def to_unix(dt: datetime) -> int:
    return int(dt.timestamp())


def from_unix(ts: int) -> datetime:
    return datetime.fromtimestamp(ts)


def convert_timezone(dt: datetime, target_tz: str) -> datetime:
    tz = resolve_tz(target_tz)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(tz)


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
def timetool_now(utc: bool = False, format: str = _DEFAULT_TIME_FORMAT) -> None:
    """显示当前时间（now）子命令。"""
    dt = now_utc() if utc else now_local()
    print(format_time(dt, format))


@action(
    "timetool_parse",
    param_help={
        "time": "时间字符串",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_parse(time: str, format: str = _DEFAULT_TIME_FORMAT) -> None:
    """解析时间字符串（parse）子命令。"""
    try:
        dt = parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(format_time(dt, format))


@action(
    "timetool_unix",
    param_help={
        "time": "时间字符串（视为本地时间）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_unix(time: str, format: str = _DEFAULT_TIME_FORMAT) -> None:
    """转 Unix 时间戳（unix）子命令。"""
    try:
        dt = parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    print(to_unix(dt))


@action(
    "timetool_fromunix",
    param_help={
        "ts": "Unix 时间戳（秒）",
        "format": "strftime 格式（默认 %%Y-%%m-%%d %%H:%%M:%%S）",
    },
)
def timetool_fromunix(ts: int, format: str = _DEFAULT_TIME_FORMAT) -> None:
    """Unix 时间戳转时间（fromunix）子命令。"""
    dt = from_unix(ts)
    print(format_time(dt, format))


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
    format: str = _DEFAULT_TIME_FORMAT,
    from_tz: str = "",
) -> None:
    """时区转换（convert）子命令。"""
    try:
        dt = parse_time(time, format)
    except ValueError as exc:
        print(f"解析失败: {exc}")
        return
    if from_tz:
        try:
            src_tz = resolve_tz(from_tz)
        except ValueError as exc:
            print(f"错误: {exc}")
            return
        dt = dt.replace(tzinfo=src_tz)
    try:
        converted = convert_timezone(dt, target_tz)
    except ValueError as exc:
        print(f"错误: {exc}")
        return
    print(format_time(converted, format))
