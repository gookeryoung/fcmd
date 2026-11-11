"""stattool 工具：均值、中位数、标准差、方差与统计摘要。

计算逻辑在 :mod:`fcmd.infras.calc`；本模块负责文件加载错误的呈现与结果输出。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fcmd as fx
from fcmd.infras import calc


def _run_stat_action(
    func: Callable[[list[float]], float | dict[str, float | int]],
    label: str,
    file: Path,
) -> None:
    """stattool 五子命令共享的加载→计算→输出模板。"""
    try:
        numbers = calc.load_numbers(file)
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


@fx.tool(
    "stattool",
    subcommand="mean",
    help="计算算术平均值",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_mean(file: Path) -> None:
    """计算算术平均值（mean）子命令。"""
    _run_stat_action(calc.stat_mean, "平均值", file)


@fx.tool(
    "stattool",
    subcommand="median",
    help="计算中位数",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_median(file: Path) -> None:
    """计算中位数（median）子命令。"""
    _run_stat_action(calc.stat_median, "中位数", file)


@fx.tool(
    "stattool",
    subcommand="stddev",
    help="计算样本标准差",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_stddev(file: Path) -> None:
    """计算样本标准差（stddev）子命令。"""
    _run_stat_action(calc.stat_stddev, "标准差", file)


@fx.tool(
    "stattool",
    subcommand="variance",
    help="计算样本方差",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_variance(file: Path) -> None:
    """计算样本方差（variance）子命令。"""
    _run_stat_action(calc.stat_variance, "方差", file)


@fx.tool(
    "stattool",
    subcommand="summarize",
    help="输出完整统计摘要",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_summarize(file: Path) -> None:
    """输出完整统计摘要（summarize）子命令。"""
    _run_stat_action(calc.stat_summarize, "统计摘要", file)
