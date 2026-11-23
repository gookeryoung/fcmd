"""``fcmd.infras.data.toml``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fcmd.infras import _toml as tomllib

from .csv import _require_file

__all__ = ["format_toml", "get_toml_value", "keys_toml", "read_toml", "validate_toml"]


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
