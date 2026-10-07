"""gittool - Git 执行工具。

提供初始化/添加提交/初始化子目录等子命令。

子命令来源：exec 型与守卫型子命令（a/i/clean/c/ca/p/pl）由
``src/fcmd/commands/gittool.toml`` DSL 声明——a/i 经链式内部子命令
``_init``/``_add``/``_commit`` + ``when`` 探针守卫编排（无仓库自动 init、
无更改不提交），发现时与本模块的子命令合并注册（见 ``fcmd.cli._discovery``）。
本模块仅保留含遍历逻辑的 fn 子命令：

- ``isub``：初始化所有子目录的 Git 仓库

示例
----
    fcmd gittool a --message "feat: 新功能"   # 添加并提交（有更改时）
    fcmd gittool i                             # 初始化并提交
    fcmd gittool isub                          # 初始化所有子目录的 Git 仓库
    fcmd gittool c                             # 清理未跟踪文件（保留排除目录）并查看状态
    fcmd gittool ca                            # 清理全部未跟踪文件（含排除目录）
    fcmd gittool p                             # 推送
    fcmd gittool pl                            # 拉取
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import fcmd

__all__ = ["git_init_sub_dirs"]


# ============================================================================
# fn 子命令
# ============================================================================


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
