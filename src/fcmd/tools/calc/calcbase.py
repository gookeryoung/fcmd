"""计算域公共实现：randtool / stattool / timetool（无工具注册副作用）。

三组工具全部为纯 Python 实现（标准库 secrets / statistics / datetime /
zoneinfo），无子进程调用。DSL 层 :mod:`fcmd.dsl.actions.toolcmd.calc` 只保留
``@action`` 声明并转接本模块函数，避免出现第二套实现。
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
