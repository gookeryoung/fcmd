"""cli 共享辅助：跨工具复用的常量与错误提示。

本模块以下划线开头，``ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

import difflib

from fcmd.console import get_console

__all__ = ["IGNORE_DIRS", "IGNORE_EXT", "_BUILTIN_COMMANDS", "print_unknown_tool"]

# 内建命令名（不通过 @fx.tool 注册，由 FcmdApp 直接处理）
# 放在此处供 main.py / _completion_scripts.py 共享，避免循环导入
_BUILTIN_COMMANDS: tuple[str, ...] = ("graph", "info", "completion", "yaml", "env", "doctor", "profiler")

# 文件遍历时跳过的目录名（跨工具共享）
IGNORE_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".venv",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pyrefly_cache",
    "*.egg-info",
}

# 文件遍历时跳过的扩展名（压缩包等）
IGNORE_EXT: set[str] = {".zip", ".rar", ".7z", ".tar", ".gz", ".pyc", ".pyo"}


def print_unknown_tool(name: str) -> None:
    """打印未知工具错误 + 模糊匹配建议。"""
    from fcmd.cli._discovery import _TOOL_ALIASES

    console = get_console()
    console.print(f"[red]错误:[/red] 未知工具 [yellow]{name!r}[/yellow]")
    suggestions = difflib.get_close_matches(name, list(_TOOL_ALIASES), n=3, cutoff=0.5)
    if suggestions:
        console.print(f"[dim]是否想用: {', '.join(suggestions)}[/dim]")
    console.print("[dim]运行 'fcmd' 查看可用工具列表[/dim]")
