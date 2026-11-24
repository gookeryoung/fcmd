"""txttool —— 文本文件行数/词数/字符数统计、排序、去重与大小写转换。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import text as textinfra

__all__: list[str] = []


def _require_file(subcommand: str, path: str, json_output: bool) -> Path | None:
    """检查文件存在；不存在时呈现错误并返回 ``None``。"""
    file_path = Path(path)
    if file_path.is_file():
        return file_path
    report_error(
        "txttool",
        subcommand,
        code="FILE_NOT_FOUND",
        message=f"文件不存在: {file_path}",
        field="path",
        expected="存在的文本文件",
        json_output=json_output,
    )
    return None


@fx.tool(
    "txttool",
    subcommand="count",
    help="统计行/词/字符/字节数",
    param_help={"path": "目标文件路径"},
)
def count(path: str, json_output: bool = False) -> None:
    """统计文本文件的行数、单词数、字符数与字节数（磁盘大小）。"""
    file_path = _require_file("count", path, json_output)
    if file_path is None:
        return
    stats: dict[str, int] = dict(textinfra.count_text(textinfra.read_text_file(file_path)))
    stats["bytes"] = file_path.stat().st_size
    if json_output:
        emit_result("txttool", "count", stats)
        return
    print(f"行数: {stats['lines']}")
    print(f"词数: {stats['words']}")
    print(f"字符数: {stats['chars']}")
    print(f"字节数: {stats['bytes']}")


@fx.tool(
    "txttool",
    subcommand="sort",
    help="排序文本行",
    param_help={
        "path": "目标文件路径",
        "reverse": "是否逆序排序（默认 False）",
    },
)
def sort(path: str, reverse: bool = False, json_output: bool = False) -> None:
    """对文本文件的行排序。"""
    file_path = _require_file("sort", path, json_output)
    if file_path is None:
        return
    result = textinfra.sort_lines(textinfra.read_text_file(file_path), reverse=reverse)
    if json_output:
        emit_result("txttool", "sort", {"result": result})
        return
    print(result)


@fx.tool(
    "txttool",
    subcommand="unique",
    help="去重文本行",
    param_help={"path": "目标文件路径"},
)
def unique(path: str, json_output: bool = False) -> None:
    """去重文本文件的行（保持首次出现的顺序）。"""
    file_path = _require_file("unique", path, json_output)
    if file_path is None:
        return
    result = textinfra.unique_lines(textinfra.read_text_file(file_path))
    if json_output:
        emit_result("txttool", "unique", {"result": result})
        return
    print(result)


@fx.tool(
    "txttool",
    subcommand="case",
    help="大小写转换",
    param_help={
        "path": "目标文件路径",
        "mode": "转换模式（upper/lower/title/capitalize/swapcase，默认 upper）",
    },
)
def case(path: str, mode: str = "upper", json_output: bool = False) -> None:
    """转换文本文件的大小写。"""
    file_path = _require_file("case", path, json_output)
    if file_path is None:
        return
    try:
        result = textinfra.convert_case(textinfra.read_text_file(file_path), mode)
    except ValueError as error:
        report_error(
            "txttool",
            "case",
            code="INVALID_ARGUMENT",
            message=str(error),
            field="mode",
            expected="upper/lower/title/capitalize/swapcase",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("txttool", "case", {"result": result})
        return
    print(result)
