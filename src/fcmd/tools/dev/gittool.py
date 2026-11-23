"""gittool - Git 执行工具。

提供初始化/添加提交/初始化子目录/清理/推送/拉取等子命令。

``a`` / ``i`` 都用 ``steps`` 声明顺序流水线，并把「必须已在仓库内」写成
``preconditions``：

- ``a``：``preconditions=[_require_repo]`` + ``steps=["_add", "_commit"]``；
  不在仓库内直接报错，不会隐式 ``git init``；
- ``i``：``steps=[step("_init", create=True), "_add", "_commit"]``；只有它会把
  ``create=True`` 注入内部步骤，用户命令行上没有多余的 ``--init`` 开关；
- ``_add`` / ``_commit`` 是纯步骤，不在各自的装饰器里写 ``needs``。

示例
----
    fcmd gitt a --message "feat: 新功能"   # 添加并提交（要求已是 Git 仓库）
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
# 内部工具：Git 仓库判定 / 子进程命令
# ============================================================================


def _run_git(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """执行一条 git 命令（不抛错，由调用方按返回码决定后续）。"""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def _failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    """从失败结果里取一句可读原因（优先 stderr 最后一行）。"""
    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in (stream or "").splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return f"返回码 {result.returncode}"


def _repo_root() -> Path | None:
    """返回当前目录所属 Git 仓库根；不在任何仓库内时返回 ``None``。

    用 ``git rev-parse --show-toplevel`` 而不是 ``Path(".git").exists()``：
    后者在仓库子目录里会误判为「没有仓库」，进而 ``git init`` 出嵌套仓库。
    """
    result = _run_git(["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return Path(text) if text else None


def _require_repo() -> None:
    """前置断言：当前目录必须已在 Git 仓库内（仓库子目录也算）。"""
    if _repo_root() is None:
        raise RuntimeError("当前目录不是 Git 仓库；请先运行 `fcmd gitt i` 初始化")


def _has_staged_changes(cwd: Path | None = None) -> bool:
    """暂存区是否有待提交内容；无法读取 Git 状态时抛可读错误。"""
    result = _run_git(["git", "status", "--porcelain"], cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"读取 Git 状态失败: {_failure_detail(result)}")
    return bool(result.stdout.strip())


# ============================================================================
# fn 子命令
# ============================================================================


@fx.tool("gittool", subcommand="isub", help="初始化子目录 Git 仓库")
def git_init_sub_dirs(message: str = "init commit") -> None:
    """遍历当前目录的子目录，逐个子目录执行 git init + add + commit。

    跳过非目录文件、隐藏目录（``.git`` / ``.venv`` 这类不该被当成项目）
    以及已经是 Git 仓库的子目录；每一步都检查返回码，失败项收集后统一报错，
    不再把失败伪装成「已初始化」。

    Parameters
    ----------
    message:
        提交信息（默认 ``init commit``）
    """
    cwd = Path.cwd()
    sub_dirs = sorted(d for d in cwd.iterdir() if d.is_dir() and not d.name.startswith("."))
    if not sub_dirs:
        print("当前目录下没有可初始化的子目录")
        return
    failed: list[str] = []
    for subdir in sub_dirs:
        if (subdir / ".git").exists():
            print(f"跳过: {subdir.name}（已是 Git 仓库）")
            continue
        init = _run_git(["git", "init"], cwd=subdir)
        if init.returncode != 0:
            failed.append(f"{subdir.name}: git init 失败（{_failure_detail(init)}）")
            continue
        add = _run_git(["git", "add", "."], cwd=subdir)
        if add.returncode != 0:
            failed.append(f"{subdir.name}: git add 失败（{_failure_detail(add)}）")
            continue
        try:
            staged = _has_staged_changes(subdir)
        except RuntimeError as exc:
            failed.append(f"{subdir.name}: {exc}")
            continue
        if not staged:
            print(f"已初始化: {subdir.name}（无更改可提交）")
            continue
        commit = _run_git(["git", "commit", "-m", message], cwd=subdir)
        if commit.returncode != 0:
            failed.append(f"{subdir.name}: git commit 失败（{_failure_detail(commit)}）")
            continue
        print(f"已初始化: {subdir.name}")
    if failed:
        raise RuntimeError("以下子目录初始化失败: " + "；".join(failed))


# ============================================================================
# 步骤链：_init → _add → _commit（顺序由 a / i 的 steps 声明）
# ============================================================================


@fx.tool("gittool", subcommand="_init", help="确保 Git 仓库已就绪（内部）", hidden=True)
def git_init(create: bool = False) -> None:
    """确保当前目录在 Git 仓库内；``create=True`` 时不存在则初始化。

    - 已在仓库内（含仓库子目录）：no-op，不再重复 ``git init``；
    - 不在仓库内且 ``create=False``：报错提示先执行 ``fcmd gitt i``；
    - 不在仓库内且 ``create=True``：执行 ``git init``。

    ``create`` 不进入 ``a`` / ``i`` 的命令行，只由 ``step("_init", create=True)``
    注入；是否需要初始化由聚合入口决定。

    Parameters
    ----------
    create:
        是否允许创建仓库（仅 ``i`` 的步骤声明传 ``True``）
    """
    if _repo_root() is not None:
        return
    if not create:
        raise RuntimeError("当前目录不是 Git 仓库；请先运行 `fcmd gitt i` 初始化")
    subprocess.run(["git", "init"], check=True)


@fx.tool(
    "gittool",
    subcommand="_add",
    help="暂存全部更改（内部）",
    cmd=["git", "add", "."],
    hidden=True,
)
def git_add() -> None:
    """暂存全部更改（顺序由调用方的 ``steps`` 保证）。"""


@fx.tool(
    "gittool",
    subcommand="_commit",
    help="检查并提交更改（内部）",
    hidden=True,
)
def git_commit(message: str = "init commit") -> None:
    """提交暂存更改（暂存区为空时提示并跳过）。

    Parameters
    ----------
    message:
        提交信息
    """
    if not _has_staged_changes():
        from fcmd.console import get_console

        get_console().print("  没有需要提交的更改", style="dim")
        return
    subprocess.run(["git", "commit", "-m", message], check=True)


@fx.tool(
    "gittool",
    subcommand="a",
    help="添加并提交（要求已是 Git 仓库）",
    aggregate=True,
    preconditions=[_require_repo],
    steps=["_add", "_commit"],
)
def add_and_commit(message: str = "chore: update") -> None:
    """添加并提交；不在 Git 仓库内时报错，不会自动初始化。

    Parameters
    ----------
    message:
        提交信息
    """


@fx.tool(
    "gittool",
    subcommand="i",
    help="初始化并提交",
    aggregate=True,
    steps=[fx.step("_init", create=True), "_add", "_commit"],
)
def init_and_commit(message: str = "init commit") -> None:
    """初始化并提交（仓库已存在时不重复初始化）。

    ``create=True`` 只注入给 ``_init`` 这一步；``a`` / ``i`` 的命令行只保留
    ``--message``。

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
