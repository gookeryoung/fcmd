"""编解码与转换基础件（纯函数，无输出副作用）。

* 字符串编解码：Base64 / URL / Hex / ROT13 / HTML
* 单位换算：长度 / 重量 / 温度 / 数据大小
* URL 解析：组成部分 / 查询参数 / 基础 URL

由 ``fcmd.tools.crypto`` 下的 codetool / convtool / urltool 做参数适配与输出。
"""

from __future__ import annotations

import base64
import codecs
import html
import urllib.parse

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
# 字符串编解码
# ======================================================================== #


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


# ======================================================================== #
# 单位换算
# ======================================================================== #

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


# ======================================================================== #
# URL
# ======================================================================== #


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
