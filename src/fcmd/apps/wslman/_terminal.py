"""在新终端窗口中打开发行版：``wt.exe`` 优先，回退 ``wsl.exe`` + 新控制台。

argv 一律以列表传递，含空格的发行版名与路径不依赖 shell 引号；启动器可注入，
单元测试只记录 argv，不真正拉起窗口。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Protocol

__all__ = [
    "LaunchError",
    "Launcher",
    "SubprocessLauncher",
    "TerminalCommand",
    "build_command",
    "find_wt",
    "uses_windows_path",
]

_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
_LAUNCH_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class TerminalCommand:
    """一条待启动的终端命令；``wait_for_exit`` 只对启动器进程有意义。"""

    terminal: str  # "wt" | "conhost"
    argv: tuple[str, ...]
    new_console: bool
    wait_for_exit: bool


class LaunchError(RuntimeError):
    """启动终端失败；``hint`` 供 CLI 原样展示。"""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class Launcher(Protocol):
    """终端启动器协议。"""

    def launch(self, command: TerminalCommand, *, timeout: float | None = None) -> int: ...


class SubprocessLauncher:
    """真实启动器：``wt.exe`` 等启动器进程会自行退出，``wsl.exe`` 则持续运行。"""

    def launch(self, command: TerminalCommand, *, timeout: float | None = _LAUNCH_TIMEOUT_S) -> int:
        try:
            process = subprocess.Popen(
                list(command.argv),
                creationflags=_CREATE_NEW_CONSOLE if command.new_console else 0,
                close_fds=True,
            )
        except OSError as exc:
            raise LaunchError(
                f"启动 {command.argv[0]} 失败：{exc}",
                hint="确认 Windows Terminal 或 wsl.exe 可执行；可在 ~/.fcmd/wslman.json 把 terminal.program 设为 conhost",
            ) from exc

        if not command.wait_for_exit:
            return process.pid
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 窗口已拉起且进程未退出，按成功处理，不阻塞调用方。
            return process.pid
        if returncode != 0:
            raise LaunchError(f"{command.argv[0]} 退出码 {returncode}")
        return process.pid


def find_wt() -> str | None:
    """在 PATH 与 ``%LOCALAPPDATA%\\Microsoft\\WindowsApps`` 中查找 ``wt.exe``。"""
    found = shutil.which("wt.exe")
    if found:
        return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Microsoft" / "WindowsApps" / "wt.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def build_command(  # noqa: PLR0913 - argv 构造按 ADR 逐项展开，保持扁平
    *,
    wsl_path: str,
    distro: str,
    cwd: str | None = None,
    user: str | None = None,
    program: str = "auto",
    extra_args: Sequence[str] = (),
    wt_path: str | None = None,
) -> TerminalCommand:
    """构造启动命令；``program`` 为配置里的 ``terminal.program``（auto/wt/conhost）。

    ``extra_args`` 只作用于 ``wt.exe``（插在 ``new-tab`` 之后），回退路径没有对应的
    扩展点。
    """
    inner = [wsl_path, "-d", distro]
    if cwd:
        inner.extend(["--cd", cwd])
    if user:
        inner.extend(["-u", user])

    if program == "wt":
        resolved = wt_path if wt_path is not None else find_wt()
        if resolved is None:
            raise LaunchError(
                "terminal.program=wt，但未找到 wt.exe",
                hint="安装 Windows Terminal，或把 ~/.fcmd/wslman.json 的 terminal.program 改为 auto / conhost",
            )
    elif program == "auto":
        resolved = wt_path if wt_path is not None else find_wt()
    else:
        resolved = None

    if resolved is None:
        return TerminalCommand(terminal="conhost", argv=tuple(inner), new_console=True, wait_for_exit=False)

    argv = [resolved, "new-tab", *extra_args, "--title", f"wsl:{distro}", "--", *inner]
    return TerminalCommand(terminal="wt", argv=tuple(argv), new_console=False, wait_for_exit=True)


def uses_windows_path(value: str) -> bool:
    """判断 ``--cwd`` 是否为带盘符的 Windows 路径（不要求路径已存在）。"""
    return bool(PureWindowsPath(value).drive) and ("/" in value or "\\" in value)
