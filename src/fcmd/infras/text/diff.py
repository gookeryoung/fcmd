"""``fcmd.infras.text.diff``（由 ``infras/text.py`` 拆分）。"""

from __future__ import annotations

import difflib
import filecmp
from pathlib import Path
from typing import Any

from fcmd.infras.textio import read_text_lines

__all__ = [
    "colorize_diff",
    "compare_directories",
    "compare_directories_result",
    "compare_files",
    "compare_files_result",
    "diff_stats",
    "format_directory_result",
    "read_lines",
]


_RED = "\033[31m"


_GREEN = "\033[32m"


_CYAN = "\033[36m"


_RESET = "\033[0m"


def read_lines(filepath: Path) -> list[str]:
    """读取文本文件行列表（内部 helper，复用中立文本 IO）。"""
    return read_text_lines(filepath, binary_message="二进制文件不支持比较")


def colorize_diff(diff_text: str) -> str:
    """为 unified diff 文本添加 ANSI 颜色。"""
    lines = diff_text.splitlines(keepends=True)
    result: list[str] = []
    for line in lines:
        if line.startswith(("---", "+++")):
            result.append(line)
        elif line.startswith("-"):
            result.append(f"{_RED}{line}{_RESET}")
        elif line.startswith("+"):
            result.append(f"{_GREEN}{line}{_RESET}")
        elif line.startswith("@@"):
            result.append(f"{_CYAN}{line}{_RESET}")
        else:
            result.append(line)
    return "".join(result)


def compare_files(file1: Path, file2: Path, context: int = 3) -> str:
    """比较两个文本文件，返回 unified diff 字符串。"""
    lines1 = read_lines(file1)
    lines2 = read_lines(file2)
    diff = difflib.unified_diff(lines1, lines2, fromfile=str(file1), tofile=str(file2), n=context)
    return "".join(diff)


def diff_stats(diff_text: str) -> dict[str, int]:
    """统计 unified diff 的增删行数与 hunk 数（不改变 diff 原文）。"""
    added = 0
    removed = 0
    hunks = 0
    for line in diff_text.splitlines():
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        elif line.startswith("@@"):
            hunks += 1
    return {"added": added, "removed": removed, "hunks": hunks}


def compare_files_result(file1: Path, file2: Path, context: int = 3) -> dict[str, Any]:
    """比较两个文本文件，返回结构化结果（diff 原文 + 增删统计）。"""
    diff = compare_files(file1, file2, context)
    return {"equal": not diff, "diff": diff, **diff_stats(diff)}


def compare_directories_result(
    dir1: Path,
    dir2: Path,
    pattern: str = "*",
    recursive: bool = True,
) -> dict[str, Any]:
    """比较两个目录，返回结构化差异（文本渲染与 JSON 输出共用）。"""
    if recursive:
        files1 = {p.relative_to(dir1).as_posix() for p in dir1.rglob(pattern) if p.is_file()}
        files2 = {p.relative_to(dir2).as_posix() for p in dir2.rglob(pattern) if p.is_file()}
    else:
        files1 = {p.name for p in dir1.glob(pattern) if p.is_file()}
        files2 = {p.name for p in dir2.glob(pattern) if p.is_file()}

    only_left = sorted(files1 - files2)
    only_right = sorted(files2 - files1)
    common = sorted(files1 & files2)

    diffs: list[str] = []
    errors: list[str] = []
    for rel in common:
        try:
            if not filecmp.cmp(dir1 / rel, dir2 / rel, shallow=False):
                diffs.append(rel)
        except OSError:
            errors.append(rel)

    return {
        "equal": not (only_left or only_right or diffs or errors),
        "only_left": only_left,
        "only_right": only_right,
        "different": diffs,
        "errors": errors,
    }


def format_directory_result(result: dict[str, Any], dir1: Path, dir2: Path) -> str:
    """把结构化目录差异渲染为人类可读文本（保持既有输出格式）。"""
    lines: list[str] = []
    if result["only_left"]:
        lines.append(f"仅在 {dir1}:")
        lines.extend(f"  {name}" for name in result["only_left"])
    if result["only_right"]:
        lines.append(f"仅在 {dir2}:")
        lines.extend(f"  {name}" for name in result["only_right"])
    if result["different"]:
        lines.append("内容不同:")
        lines.extend(f"  {name}" for name in result["different"])
    if result["errors"]:
        lines.append("无法比较:")
        lines.extend(f"  {name}" for name in result["errors"])
    if not lines:
        return "目录内容相同"
    return "\n".join(lines)


def compare_directories(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> str:
    """比较两个目录，返回差异报告字符串。"""
    result = compare_directories_result(dir1, dir2, pattern, recursive)
    return format_directory_result(result, dir1, dir2)
