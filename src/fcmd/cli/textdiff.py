"""textdiff - 文本比较工具。

基于标准库 difflib/filecmp 提供文件与目录差异比较，输出 unified diff 格式。

示例
----
    fcmd textdiff file old.txt new.txt              # 比较两文件
    fcmd textdiff file old.txt new.txt --context 5  # 5 行上下文
    fcmd textdiff file old.txt new.txt --color      # 彩色输出
    fcmd textdiff dir dir1/ dir2/                   # 递归比较两目录
    fcmd textdiff dir dir1/ dir2/ --pattern "*.py"  # 仅比较 .py 文件
    fcmd textdiff dir dir1/ dir2/ --no-recursive    # 仅比较顶层
"""

from __future__ import annotations

import difflib
import filecmp
from pathlib import Path

import fcmd

__all__ = [
    "colorize_diff",
    "compare_directories",
    "compare_files",
]

# ANSI 颜色码
_RED = "\033[31m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_RESET = "\033[0m"


def _read_lines(filepath: Path) -> list[str]:
    """读取文本文件行列表。

    优先 utf-8，失败回退 utf-8 + ``errors='replace'``（损失但可用）。
    二进制文件（前 1024 字节含 ``\\x00``）抛 :class:`ValueError`。

    Parameters
    ----------
    filepath:
        文件路径

    Returns
    -------
    list[str]
        行列表（保留换行符）

    Raises
    ------
    ValueError
        文件为二进制
    """
    with filepath.open("rb") as f:
        if b"\x00" in f.read(1024):
            raise ValueError(f"二进制文件不支持比较: {filepath}")
    try:
        with filepath.open("r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with filepath.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()


def colorize_diff(diff_text: str) -> str:
    """为 unified diff 文本添加 ANSI 颜色。

    - ``-`` 开头行：红色（删除）
    - ``+`` 开头行：绿色（新增）
    - ``@@`` 开头行：青色（位置标记）
    - ``---``/``+++`` 文件头：不着色

    Parameters
    ----------
    diff_text:
        unified diff 文本

    Returns
    -------
    str
        着色后的文本
    """
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
    """比较两个文本文件，返回 unified diff 字符串。

    Parameters
    ----------
    file1, file2:
        待比较文件路径
    context:
        上下文行数（默认 3）

    Returns
    -------
    str
        unified diff 文本；文件相同时返回空字符串
    """
    lines1 = _read_lines(file1)
    lines2 = _read_lines(file2)
    diff = difflib.unified_diff(lines1, lines2, fromfile=str(file1), tofile=str(file2), n=context)
    return "".join(diff)


def compare_directories(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> str:
    """比较两个目录，返回差异报告字符串。

    报告包含：仅在左目录的文件、仅在右目录的文件、内容不同的文件。
    默认递归比较子目录。

    Parameters
    ----------
    dir1, dir2:
        待比较目录路径
    pattern:
        文件名 glob 模式（默认 ``"*"`` 所有文件）
    recursive:
        是否递归比较子目录（默认 True）

    Returns
    -------
    str
        差异报告；无差异时返回 ``"目录内容相同"``
    """
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

    lines: list[str] = []
    if only_left:
        lines.append(f"仅在 {dir1}:")
        lines.extend(f"  {name}" for name in only_left)
    if only_right:
        lines.append(f"仅在 {dir2}:")
        lines.extend(f"  {name}" for name in only_right)
    if diffs:
        lines.append("内容不同:")
        lines.extend(f"  {name}" for name in diffs)
    if errors:
        lines.append("无法比较:")
        lines.extend(f"  {name}" for name in errors)
    if not lines:
        return "目录内容相同"
    return "\n".join(lines)


@fcmd.tool("textdiff", subcommand="file", help="比较两个文本文件")
def textdiff_file(file1: Path, file2: Path, context: int = 3, color: bool = False) -> None:
    """输出两个文本文件的 unified diff。

    Parameters
    ----------
    file1, file2:
        待比较文件路径
    context:
        上下文行数（默认 3）
    color:
        启用 ANSI 彩色输出（默认关闭）
    """
    if not file1.exists():
        print(f"文件不存在: {file1}")
        return
    if not file2.exists():
        print(f"文件不存在: {file2}")
        return
    try:
        result = compare_files(file1, file2, context)
    except ValueError as e:
        print(str(e))
        return
    if not result:
        print("文件内容相同")
        return
    if color:
        result = colorize_diff(result)
    print(result, end="")


@fcmd.tool("textdiff", subcommand="dir", help="比较两个目录")
def textdiff_dir(dir1: Path, dir2: Path, pattern: str = "*", recursive: bool = True) -> None:
    """列出两个目录中差异文件。

    Parameters
    ----------
    dir1, dir2:
        待比较目录路径
    pattern:
        文件名 glob 模式（默认 ``"*"`` 所有文件）
    recursive:
        是否递归比较子目录（默认 True）
    """
    if not dir1.is_dir():
        print(f"目录不存在: {dir1}")
        return
    if not dir2.is_dir():
        print(f"目录不存在: {dir2}")
        return
    print(compare_directories(dir1, dir2, pattern, recursive))
