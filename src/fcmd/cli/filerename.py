"""filerename - 文件批量重命名工具。

提供正则替换、位置插入、大小写转换三种重命名模式，操作文件名主干（保留扩展名）。
支持 ``--preview`` 预览变更，遇到目标名冲突时跳过并提示。

示例
----
    fcmd filerename replace *.txt --pattern "\\s+" --replacement "_"   # 空格替换为下划线
    fcmd filerename insert *.txt --text "NEW_" --position 0            # 开头插入前缀
    fcmd filerename case *.txt --mode lower                            # 转小写
    fcmd filerename replace *.txt --pattern "\\d+" --preview           # 预览不执行
"""

from __future__ import annotations

import re
from pathlib import Path

import fcmd

__all__ = [
    "change_case",
    "insert_text",
    "replace_pattern",
]


def _safe_rename(filepath: Path, new_stem: str, preview: bool) -> bool:
    """安全重命名文件（保留扩展名），返回是否执行。

    目标名与原名相同时跳过；目标已存在且非同一文件时跳过避免覆盖。
    大小写不敏感文件系统（Windows/macOS）上仅大小写不同的重命名会被允许。
    """
    if new_stem == filepath.stem:
        return False

    target = filepath.with_name(new_stem + filepath.suffix)
    # 大小写不敏感文件系统上，仅大小写不同时 target.exists() 返回 True 但实际是同一文件
    if target.exists() and target.resolve() != filepath.resolve():
        print(f"跳过（目标已存在）: {filepath.name} -> {target.name}")
        return False

    if preview:
        print(f"[预览] {filepath.name} -> {target.name}")
    else:
        filepath.rename(target)
        print(f"重命名: {filepath.name} -> {target.name}")
    return True


def replace_pattern(filepath: Path, pattern: re.Pattern[str], replacement: str, preview: bool = False) -> bool:
    """正则替换文件名主干。

    仅当文件名主干匹配 ``pattern`` 时才执行替换，否则跳过。

    Parameters
    ----------
    filepath:
        文件路径
    pattern:
        编译后的正则模式
    replacement:
        替换字符串（支持反向引用，如 ``\\1``）
    preview:
        ``True`` 仅预览不执行

    Returns
    -------
    bool
        是否执行了重命名
    """
    if not pattern.search(filepath.stem):
        return False
    new_stem = pattern.sub(replacement, filepath.stem)
    return _safe_rename(filepath, new_stem, preview)


def insert_text(filepath: Path, text: str, position: int, preview: bool = False) -> bool:
    """在文件名主干指定位置插入文本。

    ``position`` 为负数时从末尾计算，超出范围时自动截断到边界。

    Parameters
    ----------
    filepath:
        文件路径
    text:
        待插入文本
    position:
        插入位置（0=开头，负数从末尾计算）
    preview:
        ``True`` 仅预览不执行

    Returns
    -------
    bool
        是否执行了重命名
    """
    if not text:
        return False
    stem = filepath.stem
    pos = max(0, min(position, len(stem)))
    new_stem = stem[:pos] + text + stem[pos:]
    return _safe_rename(filepath, new_stem, preview)


def change_case(filepath: Path, mode: str, preview: bool = False) -> bool:
    """转换文件名主干大小写。

    Parameters
    ----------
    filepath:
        文件路径
    mode:
        转换模式：``lower`` / ``upper`` / ``title``
    preview:
        ``True`` 仅预览不执行

    Returns
    -------
    bool
        是否执行了重命名

    Raises
    ------
    ValueError
        ``mode`` 不在可选范围内时
    """
    mode_map = {"lower": str.lower, "upper": str.upper, "title": str.title}
    if mode not in mode_map:
        raise ValueError(f"不支持的大小写模式: {mode}（可选: lower/upper/title）")
    new_stem = mode_map[mode](filepath.stem)
    return _safe_rename(filepath, new_stem, preview)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("filerename", subcommand="replace", help="正则替换文件名主干")
def filerename_replace(files: list[Path], pattern: str, replacement: str = "", preview: bool = False) -> None:
    """正则替换文件名主干中的匹配部分（保留扩展名）。

    Parameters
    ----------
    files:
        待重命名的文件列表
    pattern:
        正则表达式（Python ``re`` 语法，支持反向引用）
    replacement:
        替换字符串（默认空字符串，即删除匹配部分）
    preview:
        仅预览不实际执行
    """
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        print(f"无效的正则表达式: {e}")
        return
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        replace_pattern(filepath, compiled, replacement, preview)


@fcmd.tool("filerename", subcommand="insert", help="在文件名指定位置插入文本")
def filerename_insert(files: list[Path], text: str, position: int = 0, preview: bool = False) -> None:
    """在文件名主干指定位置插入文本（保留扩展名）。

    Parameters
    ----------
    files:
        待重命名的文件列表
    text:
        待插入文本
    position:
        插入位置（0=开头，负数从末尾计算，默认 0）
    preview:
        仅预览不实际执行
    """
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        insert_text(filepath, text, position, preview)


@fcmd.tool("filerename", subcommand="case", help="转换文件名大小写")
def filerename_case(files: list[Path], mode: str = "lower", preview: bool = False) -> None:
    """转换文件名主干大小写（保留扩展名）。

    Parameters
    ----------
    files:
        待重命名的文件列表
    mode:
        转换模式：``lower`` / ``upper`` / ``title``（默认 ``lower``）
    preview:
        仅预览不实际执行
    """
    if mode not in ("lower", "upper", "title"):
        print(f"不支持的模式: {mode}（可选: lower/upper/title）")
        return
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        change_case(filepath, mode, preview)
