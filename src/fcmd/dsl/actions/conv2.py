"""转换工具组二：编解码（codetool）、单位换算（convtool）、URL 解析（urltool）。

原模块位置：
- ``fcmd.cli.conv.codetool`` —— Base64 / URL / Hex / ROT13 / HTML 编解码（5 子命令）
- ``fcmd.cli.conv.convtool`` —— 长度 / 重量 / 温度 / 数据大小换算（4 子命令）
- ``fcmd.cli.conv.urltool`` —— URL 解析 / 查询参数 / 基础 URL 提取（4 子命令）

迁移为 DSL action：所有 CLI 子命令改为 ``@action("<tool>_<sub>", param_help={...})``，
动作签名即 CLI 参数 schema；公共函数保持 ``__all__`` 导出不变供测试与 API 调用。
"""

from __future__ import annotations

import base64
import binascii
import codecs
import html
import urllib.parse
from collections.abc import Callable
from typing import Any

from fcmd.console import get_console
from fcmd.dsl.actions import action

__all__ = [
    "add_query_param",
    "convert_datasize",
    "convert_length",
    "convert_temperature",
    "convert_weight",
    "decode_base64",
    "decode_hex",
    "decode_url",
    "encode_base64",
    "encode_hex",
    "encode_url",
    "escape_html",
    "get_base_url",
    "get_query_param",
    "list_datasize_units",
    "list_length_units",
    "list_temperature_units",
    "list_weight_units",
    "parse_url",
    "rot13",
    "unescape_html",
]

# ======================================================================== #
# codetool —— Base64 / URL / Hex / ROT13 / HTML 编解码
# ======================================================================== #

# ------------------------------------------------------------------ #
# 公共函数
# ------------------------------------------------------------------ #


def encode_base64(text: str) -> str:
    """将文本编码为 Base64 字符串。"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_base64(text: str) -> str:
    """将 Base64 字符串解码为文本。"""
    return base64.b64decode(text, validate=True).decode("utf-8")


def encode_url(text: str) -> str:
    """对文本进行 URL 编码（百分号编码）。"""
    return urllib.parse.quote(text, safe="")


def decode_url(text: str) -> str:
    """对 URL 编码的文本进行解码。"""
    return urllib.parse.unquote(text)


def encode_hex(text: str) -> str:
    """将文本编码为十六进制字符串。"""
    return text.encode("utf-8").hex()


def decode_hex(text: str) -> str:
    """将十六进制字符串解码为文本。"""
    return bytes.fromhex(text).decode("utf-8")


def rot13(text: str) -> str:
    """对文本执行 ROT13 转换（自逆，编解码同一函数）。"""
    return codecs.encode(text, "rot_13")


def escape_html(text: str) -> str:
    """对文本进行 HTML 转义（<、>、&、"、'）。"""
    return html.escape(text)


def unescape_html(text: str) -> str:
    """对 HTML 转义的文本进行反转义。"""
    return html.unescape(text)


# ------------------------------------------------------------------ #
# 动作：5 个子命令
# ------------------------------------------------------------------ #


@action(
    "codetool_base64",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def _codetool_base64(text: str, decode: bool = False) -> None:
    """对文本执行 Base64 编码或解码。"""
    if decode:
        try:
            print(decode_base64(text))
        except (binascii.Error, UnicodeDecodeError) as exc:
            get_console().print(f"[red]错误:[/red] Base64 解码失败: {exc}")
    else:
        print(encode_base64(text))


@action(
    "codetool_url",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def _codetool_url(text: str, decode: bool = False) -> None:
    """对文本执行 URL 编码或解码。"""
    print(decode_url(text) if decode else encode_url(text))


@action(
    "codetool_hex",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时解码，否则编码（默认 False）",
    },
)
def _codetool_hex(text: str, decode: bool = False) -> None:
    """对文本执行十六进制编码或解码。"""
    if decode:
        try:
            print(decode_hex(text))
        except (ValueError, UnicodeDecodeError) as exc:
            get_console().print(f"[red]错误:[/red] Hex 解码失败: {exc}")
    else:
        print(encode_hex(text))


@action("codetool_rot13", param_help={"text": "待转换的文本"})
def _codetool_rot13(text: str) -> None:
    """对文本执行 ROT13 转换。"""
    print(rot13(text))


@action(
    "codetool_html",
    param_help={
        "text": "待处理的文本",
        "decode": "为 True 时反转义，否则转义（默认 False）",
    },
)
def _codetool_html(text: str, decode: bool = False) -> None:
    """对文本执行 HTML 转义或反转义。"""
    print(unescape_html(text) if decode else escape_html(text))


# ======================================================================== #
# convtool —— 长度 / 重量 / 温度 / 数据大小换算
# ======================================================================== #

# ------------------------------------------------------------------ #
# 公共数据与函数
# ------------------------------------------------------------------ #

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
    """长度换算。"""
    if from_unit not in _LENGTH_FACTORS:
        raise ValueError(f"不支持的长度单位: {from_unit}，支持: {sorted(_LENGTH_FACTORS)}")
    if to_unit not in _LENGTH_FACTORS:
        raise ValueError(f"不支持的长度单位: {to_unit}，支持: {sorted(_LENGTH_FACTORS)}")
    meters = value * _LENGTH_FACTORS[from_unit]
    return meters / _LENGTH_FACTORS[to_unit]


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
    """重量换算。"""
    if from_unit not in _WEIGHT_FACTORS:
        raise ValueError(f"不支持的重量单位: {from_unit}，支持: {sorted(_WEIGHT_FACTORS)}")
    if to_unit not in _WEIGHT_FACTORS:
        raise ValueError(f"不支持的重量单位: {to_unit}，支持: {sorted(_WEIGHT_FACTORS)}")
    grams = value * _WEIGHT_FACTORS[from_unit]
    return grams / _WEIGHT_FACTORS[to_unit]


_TEMP_UNITS = ("C", "F", "K")


def list_temperature_units() -> list[str]:
    """列出支持的温度单位。"""
    return list(_TEMP_UNITS)


def convert_temperature(value: float, from_unit: str, to_unit: str) -> float:
    """温度换算（支持 C/F/K 之间互转）。"""
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
    """数据大小换算。"""
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

    from_idx = _DATASIZE_UNITS.index(from_u)
    to_idx = _DATASIZE_UNITS.index(to_u)
    bytes_value = value * (factor**from_idx)
    return bytes_value / (factor**to_idx)


def _print_conversion(fn: Callable[..., float], *args: Any, **kwargs: Any) -> None:
    """执行换算函数并打印结果，捕获 ValueError 作为错误信息输出。"""
    try:
        result = fn(*args, **kwargs)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    print(result)


# ------------------------------------------------------------------ #
# 动作：4 个子命令
# ------------------------------------------------------------------ #


@action(
    "convtool_length",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（如 m/km/mile/ft/in）",
        "to_unit": "目标单位",
    },
)
def _convtool_length(value: float, from_unit: str, to_unit: str) -> None:
    """长度换算。"""
    _print_conversion(convert_length, value, from_unit, to_unit)


@action(
    "convtool_weight",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（如 g/kg/lb/oz）",
        "to_unit": "目标单位",
    },
)
def _convtool_weight(value: float, from_unit: str, to_unit: str) -> None:
    """重量换算。"""
    _print_conversion(convert_weight, value, from_unit, to_unit)


@action(
    "convtool_temp",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（C/F/K）",
        "to_unit": "目标单位（C/F/K）",
    },
)
def _convtool_temp(value: float, from_unit: str, to_unit: str) -> None:
    """温度换算。"""
    _print_conversion(convert_temperature, value, from_unit, to_unit)


@action(
    "convtool_datasize",
    param_help={
        "value": "待换算的数值",
        "from_unit": "源单位（B/KB/MB/GB/TB/PB）",
        "to_unit": "目标单位",
        "base": "进制基础（binary=1024 默认；decimal=1000）",
    },
)
def _convtool_datasize(
    value: float,
    from_unit: str,
    to_unit: str,
    base: str = "binary",
) -> None:
    """数据大小换算。"""
    _print_conversion(convert_datasize, value, from_unit, to_unit, base=base)


# ======================================================================== #
# urltool —— URL 解析 / 查询参数 / 基础 URL 提取
# ======================================================================== #

# ------------------------------------------------------------------ #
# 公共函数
# ------------------------------------------------------------------ #


def parse_url(url: str) -> dict[str, str]:
    """解析 URL 为各组成部分。"""
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    return {
        "scheme": parsed.scheme,
        "netloc": parsed.netloc,
        "path": parsed.path,
        "query": parsed.query,
        "fragment": parsed.fragment,
    }


def get_query_param(url: str, key: str) -> str | None:
    """提取 URL 查询参数值。"""
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qs(parsed.query)
    values = params.get(key)
    if not values:
        return None
    return values[0]


def add_query_param(url: str, key: str, value: str) -> str:
    """向 URL 添加查询参数（保留原参数，重复键追加）。"""
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    params = urllib.parse.parse_qsl(parsed.query)
    params.append((key, value))
    new_query = urllib.parse.urlencode(params)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def get_base_url(url: str) -> str:
    """提取基础 URL（仅 scheme://netloc）。"""
    if not url:
        raise ValueError("URL 不能为空")
    parsed = urllib.parse.urlsplit(url)
    if not parsed.scheme:
        raise ValueError(f"URL 缺少 scheme: {url!r}")
    if not parsed.netloc:
        raise ValueError(f"URL 缺少 netloc: {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


# ------------------------------------------------------------------ #
# 动作：4 个子命令
# ------------------------------------------------------------------ #


@action("urltool_parse", param_help={"url": "待解析的 URL 字符串"})
def _urltool_parse(url: str) -> None:
    """解析 URL 并逐行打印各组成部分。"""
    try:
        parts = parse_url(url)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    for key, value in parts.items():
        print(f"{key}: {value}")


@action(
    "urltool_query",
    param_help={
        "url": "URL 字符串",
        "key": "查询参数名",
    },
)
def _urltool_query(url: str, key: str) -> None:
    """提取 URL 查询参数值并打印。"""
    try:
        value = get_query_param(url, key)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    if value is None:
        print(f"参数不存在: {key}")
        return
    print(value)


@action(
    "urltool_addquery",
    param_help={
        "url": "原 URL 字符串",
        "key": "参数名",
        "value": "参数值",
    },
)
def _urltool_addquery(url: str, key: str, value: str) -> None:
    """向 URL 添加查询参数并打印结果。"""
    try:
        result = add_query_param(url, key, value)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    print(result)


@action("urltool_baseurl", param_help={"url": "完整 URL 字符串"})
def _urltool_baseurl(url: str) -> None:
    """提取 URL 的基础部分（scheme://netloc）并打印。"""
    try:
        result = get_base_url(url)
    except ValueError as exc:
        get_console().print(f"[red]错误:[/red] {exc}")
        return
    print(result)
