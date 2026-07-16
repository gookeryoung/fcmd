"""reseticoncache - 重置 Windows 图标缓存工具。

执行流程：终止 explorer → 删除 IconCache.db → 删除 iconcache* → 重启 explorer。
仅在 Windows 上执行，非 Windows 平台打印提示并跳过。

示例
----
    fcmd reseticoncache
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = ["reset_icon_cache_run"]


@fcmd.tool("reseticoncache", help="重置 Windows 图标缓存")
def reset_icon_cache_run() -> None:
    """重置 Windows 图标缓存。

    执行流程：终止 explorer → 删除 IconCache.db → 删除 iconcache* → 重启 explorer。
    仅在 Windows 上执行，非 Windows 平台打印提示并跳过。
    """
    if sys.platform != "win32":
        print("reseticoncache: 仅在 Windows 上支持")
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        print("reseticoncache: LOCALAPPDATA 环境变量未设置")
        return

    icon_cache_db = Path(local_app_data) / "IconCache.db"
    explorer_cache_dir = Path(local_app_data) / "Microsoft" / "Windows" / "Explorer"

    print("正在终止 explorer 进程...")
    run_command(["taskkill", "/f", "/im", "explorer.exe"])

    if icon_cache_db.exists():
        print(f"删除图标缓存: {icon_cache_db}")
        run_command(["cmd", "/c", "del", "/a", "/q", str(icon_cache_db)])

    if explorer_cache_dir.exists():
        print(f"清理 Explorer 缓存: {explorer_cache_dir}")
        run_command(["cmd", "/c", "del", "/a", "/q", str(explorer_cache_dir / "iconcache*")])

    print("重启 explorer...")
    run_command(["cmd", "/c", "start", "explorer.exe"])
    print("图标缓存已重置")
