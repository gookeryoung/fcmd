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
from typing import Any, Protocol

__all__ = [
    "LaunchError",
    "Launcher",
    "SubprocessLauncher",
    "TerminalCommand",
    "build_command",
    "find_wt",
    "spawn_detached",
    "uses_windows_path",
]

_CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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


def _hidden_startupinfo() -> Any:
    """Windows：显式要求隐藏窗口；非 Windows / 无 ``STARTUPINFO`` 时返回 ``None``。"""
    if not hasattr(subprocess, "STARTUPINFO"):
        return None
    info = subprocess.STARTUPINFO()
    info.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    info.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return info


def spawn_detached(argv: Sequence[str]) -> int:
    """启动后台进程（实例的看护循环），返回 pid；不弹控制台、不等退出。

    用于实例的看护进程（``sleep`` 循环）：wslmanw 关闭后它要继续运行。隐藏窗口
    与 :class:`~fcmd.apps.wslman._runner.SubprocessRunner` 同口径 —— 只用
    ``CREATE_NO_WINDOW``（``DETACHED_PROCESS`` 与它互斥：两个一起给时 Windows
    会给这个控制台程序反过来建一个可见的空白控制台），再叠一层
    ``STARTF_USESHOWWINDOW`` + ``SW_HIDE`` 兜底。Windows 不会因为父进程退出而
    杀子进程，所以不必再靠 ``DETACHED_PROCESS`` 保活。
    """
    try:
        process = subprocess.Popen(
            list(argv),
            creationflags=_CREATE_NO_WINDOW,
            startupinfo=_hidden_startupinfo(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        raise LaunchError(
            f"启动 {argv[0]} 失败：{exc}",
            hint="确认 wsl.exe 可用；若 WSL 未启用请以管理员运行 wsl --install",
        ) from exc
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
    run: Sequence[str] = (),
) -> TerminalCommand:
    """构造启动命令；``program`` 为配置里的 ``terminal.program``（auto/wt/conhost）。

    ``extra_args`` 只作用于 ``wt.exe``（插在 ``new-tab`` 之后），回退路径没有对应的
    扩展点。``run`` 追加到 ``wsl.exe`` 的 argv 末尾，用于 ``--exec <程序>`` 之类的
    场景（例如在实例内以当前目录启动 AI Agent）；它对两种终端形态都生效。
    """
    inner = [wsl_path, "-d", distro]
    if cwd:
        inner.extend(["--cd", cwd])
    if user:
        inner.extend(["-u", user])
    inner.extend(run)

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
