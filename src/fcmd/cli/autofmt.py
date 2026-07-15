"""autofmt - 代码格式化与检查工具。

封装 ruff format / ruff check，提供统一的代码风格维护入口。

示例
----
    fcmd autofmt fmt                       # 格式化当前目录
    fcmd autofmt fmt src                   # 格式化指定目录
    fcmd autofmt lint                      # 检查当前目录
    fcmd autofmt lint src --fix            # 检查并自动修复
"""

from __future__ import annotations

import subprocess

import fcmd

__all__ = [
    "fmt",
    "lint",
]

# ============================================================================
# 私有辅助函数
# ============================================================================


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """执行命令并返回结果（不抛异常，输出透传到终端）。

    Parameters
    ----------
    cmd:
        命令列表

    Returns
    -------
    subprocess.CompletedProcess[str]
        命令执行结果
    """
    return subprocess.run(cmd, check=False, text=True)


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
    _run(["ruff", "format", target])
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
    _run(cmd)
    print(f"ruff check 完成: {target}")
