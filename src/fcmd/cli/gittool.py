"""gittool - Git 执行工具。

提供添加提交/初始化/初始化子目录/清理/推送/拉取子命令。

示例
----
    fcmd gittool a -m "feat: 新功能"   # 添加并提交
    fcmd gittool i                       # 初始化并提交
    fcmd gittool isub                    # 初始化所有子目录的 Git 仓库
    fcmd gittool c                       # 清理未跟踪文件并查看状态
    fcmd gittool p                       # 推送
    fcmd gittool pl                      # 拉取
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = [
    "git_add_commit",
    "git_init_add_commit",
    "git_init_sub_dirs",
    "has_files",
    "not_has_git_repo",
]

# git clean -xfd 排除的目录（编辑器/项目缓存等）
EXCLUDE_DIRS: list[str] = [
    ".vscode",
    ".idea",
    ".editorconfig",
    ".trae",
    ".qoder",
    ".venv",
    ".git",
    ".ruff_cache",
    ".tox",
    "node_modules",
]

# 展开为 ``-e dir1 -e dir2 ...`` 供 git clean 使用
EXCLUDE_CMDS: list[str] = [arg for d in EXCLUDE_DIRS for arg in ["-e", d]]


# ============================================================================
# 私有辅助函数
# ============================================================================


def not_has_git_repo() -> bool:
    """检查当前目录没有 Git 仓库。

    Returns
    -------
    bool
        当前目录不存在或没有 ``.git`` 目录时返回 ``True``
    """
    cwd = Path.cwd()
    return not cwd.exists() or not (cwd / ".git").is_dir()


def has_files() -> bool:
    """检查当前 Git 仓库是否有未提交的更改。

    Returns
    -------
    bool
        有未提交更改时返回 ``True``
    """
    result = run_command(["git", "status", "--porcelain"], capture=True)
    return bool(result.stdout.strip())


# ============================================================================
# fn 子命令
# ============================================================================


@fcmd.tool("gittool", subcommand="a", help="添加并提交")
def git_add_commit(message: str = "chore: update") -> None:
    """执行 git add + git commit（仅当有未提交更改时）。

    Parameters
    ----------
    message:
        提交信息（默认 ``chore: update``）
    """
    if not has_files():
        print("没有文件需要提交")
        return
    run_command(["git", "add", "."])
    run_command(["git", "commit", "-m", message])


@fcmd.tool("gittool", subcommand="i", help="初始化并提交")
def git_init_add_commit(message: str = "init commit") -> None:
    """执行 git init（若需）+ git add + git commit（若有更改）。

    Parameters
    ----------
    message:
        提交信息（默认 ``init commit``）
    """
    if not_has_git_repo():
        run_command(["git", "init"])
    if has_files():
        run_command(["git", "add", "."])
        run_command(["git", "commit", "-m", message])
    else:
        print("没有文件需要提交")


@fcmd.tool("gittool", subcommand="isub", help="初始化子目录 Git 仓库")
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
# cmd 子命令
# ============================================================================


@fcmd.tool(
    "gittool",
    subcommand="clean",
    help="清理未跟踪文件",
    cmd=["git", "clean", "-xfd", *EXCLUDE_CMDS],
    hidden=True,
)
def clean() -> None:
    """清理 Git 未跟踪文件（隐藏命令，被 c 依赖）。"""


@fcmd.tool(
    "gittool",
    subcommand="c",
    help="清理并查看状态",
    cmd=["git", "status", "--porcelain"],
    needs=["clean"],
)
def c() -> None:
    """清理未跟踪文件并查看 Git 状态。"""


@fcmd.tool("gittool", subcommand="p", help="推送", cmd=["git", "push"])
def p() -> None:
    """推送代码到远程仓库。"""


@fcmd.tool("gittool", subcommand="pl", help="拉取", cmd=["git", "pull"])
def pl() -> None:
    """从远程仓库拉取代码。"""
