"""csvtool - CSV 预览、转 JSON、列筛选与多文件合并。

示例
----
    fcmd csvtool show data.csv --rows 3
    fcmd csvtool to-json data.csv
    fcmd csvtool from-json data.json --output data.csv
    fcmd csvtool select data.csv name city
    fcmd csvtool merge a.csv b.csv --mode intersection
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_error, emit_result
from fcmd.infras import data


@fx.tool(
    "csvtool",
    subcommand="show",
    help="预览 CSV 前几行",
    param_help={
        "file": "CSV 文件路径",
        "rows": "显示的行数（默认 5）",
        "header": "首行是否为表头（默认 True，使用 --no-header 关闭）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd csvtool show data.csv",
        "fcmd csvtool show data.csv --rows 3",
        "fcmd csvtool show data.csv --json",
    ],
)
def show(file: Path, rows: int = 5, header: bool = True, json_output: bool = False) -> None:
    """预览 CSV 前几行。"""
    try:
        hdr, rows_data = data.read_csv(file, has_header=header)
    except FileNotFoundError as exc:
        if json_output:
            emit_error(
                "csvtool",
                "show",
                code="FILE_NOT_FOUND",
                message=str(exc),
                field="file",
                expected="已存在的 CSV 文件",
                hint="确认路径存在且是文件",
            )
            return
        print(f"错误: {exc}")
        return
    preview_rows = rows_data[:rows]
    if json_output:
        # 与默认路径同源：空文件/无表头也照常返回结构，用 total_rows 与 columns 表达
        # 「没有数据 / 没有列名」，不制造只存在于机读形态里的错误。
        emit_result(
            "csvtool",
            "show",
            {
                "file": str(file),
                "rows": rows,
                "total_rows": len(rows_data),
                "returned": len(preview_rows),
                "truncated": len(preview_rows) < len(rows_data),
                "columns": hdr or [],
                "data": preview_rows,
            },
        )
        return
    print(data.format_table(hdr, preview_rows))
    print(f"\n共 {len(rows_data)} 行（显示前 {len(preview_rows)} 行）")


@fx.tool(
    "csvtool",
    subcommand="to-json",
    help="CSV 转 JSON",
    param_help={
        "file": "CSV 文件路径",
        "indent": "JSON 缩进空格数（默认 2）",
    },
)
def to_json(file: Path, indent: int = 2) -> None:
    """CSV 转 JSON。"""
    try:
        print(data.csv_to_json(file, indent=indent))
    except FileNotFoundError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "csvtool",
    subcommand="from-json",
    help="JSON 转 CSV",
    param_help={
        "file": "JSON 文件路径",
        "output": "输出 CSV 路径（默认: 同名 .csv 文件）",
    },
)
def from_json(file: Path, output: str = "") -> None:
    """JSON 转 CSV。"""
    try:
        header, rows = data.json_to_csv(file)
        out_path = Path(output) if output else file.with_suffix(".csv")
        data.write_csv(out_path, rows, header=header)
        print(f"转换完成: {file} -> {out_path}（{len(rows)} 行）")
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "csvtool",
    subcommand="select",
    help="按列筛选 CSV",
    param_help={
        "file": "CSV 文件路径",
        "columns": "欲保留的列名（按输出顺序）",
        "output": "输出 CSV 路径（默认: 打印到标准输出）",
    },
)
def select(file: Path, columns: list[str], output: str = "") -> None:
    """按列筛选 CSV。"""
    try:
        header, rows = data.read_csv(file, has_header=True)
        if header is None:
            print("CSV 文件为空")
            return
        new_header, new_rows = data.select_columns(rows, header, columns)
        if output:
            data.write_csv(Path(output), new_rows, header=new_header)
            print(f"筛选完成: {file} -> {output}（{len(new_rows)} 行）")
        else:
            print(data.format_table(new_header, new_rows))
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "csvtool",
    subcommand="merge",
    help="合并多个 CSV",
    param_help={
        "files": "CSV 文件路径列表（至少 2 个）",
        "mode": "合并模式（union 或 intersection，默认 union）",
        "output": "输出 CSV 路径（默认: 打印到标准输出）",
    },
)
def merge(files: list[Path], mode: str = "union", output: str = "") -> None:
    """合并多个 CSV。"""
    try:
        header, rows = data.merge_csvs(files, mode=mode)
        if output:
            data.write_csv(Path(output), rows, header=header)
            print(f"合并完成: {len(files)} 个文件 -> {output}（{len(rows)} 行）")
        else:
            print(data.format_table(header, rows))
    except (ValueError, FileNotFoundError) as exc:
        print(f"错误: {exc}")
