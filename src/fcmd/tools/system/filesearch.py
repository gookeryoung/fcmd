"""filesearch - 按文件名或内容递归搜索目录。"""

from __future__ import annotations

import re
from pathlib import Path

import fcmd as fx
from fcmd.core.result import emit_error, emit_result
from fcmd.infras.fs import search_by_content, search_by_name

__all__ = ["content", "name"]


@fx.tool(
    "filesearch",
    subcommand="name",
    help="按文件名 glob 搜索",
    param_help={
        "directory": "搜索根目录",
        "pattern": "文件名 glob 模式（fnmatch 语法，如 *.py）",
        "include_dirs": "是否同时返回匹配的目录",
        "limit": "最多返回多少条（0 表示不限）",
        "count": "只输出匹配总数，不列出路径",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        'fcmd filesearch name . "*.py"',
        'fcmd filesearch name . "*.py" --limit 20',
        'fcmd filesearch name . "*.py" --count',
        'fcmd filesearch name . "*.py" --json',
    ],
)
def name(  # noqa: PLR0913, PLR0917 - CLI 参数由 argparse 逐个生成，保持扁平签名
    directory: Path,
    pattern: str,
    include_dirs: bool = False,
    limit: int = 0,
    count: bool = False,
    json_output: bool = False,
) -> None:
    """按文件名 glob 模式递归搜索目录。"""
    try:
        results = search_by_name(directory, pattern, include_dirs=include_dirs)
    except (FileNotFoundError, NotADirectoryError) as exc:
        if json_output:
            emit_error(
                "filesearch",
                "name",
                code="DIR_NOT_FOUND",
                message=str(exc),
                field="directory",
                expected="已存在的目录",
                hint="确认路径存在且是目录",
            )
            return
        print(f"错误: {exc}")
        return
    total = len(results)
    if count:
        if json_output:
            emit_result("filesearch", "name", {"total": total})
        else:
            print(total)
        return
    shown = results[:limit] if limit > 0 else results
    if json_output:
        emit_result(
            "filesearch",
            "name",
            {
                "matches": [str(path) for path in shown],
                "total": total,
                "returned": len(shown),
                "truncated": len(shown) < total,
            },
        )
        return
    if not results:
        print("（无匹配）")
        return
    for path in shown:
        print(path)
    if len(shown) < total:
        print(f"（共 {total} 条，已显示前 {len(shown)} 条）")


@fx.tool(
    "filesearch",
    subcommand="content",
    help="按内容正则搜索",
    param_help={
        "directory": "搜索根目录",
        "pattern": "正则表达式",
        "extension": "限定扩展名（如 .py），空串不限",
        "limit": "最多返回多少条（0 表示不限）",
        "count": "只输出匹配总数，不列出命中行",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        'fcmd filesearch content . "TODO" --extension .py',
        'fcmd filesearch content . "def main" --limit 10',
        'fcmd filesearch content . "TODO" --count',
        'fcmd filesearch content . "TODO" --json',
    ],
)
def content(  # noqa: PLR0911, PLR0913, PLR0917 - CLI 参数逐个生成，错误/计数分支提前返回
    directory: Path,
    pattern: str,
    extension: str = "",
    limit: int = 0,
    count: bool = False,
    json_output: bool = False,
) -> None:
    """按文件内容正则递归搜索，输出 文件:行号:行内容。"""
    try:
        results = search_by_content(directory, pattern, extension=extension)
    except (FileNotFoundError, NotADirectoryError) as exc:
        if json_output:
            emit_error(
                "filesearch",
                "content",
                code="DIR_NOT_FOUND",
                message=str(exc),
                field="directory",
                expected="已存在的目录",
                hint="确认路径存在且是目录",
            )
            return
        print(f"错误: {exc}")
        return
    except re.error as exc:
        if json_output:
            emit_error(
                "filesearch",
                "content",
                code="INVALID_REGEX",
                message=f"正则表达式错误: {exc}",
                field="pattern",
                expected="Python 正则表达式",
                hint="检查括号、转义与量词写法",
            )
            return
        print(f"错误: 正则表达式错误: {exc}")
        return
    total = len(results)
    if count:
        if json_output:
            emit_result("filesearch", "content", {"total": total})
        else:
            print(total)
        return
    shown = results[:limit] if limit > 0 else results
    if json_output:
        emit_result(
            "filesearch",
            "content",
            {
                "matches": [{"path": str(path), "line": lineno, "text": line} for path, lineno, line in shown],
                "total": total,
                "returned": len(shown),
                "truncated": len(shown) < total,
            },
        )
        return
    if not results:
        print("（无匹配）")
        return
    for path, lineno, line in shown:
        print(f"{path}:{lineno}:{line}")
    if len(shown) < total:
        print(f"（共 {total} 条，已显示前 {len(shown)} 条）")
