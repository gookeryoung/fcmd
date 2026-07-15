"""filedate - 文件日期前缀处理工具。

为文件添加或清除日期前缀（基于文件修改/创建时间）。

示例
----
    fcmd filedate add report.pdf           # 添加日期前缀：20260715_report.pdf
    fcmd filedate clear 20260715_report.pdf  # 清除日期前缀：report.pdf
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import fcmd

__all__ = [
    "DATE_PATTERN",
    "SEP",
    "add_date_prefix",
    "get_file_timestamp",
    "process_file_date",
    "process_files_date",
    "remove_date_prefix",
]

# ============================================================================
# 配置
# ============================================================================

# 日期前缀正则：匹配 19xx/20xx 开头的 YYYYMMDD（允许分隔符 -_#.~）
DATE_PATTERN = re.compile(r"(20|19)\d{2}[-_#.~]?((0[1-9])|(1[012]))[-_#.~]?((0[1-9])|([12]\d)|(3[01]))[-_#.~]?")
SEP = "_"


# ============================================================================
# 公共函数
# ============================================================================


def get_file_timestamp(filepath: Path) -> str:
    """获取文件时间戳（取修改时间与创建时间的较大值）。

    Parameters
    ----------
    filepath:
        文件路径

    Returns
    -------
    str
        ``YYYYMMDD`` 格式时间戳
    """
    stat = filepath.stat()
    return time.strftime("%Y%m%d", time.localtime(max(stat.st_mtime, stat.st_ctime)))


def remove_date_prefix(filepath: Path) -> Path:
    """移除文件名中的日期前缀。

    Parameters
    ----------
    filepath:
        原文件路径

    Returns
    -------
    Path
        重命名后的路径（无前缀时返回原路径）
    """
    stem = filepath.stem
    new_stem = DATE_PATTERN.sub("", stem)
    if new_stem != stem:
        new_path = filepath.with_name(new_stem + filepath.suffix)
        filepath.rename(new_path)
        return new_path
    return filepath


def add_date_prefix(filepath: Path) -> Path:
    """为文件名添加日期前缀（基于文件时间戳）。

    Parameters
    ----------
    filepath:
        原文件路径

    Returns
    -------
    Path
        重命名后的路径
    """
    timestamp = get_file_timestamp(filepath)
    new_stem = f"{timestamp}{SEP}{filepath.stem}"
    new_path = filepath.with_name(new_stem + filepath.suffix)
    filepath.rename(new_path)
    return new_path


def process_file_date(filepath: Path, clear: bool = False) -> None:
    """处理单个文件的日期前缀。

    Parameters
    ----------
    filepath:
        文件路径
    clear:
        ``True`` 清除前缀；``False`` 先清除再添加（更新日期）
    """
    if clear:
        remove_date_prefix(filepath)
    else:
        # 先清除旧前缀再添加新前缀，避免重复
        new_path = remove_date_prefix(filepath)
        add_date_prefix(new_path)


def process_files_date(targets: list[Path], clear: bool = False) -> None:
    """批量处理文件日期前缀。

    Parameters
    ----------
    targets:
        文件路径列表
    clear:
        ``True`` 清除前缀；``False`` 添加/更新前缀
    """
    for target in targets:
        if target.exists() and not target.name.startswith("."):
            process_file_date(target, clear)


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("filedate", subcommand="add", help="添加/更新文件日期前缀")
def process_files_date_add(files: list[Path]) -> None:
    """添加/更新文件日期前缀。

    Parameters
    ----------
    files:
        文件路径列表
    """
    process_files_date(files, clear=False)


@fcmd.tool("filedate", subcommand="clear", help="清除文件日期前缀")
def process_files_date_clear(files: list[Path]) -> None:
    """清除文件日期前缀。

    Parameters
    ----------
    files:
        文件路径列表
    """
    process_files_date(files, clear=True)
