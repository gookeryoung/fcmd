"""_env_persist - 跨平台环境变量持久化辅助（私有，不参与工具发现）。

``os.environ[name] = value`` 仅修改当前进程的环境变量副本，进程退出即失效。
本模块提供 :func:`persist_env`，将环境变量实质写入用户层面：

- Windows：写入注册表 ``HKEY_CURRENT_USER\\Environment``（用户级环境变量），
  并广播 ``WM_SETTINGCHANGE`` 通知已运行的进程刷新环境（如资源管理器、
  新开的终端）。相比 ``setx`` 无 1024 字符长度截断限制，且不依赖外部命令。
- Linux/macOS：在 shell 启动文件（默认 ``~/.profile``）中追加或更新
  ``export NAME="value"`` 行，登录后新终端自动生效。

无论平台，同时更新当前进程的 ``os.environ``，使本次会话内立即可用。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

__all__ = ["persist_env", "shell_profile_path"]

# Windows 注册表用户环境变量键路径（HKEY_CURRENT_USER 下）。
_WIN_ENV_KEY: str = "Environment"


def shell_profile_path() -> Path:
    """返回 Linux/macOS 上用于持久化环境变量的 shell 启动文件路径。

    优先使用 ``~/.profile``（登录 shell 通用，bash/sh/zsh 均会读取）。
    """
    return Path.home() / ".profile"


def _persist_env_windows(name: str, value: str) -> None:  # pragma: no cover - 仅 Windows 运行
    """在 Windows 上将环境变量写入注册表 ``HKCU\\Environment`` 并广播刷新通知。"""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_EXPAND_SZ, value)

    # 广播 WM_SETTINGCHANGE，通知已运行进程刷新环境块（否则新终端才生效）。
    hwnd_broadcast = 0xFFFF  # HWND_BROADCAST
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
        smto_abortifhung,
        5000,
        None,
    )


def _persist_env_unix(name: str, value: str) -> None:
    """在 Linux/macOS 上将 ``export NAME="value"`` 追加或更新到 shell 启动文件。"""
    profile = shell_profile_path()
    profile.parent.mkdir(parents=True, exist_ok=True)

    export_line = f'export {name}="{value}"'
    try:
        existing = profile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

    # 匹配同名变量的已有 export 行（行首可有空白），存在则替换，否则追加。
    pattern = re.compile(rf"^\s*export\s+{re.escape(name)}=.*$", re.MULTILINE)
    if pattern.search(existing):
        updated = pattern.sub(export_line, existing)
    else:
        prefix = existing if existing.endswith("\n") or not existing else existing + "\n"
        updated = f"{prefix}{export_line}\n"

    profile.write_text(updated, encoding="utf-8")


def persist_env(name: str, value: str) -> None:
    """持久化设置环境变量（跨平台），并同步更新当前进程。

    Parameters
    ----------
    name:
        环境变量名。
    value:
        环境变量值。

    Notes
    -----
    Windows 写入注册表用户环境变量并广播刷新；Linux/macOS 写入
    ``~/.profile``。两种平台均更新 ``os.environ`` 使本进程立即可用。
    持久化写入通常需重开终端或重新登录后对新进程生效。
    """
    os.environ[name] = value
    if sys.platform == "win32":
        _persist_env_windows(name, value)
    else:
        _persist_env_unix(name, value)
