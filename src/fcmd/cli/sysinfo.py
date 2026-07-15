"""sysinfo - 系统信息收集工具。

收集并打印当前环境的系统信息（Python/平台/内存/磁盘/CPU），用于环境诊断与 bug 报告。

示例
----
    fcmd sysinfo
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

import fcmd

__all__ = [
    "collect_sysinfo",
    "print_sysinfo",
]


def _format_bytes(size: int) -> str:
    """将字节数格式化为人类可读字符串。

    Parameters
    ----------
    size:
        字节数

    Returns
    -------
    str
        形如 ``1.5 GB`` 的字符串
    """
    value: float = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def collect_sysinfo() -> dict[str, str]:
    """收集系统信息，返回有序字典。

    Returns
    -------
    dict[str, str]
        系统信息键值对（Python 版本/路径、平台、架构、内存、磁盘、CPU 等）
    """
    info: dict[str, str] = {
        "Python 版本": sys.version.split()[0],
        "Python 路径": sys.executable,
        "平台": platform.platform(),
        "架构": platform.machine(),
        "处理器": platform.processor() or "未知",
        "操作系统": f"{platform.system()} {platform.release()}",
    }

    # 内存信息（仅 Linux/macOS 可获取， Windows 无 resource 模块）
    try:
        import resource

        # getrusage 返回 ru_maxrss: Linux 上单位 KB, macOS 上单位字节
        usage = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[missing-attribute]
        if platform.system() == "Darwin":
            info["内存峰值"] = _format_bytes(usage.ru_maxrss)
        else:
            info["内存峰值"] = _format_bytes(usage.ru_maxrss * 1024)
    except (OSError, AttributeError, ImportError):
        pass

    # 磁盘信息（当前目录所在分区）
    try:
        usage = shutil.disk_usage(Path.cwd())
        info["磁盘总量"] = _format_bytes(usage.total)
        info["磁盘已用"] = _format_bytes(usage.used)
        info["磁盘可用"] = _format_bytes(usage.free)
    except OSError:  # pragma: no cover - 罕见平台
        pass

    # CPU 核心数
    info["CPU 核心数"] = str(os.cpu_count() or "未知")

    # 当前工作目录
    info["工作目录"] = str(Path.cwd())

    return info


def print_sysinfo() -> None:
    """收集并打印系统信息。"""
    info = collect_sysinfo()
    print("=" * 50)
    print("系统信息")
    print("=" * 50)
    for key, value in info.items():
        print(f"  {key:16s}: {value}")
    print("=" * 50)


@fcmd.tool("sysinfo", help="收集并打印系统信息")
def sysinfo_run() -> None:
    """收集并打印当前环境的系统信息。

    包括 Python 版本、平台、架构、内存、磁盘、CPU 核心数等，用于环境诊断与 bug 报告。
    """
    print_sysinfo()
