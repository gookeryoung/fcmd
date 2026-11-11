"""sysinfo - 收集并打印当前环境的系统信息。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras.system import collect_system_info

__all__ = ["sysinfo"]


@fx.tool("sysinfo", help="收集并打印系统信息")
def sysinfo() -> None:
    """收集并打印当前环境的系统信息（Python/平台/内存/磁盘/CPU）。"""
    info = collect_system_info()
    print("=" * 50)
    print("系统信息")
    print("=" * 50)
    for key, value in info.items():
        print(f"  {key:16s}: {value}")
    print("=" * 50)
