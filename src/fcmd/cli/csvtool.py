"""csvtool - CSV 处理工具。

基于标准库 ``csv``/``json`` 提供 CSV 文件预览、与 JSON 互转、按列筛选能力。

示例
----
    fcmd csvtool show data.csv                    # 预览前 5 行
    fcmd csvtool show data.csv --rows 10          # 预览前 10 行
    fcmd csvtool show data.csv --no-header        # 无表头模式
    fcmd csvtool to-json data.csv                 # CSV 转 JSON 打印
    fcmd csvtool to-json data.csv --indent 4      # 4 空格缩进
    fcmd csvtool from-json data.json --output out.csv
    fcmd csvtool select data.csv name age         # 按列筛选并重排
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import fcmd

__all__ = [
    "csv_to_json",
    "format_table",
    "json_to_csv",
    "read_csv",
    "select_columns",
    "write_csv",
]

# 表格输出中每列最大宽度，超出截断
_MAX_COL_WIDTH = 30


def read_csv(filepath: Path, has_header: bool = True) -> tuple[list[str] | None, list[list[str]]]:
    """读取 CSV 文件，返回表头与数据行。

    Parameters
    ----------
    filepath:
        CSV 文件路径
    has_header:
        是否将首行视为表头（默认 True）

    Returns
    -------
    tuple
        ``(header, rows)``：``header`` 为 None 表示无表头；``rows`` 为数据行列表

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
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
    """写入 CSV 文件。

    Parameters
    ----------
    filepath:
        目标 CSV 文件路径
    rows:
        数据行列表
    header:
        表头列表（可选，写入首行）
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if header is not None:
            writer.writerow(header)
        writer.writerows(rows)


def csv_to_json(filepath: Path, indent: int = 2) -> str:
    """CSV 转 JSON 字符串。

    首行作为表头，每行数据转为以表头为键的对象；无表头模式使用 ``col0``/``col1``/... 作键。

    Parameters
    ----------
    filepath:
        CSV 文件路径
    indent:
        JSON 缩进空格数（默认 2）

    Returns
    -------
    str
        JSON 字符串（数组）

    Raises
    ------
    FileNotFoundError
        文件不存在
    """
    header, rows = read_csv(filepath, has_header=True)
    if header is None:
        # 空文件
        return "[]"
    items: list[dict[str, str]] = []
    for row in rows:
        # 行长不足时补空字符串，超出时截断
        padded = row + [""] * (len(header) - len(row))
        items.append({header[i]: padded[i] for i in range(len(header))})
    return json.dumps(items, ensure_ascii=False, indent=indent)


def json_to_csv(filepath: Path) -> tuple[list[str], list[list[str]]]:
    """JSON 文件转 CSV 数据。

    要求 JSON 为对象数组，所有对象的键并集作为表头；缺失键补空字符串。

    Parameters
    ----------
    filepath:
        JSON 文件路径

    Returns
    -------
    tuple
        ``(header, rows)``

    Raises
    ------
    FileNotFoundError
        文件不存在
    ValueError
        JSON 顶层不是数组，或数组元素不是对象
    """
    if not filepath.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")
    with filepath.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("JSON 顶层必须是数组")
    # 收集所有键作为表头（保持首次出现顺序）
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
    """按列名筛选并重排列。

    Parameters
    ----------
    rows:
        数据行列表
    header:
        表头列表
    columns:
        欲保留的列名（按输出顺序）

    Returns
    -------
    tuple
        ``(new_header, new_rows)``

    Raises
    ------
    ValueError
        ``columns`` 中存在表头未包含的列名
    """
    # 列名 -> 索引
    index_map = {name: idx for idx, name in enumerate(header)}
    missing = [col for col in columns if col not in index_map]
    if missing:
        raise ValueError(f"列不存在: {', '.join(missing)}")
    indices = [index_map[col] for col in columns]
    new_rows = [[row[i] if i < len(row) else "" for i in indices] for row in rows]
    return (list(columns), new_rows)


def format_table(header: list[str] | None, rows: list[list[str]], max_width: int = _MAX_COL_WIDTH) -> str:
    """格式化为对齐的文本表格输出。

    每列宽度取表头与所有数据最大宽度，超过 ``max_width`` 截断并加省略号。

    Parameters
    ----------
    header:
        表头列表（可为 None）
    rows:
        数据行列表
    max_width:
        每列最大宽度（默认 30）

    Returns
    -------
    str
        对齐后的多行文本
    """
    if header is None and not rows:
        return "（空）"
    # 计算每列宽度
    all_rows = ([header] if header is not None else []) + rows
    num_cols = max(len(r) for r in all_rows)
    widths = [0] * num_cols
    for r in all_rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    # 截断过宽列
    widths = [min(w, max_width) for w in widths]

    def _format_row(r: list[str]) -> str:
        cells = []
        for i in range(num_cols):
            cell = r[i] if i < len(r) else ""
            if len(cell) > max_width:
                cell = cell[: max_width - 3] + "..."
            cells.append(cell.ljust(widths[i]))
        return "  ".join(cells).rstrip()

    lines = []
    if header is not None:
        lines.append(_format_row(header))
        lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append(_format_row(r))
    return "\n".join(lines)


@fcmd.tool("csvtool", subcommand="show", help="预览 CSV 前几行")
def csvtool_show(file: Path, rows: int = 5, header: bool = True) -> None:
    """预览 CSV 文件前 N 行，表格对齐输出。

    Parameters
    ----------
    file:
        CSV 文件路径
    rows:
        显示的行数（默认 5）
    header:
        首行是否为表头（默认 True，使用 ``--no-header`` 关闭）
    """
    try:
        hdr, data = read_csv(file, has_header=header)
    except FileNotFoundError as e:
        print(str(e))
        return
    preview_rows = data[:rows]
    print(format_table(hdr, preview_rows))
    print(f"\n共 {len(data)} 行（显示前 {len(preview_rows)} 行）")


@fcmd.tool("csvtool", subcommand="to-json", help="CSV 转 JSON")
def csvtool_to_json(file: Path, indent: int = 2) -> None:
    """将 CSV 转为 JSON 并打印到标准输出。

    Parameters
    ----------
    file:
        CSV 文件路径
    indent:
        JSON 缩进空格数（默认 2）
    """
    try:
        text = csv_to_json(file, indent=indent)
    except FileNotFoundError as e:
        print(str(e))
        return
    print(text)


@fcmd.tool("csvtool", subcommand="from-json", help="JSON 转 CSV")
def csvtool_from_json(file: Path, output: str = "") -> None:
    """将 JSON 数组转为 CSV 文件。

    Parameters
    ----------
    file:
        JSON 文件路径
    output:
        输出 CSV 路径（默认: 同名 ``.csv`` 文件）
    """
    try:
        header, rows = json_to_csv(file)
    except (FileNotFoundError, ValueError) as e:
        print(str(e))
        return
    out_path = Path(output) if output else file.with_suffix(".csv")
    write_csv(out_path, rows, header=header)
    print(f"转换完成: {file} -> {out_path}（{len(rows)} 行）")


@fcmd.tool("csvtool", subcommand="select", help="按列筛选 CSV")
def csvtool_select(file: Path, columns: list[str], output: str = "") -> None:
    """按列名筛选并重排 CSV，输出到文件或打印。

    Parameters
    ----------
    file:
        CSV 文件路径
    columns:
        欲保留的列名（按输出顺序）
    output:
        输出 CSV 路径（默认: 打印到标准输出）
    """
    try:
        header, rows = read_csv(file, has_header=True)
    except FileNotFoundError as e:
        print(str(e))
        return
    if header is None:
        print("CSV 文件为空")
        return
    try:
        new_header, new_rows = select_columns(rows, header, columns)
    except ValueError as e:
        print(str(e))
        return
    if output:
        write_csv(Path(output), new_rows, header=new_header)
        print(f"筛选完成: {file} -> {output}（{len(new_rows)} 行）")
    else:
        print(format_table(new_header, new_rows))
