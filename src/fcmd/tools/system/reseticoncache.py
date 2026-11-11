"""reseticoncache - 重置 Windows 图标缓存（平台命令）。"""

from __future__ import annotations

import sys

import fcmd as fx

__all__ = ["reseticoncache"]

_WIN_CMD = (
    r"%SystemRoot%\System32\taskkill.exe /f /im explorer.exe"
    r' & if exist "%LOCALAPPDATA%\IconCache.db" del /a /q "%LOCALAPPDATA%\IconCache.db"'
    r' & if exist "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*"'
    r' del /a /q "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*"'
    r" & start explorer.exe"
)
_UNIX_CMD = 'echo "reseticoncache: 仅在 Windows 上支持"'


@fx.tool(
    "reseticoncache",
    help="重置 Windows 图标缓存",
    description="重置 Windows 图标缓存（仅 Windows 生效）",
    cmd=_WIN_CMD if sys.platform == "win32" else _UNIX_CMD,
)
def reseticoncache() -> None:
    pass  # pragma: no cover - cmd 任务：签名仅驱动 CLI，函数体不执行
