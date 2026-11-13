"""textdiff —— 文本文件 unified diff 与目录差异比较。"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_error, emit_result
from fcmd.infras import text as textinfra

__all__: list[str] = []


@fx.tool(
    "textdiff",
    subcommand="file",
    help="比较两个文本文件",
    param_help={
        "file1": "待比较文件路径",
        "file2": "待比较文件路径",
        "context": "上下文行数（默认 3）",
        "color": "启用 ANSI 彩色输出（默认关闭；--json 时忽略）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd textdiff file a.txt b.txt",
        "fcmd textdiff file a.txt b.txt --context 5",
        "fcmd textdiff file a.txt b.txt --json",
    ],
)
def file(  # noqa: PLR0911 - 两个文件分别校验，错误分支提前返回
    file1: Path, file2: Path, context: int = 3, color: bool = False, json_output: bool = False
) -> None:
    """输出两个文本文件的 unified diff。"""
    if not file1.exists():
        if json_output:
            emit_error(
                "textdiff",
                "file",
                code="FILE_NOT_FOUND",
                message=f"文件不存在: {file1}",
                field="file1",
                expected="已存在的文件",
                hint="确认第一个文件路径存在",
            )
            return
        print(f"文件不存在: {file1}")
        return
    if not file2.exists():
        if json_output:
            emit_error(
                "textdiff",
                "file",
                code="FILE_NOT_FOUND",
                message=f"文件不存在: {file2}",
                field="file2",
                expected="已存在的文件",
                hint="确认第二个文件路径存在",
            )
            return
        print(f"文件不存在: {file2}")
        return
    try:
        result = textinfra.compare_files_result(file1, file2, context)
    except ValueError as exc:
        if json_output:
            emit_error(
                "textdiff",
                "file",
                code="NOT_TEXT",
                message=f"错误: {exc}",
                field="file1",
                expected="UTF-8 文本文件",
                hint="确认两个文件都是文本文件（二进制文件不支持比较）",
            )
            return
        print(f"错误: {exc}")
        return
    if json_output:
        emit_result("textdiff", "file", result)
        return
    if result["equal"]:
        print("文件内容相同")
        return
    diff = str(result["diff"])
    if color:
        diff = textinfra.colorize_diff(diff)
    print(diff, end="")


@fx.tool(
    "textdiff",
    subcommand="dir",
    help="比较两个目录",
    param_help={
        "dir1": "待比较目录路径",
        "dir2": "待比较目录路径",
        "pattern": "文件名 glob 模式（默认所有文件）",
        "recursive": "是否递归比较子目录（默认 True）",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd textdiff dir src/old src/new",
        'fcmd textdiff dir src/old src/new --pattern "*.py"',
        "fcmd textdiff dir src/old src/new --json",
    ],
)
def dir(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True, json_output: bool = False) -> None:
    """列出两个目录中差异文件。"""
    if not dir1.is_dir():
        if json_output:
            emit_error(
                "textdiff",
                "dir",
                code="DIR_NOT_FOUND",
                message=f"目录不存在: {dir1}",
                field="dir1",
                expected="已存在的目录",
                hint="确认第一个目录路径存在",
            )
            return
        print(f"目录不存在: {dir1}")
        return
    if not dir2.is_dir():
        if json_output:
            emit_error(
                "textdiff",
                "dir",
                code="DIR_NOT_FOUND",
                message=f"目录不存在: {dir2}",
                field="dir2",
                expected="已存在的目录",
                hint="确认第二个目录路径存在",
            )
            return
        print(f"目录不存在: {dir2}")
        return
    result = textinfra.compare_directories_result(dir1, dir2, pattern, recursive)
    if json_output:
        emit_result("textdiff", "dir", result)
        return
    print(textinfra.format_directory_result(result, dir1, dir2))
