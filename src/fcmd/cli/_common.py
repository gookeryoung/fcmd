"""cli 共享辅助：跨工具复用的常量与错误提示。

本模块以下划线开头，``ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

import difflib

__all__ = ["_BUILTIN_COMMANDS", "print_unknown_tool"]

# 内建命令名（不通过 @fx.tool 注册，由 FcmdApp 直接处理）
# 放在此处供 main.py / _completion_scripts.py 共享，避免循环导入
_BUILTIN_COMMANDS: tuple[str, ...] = ("graph", "info", "completion", "yaml", "env", "doctor", "profiler")


def print_unknown_tool(name: str) -> None:
    """打印未知工具错误 + 模糊匹配建议（用法错误，退出码 2）。"""
    from rich.markup import escape

    from fcmd.cli._discovery import _TOOL_ALIASES
    from fcmd.console import print_error

    suggestion = ""
    matches = difflib.get_close_matches(name, list(_TOOL_ALIASES), n=3, cutoff=0.5)
    if matches:
        suggestion = f"是否想用: {', '.join(matches)}？；"
    print_error(
        f"未知工具 {escape(name)!r}",
        code="UNKNOWN_TOOL",
        hint=f"{suggestion}运行 'fcmd' 查看可用工具列表",
    )
