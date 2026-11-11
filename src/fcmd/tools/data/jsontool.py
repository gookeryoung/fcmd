"""jsontool - JSON 格式化、压缩、点路径查询与按键排序。

示例
----
    fcmd jsontool pretty data.json --indent 4
    fcmd jsontool minify data.json
    fcmd jsontool query data.json a.b.0.c
    fcmd jsontool sort data.json --output sorted.json
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras import data

__tool_aliases__ = ["jsont"]


@fx.tool(
    "jsontool",
    subcommand="pretty",
    help="格式化打印 JSON",
    param_help={
        "file": "JSON 文件路径",
        "indent": "缩进空格数（默认 2）",
    },
)
def pretty(file: Path, indent: int = 2) -> None:
    """格式化打印 JSON。"""
    try:
        print(data.pretty_json(data.read_json(file), indent=indent))
    except FileNotFoundError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "jsontool",
    subcommand="minify",
    help="压缩 JSON 为单行",
    param_help={"file": "JSON 文件路径"},
)
def minify(file: Path) -> None:
    """压缩 JSON 为单行。"""
    try:
        print(data.minify_json(data.read_json(file)))
    except FileNotFoundError as exc:
        print(f"错误: {exc}")


@fx.tool(
    "jsontool",
    subcommand="query",
    help="按点路径查询 JSON",
    param_help={
        "file": "JSON 文件路径",
        "path": "点分路径（如 a.b.0.c）",
    },
)
def query(file: Path, path: str) -> None:
    """按点路径查询 JSON。"""
    try:
        result = data.query_json(data.read_json(file), path)
        if isinstance(result, (dict, list)):
            print(data.pretty_json(result))
        else:
            print(result)
    except (FileNotFoundError, KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"错误: {exc}")


@fx.tool(
    "jsontool",
    subcommand="sort",
    help="按键排序 JSON",
    param_help={
        "file": "JSON 文件路径",
        "output": "输出 JSON 路径（默认: 打印到标准输出）",
    },
)
def sort(file: Path, output: str = "") -> None:
    """按键排序 JSON。"""
    try:
        sorted_data = data.sort_keys(data.read_json(file))
        if output:
            data.write_json(Path(output), sorted_data)
            print(f"排序完成: {file} -> {output}")
        else:
            print(data.pretty_json(sorted_data))
    except FileNotFoundError as exc:
        print(f"错误: {exc}")
