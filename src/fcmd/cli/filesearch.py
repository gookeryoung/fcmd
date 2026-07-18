"""filesearch - 文件搜索工具。

基于标准库 ``pathlib``/``fnmatch``/``re`` 提供按文件名 glob 搜索、按内容正则匹配能力。

示例
----
    fcmd filesearch name src "*.py"                 # 搜索 src 下所有 .py 文件
    fcmd filesearch name src "*.py" --include-dirs  # 同时包含匹配的目录
    fcmd filesearch content src "TODO|FIXME"        # 搜索内容含 TODO/FIXME 的行
    fcmd filesearch content src "def \\w+" --extension .py
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import fcmd

__all__ = [
    "is_binary_file",
    "read_text_lines",
    "search_by_content",
    "search_by_name",
]

# 二进制检测读取前 1024 字节
_BINARY_SNIFF_SIZE = 1024
# 内容搜索单文件最大读取行数，避免大文件内存爆炸
_MAX_LINES_PER_FILE = 100_000


def _should_skip_part(parts: tuple[str, ...], ignore_dirs: set[str]) -> bool:
    """判断路径组件是否命中忽略目录集合（支持 ``*.egg-info`` 通配）。"""
    for part in parts:
        if part in ignore_dirs:
            return True
        if any(fnmatch.fnmatch(part, pat) for pat in ignore_dirs if "*" in pat):
            return True
    return False


def is_binary_file(path: Path) -> bool:
    """通过前 1024 字节是否含 ``\\x00`` 判定二进制文件。

    Parameters
    ----------
    path:
        目标文件路径

    Returns
    -------
    bool
        True 表示二进制文件；读取失败也返回 True（保守跳过）
    """
    try:
        with path.open("rb") as f:
            chunk = f.read(_BINARY_SNIFF_SIZE)
    except OSError:
        return True
    return b"\x00" in chunk


def read_text_lines(path: Path) -> list[str]:
    """读取文本文件所有行（保留行尾），utf-8 失败时回退为 replace。

    Parameters
    ----------
    path:
        目标文件路径

    Returns
    -------
    list[str]
        行列表（含行尾换行符）

    Raises
    ------
    ValueError
        文件为二进制
    """
    if is_binary_file(path):
        raise ValueError(f"二进制文件: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            return f.readlines()
    except UnicodeDecodeError:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()


def search_by_name(
    directory: Path,
    pattern: str,
    include_dirs: bool = False,
    ignore_dirs: set[str] | None = None,
) -> list[Path]:
    """按文件名 glob 模式递归搜索目录。

    ``pattern`` 使用 ``fnmatch`` 语法（如 ``*.py``、``test_*.txt``）。

    Parameters
    ----------
    directory:
        搜索根目录
    pattern:
        文件名 glob 模式
    include_dirs:
        是否同时返回匹配的目录（默认仅文件）
    ignore_dirs:
        跳过的目录名集合（默认使用 ``_common.IGNORE_DIRS``）

    Returns
    -------
    list[Path]
        匹配的路径列表（按字符串排序）

    Raises
    ------
    FileNotFoundError
        目录不存在
    NotADirectoryError
        ``directory`` 不是目录
    """
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    if ignore_dirs is None:
        from fcmd.cli._common import IGNORE_DIRS

        ignore_dirs = IGNORE_DIRS
    results: list[Path] = []
    for path in directory.rglob("*"):
        # 命中忽略目录的路径整体跳过
        if _should_skip_part(path.parts, ignore_dirs):
            continue
        if path.is_dir():
            if include_dirs and fnmatch.fnmatch(path.name, pattern):
                results.append(path)
            continue
        if fnmatch.fnmatch(path.name, pattern):
            results.append(path)
    results.sort(key=str)
    return results


def search_by_content(
    directory: Path,
    pattern: str,
    extension: str = "",
    ignore_dirs: set[str] | None = None,
) -> list[tuple[Path, int, str]]:
    """按文件内容正则递归搜索。

    ``pattern`` 使用 ``re`` 正则语法。返回每条匹配的 ``(文件, 行号, 行内容)`` 三元组。

    Parameters
    ----------
    directory:
        搜索根目录
    pattern:
        正则表达式
    extension:
        限定扩展名（如 ``.py``），空字符串表示不限
    ignore_dirs:
        跳过的目录名集合（默认使用 ``_common.IGNORE_DIRS``）

    Returns
    -------
    list[tuple[Path, int, str]]
        ``(文件, 行号从1开始, 去除行尾的行内容)`` 列表

    Raises
    ------
    FileNotFoundError
        目录不存在
    NotADirectoryError
        ``directory`` 不是目录
    re.error
        正则语法错误
    """
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    regex = re.compile(pattern)
    if ignore_dirs is None:
        from fcmd.cli._common import IGNORE_DIRS

        ignore_dirs = IGNORE_DIRS
    results: list[tuple[Path, int, str]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip_part(path.parts, ignore_dirs):
            continue
        if extension and path.suffix != extension:
            continue
        try:
            lines = read_text_lines(path)
        except ValueError:
            continue
        for idx, raw in enumerate(lines[:_MAX_LINES_PER_FILE], start=1):
            if regex.search(raw):
                results.append((path, idx, raw.rstrip("\r\n")))
    return results


@fcmd.tool("filesearch", subcommand="name", help="按文件名 glob 搜索")
def filesearch_name(
    directory: Path,
    pattern: str,
    include_dirs: bool = False,
) -> None:
    """按文件名 glob 模式递归搜索目录。

    Parameters
    ----------
    directory:
        搜索根目录
    pattern:
        文件名 glob 模式（``fnmatch`` 语法，如 ``*.py``）
    include_dirs:
        是否同时返回匹配的目录（默认 False，使用 ``--include-dirs`` 开启）
    """
    try:
        results = search_by_name(directory, pattern, include_dirs=include_dirs)
    except (FileNotFoundError, NotADirectoryError) as e:
        print(str(e))
        return
    if not results:
        print("（无匹配）")
        return
    for path in results:
        print(path)


@fcmd.tool("filesearch", subcommand="content", help="按内容正则搜索")
def filesearch_content(
    directory: Path,
    pattern: str,
    extension: str = "",
) -> None:
    """按文件内容正则递归搜索，输出 ``文件:行号:行内容``。

    Parameters
    ----------
    directory:
        搜索根目录
    pattern:
        正则表达式
    extension:
        限定扩展名（如 ``.py``），默认不限
    """
    try:
        results = search_by_content(directory, pattern, extension=extension)
    except (FileNotFoundError, NotADirectoryError) as e:
        print(str(e))
        return
    except re.error as e:
        print(f"正则表达式错误: {e}")
        return
    if not results:
        print("（无匹配）")
        return
    for path, lineno, line in results:
        print(f"{path}:{lineno}:{line}")
