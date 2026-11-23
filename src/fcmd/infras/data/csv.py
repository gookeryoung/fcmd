"""``fcmd.infras.data.csv``（由 ``infras/data.py`` 拆分）。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

__all__ = ["csv_to_json", "format_table", "json_to_csv", "merge_csvs", "read_csv", "select_columns", "write_csv"]


def _require_file(filepath: Path) -> None:
    """校验文件存在，缺失时抛 ``FileNotFoundError``（消息含路径）。"""
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")


_MAX_COL_WIDTH = 30


def read_csv(filepath: Path, has_header: bool = True) -> tuple[list[str] | None, list[list[str]]]:
    """读取 CSV 文件，返回表头与数据行。"""
    _require_file(filepath)
    with filepath.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        all_rows = list(reader)
    if not all_rows:
        return (None, [])
    if has_header:
        header = all_rows[0]
        return (header, all_rows[1:])
    return (None, all_rows)


def write_csv(filepath: Path, rows: list[list[str]], header: list[str] | None = None) -> None:
    """写入 CSV 文件。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header is not None:
            writer.writerow(header)
        writer.writerows(rows)


def csv_to_json(filepath: Path, indent: int = 2) -> str:
    """CSV 转 JSON 字符串。"""
    header, rows = read_csv(filepath, has_header=True)
    if header is None:
        return "[]"
    items: list[dict[str, str]] = []
    for row in rows:
        padded = row + [""] * (len(header) - len(row))
        items.append({header[i]: padded[i] for i in range(len(header))})
    return json.dumps(items, ensure_ascii=False, indent=indent)


def json_to_csv(filepath: Path) -> tuple[list[str], list[list[str]]]:
    """JSON 文件转 CSV 数据。"""
    _require_file(filepath)
    with filepath.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("JSON 顶层必须是数组")
    header_keys: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("JSON 数组元素必须是对象")
        for key in item:
            if key not in seen:
                seen.add(key)
                header_keys.append(key)
    rows = [[str(item.get(key, "")) for key in header_keys] for item in data]
    return (header_keys, rows)


def select_columns(rows: list[list[str]], header: list[str], columns: list[str]) -> tuple[list[str], list[list[str]]]:
    """按列名筛选并重排列。"""
    index_map = {name: idx for idx, name in enumerate(header)}
    missing = [col for col in columns if col not in index_map]
    if missing:
        raise ValueError(f"列不存在: {', '.join(missing)}")
    indices = [index_map[col] for col in columns]
    new_rows = [[row[i] if i < len(row) else "" for i in indices] for row in rows]
    return (list(columns), new_rows)


def _merge_headers_union(headers: list[list[str]]) -> list[str]:
    """计算 union 模式的合并表头（保持首次出现顺序）。"""
    merged: list[str] = []
    seen: set[str] = set()
    for header in headers:
        for col in header:
            if col not in seen:
                seen.add(col)
                merged.append(col)
    return merged


def _merge_headers_intersection(headers: list[list[str]]) -> list[str]:
    """计算 intersection 模式的合并表头（按第一个 CSV 顺序，仅保留共有列）。"""
    if not headers[0]:
        return []
    common: set[str] = set(headers[0])
    for header in headers[1:]:
        common &= set(header)
    return [col for col in headers[0] if col in common]


def merge_csvs(files: list[Path], mode: str = "union") -> tuple[list[str], list[list[str]]]:
    """合并多个 CSV 文件。"""
    if len(files) < 2:
        raise ValueError("合并至少需要 2 个 CSV 文件")
    if mode not in ("union", "intersection"):
        raise ValueError(f"不支持的合并模式: {mode}（可选: union/intersection）")

    headers: list[list[str]] = []
    all_rows: list[list[list[str]]] = []
    for filepath in files:
        header, rows = read_csv(filepath, has_header=True)
        headers.append(header if header is not None else [])
        all_rows.append(rows)

    merged_header = _merge_headers_union(headers) if mode == "union" else _merge_headers_intersection(headers)

    merged_rows: list[list[str]] = []
    for header, rows in zip(headers, all_rows, strict=True):
        index_map = {name: idx for idx, name in enumerate(header)}
        indices: list[int | None] = [index_map.get(col) for col in merged_header]
        for row in rows:
            merged_rows.append([row[i] if i is not None and i < len(row) else "" for i in indices])

    return (merged_header, merged_rows)


def format_table(header: list[str] | None, rows: list[list[str]], max_width: int = _MAX_COL_WIDTH) -> str:
    """格式化为对齐的文本表格输出。"""
    if header is None and not rows:
        return "（空）"
    all_rows = ([header] if header is not None else []) + rows
    num_cols = max(len(row) for row in all_rows)
    widths = [0] * num_cols
    for row in all_rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    widths = [min(width, max_width) for width in widths]

    def _format_row(row: list[str]) -> str:
        cells = []
        for i in range(num_cols):
            cell = row[i] if i < len(row) else ""
            if len(cell) > max_width:
                cell = cell[: max_width - 3] + "..."
            cells.append(cell.ljust(widths[i]))
        return "  ".join(cells).rstrip()

    lines = []
    if header is not None:
        lines.append(_format_row(header))
        lines.append("  ".join("-" * width for width in widths))
    lines.extend(_format_row(row) for row in rows)
    return "\n".join(lines)
