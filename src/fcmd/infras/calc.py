"""计算基础件：随机生成、统计、时间与日期（纯函数，无输出副作用）。

* 随机：:func:`generate_password` / :func:`generate_number` /
  :func:`generate_string` / :func:`generate_bytes`
* 统计：:func:`load_numbers` 与 ``stat_*`` 系列
* 时间：:func:`now_utc` / :func:`parse_time` / :func:`convert_timezone` 等
* 日期：:func:`add_days` / :func:`count_workdays` / :func:`date_diff` /
  :func:`compare_dates`

全部基于标准库 ``secrets`` / ``statistics`` / ``datetime`` / ``zoneinfo``，
由 ``fcmd.tools.calc`` 下的工具模块做参数适配与输出。
"""

from __future__ import annotations

import base64
import secrets
import statistics
import string
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ============================================================================
# 随机生成
# ============================================================================

# 默认密码字符集
_LOWERCASE = string.ascii_lowercase
_UPPERCASE = string.ascii_uppercase
_DIGITS = string.digits
# 安全符号集：排除易混淆字符（引号/反斜杠/反引号）
_SYMBOLS = "!@#$%^&*()-_=+[]{}<>?"

_DEFAULT_PASSWORD_LENGTH = 16
_DEFAULT_STRING_LENGTH = 16


def generate_password(length: int = 16, symbols: bool = True) -> str:
    """生成强密码，至少包含大小写字母与数字各一个。"""
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
    """生成 ``[min_val, max_val]`` 闭区间内的随机整数。"""
    if min_val > max_val:
        raise ValueError(f"min_val({min_val}) 不能大于 max_val({max_val})")
    return min_val + secrets.randbelow(max_val - min_val + 1)


def generate_string(length: int = 16, chars: str = "") -> str:
    """生成随机字符串；``chars`` 为空时使用字母 + 数字字符集。"""
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    pool = chars if chars else (string.ascii_letters + string.digits)
    return "".join(secrets.choice(pool) for _ in range(length))


def generate_bytes(length: int, encoding: str = "hex") -> str:
    """生成随机字节并以 ``hex`` 或 ``base64`` 编码输出。"""
    if length <= 0:
        raise ValueError(f"length 必须大于 0，当前: {length}")
    raw = secrets.token_bytes(length)
    if encoding == "hex":
        return raw.hex()
    if encoding == "base64":
        return base64.b64encode(raw).decode("ascii")
    raise ValueError(f"不支持的编码: {encoding}，支持: hex, base64")


# ============================================================================
# 统计
# ============================================================================


def load_numbers(filepath: Path) -> list[float]:
    """从文本文件加载数字（每行一个，空行与 ``#`` 注释行忽略）。"""
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
    """算术平均值。"""
    if not numbers:
        raise ValueError("数据列表为空，无法计算平均值")
    return statistics.mean(numbers)


def stat_median(numbers: list[float]) -> float:
    """中位数。"""
    if not numbers:
        raise ValueError("数据列表为空，无法计算中位数")
    return statistics.median(numbers)


def stat_stddev(numbers: list[float]) -> float:
    """样本标准差。"""
    if len(numbers) < 2:
        raise ValueError("样本标准差需要至少 2 个数据点")
    return statistics.stdev(numbers)


def stat_variance(numbers: list[float]) -> float:
    """样本方差。"""
    if len(numbers) < 2:
        raise ValueError("样本方差需要至少 2 个数据点")
    return statistics.variance(numbers)


def stat_summarize(numbers: list[float]) -> dict[str, float | int]:
    """完整统计摘要（count/sum/mean/median/min/max/stddev/variance）。"""
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


# ============================================================================
# 时间
# ============================================================================

_DEFAULT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def resolve_tz(name: str):
    """解析时区名（``UTC`` 或 IANA 名称），无效时抛 ``ValueError``。"""
    if name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(name)
    except KeyError as exc:
        raise ValueError(f"无效或不可用的时区: {name}") from exc


def now_utc() -> datetime:
    """当前 UTC 时间（aware）。"""
    return datetime.now(UTC)


def now_local() -> datetime:
    """当前本地时间（naive）。"""
    return datetime.now()


def parse_time(time_str: str, fmt: str = _DEFAULT_TIME_FORMAT) -> datetime:
    """按 ``fmt`` 解析时间字符串。"""
    return datetime.strptime(time_str, fmt)


def format_time(dt: datetime, fmt: str = _DEFAULT_TIME_FORMAT) -> str:
    """按 ``fmt`` 格式化时间。"""
    return dt.strftime(fmt)


def to_unix(dt: datetime) -> int:
    """时间转 Unix 时间戳（秒）。"""
    return int(dt.timestamp())


def from_unix(ts: int) -> datetime:
    """Unix 时间戳转本地时间。"""
    return datetime.fromtimestamp(ts)


def convert_timezone(dt: datetime, target_tz: str) -> datetime:
    """把时间转换到目标时区；naive 时间按本地时间解释。"""
    tz = resolve_tz(target_tz)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(tz)


# ============================================================================
# 日期
# ============================================================================


def parse_date(date_str: str) -> date:
    """解析 ISO 8601 日期字符串（``YYYY-MM-DD``）。"""
    return date.fromisoformat(date_str)


def add_days(date_str: str, days: int) -> str:
    """日期加减天数，返回新日期字符串。"""
    d = parse_date(date_str)
    result = d + timedelta(days=days)
    return result.isoformat()


def count_workdays(start_str: str, end_str: str) -> int:
    """计算两个日期之间的工作日数（含首尾，排除周六周日）。"""
    start = parse_date(start_str)
    end = parse_date(end_str)
    if start > end:
        start, end = end, start
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def date_diff(date1_str: str, date2_str: str) -> int:
    """计算两个日期的差值（``date2 - date1`` 的天数）。"""
    d1 = parse_date(date1_str)
    d2 = parse_date(date2_str)
    return (d2 - d1).days


def compare_dates(date1_str: str, date2_str: str) -> str:
    """比较两个日期的先后关系，返回 before/equal/after。"""
    d1 = parse_date(date1_str)
    d2 = parse_date(date2_str)
    if d1 < d2:
        return "before"
    if d1 > d2:
        return "after"
    return "equal"
