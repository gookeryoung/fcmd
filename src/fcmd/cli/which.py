"""which - 命令查找工具。

跨平台查找可执行命令路径，对每个命令打印 ``<cmd> -> <path>`` 或 ``<cmd> -> 未找到``。

示例
----
    fcmd which python git uv
    fcmd which ruff
"""

from __future__ import annotations

import shutil

import fcmd

__all__ = [
    "find_command",
    "which_run",
]


def find_command(command: str) -> str | None:
    """查找可执行命令的完整路径。

    跨平台使用 ``shutil.which``（Windows/Linux/macOS 均适用）。

    Parameters
    ----------
    command:
        要查找的命令名称

    Returns
    -------
    str | None
        命令完整路径，未找到时返回 ``None``
    """
    return shutil.which(command)


@fcmd.tool("which", help="查找可执行命令路径（跨平台）")
def which_run(commands: list[str]) -> None:
    """查找可执行命令路径（跨平台）。

    对每个命令打印 ``<cmd> -> <path>`` 或 ``<cmd> -> 未找到``。

    Parameters
    ----------
    commands:
        要查找的命令名称列表
    """
    for command in commands:
        path = find_command(command)
        if path is None:
            print(f"{command} -> 未找到")
        else:
            print(f"{command} -> {path}")
