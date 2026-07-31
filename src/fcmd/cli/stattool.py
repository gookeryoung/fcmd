"""stattool - 统计工具。

基于标准库 ``statistics`` 提供常用统计计算：均值、中位数、标准差、方差、
完整统计摘要。

输入数据从文本文件读取，每行一个数字；空行与 ``#`` 起始的注释行被忽略。

示例
----
    fcmd stattool mean data.txt                # 计算平均值
    fcmd stattool median data.txt              # 计算中位数
    fcmd stattool stddev data.txt              # 计算样本标准差
    fcmd stattool variance data.txt            # 计算样本方差
    fcmd stattool summarize data.txt          # 输出完整统计摘要
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import fcmd

__all__ = [
    "load_numbers",
    "stat_mean",
    "stat_median",
    "stat_stddev",
    "stat_summarize",
    "stat_variance",
]


# ============================================================================
# 公共函数
# ============================================================================


def load_numbers(filepath: Path) -> list[float]:
    """从文本文件加载数字列表。

    每行一个数字；空行与 ``#`` 起始的注释行被忽略。

    Parameters
    ----------
    filepath:
        数据文件路径

    Returns
    -------
    list[float]
        解析得到的数字列表

    Raises
    ------
    FileNotFoundError
        文件不存在
    ValueError
        某行无法解析为数字时（含行号）
    """
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
    """计算算术平均值。

    Parameters
    ----------
    numbers:
        数字列表

    Returns
    -------
    float
        算术平均值

    Raises
    ------
    ValueError
        ``numbers`` 为空时
    """
    if not numbers:
        raise ValueError("数据列表为空，无法计算平均值")
    return statistics.mean(numbers)


def stat_median(numbers: list[float]) -> float:
    """计算中位数。

    Parameters
    ----------
    numbers:
        数字列表

    Returns
    -------
    float
        中位数（偶数个时为中间两值的平均）

    Raises
    ------
    ValueError
        ``numbers`` 为空时
    """
    if not numbers:
        raise ValueError("数据列表为空，无法计算中位数")
    return statistics.median(numbers)


def stat_stddev(numbers: list[float]) -> float:
    """计算样本标准差（n-1 除法）。

    Parameters
    ----------
    numbers:
        数字列表

    Returns
    -------
    float
        样本标准差

    Raises
    ------
    ValueError
        ``numbers`` 少于 2 个时
    """
    if len(numbers) < 2:
        raise ValueError("样本标准差需要至少 2 个数据点")
    return statistics.stdev(numbers)


def stat_variance(numbers: list[float]) -> float:
    """计算样本方差（n-1 除法）。

    Parameters
    ----------
    numbers:
        数字列表

    Returns
    -------
    float
        样本方差

    Raises
    ------
    ValueError
        ``numbers`` 少于 2 个时
    """
    if len(numbers) < 2:
        raise ValueError("样本方差需要至少 2 个数据点")
    return statistics.variance(numbers)


def stat_summarize(numbers: list[float]) -> dict[str, float | int]:
    """计算完整统计摘要。

    Parameters
    ----------
    numbers:
        数字列表

    Returns
    -------
    dict[str, float | int]
        包含以下键的统计摘要：
        - ``count``: 数据点数（int）
        - ``sum``: 总和（float）
        - ``mean``: 平均值
        - ``median``: 中位数
        - ``min``: 最小值
        - ``max``: 最大值
        - ``stddev``: 样本标准差（数据点 < 2 时为 0.0）
        - ``variance``: 样本方差（数据点 < 2 时为 0.0）

    Raises
    ------
    ValueError
        ``numbers`` 为空时
    """
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
# CLI 子命令
# ============================================================================


def _load_or_print(filepath: Path) -> list[float] | None:
    """加载数字列表，失败时打印错误并返回 ``None``。"""
    try:
        return load_numbers(filepath)
    except FileNotFoundError as exc:
        print(str(exc))
        return None
    except ValueError as exc:
        print(f"数据解析失败: {exc}")
        return None


def _calc_or_print(
    numbers: list[float],
    func: Any,
    name: str,
) -> Any:
    """调用统计函数，失败时打印错误并返回 ``None``。

    返回类型为 ``Any`` 以兼容标量统计值与 ``summarize`` 返回的字典。
    """
    try:
        return func(numbers)
    except ValueError as exc:
        print(f"{name} 计算失败: {exc}")
        return None


@fcmd.tool("stattool", subcommand="mean", help="计算算术平均值")
def mean_cmd(file: Path) -> None:
    """计算文件中数字的算术平均值。

    Parameters
    ----------
    file:
        数据文件路径（每行一个数字）
    """
    numbers = _load_or_print(file)
    if numbers is None:
        return
    result = _calc_or_print(numbers, stat_mean, "平均值")
    if result is not None:
        print(result)


@fcmd.tool("stattool", subcommand="median", help="计算中位数")
def median_cmd(file: Path) -> None:
    """计算文件中数字的中位数。

    Parameters
    ----------
    file:
        数据文件路径（每行一个数字）
    """
    numbers = _load_or_print(file)
    if numbers is None:
        return
    result = _calc_or_print(numbers, stat_median, "中位数")
    if result is not None:
        print(result)


@fcmd.tool("stattool", subcommand="stddev", help="计算样本标准差")
def stddev_cmd(file: Path) -> None:
    """计算文件中数字的样本标准差。

    Parameters
    ----------
    file:
        数据文件路径（每行一个数字）
    """
    numbers = _load_or_print(file)
    if numbers is None:
        return
    result = _calc_or_print(numbers, stat_stddev, "标准差")
    if result is not None:
        print(result)


@fcmd.tool("stattool", subcommand="variance", help="计算样本方差")
def variance_cmd(file: Path) -> None:
    """计算文件中数字的样本方差。

    Parameters
    ----------
    file:
        数据文件路径（每行一个数字）
    """
    numbers = _load_or_print(file)
    if numbers is None:
        return
    result = _calc_or_print(numbers, stat_variance, "方差")
    if result is not None:
        print(result)


@fcmd.tool("stattool", subcommand="summarize", help="输出完整统计摘要")
def summarize_cmd(file: Path) -> None:
    """输出文件中数字的完整统计摘要。

    Parameters
    ----------
    file:
        数据文件路径（每行一个数字）
    """
    numbers = _load_or_print(file)
    if numbers is None:
        return
    summary = _calc_or_print(numbers, stat_summarize, "统计摘要")
    if summary is None:
        return
    for key, value in summary.items():
        # 整数项（count）直接打印，浮点项保留 4 位小数
        if isinstance(value, int):
            print(f"{key}: {value}")
        else:
            print(f"{key}: {value:.4f}")


@fcmd.main("stattool")
def main() -> None:
    pass


if __name__ == "__main__":
    main()
