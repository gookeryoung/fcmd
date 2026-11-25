"""本地镜像包 → 可交给 ``wsl --import`` / ``--import-in-place`` 的载荷。

支持直接导入的 ``.tar`` / ``.tar.gz`` / ``.wsl``，磁盘 ``.vhdx``，以及常见压缩包
（zip / 7z / rar / gz / bz2 / xz）。压缩包会解到临时目录后再找 ``ext4.vhdx``、
``rootfs.tar`` 等载荷；只有文件树时临时打包成 tar，安装结束由调用方清理。
"""

from __future__ import annotations

import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fcmd.apps.wslman._errors import invalid_argument, runtime_error
from fcmd.apps.wslman._images import ARCHIVE_SUFFIXES, INSTALL_SUFFIXES, TAR_SUFFIXES, VHDX_SUFFIXES
from fcmd.tools.archive.archivex import extract_archive

__all__ = [
    "ARCHIVE_SUFFIXES",
    "INSTALL_SUFFIXES",
    "TAR_SUFFIXES",
    "VHDX_SUFFIXES",
    "InstallPayload",
    "prepare_payload",
]


@dataclass(frozen=True, slots=True)
class InstallPayload:
    """一份可直接安装的载荷；``cleanup`` 是安装结束后要删除的临时目录。"""

    kind: Literal["tar", "vhdx"]
    path: Path
    cleanup: Path | None = None


def _suffix(path: Path) -> str:
    name = path.name.casefold()
    for suffix in INSTALL_SUFFIXES:
        if name.endswith(suffix):
            return suffix
    return ""


def _find_vhdx(root: Path) -> Path | None:
    """优先 ``ext4.vhdx``，其次任意 ``*.vhdx``。"""
    for name in ("ext4.vhdx",):
        for path in root.rglob(name):
            if path.is_file():
                return path
    for path in sorted(root.rglob("*.vhdx"), key=lambda item: str(item).casefold()):
        if path.is_file():
            return path
    return None


def _find_tar(root: Path) -> Path | None:
    """按常见名字 / 后缀 / 内容识别可导入的 tar。"""
    for name in ("install.tar", "rootfs.tar"):
        for path in root.rglob(name):
            if path.is_file():
                return path
    for suffix in TAR_SUFFIXES:
        for path in sorted(root.rglob(f"*{suffix}"), key=lambda item: str(item).casefold()):
            if path.is_file():
                return path
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        if path.is_file() and tarfile.is_tarfile(path):
            return path
    return None


def _find_payload(root: Path) -> InstallPayload | None:
    """在解压目录里找 vhdx / rootfs tar / 文件树。"""
    disk = _find_vhdx(root)
    if disk is not None:
        return InstallPayload("vhdx", disk, root)
    tar = _find_tar(root)
    if tar is not None:
        return InstallPayload("tar", tar, root)
    rootfs = _find_rootfs_dir(root)
    if rootfs is None:
        return None
    target = root / ".fcmd-rootfs.tar"
    _pack_directory(rootfs, target)
    return InstallPayload("tar", target, root)


def _find_rootfs_dir(root: Path) -> Path | None:
    """找 Linux 根目录（含 ``etc`` 与 ``bin`` / ``usr``）；找不到返回 ``None``。"""
    candidates = [root, *sorted((path for path in root.rglob("*") if path.is_dir()), key=lambda item: len(item.parts))]
    for path in candidates:
        if (path / "etc").is_dir() and ((path / "bin").is_dir() or (path / "usr").is_dir()):
            return path
    return None


def _pack_directory(source: Path, target: Path) -> None:
    """把 rootfs 目录打包成 tar；归档内以目录内容为根（``bin/``、``etc/`` …）。"""
    try:
        with tarfile.open(target, "w") as archive:
            for child in sorted(source.iterdir(), key=lambda item: item.name.casefold()):
                archive.add(child, arcname=child.name, recursive=True)
    except OSError as exc:
        raise runtime_error(f"打包 rootfs 失败：{source}", hint=str(exc)) from exc


def prepare_payload(source: Path, workspace: Path) -> InstallPayload:
    """把本地文件变成可导入载荷；压缩包解到 ``workspace`` 下的临时目录。"""
    if not source.is_file():
        raise invalid_argument(f"镜像包不存在：{source}", hint="重新选择文件后重试")
    suffix = _suffix(source)
    if not suffix:
        raise invalid_argument(
            f"不支持的镜像包格式：{source.name}",
            hint="支持 .tar / .tar.gz / .wsl / .vhdx / zip / 7z / rar / gz / bz2 / xz",
        )
    if suffix in VHDX_SUFFIXES:
        return InstallPayload("vhdx", source)
    if suffix in TAR_SUFFIXES:
        return InstallPayload("tar", source)
    workdir = workspace / f".wslman-install-{source.stem}"
    # 清掉上次失败留下的同名目录，避免旧载荷被误选中。
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        extract_archive(source, workdir)
        payload = _find_payload(workdir)
    except (OSError, ValueError, RuntimeError) as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        raise invalid_argument(
            f"解压失败：{source.name}",
            hint=f"{exc}；确认压缩包完整，7z / rar 需要系统安装 7z 或 unrar",
        ) from exc
    if payload is None:
        shutil.rmtree(workdir, ignore_errors=True)
        raise invalid_argument(
            f"压缩包里没有找到可安装的镜像：{source.name}",
            hint="需要包含 ext4.vhdx、rootfs.tar / install.tar，或完整的 Linux 根目录",
        )
    return payload
