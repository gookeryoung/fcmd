"""convtool - 单位换算工具。

提供长度、重量、温度、数据大小四类常用单位换算。所有换算基于标准库实现，
无需外部依赖。

示例
----
    fcmd convtool length 1 m ft                  # 1 米换算为英尺
    fcmd convtool length 1 km mile               # 1 千米换算为英里
    fcmd convtool weight 1 kg lb                 # 1 千克换算为磅
    fcmd convtool temp 100 C F                   # 100 摄氏度换算为华氏度
    fcmd convtool temp 0 C K                    # 0 摄氏度换算为开尔文
    fcmd convtool datasize 1 GB MB               # 1 GB 换算为 MB（默认 1024 进制）
    fcmd convtool datasize 1 GB MB --base decimal  # 1 GB 换算为 MB（1000 进制）
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fcmd
from fcmd.console import get_console

__all__ = [
    "convert_datasize",
    "convert_length",
    "convert_temperature",
    "convert_weight",
    "list_datasize_units",
    "list_length_units",
    "list_temperature_units",
    "list_weight_units",
]

# ============================================================================
# 长度换算（基准：米）
# ============================================================================
_LENGTH_FACTORS: dict[str, float] = {
    "m": 1.0,
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "mile": 1609.344,
    "ft": 0.3048,
    "in": 0.0254,
    "yd": 0.9144,
}


def list_length_units() -> list[str]:
    """列出支持的长度单位。"""
    return list(_LENGTH_FACTORS.keys())


def convert_length(value: float, from_unit: str, to_unit: str) -> float:
    """长度换算。

    Parameters
    ----------
    value:
        待换算的数值
    from_unit:
        源单位（如 ``m``/``km``/``mile``/``ft``/``in``）
    to_unit:
        目标单位

    Returns
    -------
    float
        换算后的数值

    Raises
    ------
    ValueError
        单位不支持时
    """
    if from_unit not in _LENGTH_FACTORS:
        raise ValueError(f"不支持的长度单位: {from_unit}，支持: {sorted(_LENGTH_FACTORS)}")
    if to_unit not in _LENGTH_FACTORS:
        raise ValueError(f"不支持的长度单位: {to_unit}，支持: {sorted(_LENGTH_FACTORS)}")
    meters = value * _LENGTH_FACTORS[from_unit]
    return meters / _LENGTH_FACTORS[to_unit]


# ============================================================================
# 重量换算（基准：克）
# ============================================================================
_WEIGHT_FACTORS: dict[str, float] = {
    "g": 1.0,
    "kg": 1000.0,
    "mg": 0.001,
    "t": 1_000_000.0,  # 公吨
    "lb": 453.59237,
    "oz": 28.349523125,
}


def list_weight_units() -> list[str]:
    """列出支持的重量单位。"""
    return list(_WEIGHT_FACTORS.keys())


def convert_weight(value: float, from_unit: str, to_unit: str) -> float:
    """重量换算。

    Parameters
    ----------
    value:
        待换算的数值
    from_unit:
        源单位（如 ``g``/``kg``/``lb``/``oz``）
    to_unit:
        目标单位

    Returns
    -------
    float
        换算后的数值

    Raises
    ------
    ValueError
        单位不支持时
    """
    if from_unit not in _WEIGHT_FACTORS:
        raise ValueError(f"不支持的重量单位: {from_unit}，支持: {sorted(_WEIGHT_FACTORS)}")
    if to_unit not in _WEIGHT_FACTORS:
        raise ValueError(f"不支持的重量单位: {to_unit}，支持: {sorted(_WEIGHT_FACTORS)}")
    grams = value * _WEIGHT_FACTORS[from_unit]
    return grams / _WEIGHT_FACTORS[to_unit]


# ============================================================================
# 温度换算（C/F/K）
# ============================================================================
_TEMP_UNITS = ("C", "F", "K")


def list_temperature_units() -> list[str]:
    """列出支持的温度单位。"""
    return list(_TEMP_UNITS)


def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    """温度换算。

    支持 C（摄氏）、F（华氏）、K（开尔文）之间互转。

    Parameters
    ----------
    value:
        待换算的数值
    from_unit:
        源单位（``C``/``F``/``K``）
    to_unit:
        目标单位（``C``/``F``/``K``）

    Returns
    -------
    float
        换算后的数值

    Raises
    ------
    ValueError
        单位不支持时，或开尔文值小于 0 时
    """
    from_u = from_unit.upper()
    to_u = to_unit.upper()
    if from_u not in _TEMP_UNITS:
        raise ValueError(f"不支持的温度单位: {from_unit}，支持: {list(_TEMP_UNITS)}")
    if to_u not in _TEMP_UNITS:
        raise ValueError(f"不支持的温度单位: {to_unit}，支持: {list(_TEMP_UNITS)}")

    # 先统一转为摄氏度
    if from_u == "C":
        celsius = value
    elif from_u == "F":
        celsius = (value - 32) * 5 / 9
    else:  # K
        celsius = value - 273.15

    if celsius < -273.15:
        raise ValueError(f"温度低于绝对零度（-273.15°C）: {value}{from_u}")

    # 再从摄氏度转为目标单位
    if to_u == "C":
        return celsius
    if to_u == "F":
        return celsius * 9 / 5 + 32
    return celsius + 273.15  # K


# ============================================================================
# 数据大小换算（基准：字节）
# ============================================================================
_DATASIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def list_datasize_units() -> list[str]:
    """列出支持的数据大小单位。"""
    return list(_DATASIZE_UNITS)


def convert_datasize(
    value: float,
    from_unit: str,
    to_unit: str,
    base: str = "binary",
) -> float:
    """数据大小换算。

    Parameters
    ----------
    value:
        待换算的数值
    from_unit:
        源单位（``B``/``KB``/``MB``/``GB``/``TB``/``PB``）
    to_unit:
        目标单位
    base:
        进制基础（``binary``=1024，默认；``decimal``=1000）

    Returns
    -------
    float
        换算后的数值

    Raises
    ------
    ValueError
        单位不支持或 ``base`` 不支持时
    """
    from_u = from_unit.upper()
    to_u = to_unit.upper()
    if from_u not in _DATASIZE_UNITS:
        raise ValueError(f"不支持的数据大小单位: {from_unit}，支持: {list(_DATASIZE_UNITS)}")
    if to_u not in _DATASIZE_UNITS:
        raise ValueError(f"不支持的数据大小单位: {to_unit}，支持: {list(_DATASIZE_UNITS)}")
    if base == "binary":
        factor = 1024
    elif base == "decimal":
        factor = 1000
    else:
        raise ValueError(f"不支持的进制基础: {base}，支持: binary, decimal")

    # 先转为字节数
    from_idx = _DATASIZE_UNITS.index(from_u)
    to_idx = _DATASIZE_UNITS.index(to_u)
    bytes_value = value * (factor**from_idx)
    return bytes_value / (factor**to_idx)


# ============================================================================
# CLI 子命令
# ============================================================================


def _print_conversion(fn: Callable[..., float], *args: Any, **kwargs: Any) -> None:
    """执行换算函数并打印结果，捕获 ValueError 作为错误信息输出。

    Parameters
    ----------
    fn:
        换算函数（须在单位非法时抛 ``ValueError``）
    *args:
        位置参数
    **kwargs:
        关键字参数
    """
    try:
        result = fn(*args, **kwargs)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    print(result)


@fcmd.tool("convtool", subcommand="length", help="长度换算")
def length_cmd(value: float, from_unit: str, to_unit: str) -> None:
    """长度换算。用法：``fcmd convtool length <value> <from> <to>``"""
    _print_conversion(convert_length, value, from_unit, to_unit)


@fcmd.tool("convtool", subcommand="weight", help="重量换算")
def weight_cmd(value: float, from_unit: str, to_unit: str) -> None:
    """重量换算。用法：``fcmd convtool weight <value> <from> <to>``"""
    _print_conversion(convert_weight, value, from_unit, to_unit)


@fcmd.tool("convtool", subcommand="temp", help="温度换算")
def temp_cmd(value: float, from_unit: str, to_unit: str) -> None:
    """温度换算。用法：``fcmd convtool temp <value> <from> <to>``"""
    _print_conversion(convert_temperature, value, from_unit, to_unit)


@fcmd.tool("convtool", subcommand="datasize", help="数据大小换算")
def datasize_cmd(
    value: float,
    from_unit: str,
    to_unit: str,
    base: str = "binary",
) -> None:
    """数据大小换算。用法：``fcmd convtool datasize <value> <from> <to> [--base binary|decimal]``"""
    _print_conversion(convert_datasize, value, from_unit, to_unit, base=base)


@fcmd.main("convtool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
