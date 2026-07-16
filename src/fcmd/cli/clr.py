"""clr - 清屏工具。

跨平台清屏：Windows 调用 ``cls``，Linux/macOS 调用 ``clear``。

示例
----
    fcmd clr
"""

from __future__ import annotations

import subprocess
import sys

import fcmd

__all__ = ["clear_screen"]


def clear_screen() -> int:
    """清屏（跨平台）。

    Windows 调用 ``cls``，Linux/macOS 调用 ``clear``。
    清屏失败（如非 TTY 环境）不抛异常，仅返回非零退出码。

    Returns
    -------
    int
        命令返回码（``0`` 表示成功）
    """
    cmd = ["cls"] if sys.platform == "win32" else ["clear"]
    result = subprocess.run(cmd, check=False)
    return result.returncode


@fcmd.tool("clr", help="清屏（跨平台）")
def clear_screen_run() -> None:
    """清屏（跨平台）。

    Windows 调用 ``cls``，Linux/macOS 调用 ``clear``。
    """
    clear_screen()
