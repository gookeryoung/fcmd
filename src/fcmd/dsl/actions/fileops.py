"""文件与路径操作 DSL 动作：filesearch / pathtool。

原模块 ``fcmd.cli.fileops.filesearch`` 与 ``fcmd.cli.fileops.pathtool``
均为纯 Python 实现（pathlib / fnmatch / re），无子进程调用。迁移后
由 ``@action`` 装饰器注册，TOML 声明 ``action = "<名>"`` 直接引用。
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from fcmd.dsl.actions import action

__all__: list[str] = []


# ============================================================================
# filesearch 公共逻辑
# ============================================================================

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
    """通过前 1024 字节是否含 ``\\x00`` 判定二进制文件。"""
    try:
        with path.open("rb") as f:
            chunk = f.read(_BINARY_SNIFF_SIZE)
    except OSError:
        return True
    return b"\x00" in chunk


def read_text_lines(path: Path) -> list[str]:
    """读取文本文件所有行（保留行尾），utf-8 失败时回退为 replace。"""
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
    """按文件名 glob 模式递归搜索目录。"""
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    if ignore_dirs is None:
        from fcmd.cli._common import IGNORE_DIRS

        ignore_dirs = IGNORE_DIRS
    results: list[Path] = []
    for path in directory.rglob("*"):
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
    """按文件内容正则递归搜索。"""
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


# ============================================================================
# filesearch DSL 动作
# ============================================================================


@action(
    "filesearch_name",
    param_help={
        "directory": "搜索根目录",
        "pattern": "文件名 glob 模式（fnmatch 语法，如 *.py）",
        "include_dirs": "是否同时返回匹配的目录",
    },
)
def filesearch_name(
    directory: Path,
    pattern: str,
    include_dirs: bool = False,
) -> None:
    """按文件名 glob 模式递归搜索目录。"""
    try:
        results = search_by_name(directory, pattern, include_dirs=include_dirs)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"错误: {exc}")
        return
    if not results:
        print("（无匹配）")
        return
    for p in results:
        print(p)


@action(
    "filesearch_content",
    param_help={
        "directory": "搜索根目录",
        "pattern": "正则表达式",
        "extension": "限定扩展名（如 .py），空串不限",
    },
)
def filesearch_content(
    directory: Path,
    pattern: str,
    extension: str = "",
) -> None:
    """按文件内容正则递归搜索，输出 文件:行号:行内容。"""
    try:
        results = search_by_content(directory, pattern, extension=extension)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"错误: {exc}")
        return
    except re.error as exc:
        print(f"错误: 正则表达式错误: {exc}")
        return
    if not results:
        print("（无匹配）")
        return
    for p, lineno, line in results:
        print(f"{p}:{lineno}:{line}")


# ============================================================================
# pathtool 公共逻辑
# ============================================================================


def normalize_path(path: Path) -> Path:
    """规范化路径：展开用户目录、解析为绝对路径、消除 ``..``/``.``。"""
    return path.expanduser().absolute().resolve(strict=False)


def relative_to(path: Path, base: Path) -> Path:
    """计算 ``path`` 相对 ``base`` 的相对路径。"""
    p1 = normalize_path(path)
    p2 = normalize_path(base)
    return p1.relative_to(p2)


def path_parts(path: Path) -> dict[str, Any]:
    """提取路径各部分信息。"""
    p = normalize_path(path)
    return {
        "input": str(path),
        "absolute": str(p),
        "anchor": p.anchor,
        "parent": str(p.parent),
        "name": p.name,
        "stem": p.stem,
        "suffix": p.suffix,
        "suffixes": p.suffixes,
        "parts": list(p.parts),
    }


def path_diff(p1: Path, p2: Path) -> tuple[list[str], list[str], list[str]]:
    """比较两路径的组件差异，返回 ``(common, only_p1, only_p2)``。"""
    parts1 = list(normalize_path(p1).parts)
    parts2 = list(normalize_path(p2).parts)
    common: list[str] = []
    for a, b in zip(parts1, parts2, strict=False):
        if a != b:
            break
        common.append(a)
    only_p1 = parts1[len(common) :]
    only_p2 = parts2[len(common) :]
    return (common, only_p1, only_p2)


# ============================================================================
# pathtool DSL 动作
# ============================================================================


@action("pathtool_show", param_help={"path": "待解析的路径"})
def pathtool_show(path: Path) -> None:
    """显示路径各部分信息（anchor/parent/name/stem/suffix 等）。"""
    info = path_parts(path)
    print(f"输入路径:   {info['input']}")
    print(f"绝对路径:   {info['absolute']}")
    print(f"卷标/root: {info['anchor'] or '(无)'}")
    print(f"父目录:     {info['parent']}")
    print(f"文件名:     {info['name'] or '(无)'}")
    print(f"主干名:     {info['stem'] or '(无)'}")
    print(f"扩展名:     {info['suffix'] or '(无)'}")
    if info["suffixes"]:
        print(f"所有扩展名: {' '.join(info['suffixes'])}")
    print(f"组件:       {' / '.join(info['parts'])}")


@action(
    "pathtool_rel",
    param_help={
        "path": "目标路径",
        "base": "基准路径",
    },
)
def pathtool_rel(path: Path, base: Path) -> None:
    """计算 ``path`` 相对 ``base`` 的相对路径。"""
    try:
        rel = relative_to(path, base)
    except ValueError as exc:
        print(f"错误: 无法计算相对路径: {exc}")
        return
    print(str(rel))


@action("pathtool_norm", param_help={"path": "待规范化的路径"})
def pathtool_norm(path: Path) -> None:
    """规范化路径（展开 ~、绝对化、消除 ``..``/``.``）。"""
    print(str(normalize_path(path)))


@action(
    "pathtool_diff",
    param_help={
        "p1": "待比较的路径 1",
        "p2": "待比较的路径 2",
    },
)
def pathtool_diff(p1: Path, p2: Path) -> None:
    """比较两路径组件差异，输出公共前缀与各自独有部分。"""
    common, only1, only2 = path_diff(p1, p2)
    print(f"路径 1: {normalize_path(p1)}")
    print(f"路径 2: {normalize_path(p2)}")
    if common:
        print(f"公共前缀: {' / '.join(common)}")
    else:
        print("公共前缀: (无)")
    if only1:
        print(f"仅路径 1: {' / '.join(only1)}")
    else:
        print("仅路径 1: (无)")
    if only2:
        print(f"仅路径 2: {' / '.join(only2)}")
    else:
        print("仅路径 2: (无)")
