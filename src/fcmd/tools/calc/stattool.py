"""stattool 工具：均值、中位数、标准差、方差与统计摘要。

计算逻辑在 :mod:`fcmd.infras.calc`；本模块负责文件加载错误的呈现与结果输出。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import calc


def _run_stat_action(
    subcommand: str,
    func: Callable[[list[float]], float | dict[str, float | int]],
    label: str,
    file: Path,
    json_output: bool,
) -> None:
    """stattool 五子命令共享的加载→计算→输出模板。"""
    try:
        numbers = calc.load_numbers(file)
    except FileNotFoundError as exc:
        report_error(
            "stattool",
            subcommand,
            code="FILE_NOT_FOUND",
            message=str(exc),
            field="file",
            expected="存在的数据文件",
            hint="确认文件路径与扩展名",
            json_output=json_output,
        )
        return
    except ValueError as exc:
        report_error(
            "stattool",
            subcommand,
            code="INVALID_ARGUMENT",
            message=f"数据解析失败: {exc}",
            field="file",
            expected="每行一个数字（空行与 # 注释行忽略）",
            json_output=json_output,
        )
        return
    try:
        result = func(numbers)
    except ValueError as exc:
        report_error(
            "stattool",
            subcommand,
            code="INVALID_ARGUMENT",
            message=f"{label} 计算失败: {exc}",
            json_output=json_output,
        )
        return
    if json_output:
        # summarize 本身是键值字典；其余子命令为单个数值
        emit_result("stattool", subcommand, dict(result) if isinstance(result, dict) else {"result": result})
        return
    # summarize 返回字典，逐行格式化；其余直接打印
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
def stattool_mean(file: Path, json_output: bool = False) -> None:
    """计算算术平均值（mean）子命令。"""
    _run_stat_action("mean", calc.stat_mean, "平均值", file, json_output)


@fx.tool(
    "stattool",
    subcommand="median",
    help="计算中位数",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_median(file: Path, json_output: bool = False) -> None:
    """计算中位数（median）子命令。"""
    _run_stat_action("median", calc.stat_median, "中位数", file, json_output)


@fx.tool(
    "stattool",
    subcommand="stddev",
    help="计算样本标准差",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_stddev(file: Path, json_output: bool = False) -> None:
    """计算样本标准差（stddev）子命令。"""
    _run_stat_action("stddev", calc.stat_stddev, "标准差", file, json_output)


@fx.tool(
    "stattool",
    subcommand="variance",
    help="计算样本方差",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_variance(file: Path, json_output: bool = False) -> None:
    """计算样本方差（variance）子命令。"""
    _run_stat_action("variance", calc.stat_variance, "方差", file, json_output)


@fx.tool(
    "stattool",
    subcommand="summarize",
    help="输出完整统计摘要",
    param_help={"file": "数据文件路径（每行一个数字）"},
)
def stattool_summarize(file: Path, json_output: bool = False) -> None:
    """输出完整统计摘要（summarize）子命令。"""
    _run_stat_action("summarize", calc.stat_summarize, "统计摘要", file, json_output)
