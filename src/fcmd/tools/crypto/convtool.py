"""convtool 工具：长度 / 重量 / 温度 / 数据大小换算。

换算逻辑在 :mod:`fcmd.infras.codec`，本模块只做参数适配与错误呈现。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import codec


def _convert(
    subcommand: str,
    fn: Callable[..., float],
    *args: Any,
    json_output: bool = False,
    **kwargs: Any,
) -> None:
    """执行换算并输出结果；``ValueError`` 转为统一错误行（退出码 3）。"""
    try:
        result = fn(*args, **kwargs)
    except ValueError as error:
        report_error(
            "convtool",
            subcommand,
            code="INVALID_ARGUMENT",
            message=str(error),
            hint="检查数值范围与单位拼写（源/目标单位需在支持列表内）",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("convtool", subcommand, {"result": result})
        return
    print(result)


@fx.tool(
    "convtool",
    subcommand="length",
    help="长度换算",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（如 m/km/mile/ft/in）",
        "to_unit": "目标单位",
    },
)
def convtool_length(value: float, from_unit: str, to_unit: str, json_output: bool = False) -> None:
    """长度换算。"""
    _convert("length", codec.convert_length, value, from_unit, to_unit, json_output=json_output)


@fx.tool(
    "convtool",
    subcommand="weight",
    help="重量换算",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（如 g/kg/lb/oz）",
        "to_unit": "目标单位",
    },
)
def convtool_weight(value: float, from_unit: str, to_unit: str, json_output: bool = False) -> None:
    """重量换算。"""
    _convert("weight", codec.convert_weight, value, from_unit, to_unit, json_output=json_output)


@fx.tool(
    "convtool",
    subcommand="temp",
    help="温度换算",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（C/F/K）",
        "to_unit": "目标单位（C/F/K）",
    },
)
def convtool_temp(value: float, from_unit: str, to_unit: str, json_output: bool = False) -> None:
    """温度换算。"""
    _convert("temp", codec.convert_temperature, value, from_unit, to_unit, json_output=json_output)


@fx.tool(
    "convtool",
    subcommand="datasize",
    help="数据大小换算",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（B/KB/MB/GB/TB/PB）",
        "to_unit": "目标单位",
        "base": "进制基础（binary=1024 默认；decimal=1000）",
    },
)
def convtool_datasize(
    value: float,
    from_unit: str,
    to_unit: str,
    base: str = "binary",
    json_output: bool = False,
) -> None:
    """数据大小换算。"""
    _convert("datasize", codec.convert_datasize, value, from_unit, to_unit, json_output=json_output, base=base)
