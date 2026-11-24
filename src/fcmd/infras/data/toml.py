"""``fcmd.infras.data.toml``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from fcmd.infras import _toml as tomllib

from .csv import _require_file

__all__ = ["format_toml", "get_toml_value", "keys_toml", "read_toml", "to_toml", "validate_toml"]

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def read_toml(filepath: Path) -> dict[str, Any]:
    """读取 TOML 文件并解析为字典。"""
    _require_file(filepath)
    with filepath.open("rb") as f:
        return tomllib.load(f)


def get_toml_value(filepath: Path, key: str) -> Any:
    """按点路径获取 TOML 文件中指定键的值。"""
    data = read_toml(filepath)
    result: Any = data
    for part in key.split("."):
        if not isinstance(result, dict):
            raise KeyError(f"键 {key!r} 不存在：{part!r} 处不是字典")
        if part not in result:
            raise KeyError(f"键 {key!r} 不存在：{part!r} 未找到")
        result = result[part]
    return result


def keys_toml(filepath: Path) -> list[str]:
    """列出 TOML 文件的顶层键。"""
    return list(read_toml(filepath).keys())


def format_toml(filepath: Path, indent: int = 2) -> str:
    """将 TOML 文件转为 JSON 格式化字符串。"""
    return json.dumps(read_toml(filepath), indent=indent, ensure_ascii=False, default=str)


def validate_toml(filepath: Path) -> None:
    """校验 TOML 文件语法是否正确。"""
    read_toml(filepath)


def _fmt_key(key: str) -> str:
    """格式化键名：裸键原样输出，其余用基础字符串。"""
    return key if _BARE_KEY.match(key) else _quote(key)


def _quote(value: str) -> str:
    """把字符串渲染为 TOML 基础字符串（双引号）。"""
    parts: list[str] = []
    for char in value:
        escaped = _ESCAPES.get(char)
        if escaped is not None:
            parts.append(escaped)
        elif char < " " or char == "\x7f":
            parts.append(f"\\u{ord(char):04X}")
        else:
            parts.append(char)
    return '"' + "".join(parts) + '"'


def _fmt_number(value: int | float) -> str:
    """渲染整数与浮点数（含 ``nan`` / ``inf`` 字面量）。"""
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    return str(value)


def _fmt_value(value: Any) -> str:
    """把单个 TOML 值渲染为文本（标量或数组）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _fmt_number(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt_value(item) for item in value) + "]"
    raise ValueError(f"TOML 无法表示的类型 {type(value).__name__}: {value!r}")


def _emit_table(lines: list[str], table: dict[str, Any], path: tuple[str, ...]) -> None:
    """按 TOML 顺序输出一张表：先标量键，再子表，最后表数组。"""
    subtables: list[tuple[str, dict[str, Any]]] = []
    arrays: list[tuple[str, list[dict[str, Any]]]] = []
    for key, value in table.items():
        if not isinstance(key, str):
            raise ValueError(f"TOML 键必须是字符串，得到 {type(key).__name__}: {key!r}")
        if isinstance(value, dict):
            subtables.append((key, value))
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            arrays.append((key, value))
        elif value is None:
            raise ValueError(f"键 {key!r} 的值为空（None），TOML 无法表示")
        else:
            lines.append(f"{_fmt_key(key)} = {_fmt_value(value)}")
    for key, sub in subtables:
        header = ".".join(_fmt_key(part) for part in (*path, key))
        lines.extend(["", f"[{header}]"])
        _emit_table(lines, sub, (*path, key))
    for key, items in arrays:
        header = ".".join(_fmt_key(part) for part in (*path, key))
        for item in items:
            lines.extend(["", f"[[{header}]]"])
            _emit_table(lines, item, (*path, key))


def to_toml(data: Any) -> str:
    """把映射序列化为 TOML 文本（空映射返回空串，其余以换行结尾）。

    支持标量、数组、嵌套映射（子表）与「映射数组」（表数组）；``None``、
    非字符串键等 TOML 无法表达的形态抛 ``ValueError``。
    """
    if not isinstance(data, dict):
        raise ValueError(f"TOML 顶层必须是映射（dict），得到 {type(data).__name__}")
    lines: list[str] = []
    _emit_table(lines, data, ())
    text = "\n".join(lines).strip("\n")
    return f"{text}\n" if text else ""
