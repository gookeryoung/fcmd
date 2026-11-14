"""devman - Windows 开发环境管理（Node.js / Python 多版本）。

子命令：``list`` / ``versions`` / ``install`` / ``uninstall`` / ``use`` /
``upgrade`` / ``init`` / ``uninit`` / ``env`` / ``exec`` / ``doctor``。

设计见 ``docs/adr/0024-devman.md``。默认不改 PATH、不碰既有的
node / python / conda / venv；所有外部副作用都可 ``--dry-run`` 预演。
"""

from __future__ import annotations

__all__: list[str] = []
