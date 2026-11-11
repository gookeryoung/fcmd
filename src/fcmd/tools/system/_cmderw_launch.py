"""cmderw 启动参数解析：``--left`` / ``--right`` / ``--select`` 的路径归一与容错。

纯逻辑与 Tk 分离：非法或缺失的路径不让启动失败，而是记录提示后忽略该项。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["LaunchRequest", "LaunchResolution", "resolve_launch"]


@dataclass(frozen=True)
class LaunchRequest:
    """命令行传入的原始参数。"""

    left: str | None = None
    right: str | None = None
    select: str | None = None


@dataclass(frozen=True)
class LaunchResolution:
    """归一后的启动目标；不可用项为 ``None``，原因写入 ``messages``。"""

    left: str | None = None
    right: str | None = None
    select_dir: str | None = None
    select_name: str | None = None
    messages: tuple[str, ...] = ()


def _absolute(raw: str, cwd: str | None) -> Path:
    path = Path(os.path.expandvars(raw.strip())).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    return path


def _resolve_dir(raw: str | None, label: str, cwd: str | None, messages: list[str]) -> str | None:
    if raw is None:
        return None
    if not raw.strip():
        messages.append(f"{label} 为空，已忽略")
        return None
    path = _absolute(raw, cwd)
    if not path.is_dir():
        messages.append(f"{label} 不是可用目录：{path}")
        return None
    return str(path)


def resolve_launch(request: LaunchRequest, *, cwd: str | None = None) -> LaunchResolution:
    """把启动参数归一为绝对路径。

    参数:
        request: 原始 ``--left`` / ``--right`` / ``--select``。
        cwd: 相对路径的基准目录；默认用当前工作目录。

    返回:
        解析结果；非法项置空并在 ``messages`` 中说明原因，调用方只展示不中断启动。
    """
    messages: list[str] = []
    left = _resolve_dir(request.left, "--left", cwd, messages)
    right = _resolve_dir(request.right, "--right", cwd, messages)
    select_dir: str | None = None
    select_name: str | None = None
    if request.select is not None:
        if not request.select.strip():
            messages.append("--select 为空，已忽略")
        else:
            path = _absolute(request.select, cwd)
            if path.is_dir():
                select_dir = str(path)
            elif path.is_file():
                select_dir = str(path.parent)
                select_name = path.name
            else:
                messages.append(f"--select 路径不存在：{path}")
    return LaunchResolution(
        left=left,
        right=right,
        select_dir=select_dir,
        select_name=select_name,
        messages=tuple(messages),
    )
