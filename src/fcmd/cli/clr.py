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


def _clear_cmd() -> str:
    """返回当前平台的清屏命令。"""
    return "cls" if sys.platform == "win32" else "clear"


def clear_screen() -> int:
    """清屏（跨平台）。

    Windows 调用 ``cls``（通过 cmd shell），Linux/macOS 调用 ``clear``。
    清屏失败（如非 TTY 环境）不抛异常，仅返回非零退出码。

    Returns
    -------
    int
        命令返回码（``0`` 表示成功）

    Raises
    ------
    RuntimeError
        命令未找到时抛出（包含命令名，便于诊断）
    """
    cmd = _clear_cmd()
    try:
        result = subprocess.run(cmd, shell=sys.platform == "win32", check=False)
    except FileNotFoundError:
        raise RuntimeError(f"清屏命令未找到: {cmd}") from None
    return result.returncode


@fcmd.tool("clr", help="清屏（跨平台）")
def clear_screen_run() -> None:
    """清屏（跨平台）。

    Windows 调用 ``cls``，Linux/macOS 调用 ``clear``。
    """
    clear_screen()
