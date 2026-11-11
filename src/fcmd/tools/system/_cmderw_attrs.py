"""cmderw 时间戳与文件属性修改的纯逻辑。

时间戳复用 ``os.utime``；Windows 的只读走 ``os.chmod``、隐藏走
``GetFileAttributesW`` / ``SetFileAttributesW``，读取与写入均可注入便于单测。
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = [
    "AttributeChange",
    "AttributeReport",
    "FileAttributes",
    "apply_attributes",
    "format_timestamp",
    "parse_timestamp",
    "read_attributes",
    "set_hidden",
    "windows_attributes",
    "write_windows_attributes",
]

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
_INVALID_ATTRIBUTES = 0xFFFFFFFF

_TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def parse_timestamp(text: str) -> float | None:
    """解析 ``YYYY-MM-DD[ HH:MM[:SS]]`` 为时间戳；空串返回 ``None``。"""
    raw = text.strip()
    if not raw:
        return None
    for pattern in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, pattern).timestamp()
        except ValueError:
            continue
    raise ValueError(f"时间格式应为 YYYY-MM-DD HH:MM:SS：{text}")


def format_timestamp(timestamp: float) -> str:
    """把时间戳格式化为 ``YYYY-MM-DD HH:MM:SS``；无效时间返回空串。"""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(frozen=True)
class FileAttributes:
    """条目的只读 / 隐藏状态。"""

    readonly: bool
    hidden: bool


def windows_attributes(path: Path) -> int:
    """读取 Windows 文件属性位；失败时抛 ``OSError``。"""
    import ctypes

    value = int(ctypes.windll.kernel32.GetFileAttributesW(str(path)))  # type: ignore[attr-defined]
    if value == _INVALID_ATTRIBUTES:
        raise OSError(f"无法读取属性: {path}")
    return value


def write_windows_attributes(path: Path, attributes: int) -> None:
    """写入 Windows 文件属性位；失败时抛 ``OSError``。"""
    import ctypes

    if not ctypes.windll.kernel32.SetFileAttributesW(str(path), attributes):  # type: ignore[attr-defined]
        raise OSError(f"无法写入属性: {path}")


def read_attributes(
    path: Path,
    *,
    platform: str | None = None,
    reader: Callable[[Path], int] = windows_attributes,
) -> FileAttributes:
    """读取只读 / 隐藏状态；POSIX 用权限位与点文件名推断。"""
    name = platform if platform is not None else sys.platform
    if name.startswith("win"):
        attributes = reader(path)
        return FileAttributes(
            readonly=bool(attributes & FILE_ATTRIBUTE_READONLY),
            hidden=bool(attributes & FILE_ATTRIBUTE_HIDDEN),
        )
    mode = path.stat().st_mode
    readonly = not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    return FileAttributes(readonly=readonly, hidden=path.name.startswith("."))


def set_hidden(
    path: Path,
    hidden: bool,
    *,
    reader: Callable[[Path], int] = windows_attributes,
    writer: Callable[[Path, int], None] = write_windows_attributes,
) -> None:
    """切换 Windows 隐藏属性，保留其他属性位。"""
    attributes = reader(path)
    updated = attributes | FILE_ATTRIBUTE_HIDDEN if hidden else attributes & ~FILE_ATTRIBUTE_HIDDEN
    if updated != attributes:
        writer(path, updated)


@dataclass(frozen=True)
class AttributeChange:
    """一次批量修改；``None`` 表示该字段保持不变。"""

    modified: float | None = None
    accessed: float | None = None
    readonly: bool | None = None
    hidden: bool | None = None

    @property
    def active(self) -> bool:
        """是否至少指定了一项修改。"""
        return any(value is not None for value in (self.modified, self.accessed, self.readonly, self.hidden))


@dataclass
class AttributeReport:
    """批量修改结果。"""

    updated: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否全部成功。"""
        return not self.failed

    def message(self) -> str:
        """面向状态栏 / 对话框的结果文本。"""
        if not self.failed:
            return f"已更新 {self.updated} 项属性"
        names = "，".join(path.name for path, _error in self.failed[:5])
        suffix = "" if len(self.failed) <= 5 else f" 等 {len(self.failed)} 项"
        return f"已更新 {self.updated} 项，失败 {len(self.failed)} 项：{names}{suffix}"


def _apply_one(path: Path, change: AttributeChange, *, windows: bool) -> None:
    """对单个条目应用时间戳与（Windows）属性修改。"""
    if change.modified is not None or change.accessed is not None:
        current = path.stat()
        mtime = change.modified if change.modified is not None else current.st_mtime
        atime = change.accessed if change.accessed is not None else current.st_atime
        os.utime(path, (atime, mtime))
    if not windows:
        return
    if change.readonly is not None:
        mode = path.stat().st_mode
        path.chmod(mode & ~stat.S_IWRITE if change.readonly else mode | stat.S_IWRITE)
    if change.hidden is not None:
        set_hidden(path, change.hidden)


def apply_attributes(
    paths: Iterable[Path],
    change: AttributeChange,
    *,
    platform: str | None = None,
) -> AttributeReport:
    """批量应用修改，逐项收集失败原因。"""
    if not change.active:
        raise ValueError("没有需要修改的属性")
    name = platform if platform is not None else sys.platform
    windows = name.startswith("win")
    report = AttributeReport()
    for path in paths:
        try:
            _apply_one(path, change, windows=windows)
        except OSError as exc:
            report.failed.append((path, str(exc)))
        else:
            report.updated += 1
    return report
