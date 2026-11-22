"""磁盘空间与发行版占用估算。

稀疏 vhdx 的 ``st_size`` 是上限而非实际占用，因此所有估算都带 ``estimated`` 标记，
由调用方决定展示口径。
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from fcmd.infras.system import format_bytes as _format_bytes

__all__ = [
    "VolumeInfo",
    "distro_disk_bytes",
    "distro_export_bytes",
    "format_bytes",
    "recommended_backup_dir",
    "volume_info",
]

_WALK_BUDGET_S = 5.0
_WALK_FILE_LIMIT = 200_000


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    path: str
    free_bytes: int | None
    total_bytes: int | None
    error: str | None


def recommended_backup_dir() -> Path:
    """推荐 / 默认备份目录：工作目录下的 ``backup``（``~/.fcmd/wsl/backup``）。

    配置里的 ``backups.dir`` 仍可覆盖；未配置时 GUI 与 CLI 的推荐值都指向这里，
    与下载归档、安装目录同处一个工作目录（``~/.fcmd/wsl``），便于整体备份 / 清理。
    """
    from fcmd.apps.wslman import _config

    return _config.default_backups_dir()


def volume_info(path: Path) -> VolumeInfo:
    """读取目标卷剩余/总空间；路径不存在时向上找最近的已存在父目录。"""
    existing = _existing_parent(path)
    try:
        usage = shutil.disk_usage(existing)
    except OSError as exc:
        return VolumeInfo(
            path=str(existing), free_bytes=None, total_bytes=None, error=f"无法读取 {existing} 的剩余空间：{exc}"
        )
    return VolumeInfo(path=str(existing), free_bytes=usage.free, total_bytes=usage.total, error=None)


def _existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def distro_disk_bytes(
    base_path: str | None,
    version: int | None,
    *,
    budget_s: float = _WALK_BUDGET_S,
) -> tuple[int | None, bool]:
    """估算发行版占用，返回 ``(字节数, 是否估算)``。

    - WSL2：``BasePath/ext4.vhdx`` 的 ``st_size``（稀疏文件会高估，标为估算）；
    - WSL1：累加 ``BasePath/rootfs``，超过时间或文件数预算即截断并标为估算。
    """
    if not base_path:
        return None, False
    base = Path(base_path)
    vhdx = base / "ext4.vhdx"
    if vhdx.is_file():
        try:
            return vhdx.stat().st_size, True
        except OSError:
            return None, False
    rootfs = base / "rootfs"
    if version == 1 or rootfs.is_dir():
        return _walk_size(rootfs, budget_s)
    return None, False


def distro_export_bytes(
    base_path: str | None,
    version: int | None,
    *,
    budget_s: float = _WALK_BUDGET_S,
) -> tuple[int | None, bool]:
    """导出 tar 的大小估算，返回 ``(字节数, 是否估算)``。

    WSL2 的 ``ext4.vhdx`` 在稀疏卷上 ``st_size`` 是逻辑上限（可能远大于实际写入 tar
    的内容），用它算导出百分比会长时间停在偏低值。这里取「逻辑大小 / 实际占用」的
    较小值：稀疏文件落到实际占用，普通文件仍沿用逻辑大小；再叠加 vhdx 内部的空闲
    空间误差，所以结果始终标记为估算。WSL1 与 :func:`distro_disk_bytes` 同一口径。
    """
    if not base_path:
        return None, False
    base = Path(base_path)
    vhdx = base / "ext4.vhdx"
    if vhdx.is_file():
        try:
            logical = vhdx.stat().st_size
        except OSError:
            return None, False
        allocated = _allocated_size(vhdx)
        if allocated is not None and 0 < allocated < logical:
            logical = allocated
        return logical, True
    rootfs = base / "rootfs"
    if version == 1 or rootfs.is_dir():
        return _walk_size(rootfs, budget_s)
    return None, False


def _allocated_size(path: Path) -> int | None:
    """文件在磁盘上的实际占用（字节）；Windows 用 ``GetCompressedFileSizeW``。

    稀疏 / 压缩文件的实际占用会小于逻辑大小；读不到时返回 ``None``，调用方退回
    ``st_size``。
    """
    if os.name == "nt":
        try:
            import ctypes

            high = ctypes.c_ulong(0)
            low = ctypes.windll.kernel32.GetCompressedFileSizeW(  # type: ignore[attr-defined]
                str(path), ctypes.byref(high)
            )
            if low == 0xFFFFFFFF and ctypes.GetLastError() != 0:
                return None
            return (int(high.value) << 32) | int(low)
        except (AttributeError, OSError, ValueError):
            return None
    blocks = 0
    try:
        blocks = int(getattr(path.stat(), "st_blocks", 0) or 0)
    except OSError:
        return None
    return blocks * 512 if blocks else None


def _walk_size(root: Path, budget_s: float) -> tuple[int | None, bool]:
    if not root.is_dir():
        return None, False
    started = time.monotonic()
    total = 0
    count = 0
    stack: list[Path] = [root]
    try:
        while stack:
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                        count += 1
                        if count >= _WALK_FILE_LIMIT:
                            return total, True
            if time.monotonic() - started > budget_s:
                return total, True
    except OSError:
        return (total if count else None), True
    return total, False


def format_bytes(value: int | None, *, estimated: bool = False) -> str:
    """人类可读大小（IEC 单位）；未知返回 ``未知``，估算值带 ``≈`` 前缀。"""
    if value is None:
        return "未知"
    text = _format_bytes(value, binary=True)
    return f"≈{text}" if estimated else text
