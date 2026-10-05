"""gittool - Git 执行工具。

提供添加提交/初始化/初始化子目录/清理/推送/拉取子命令。

子命令来源：纯 exec 型子命令（clean/c/ca/p/pl）由 ``src/fcmd/commands/gittool.toml``
DSL 声明，发现时与本模块的子命令合并注册（见 ``fcmd.cli._discovery``）。
本模块仅保留含 Python 逻辑的 fn 子命令：

- ``a``：添加并提交（仅当有未提交更改时）
- ``i``：初始化并提交
- ``isub``：初始化所有子目录的 Git 仓库

示例
----
    fcmd gittool a -m "feat: 新功能"   # 添加并提交
    fcmd gittool i                       # 初始化并提交
    fcmd gittool isub                    # 初始化所有子目录的 Git 仓库
    fcmd gittool c                       # 清理未跟踪文件（保留排除目录）并查看状态
    fcmd gittool ca                      # 清理全部未跟踪文件（含排除目录）
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


@fcmd.main("gittool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
