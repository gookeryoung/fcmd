"""cmderw 卷 / 挂载点枚举与剩余空间格式化的纯逻辑。

Windows 通过 ``GetLogicalDrives`` 取盘符位图，Linux 解析 ``/proc/mounts``
并跳过伪文件系统；剩余空间统一走 ``shutil.disk_usage``，便于脱离 Tk 单测。
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "PSEUDO_FILESYSTEMS",
    "Volume",
    "enumerate_volumes",
    "format_bytes",
    "linux_mount_points",
    "parse_mounts",
    "volume_for",
    "windows_drive_roots",
]

PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "overlay",
        "proc",
        "pstore",
        "ramfs",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)
"""``/proc/mounts`` 中不属于用户可见存储的伪文件系统。"""

_MOUNT_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}


@dataclass(frozen=True)
class Volume:
    """一个可切换的卷：挂载点与容量信息（字节）。"""

    mount: Path
    total: int
    free: int

    @property
    def label(self) -> str:
        """菜单显示文本：挂载点 + 剩余 / 总容量。"""
        return f"{self.mount}  —  {format_bytes(self.free)} 可用 / {format_bytes(self.total)}"


def _compact_number(value: float) -> str:
    """去掉一位小数格式里多余的 ``.0``。"""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def format_bytes(size: int) -> str:
    """把字节数格式化为 ``512 B`` / ``1.5 GB`` 形式。"""
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{_compact_number(value)} {unit}"
    return f"{_compact_number(value)} PB"


def volume_for(mount: Path, *, usage: Callable[[Path], tuple[int, int, int]] = shutil.disk_usage) -> Volume | None:
    """读取挂载点容量；不可访问时返回 ``None``。"""
    try:
        total, _used, free = usage(mount)
    except OSError:
        return None
    return Volume(mount=mount, total=total, free=free)


def windows_drive_roots(mask: int | None = None) -> list[Path]:
    """把盘符位图转换为根路径列表；``mask`` 为空时调用 Win32 API。"""
    if mask is None:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
    return [Path(f"{chr(ord('A') + index)}:\\") for index in range(26) if mask & (1 << index)]


def _unescape_mount(text: str) -> str:
    """还原 ``/proc/mounts`` 中的八进制转义（空格 / 制表 / 换行 / 反斜杠）。"""
    for escaped, char in _MOUNT_ESCAPES.items():
        text = text.replace(escaped, char)
    return text


def parse_mounts(text: str) -> list[Path]:
    """从 ``/proc/mounts`` 文本提取真实挂载点（跳过伪文件系统并按路径去重）。"""
    mounts: list[Path] = []
    seen: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mount_point, filesystem = _unescape_mount(parts[1]), parts[2]
        if filesystem in PSEUDO_FILESYSTEMS or mount_point in seen:
            continue
        seen.add(mount_point)
        mounts.append(Path(mount_point))
    return mounts


def linux_mount_points(contents: str | None = None) -> list[Path]:
    """读取 ``/proc/mounts`` 得到挂载点；读取失败时返回空列表。"""
    if contents is None:
        try:
            contents = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
    return parse_mounts(contents)


def enumerate_volumes(*, platform: str | None = None) -> list[Volume]:
    """按平台枚举可访问的卷；无法读取容量的挂载点直接丢弃。"""
    name = platform if platform is not None else sys.platform
    roots = windows_drive_roots() if name.startswith("win") else linux_mount_points()
    volumes: list[Volume] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        volume = volume_for(root)
        if volume is not None:
            volumes.append(volume)
    return volumes
