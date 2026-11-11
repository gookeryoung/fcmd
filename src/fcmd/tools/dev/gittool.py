"""gittool - Git 执行工具。

提供初始化/添加提交/初始化子目录/清理/推送/拉取等子命令。

``a`` / ``i`` 为聚合入口，经隐藏任务链 ``_init`` → ``_add`` → ``_commit``
保证顺序：已有仓库不重复 init、无更改不提交（守卫逻辑在函数内实现）。

示例
----
    fcmd gitt a --message "feat: 新功能"   # 添加并提交（有更改时）
    fcmd gitt i                           # 初始化并提交
    fcmd gitt isub                        # 初始化所有子目录的 Git 仓库
    fcmd gitt c                           # 清理未跟踪文件（保留排除目录）并查看状态
    fcmd gitt ca                          # 清理全部未跟踪文件（含排除目录）
    fcmd gitt p                           # 推送
    fcmd gitt pl                          # 拉取
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import fcmd as fx

__all__ = ["git_init_sub_dirs"]


__tool_aliases__ = ["gitt", "git"]


# ============================================================================
# fn 子命令
# ============================================================================


@fx.tool("gittool", subcommand="isub", help="初始化子目录 Git 仓库")
def git_init_sub_dirs(message: str = "init commit") -> None:
    """遍历当前目录的子目录，对每个子目录执行 git init + add + commit。

    跳过非目录文件。每个子目录独立初始化为 Git 仓库。

    Parameters
    ----------
    message:
        提交信息（默认 ``init commit``）
    """
    cwd = Path.cwd()
    sub_dirs = sorted(d for d in cwd.iterdir() if d.is_dir())
    if not sub_dirs:
        print("当前目录无子目录")
        return
    for subdir in sub_dirs:
        subprocess.run(["git", "init"], cwd=subdir, check=False, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=subdir, check=False, capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m", message], cwd=subdir, check=False, capture_output=True, text=True)
        print(f"已初始化: {subdir.name}")


# ============================================================================
# 守卫链：_init → _add → _commit
# ============================================================================


@fx.tool("gittool", subcommand="_init", help="初始化 Git 仓库（内部）", hidden=True)
def git_init() -> None:
    """初始化 Git 仓库（已有 ``.git`` 时 no-op）。"""
    if Path(".git").exists():
        return
    subprocess.run(["git", "init"], check=True)


@fx.tool(
    "gittool",
    subcommand="_add",
    help="暂存全部更改（内部）",
    cmd=["git", "add", "."],
    needs=["_init"],
    hidden=True,
)
def git_add() -> None:
    """暂存全部更改。"""


@fx.tool(
    "gittool",
    subcommand="_commit",
    help="提交暂存更改（内部）",
    needs=["_add"],
    hidden=True,
)
def git_commit(message: str = "init commit") -> None:
    """提交暂存更改（``git status --porcelain`` 为空时 no-op）。

    Parameters
    ----------
    message:
        提交信息
    """
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    if not status.stdout.strip():
        return
    subprocess.run(["git", "commit", "-m", message], check=True)


@fx.tool(
    "gittool",
    subcommand="a",
    help="添加并提交",
    needs=["_commit"],
    strategy="sequential",
)
def add_and_commit(message: str = "chore: update") -> None:
    """添加并提交。

    Parameters
    ----------
    message:
        提交信息
    """


@fx.tool(
    "gittool",
    subcommand="i",
    help="初始化并提交",
    needs=["_commit"],
    strategy="sequential",
)
def init_and_commit(message: str = "init commit") -> None:
    """初始化并提交。

    Parameters
    ----------
    message:
        提交信息
    """


# ============================================================================
# 清理/推送/拉取
# ============================================================================


@fx.tool(
    "gittool",
    subcommand="clean",
    help="清理未跟踪文件",
    cmd=[
        "git",
        "clean",
        "-xfd",
        "-e",
        ".vscode",
        "-e",
        ".idea",
        "-e",
        ".editorconfig",
        "-e",
        ".trae",
        "-e",
        ".qoder",
        "-e",
        ".venv",
        "-e",
        ".git",
        "-e",
        ".ruff_cache",
        "-e",
        ".tox",
        "-e",
        "node_modules",
    ],
    hidden=True,
)
def clean() -> None:
    """清理未跟踪文件（保留编辑器/项目缓存目录）。"""


@fx.tool(
    "gittool",
    subcommand="c",
    help="清理并查看状态",
    cmd=["git", "status", "--porcelain"],
    needs=["clean"],
)
def clean_and_status() -> None:
    """清理并查看状态。"""


@fx.tool(
    "gittool",
    subcommand="ca",
    help="清理全部未跟踪文件（含排除目录）",
    cmd=["git", "clean", "-xfd", "."],
)
def clean_all() -> None:
    """清理全部未跟踪文件（含排除目录）。"""


@fx.tool("gittool", subcommand="p", help="推送", cmd=["git", "push"])
def push() -> None:
    """推送。"""


@fx.tool("gittool", subcommand="pl", help="拉取", cmd=["git", "pull"])
def pull() -> None:
    """拉取。"""


@fx.main("gittool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
