"""taskkill - 进程终止工具。

跨平台按名称终止进程：Windows 用 ``taskkill``，Linux/macOS 用 ``pkill``。

示例
----
    fcmd taskkill chrome.exe python
    fcmd taskkill node
"""

from __future__ import annotations

import subprocess
import sys

import fcmd

__all__ = [
    "kill_process",
    "taskkill_run",
]


def kill_process(process_name: str) -> int:
    """终止匹配名称的进程（跨平台）。

    Windows 使用 ``taskkill /f /im <name>*``，
    Linux/macOS 使用 ``pkill -f <name>*``。

    Parameters
    ----------
    process_name:
        进程名称（自动追加 ``*`` 通配符）

    Returns
    -------
    int
        命令返回码：``0`` 表示已发送终止信号，``1`` 表示未找到匹配进程，
        其他值表示终止失败。
    """
    if sys.platform == "win32":
        cmd = ["taskkill", "/f", "/im", f"{process_name}*"]
    else:
        cmd = ["pkill", "-f", f"{process_name}*"]

    # pkill 返回 1 表示无匹配进程（非错误），故 check=False + 手动检查 returncode
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    return result.returncode


@fcmd.tool("taskkill", help="按名称终止进程（跨平台）")
def taskkill_run(process_names: list[str]) -> None:
    """按名称终止进程（跨平台）。

    Windows 使用 ``taskkill /f /im <name>*``，
    Linux/macOS 使用 ``pkill -f <name>*``。

    Parameters
    ----------
    process_names:
        进程名称列表（如 ``["chrome.exe", "python"]``）
    """
    for name in process_names:
        print(f"终止进程: {name}")
        returncode = kill_process(name)
        if returncode == 0:
            print(f"  已发送终止信号: {name}")
        else:
            print(f"  未找到匹配进程或终止失败 (returncode={returncode}): {name}")
