"""传输基础设施：磁盘预检、``.part`` 原子改名、进度、并发锁与归档校验。

导出 / 导入 / 复制 / 压缩共用本模块；所有路径参数都由调用方显式给出，
这里不读配置，也不执行 ``wsl.exe``，便于单元测试用临时目录覆盖。
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import os
import shutil
import sys
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from fcmd.apps.wslman._disk import format_bytes, volume_info
from fcmd.apps.wslman._errors import WslmanError, check_failed, not_found, runtime_error

__all__ = [
    "ArchiveInfo",
    "BackupFile",
    "LockHandle",
    "ProgressReporter",
    "SpacePreflight",
    "TransferLock",
    "archive_info",
    "atomic_replace",
    "backup_files",
    "default_lock_dir",
    "gzip_to",
    "part_path",
    "preflight_space",
    "sha256_file",
    "volume_problem",
]

_PART_SUFFIX = ".part"
_LOCK_STALE_S = 6 * 3600.0
_MIN_UNKNOWN_FREE = 2 * 1024**3
_PROGRESS_INTERVAL_S = 1.0


def part_path(target: Path) -> Path:
    """导出 / 压缩先写的临时目标：``<name>.part``。"""
    return target.with_name(target.name + _PART_SUFFIX)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_replace(part: Path, final: Path) -> None:
    """同卷原子改名；``final`` 的父目录不存在时先创建。"""
    final.parent.mkdir(parents=True, exist_ok=True)
    part.replace(final)


def gzip_to(source: Path, target: Path, *, level: int = 6) -> int:
    """把 ``source`` 压缩写入 ``target``，返回压缩后字节数。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        source.open("rb") as src,
        target.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=level) as dst,
    ):
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    return target.stat().st_size


# ---------------------------------------------------------------------- #
# 磁盘预检
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class SpacePreflight:
    """一次目标卷预检的结论；``ok=False`` 时调用方不得进入执行阶段。"""

    target: Path
    free_bytes: int | None
    required_bytes: int
    estimated: bool
    reserve_bytes: int
    ok: bool
    detail: str

    def to_data(self) -> dict[str, object]:
        return {
            "target": str(self.target),
            "free_bytes": self.free_bytes,
            "required_bytes": self.required_bytes,
            "estimated": self.estimated,
            "reserve_bytes": self.reserve_bytes,
            "ok": self.ok,
            "detail": self.detail,
        }


def preflight_space(
    target: Path,
    needed_bytes: int | None,
    *,
    estimated: bool = False,
    margin: float = 1.1,
    min_free_after_mb: int = 512,
) -> SpacePreflight:
    """检查目标卷剩余空间；估算未知时按 2 GiB 下限要求。"""
    info = volume_info(target)
    reserve = max(0, min_free_after_mb) * 1024 * 1024
    if needed_bytes is None:
        required = _MIN_UNKNOWN_FREE
        estimated = True
    else:
        required = int(needed_bytes * max(margin, 1.0))
    if info.free_bytes is None:
        return SpacePreflight(
            target=target,
            free_bytes=None,
            required_bytes=required,
            estimated=estimated,
            reserve_bytes=reserve,
            ok=False,
            detail=info.error or f"无法读取 {target} 所在卷的剩余空间",
        )
    ok = info.free_bytes >= required + reserve
    label = "（估算）" if estimated else ""
    detail = (
        f"剩余 {format_bytes(info.free_bytes)}，预计需要 {format_bytes(required)}{label}，"
        f"写完后至少保留 {format_bytes(reserve)}"
    )
    if not ok:
        detail += "；空间不足"
    return SpacePreflight(
        target=target,
        free_bytes=info.free_bytes,
        required_bytes=required,
        estimated=estimated,
        reserve_bytes=reserve,
        ok=ok,
        detail=detail,
    )


def volume_problem(path: Path) -> str | None:
    """拒绝 UNC / 相对路径与不支持大文件的文件系统。"""
    text = str(path)
    if text.startswith("\\\\"):
        return "不支持 UNC 网络路径，请使用本地磁盘"
    if not path.is_absolute():
        return f"必须是绝对路径：{path}"
    filesystem = _volume_filesystem(path)
    if filesystem and filesystem.casefold() in {"fat", "fat32", "exfat"}:
        return f"目标卷文件系统为 {filesystem}，不支持大文件，请使用 NTFS"
    return None


def _volume_filesystem(path: Path) -> str | None:
    if os.name != "nt":
        return None
    try:
        import ctypes

        root = path.anchor or f"{path.drive}\\"
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            root, None, 0, None, None, None, buffer, len(buffer)
        )
    except (AttributeError, OSError, ValueError):
        return None
    return buffer.value or None


# ---------------------------------------------------------------------- #
# 进度
# ---------------------------------------------------------------------- #
class ProgressReporter:
    """后台线程按固定间隔把已写字节数打到 stderr。"""

    def __init__(
        self,
        path: Path,
        total: int | None,
        *,
        label: str,
        stream: TextIO | None = None,
        interval: float = _PROGRESS_INTERVAL_S,
    ) -> None:
        self._path = path
        self._total = total
        self._label = label
        self._stream = sys.stderr if stream is None else stream
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="wslman-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def report(self) -> None:
        try:
            written = self._path.stat().st_size
        except OSError:
            return
        if self._total:
            percent = min(99, int(written * 100 / self._total))
            text = f"[{self._label}] 已写 {format_bytes(written)} / ≈{format_bytes(self._total)}（{percent}%）"
        else:
            text = f"[{self._label}] 已写 {format_bytes(written)}"
        print(text, file=self._stream)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.report()


# ---------------------------------------------------------------------- #
# 并发锁
# ---------------------------------------------------------------------- #
def default_lock_dir() -> Path:
    home = os.environ.get("FCMD_HOME")
    base = Path(home) if home else Path.home() / ".fcmd"
    return base / "locks"


def _safe_name(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in name)
    return cleaned or "distro"


@dataclass(frozen=True, slots=True)
class LockHandle:
    path: Path
    pid: int


class TransferLock:
    """同一发行版同时只允许一个传输；陈旧锁按修改时间判定。"""

    def __init__(self, distro: str, *, directory: Path | None = None, stale_s: float = _LOCK_STALE_S) -> None:
        folder = default_lock_dir() if directory is None else directory
        self.distro = distro
        self.path = folder / f"wslman-{_safe_name(distro)}.lock"
        self.stale_s = stale_s
        self._held = False

    def __enter__(self) -> LockHandle:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if attempt == 1 and self._stale():
                    with contextlib.suppress(OSError):
                        self.path.unlink()
                    continue
                raise self._busy() from None
            try:
                payload = {"distro": self.distro, "pid": os.getpid(), "created": time.time()}
                os.write(descriptor, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(descriptor)
            self._held = True
            return LockHandle(path=self.path, pid=os.getpid())
        raise self._busy()

    def __exit__(self, *exc_info: object) -> None:
        if self._held:
            with contextlib.suppress(OSError):
                self.path.unlink()
            self._held = False

    def _stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > self.stale_s
        except OSError:
            return False

    def _busy(self) -> WslmanError:
        detail = ""
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(self.path.read_text(encoding="utf-8"))
            pid = data.get("pid")
            if isinstance(pid, int):
                detail = f"（PID {pid}）"
        return runtime_error(
            f"发行版 {self.distro} 已有传输在进行",
            hint=f"锁文件 {self.path}{detail}；确认没有传输后删除该文件再重试",
        )


# ---------------------------------------------------------------------- #
# 归档
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ArchiveInfo:
    path: Path
    size_bytes: int
    compressed: bool
    members: int
    total_bytes: int
    sha256: str

    def to_data(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "compressed": self.compressed,
            "members": self.members,
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
        }


def _count_members(archive: tarfile.TarFile) -> tuple[int, int]:
    members = 0
    total = 0
    for member in archive:
        members += 1
        total += member.size
    return members, total


def archive_info(path: Path) -> ArchiveInfo:
    """流式读取 tar（必要时先解 gzip）并计算 sha256，不落临时文件。"""
    if not path.is_file():
        raise not_found(f"备份文件不存在：{path}", hint="确认路径后重试；备份默认目录为空，需显式指定")
    compressed = path.name.casefold().endswith((".gz", ".tgz"))
    members = 0
    total = 0
    try:
        if compressed:
            with gzip.open(path, "rb") as raw, tarfile.open(fileobj=raw, mode="r|") as archive:
                members, total = _count_members(archive)
        else:
            with path.open("rb") as raw, tarfile.open(fileobj=raw, mode="r|") as archive:
                members, total = _count_members(archive)
    except (OSError, tarfile.TarError) as exc:
        raise check_failed(
            f"备份文件无法解析：{path}",
            hint=f"文件可能损坏或不完整（{exc}）；可重新导出后再校验",
        ) from exc
    return ArchiveInfo(
        path=path,
        size_bytes=path.stat().st_size,
        compressed=compressed,
        members=members,
        total_bytes=total,
        sha256=sha256_file(path),
    )


@dataclass(frozen=True, slots=True)
class BackupFile:
    path: Path
    size_bytes: int
    modified: float
    compressed: bool

    def to_data(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "modified": self.modified,
            "compressed": self.compressed,
        }


def backup_files(directory: Path) -> tuple[BackupFile, ...]:
    """列出目录内的 ``*.tar`` / ``*.tar.gz`` / ``*.tgz``，按修改时间倒序。"""
    if not directory.is_dir():
        raise not_found(
            f"备份目录不存在：{directory}",
            hint="用 --dir 指定已存在的目录，或在 ~/.fcmd/wslman.json 的 backups.dir 配置；默认为空不隐式创建",
        )
    files: list[BackupFile] = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        lowered = entry.name.casefold()
        if not lowered.endswith((".tar", ".tar.gz", ".tgz")):
            continue
        stat = entry.stat()
        files.append(
            BackupFile(
                path=entry,
                size_bytes=stat.st_size,
                modified=stat.st_mtime,
                compressed=not lowered.endswith(".tar"),
            )
        )
    return tuple(sorted(files, key=lambda item: item.modified, reverse=True))
