"""locllmw 概览页的硬件检测：CPU / GPU / 内存 / 硬盘与模型盘。

检测结果可序列化到 ``~/.fcmd/ui/locllmw.json``：首次打开窗口时检测并写入，
之后读取缓存展示，用户点「重新检测」时再刷新。磁盘 / CPU 名称读取只依赖
标准库与 ``psutil``；GPU 复用 :func:`fcmd.apps.locllm.llmengine.detect_gpu`，
便于单测注入 ``runner``。
"""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.locllm import llmengine, llmenv
from fcmd.apps.locllm.llmbase import Runner, human_size, now_iso, run_command

__all__ = [
    "DISK_SKIP_FSTYPES",
    "DiskInfo",
    "HardwareSnapshot",
    "detect_cpu_name",
    "detect_hardware",
    "disk_for_path",
    "format_disk",
    "snapshot_from_dict",
    "snapshot_to_dict",
]

#: 不纳入硬件面板的分区类型（光驱 / 虚拟盘 / 临时盘）
DISK_SKIP_FSTYPES = frozenset({"", "cdrom", "iso9660", "udf", "squashfs", "tmpfs", "devtmpfs"})

_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class DiskInfo:
    """一个固定磁盘分区（字节）。"""

    mount: str
    fstype: str
    total: int
    free: int

    @property
    def used(self) -> int:
        """已用空间。"""
        return max(self.total - self.free, 0)

    @property
    def percent(self) -> float:
        """已用比例（0–100）。"""
        return self.used / self.total * 100 if self.total else 0.0


@dataclass(frozen=True, slots=True)
class HardwareSnapshot:
    """一次硬件检测结果；``model_root`` 为当前主模型目录。"""

    cpu: str = "未知处理器"
    cpu_cores: int = 0
    cpu_threads: int = 0
    memory_total: int = 0
    gpu: str = ""
    gpu_mib: int | None = None
    disks: tuple[DiskInfo, ...] = ()
    model_root: str = ""
    detected_at: str = ""


def detect_cpu_name() -> str:
    """读取 CPU 型号：Windows 注册表 / Linux ``/proc/cpuinfo``，最后回落 platform。"""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except OSError:
            pass
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    for line in text.splitlines():
        if line.lower().startswith("model name"):
            _, _, value = line.partition(":")
            if value.strip():
                return value.strip()
    return platform.processor() or platform.machine() or "未知处理器"


def _disk_infos() -> tuple[DiskInfo, ...]:
    """枚举固定分区；读不到容量的分区跳过。"""
    import psutil

    disks: list[DiskInfo] = []
    seen: set[str] = set()
    for part in psutil.disk_partitions(all=False):
        fstype = (part.fstype or "").lower()
        if fstype in DISK_SKIP_FSTYPES or part.mountpoint in seen:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        seen.add(part.mountpoint)
        disks.append(DiskInfo(part.mountpoint, part.fstype, int(usage.total), int(usage.free)))
    return tuple(disks)


def _primary_model_root(models_root: Path | None) -> Path:
    if models_root is not None:
        return models_root.expanduser()
    return llmenv.models_dir()


def detect_hardware(
    models_root: Path | None = None,
    *,
    runner: Runner | None = None,
) -> HardwareSnapshot:
    """检测当前硬件并返回快照；GPU 不可用时 ``gpu_mib`` 为 ``None``。"""
    import psutil

    gpu = llmengine.detect_gpu(runner or run_command)
    memory = psutil.virtual_memory()
    return HardwareSnapshot(
        cpu=detect_cpu_name(),
        cpu_cores=int(psutil.cpu_count(logical=False) or 0),
        cpu_threads=int(psutil.cpu_count(logical=True) or 0),
        memory_total=int(memory.total),
        gpu=gpu.name if gpu is not None else "",
        gpu_mib=gpu.memory_mib if gpu is not None else None,
        disks=_disk_infos(),
        model_root=str(_primary_model_root(models_root)),
        detected_at=now_iso(),
    )


def disk_for_path(path: Path | str, disks: Sequence[DiskInfo]) -> DiskInfo | None:
    """返回包含 ``path`` 的磁盘分区：按挂载点最长前缀匹配。"""
    target = os.path.normcase(str(Path(path).resolve()))
    best: DiskInfo | None = None
    best_len = -1
    for disk in disks:
        mount = os.path.normcase(str(Path(disk.mount).resolve()))
        prefix = mount if mount.endswith(os.sep) else mount + os.sep
        if (target == mount or target.startswith(prefix)) and len(mount) > best_len:
            best = disk
            best_len = len(mount)
    return best


def format_disk(disk: DiskInfo | None) -> str:
    """格式化为「挂载点：可用 / 总计（xx%）」。"""
    if disk is None:
        return "未检测到磁盘"
    return f"{disk.mount}：{human_size(disk.free)} 可用 / {human_size(disk.total)}（已用 {disk.percent:.1f} %）"


def snapshot_to_dict(snapshot: HardwareSnapshot) -> dict[str, Any]:
    """序列化为可写入 JSON 的字典。"""
    return {
        "schema": _SCHEMA,
        "cpu": snapshot.cpu,
        "cpu_cores": snapshot.cpu_cores,
        "cpu_threads": snapshot.cpu_threads,
        "memory_total": snapshot.memory_total,
        "gpu": snapshot.gpu,
        "gpu_mib": snapshot.gpu_mib,
        "disks": [
            {"mount": disk.mount, "fstype": disk.fstype, "total": disk.total, "free": disk.free}
            for disk in snapshot.disks
        ],
        "model_root": snapshot.model_root,
        "detected_at": snapshot.detected_at,
    }


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def snapshot_from_dict(raw: object) -> HardwareSnapshot | None:
    """从配置字典还原快照；结构不合法时返回 ``None``（视为未检测）。"""
    if not isinstance(raw, Mapping) or raw.get("schema") != _SCHEMA:
        return None
    disks: list[DiskInfo] = []
    raw_disks = raw.get("disks")
    if isinstance(raw_disks, list):
        for item in raw_disks:
            if not isinstance(item, Mapping) or not isinstance(item.get("mount"), str):
                continue
            disks.append(
                DiskInfo(
                    mount=str(item["mount"]),
                    fstype=str(item.get("fstype", "")),
                    total=_as_int(item.get("total")),
                    free=_as_int(item.get("free")),
                )
            )
    gpu_mib = raw.get("gpu_mib")
    return HardwareSnapshot(
        cpu=str(raw.get("cpu") or "未知处理器"),
        cpu_cores=_as_int(raw.get("cpu_cores")),
        cpu_threads=_as_int(raw.get("cpu_threads")),
        memory_total=_as_int(raw.get("memory_total")),
        gpu=str(raw.get("gpu") or ""),
        gpu_mib=_as_int(gpu_mib) if gpu_mib is not None else None,
        disks=tuple(disks),
        model_root=str(raw.get("model_root") or ""),
        detected_at=str(raw.get("detected_at") or ""),
    )
