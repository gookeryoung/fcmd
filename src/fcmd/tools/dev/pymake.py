"""pymake - 项目构建工具入口。

提供构建/测试/清理/检查/格式化/发布等子命令。
``pymake <args>`` 与 ``fcmd pymake <args>`` 行为完全一致。

示例
----
    pymake b          # 构建 (uv build)
    pymake t          # 运行测试
    pymake chk        # 类型检查（聚合：pyrefly + lint + fmt + tf，thread 策略）
    pymake cov        # 测试并生成覆盖率
    pymake bump       # 升级 patch 版本号
    pymake push       # 推送代码到所有远程仓库
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx

# 工具别名：fcmd pm <args> 等价于 fcmd pymake <args>
__tool_aliases__: list[str] = ["pm"]


# ============================================================================
# 推送相关
# ============================================================================


def _push_all_remotes() -> None:
    """遍历所有 git remote，逐个推送代码 + 标签。

    与 Makefile ``push`` target 行为一致；任一 remote 推送失败立即抛
    RuntimeError 阻断后续，便于 CI 检测。
    """
    import subprocess as sp

    remotes = sp.check_output(["git", "remote"], text=True).split()
    if not remotes:
        raise RuntimeError("未检测到 git remote，无法推送")
    for remote in remotes:
        print(f"推送 {remote}...", flush=True)
        sp.run(["git", "push", remote], check=True)
        sp.run(["git", "push", remote, "--tags"], check=True)


@fx.tool(
    "pymake",
    subcommand="push",
    help="推送代码到所有远程仓库",
    cmd=_push_all_remotes,
)
def push(cwd: Path = Path()) -> None:
    """推送代码到所有远程仓库（含 tags）。"""


# ============================================================================
# 纯 exec 子命令
# ============================================================================


@fx.tool("pymake", subcommand="b", help="构建分发包 (uv build)", cmd=["uv", "build"])
def build(cwd: Path = Path()) -> None:
    """构建分发包。

    Parameters
    ----------
    cwd:
        工作目录
    """


@fx.tool(
    "pymake",
    subcommand="bump",
    help="升级 patch 版本号 (bump-my-version bump patch)",
    cmd=["uvx", "bump-my-version", "bump", "patch", "--tag"],
)
def bump_patch(cwd: Path = Path()) -> None:
    """升级 patch 版本号。"""


@fx.tool(
    "pymake",
    subcommand="bumpma",
    help="升级主版本号 (bump-my-version bump major)",
    cmd=["uvx", "bump-my-version", "bump", "major", "--tag"],
)
def bump_major(cwd: Path = Path()) -> None:
    """升级主版本号。"""


@fx.tool(
    "pymake",
    subcommand="bumpmi",
    help="升级次版本号 (bump-my-version bump minor)",
    cmd=["uvx", "bump-my-version", "bump", "minor", "--tag"],
)
def bump_minor(cwd: Path = Path()) -> None:
    """升级次版本号。"""


@fx.tool("pymake", subcommand="c", help="清理构建产物与缓存目录", cmd=["gitt", "c"])
def clean() -> None:
    """清理构建产物与缓存目录。"""


@fx.tool(
    "pymake",
    subcommand="lint",
    help="代码检查与自动修复 (ruff check --fix)",
    cmd=["ruff", "check", "--fix", "."],
)
def lint(cwd: Path = Path()) -> None:
    """代码检查与自动修复。"""


@fx.tool(
    "pymake",
    subcommand="fmt",
    help="代码格式化 (ruff format)",
    cmd=["ruff", "format", "."],
    hidden=True,
)
def fmt(cwd: Path = Path()) -> None:
    """代码格式化。"""


@fx.tool(
    "pymake",
    subcommand="fmtc",
    help="格式化检查 (ruff format --check，不修改文件)",
    cmd=["ruff", "format", "--check", "."],
    hidden=True,
)
def fmt_check(cwd: Path = Path()) -> None:
    """格式化检查（不修改文件）。"""


@fx.tool(
    "pymake",
    subcommand="pyrefly_check",
    help="pyrefly 类型检查",
    cmd=["pyrefly", "check"],
    hidden=True,
)
def pyrefly_check(cwd: Path = Path()) -> None:
    """pyrefly 类型检查。"""


@fx.tool(
    "pymake",
    subcommand="chk",
    help="类型检查 (清理 + pyrefly + lint + test_fast)",
    needs=["pyrefly_check", "lint", "fmt", "tf"],
    strategy="thread",
)
def check_all() -> None:
    """聚合检查：pyrefly + lint + fmt + tf（thread 策略并行）。"""


@fx.tool(
    "pymake",
    subcommand="tc",
    help="类型检查 (清理 + pyrefly + lint)",
    needs=["pyrefly_check", "lint", "fmt"],
    strategy="thread",
)
def type_check() -> None:
    """聚合类型检查：pyrefly + lint + fmt（thread 策略并行）。"""


@fx.tool(
    "pymake",
    subcommand="t",
    help="运行测试 (pytest)",
    cmd=["pytest", "-m", "not slow", "--color=yes", "--durations=10"],
)
def test(cwd: Path = Path()) -> None:
    """运行测试。"""


@fx.tool(
    "pymake",
    subcommand="tn",
    help="运行测试 (pytest)",
    cmd=["pytest", "-n", "8", "-m", "not slow", "--color=yes", "--durations=10"],
)
def test_parallel(cwd: Path = Path()) -> None:
    """并行运行测试。"""


@fx.tool(
    "pymake",
    subcommand="cov",
    help="测试并生成覆盖率",
    cmd=[
        "pytest",
        "-m",
        "not slow",
        "-n",
        "auto",
        "--cov=fcmd",
        "--color=yes",
        "--durations=10",
    ],
)
def coverage(cwd: Path = Path()) -> None:
    """测试并生成覆盖率。"""


@fx.tool(
    "pymake",
    subcommand="tf",
    help="快速测试 (遇到失败立即停止)",
    cmd=["pytest", "-m", "not slow", "--color=yes", "-x", "--durations=10"],
)
def test_fast(cwd: Path = Path()) -> None:
    """快速测试（遇到失败立即停止）。"""


@fx.tool(
    "pymake",
    subcommand="ts",
    help="测试 slow 标记的测试 (pytest -m slow)",
    cmd=["pytest", "-m", "slow", "-n", "8", "--color=yes", "--durations=10"],
)
def test_slow(cwd: Path = Path()) -> None:
    """测试 slow 标记的测试。"""


@fx.tool(
    "pymake",
    subcommand="tox",
    help="多版本测试 (tox -p auto)",
    cmd=["uvx", "tox", "-p", "auto"],
)
def tox(cwd: Path = Path()) -> None:
    """多版本测试。"""


@fx.tool(
    "pymake",
    subcommand="sync",
    help="同步开发依赖 (uv sync --extra dev)",
    cmd=["uv", "sync", "--extra", "dev"],
)
def sync(cwd: Path = Path()) -> None:
    """同步开发依赖。"""


@fx.tool(
    "pymake",
    subcommand="upload",
    help="使用 twine 发布到 PyPI",
    cmd=["uvx", "twine", "upload", "--disable-progress-bar", "dist/*"],
    hidden=True,
)
def upload(cwd: Path = Path()) -> None:
    """使用 twine 发布到 PyPI。"""


@fx.main("pymake")
def main() -> None:
    """pymake 主程序."""


if __name__ == "__main__":
    main()
