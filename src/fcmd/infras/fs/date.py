"""``fcmd.infras.fs.date``（由 ``infras/fs.py`` 拆分）。"""

from __future__ import annotations

import re
import time
from pathlib import Path

__all__ = ["add_date_prefixes", "clear_date_prefixes", "file_timestamp", "strip_date_prefix"]


def file_timestamp(filepath: Path) -> str:
    """获取文件时间戳（取修改时间与创建时间的较大值），``YYYYMMDD`` 格式。"""
    stat = filepath.stat()
    return time.strftime("%Y%m%d", time.localtime(max(stat.st_mtime, stat.st_ctime)))


_DATE_PATTERN = re.compile(r"(20|19)\d{2}[-_#.~]?((0[1-9])|(1[012]))[-_#.~]?((0[1-9])|([12]\d)|(3[01]))[-_#.~]?")


_DATE_SEP = "_"


def strip_date_prefix(filepath: Path) -> tuple[Path, bool]:
    """移除文件名主干中的日期前缀，返回 ``(新路径, 是否发生重命名)``。"""
    stem = filepath.stem
    new_stem = _DATE_PATTERN.sub("", stem)
    if new_stem == stem:
        return filepath, False
    new_path = filepath.with_name(new_stem + filepath.suffix)
    filepath.rename(new_path)
    return new_path, True


def add_date_prefixes(files: list[Path]) -> list[str]:
    """为文件名添加/更新日期前缀（先清后加避免重复），返回提示消息。"""
    messages: list[str] = []
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            stripped, removed = strip_date_prefix(filepath)
            if not removed:
                messages.append(f"{filepath} 无日期前缀")
            timestamp = file_timestamp(stripped)
            target = stripped.with_name(f"{timestamp}{_DATE_SEP}{stripped.stem}{stripped.suffix}")
            stripped.rename(target)
    return messages


def clear_date_prefixes(files: list[Path]) -> list[str]:
    """清除文件名中的日期前缀（缺失文件与点文件跳过），返回提示消息。"""
    messages: list[str] = []
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            _new_path, removed = strip_date_prefix(filepath)
            if not removed:
                messages.append(f"{filepath} 无日期前缀")
    return messages
