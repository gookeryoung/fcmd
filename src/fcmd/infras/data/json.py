"""``fcmd.infras.data.json``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .csv import _require_file

__all__ = ["minify_json", "pretty_json", "query_json", "read_json", "sort_keys", "write_json"]


def read_json(filepath: Path) -> Any:
    """读取 JSON 文件并解析为 Python 对象。"""
    _require_file(filepath)
    with filepath.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(filepath: Path, data: Any, indent: int = 2) -> None:
    """写入 JSON 文件。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def pretty_json(data: Any, indent: int = 2) -> str:
    """格式化为多行 JSON 字符串。"""
    return json.dumps(data, ensure_ascii=False, indent=indent)


def minify_json(data: Any) -> str:
    """压缩为单行 JSON 字符串（无空白）。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def query_json(data: Any, path: str) -> Any:
    """按点路径查询 JSON 对象。"""
    if not path:
        return data
    segments = path.split(".")
    if any(seg == "" for seg in segments):
        raise ValueError(f"路径格式错误（含空段）: {path}")
    current: Any = data
    for seg in segments:
        if isinstance(current, list):
            try:
                idx = int(seg)
            except ValueError as exc:
                raise TypeError(f"列表索引必须是整数，得到: {seg}") from exc
            if idx < 0 or idx >= len(current):
                raise IndexError(f"列表索引越界: {idx}（长度 {len(current)}）")
            current = current[idx]
        elif isinstance(current, dict):
            if seg not in current:
                raise KeyError(f"键不存在: {seg}")
            current = current[seg]
        else:
            raise TypeError(f"无法对非容器类型 {type(current).__name__} 取子项: {seg}")
    return current


def sort_keys(data: Any) -> Any:
    """递归按键名排序（仅影响对象，不影响数组顺序）。"""
    if isinstance(data, dict):
        return {key: sort_keys(data[key]) for key in sorted(data)}
    if isinstance(data, list):
        return [sort_keys(item) for item in data]
    return data
