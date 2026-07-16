"""autofmt - 代码格式化与检查工具。

封装 ruff format / ruff check，提供统一的代码风格维护入口。

示例
----
    fcmd autofmt fmt                       # 格式化当前目录
    fcmd autofmt fmt --target src          # 格式化指定目录
    fcmd autofmt lint                      # 检查当前目录
    fcmd autofmt lint --target src --fix   # 检查并自动修复
"""

from __future__ import annotations

import fcmd
from fcmd.models import run_command

__all__ = [
    "fmt",
    "lint",
]


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("autofmt", subcommand="fmt", help="格式化代码 (ruff format)")
def fmt(target: str = ".") -> None:
    """使用 ruff format 格式化代码。

    Parameters
    ----------
    target:
        目标路径（默认：当前目录）
    """
    run_command(["ruff", "format", target])
    print(f"ruff format 完成: {target}")


@fcmd.tool("autofmt", subcommand="lint", help="代码检查 (ruff check)")
def lint(target: str = ".", fix: bool = False) -> None:
    """使用 ruff check 检查代码。

    Parameters
    ----------
    target:
        目标路径（默认：当前目录）
    fix:
        自动修复问题（含 unsafe fixes）
    """
    cmd = ["ruff", "check", target]
    if fix:
        cmd.extend(["--fix", "--unsafe-fixes"])
    run_command(cmd)
    print(f"ruff check 完成: {target}")
