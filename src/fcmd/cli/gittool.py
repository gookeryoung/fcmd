"""gittool - Git 执行工具。

提供添加提交/初始化/清理/推送/拉取子命令。

示例
----
    fcmd gittool a -m "feat: 新功能"   # 添加并提交
    fcmd gittool i                       # 初始化并提交
    fcmd gittool c                       # 清理未跟踪文件并查看状态
    fcmd gittool p                       # 推送
    fcmd gittool pl                      # 拉取
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import fcmd

__all__ = [
    "git_add_commit",
    "git_init_add_commit",
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


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """执行命令并返回结果，输出透传到当前终端。

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
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        check=False,
        text=True,
    )
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
    _run(["git", "add", "."])
    _run(["git", "commit", "-m", message])


@fcmd.tool("gittool", subcommand="i", help="初始化并提交")
def git_init_add_commit(message: str = "init commit") -> None:
    """执行 git init（若需）+ git add + git commit（若有更改）。

    Parameters
    ----------
    message:
        提交信息（默认 ``init commit``）
    """
    if not_has_git_repo():
        _run(["git", "init"])
    if has_files():
        _run(["git", "add", "."])
        _run(["git", "commit", "-m", message])
    else:
        print("没有文件需要提交")


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
