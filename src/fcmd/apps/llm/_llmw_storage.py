"""llmw 存储管理：模型目录的磁盘信息与增删助手。

参考 Steam 的「存储」页：每个模型目录是一块磁盘卡片，展示盘符 / 挂载点、
文件系统与剩余空间。模型目录列表的权威读写仍在 :mod:`fcmd.apps.llm._llmconfig`，
本模块只把路径整理成可展示的 :class:`StorageLocation`，不保存第二份配置。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.llm import _llmconfig
from fcmd.apps.llm._llmw_hardware import DiskInfo, disk_for_path, disk_infos

__all__ = [
    "StorageLocation",
    "add_location",
    "drive_label",
    "locations",
    "make_primary",
    "remove_location",
]


@dataclass(frozen=True, slots=True)
class StorageLocation:
    """一个模型目录及其所在分区的展示信息。"""

    path: Path
    drive: str
    fstype: str
    total: int
    free: int
    exists: bool
    primary: bool = False

    @property
    def used(self) -> int:
        """已用空间（字节）。"""
        return max(self.total - self.free, 0)

    @property
    def percent(self) -> int:
        """已用比例（0–100 的整数，供进度条使用）。"""
        return round(self.used / self.total * 100) if self.total else 0

    @property
    def disk_label(self) -> str:
        """硬盘标识：Windows 盘符（``D:``）或 Linux 挂载点。"""
        return self.drive or str(self.path.anchor) or str(self.path.parent)


def drive_label(path: Path, disks: Sequence[DiskInfo] | None = None) -> tuple[str, str]:
    """返回 ``(盘符 / 挂载点, 文件系统)``；分区未识别时回落到路径锚点。"""
    probe = _probe_path(path)
    disk = disk_for_path(probe, disks if disks is not None else disk_infos())
    if disk is not None:
        mount = Path(disk.mount)
        label = mount.drive or (str(mount) if str(mount) not in ("", ".") else "")
        return label or mount.anchor or str(mount), disk.fstype
    drive = path.drive or str(path.anchor) or ""
    return drive.rstrip("/") or drive, ""


def _probe_path(path: Path) -> Path:
    """路径不存在时向上找最近存在的父目录（用于取所在分区的容量）。"""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return probe


def _location(path: Path, *, primary: bool, disks: Sequence[DiskInfo]) -> StorageLocation:
    drive, fstype = drive_label(path, disks)
    probe = _probe_path(path)
    total = free = 0
    try:
        usage = shutil.disk_usage(probe)
        total, free = int(usage.total), int(usage.free)
    except OSError:
        pass
    return StorageLocation(
        path=path,
        drive=drive,
        fstype=fstype,
        total=total,
        free=free,
        exists=path.is_dir(),
        primary=primary,
    )


def locations(home: Path | None = None) -> list[StorageLocation]:
    """当前全部模型目录（首个标记为主目录）及各自分区容量。"""
    disks = disk_infos()
    return [
        _location(path, primary=index == 0, disks=disks) for index, path in enumerate(_llmconfig.models_dirs(home=home))
    ]


def add_location(value: str | Path, home: Path | None = None) -> list[StorageLocation]:
    """追加模型目录并返回最新列表（已存在则保持原位置）。"""
    _llmconfig.add_models_dir(Path(str(value)).expanduser(), home)
    return locations(home)


def remove_location(path: str | Path, home: Path | None = None) -> list[StorageLocation]:
    """移除模型目录并返回最新列表。"""
    _llmconfig.remove_models_dir(path, home)
    return locations(home)


def make_primary(path: str | Path, home: Path | None = None) -> list[StorageLocation]:
    """把指定目录设为主目录（下载 / 转换产物落点），其余目录保持原顺序。"""
    target = _llmconfig.path_key(Path(str(path)).expanduser())
    current = _llmconfig.configured_models_dirs(home=home)
    if not current:
        current = _llmconfig.models_dirs(home=home)
    chosen = [item for item in current if _llmconfig.path_key(item) == target]
    rest = [item for item in current if _llmconfig.path_key(item) != target]
    _llmconfig.set_models_dirs([*chosen, *rest], home)
    return locations(home)


def display_path(path: Path) -> str:
    """展示用路径：Windows 下用反斜杠，避免与盘符混排时出现双斜杠。"""
    return os.fspath(path)
