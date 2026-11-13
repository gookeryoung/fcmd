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

__all__ = ["VolumeInfo", "distro_disk_bytes", "format_bytes", "recommended_backup_dir", "volume_info"]

_WALK_BUDGET_S = 5.0
_WALK_FILE_LIMIT = 200_000


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    path: str
    free_bytes: int | None
    total_bytes: int | None
    error: str | None


def recommended_backup_dir() -> Path:
    """``doctor`` 推荐的备份目录（只是建议，配置默认值仍为空）。"""
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / "wsl-backups"


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
    """人类可读大小；未知返回 ``未知``。"""
    if value is None:
        return "未知"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(value)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if size < 1024 or candidate == units[-1]:
            break
        size /= 1024
    prefix = "≈" if estimated else ""
    if unit == "B":
        return f"{prefix}{int(size)} B"
    return f"{prefix}{size:.1f} {unit}"
