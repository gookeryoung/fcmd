"""pymake - 项目构建工具入口。

提供构建/测试/清理/检查/格式化/发布等子命令。
``pymake <args>`` 与 ``fcmd pymake <args>`` 行为完全一致。

子命令分组
----------
- 构建：``b`` (uv build)、``sync`` (uv sync)
- 测试：``t`` (pytest)、``tf`` (快速失败)、``cov`` (覆盖率测试)、``ts`` (slow 测试)
- 检查：``chk`` (类型检查聚合，含 test_fast)、``tc`` (类型检查，不含 test_fast)、``lint`` (ruff check)、``fmt`` / ``fmtc`` (ruff format，hidden)
- 发布：``bump`` / ``bumpmi`` / ``bumpma`` (版本号)、``push`` (推送)、``upload`` (发布 PyPI)
- 文档：``doc`` (sphinx-build)
- 其他：``tox`` (多版本测试)

示例
----
    pymake b          # 构建 (uv build)
    pymake t          # 运行测试
    pymake chk        # 类型检查（聚合：c + pyrefly_check + lint + fmt + tf，thread 策略）
    pymake cov        # 测试并生成覆盖率
    pymake bump       # 升级 patch 版本号
    pymake push       # 推送代码（清理 + check + push + push tags）
"""

from __future__ import annotations

from pathlib import Path

import fcmd

# 工具别名：fcmd pm <args> 等价于 fcmd pymake <args>
__tool_aliases__: list[str] = ["pm"]

# 构建命令映射
_BUILD_COMMANDS: dict[str, list[str]] = {
    "uv": ["uv", "build"],
    "hatchling": ["hatchling", "build"],
    "pip": ["pip", "wheel", "."],
}

_COVERAGE_THRESHOLD: int = 95

# ============================================================================
# 构建相关
# ============================================================================


@fcmd.tool("pymake", subcommand="b", help="构建分发包 (uv build)", cmd=["uv", "build"])
def build(cwd: Path = Path()) -> None:
    """构建分发包 (wheel + sdist)。"""


# ============================================================================
# 发布相关
# ============================================================================


@fcmd.tool(
    "pymake",
    subcommand="bump",
    help="升级 patch 版本号 (bump-my-version bump patch)",
    cmd=["uvx", "bump-my-version", "bump", "patch", "--tag"],
)
def bump_patch(cwd: Path = Path()) -> None:
    """升级 patch 版本号（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="bumpma",
    help="升级主版本号 (bump-my-version bump major)",
    cmd=["uvx", "bump-my-version", "bump", "major", "--tag"],
)
def bump_major(cwd: Path = Path()) -> None:
    """升级主版本号 (major)。"""


@fcmd.tool(
    "pymake",
    subcommand="bumpmi",
    help="升级次版本号 (bump-my-version bump minor)",
    cmd=["uvx", "bump-my-version", "bump", "minor", "--tag"],
)
def bump_minor(cwd: Path = Path()) -> None:
    """升级次版本号 (minor)。"""


# ============================================================================
# 清理相关
# ============================================================================


@fcmd.tool("pymake", subcommand="c", cmd=["gitt", "c"], help="清理构建产物与缓存目录")
def clean() -> None:
    """清理构建产物与缓存目录."""


# ============================================================================
# 文档相关
# ============================================================================


@fcmd.tool(
    "pymake",
    subcommand="doc",
    help="构建 Sphinx 文档",
    cmd=["sphinx-build", "-b", "html", "docs", "docs/_build/html"],
)
def build_docs(cwd: Path = Path()) -> None:
    """构建 Sphinx 文档。"""


# ============================================================================
# 检查相关
# ============================================================================


@fcmd.tool(
    "pymake",
    subcommand="lint",
    help="代码检查与自动修复 (ruff check --fix)",
    cmd=["ruff", "check", "--fix", "."],
)
def lint(cwd: Path = Path()) -> None:
    """代码检查与自动修复。"""


@fcmd.tool(
    "pymake",
    subcommand="fmt",
    help="代码格式化 (ruff format)",
    cmd=["ruff", "format", "."],
    hidden=True,
)
def _format(cwd: Path = Path()) -> None:
    """代码格式化。"""


@fcmd.tool(
    "pymake",
    subcommand="fmtc",
    help="格式化检查 (ruff format --check，不修改文件)",
    cmd=["ruff", "format", "--check", "."],
    hidden=True,
)
def _format_check(cwd: Path = Path()) -> None:
    """格式化检查（不修改文件）。"""


@fcmd.tool(
    "pymake",
    subcommand="pyrefly_check",
    help="pyrefly 类型检查",
    cmd=["pyrefly", "check"],
    hidden=True,
)
def _pyrefly_check(cwd: Path = Path()) -> None:
    """pyrefly 类型检查（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="chk",
    help="类型检查 (清理 + pyrefly + lint + test_fast)",
    needs=["pyrefly_check", "lint", "fmt", "tf"],
    strategy="thread",
)
def check(cwd: Path = Path()) -> None:
    """类型检查（聚合）。"""


@fcmd.tool(
    "pymake",
    subcommand="tc",
    help="类型检查 (清理 + pyrefly + lint)",
    needs=["pyrefly_check", "lint", "fmt"],
    strategy="thread",
)
def type_check(cwd: Path = Path()) -> None:
    """类型检查（聚合）。"""


# ============================================================================
# 测试相关
# ============================================================================


@fcmd.tool(
    "pymake",
    subcommand="t",
    help="运行测试 (pytest)",
    cmd=["pytest", "-m", "not slow", "--color=yes", "--durations=10"],
)
def test(cwd: Path = Path()) -> None:
    """运行测试（不含 slow 标记）。"""


@fcmd.tool(
    "pymake",
    subcommand="tn",
    help="运行测试 (pytest)",
    cmd=["pytest", "-n", "8", "-m", "not slow", "--color=yes", "--durations=10"],
)
def test_n_cpu(cwd: Path = Path()) -> None:
    """运行测试（不含 slow 标记，使用多 CPU 核心）。"""


@fcmd.tool(
    "pymake",
    subcommand="cov",
    help="测试并生成覆盖率",
    cmd=[
        "pytest",
        "-m",
        "not slow",
        "--cov=fcmd",
        f"--cov-fail-under={_COVERAGE_THRESHOLD}",
        "--color=yes",
        "--durations=10",
    ],
    needs=["c"],
)
def test_coverage(cwd: Path = Path()) -> None:
    """测试并生成覆盖率（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="tf",
    help="快速测试 (遇到失败立即停止)",
    cmd=["pytest", "-m", "not slow", "--color=yes", "-x", "--durations=10"],
)
def test_fast(cwd: Path = Path()) -> None:
    """快速测试（首个失败即停止）。"""


@fcmd.tool("pymake", subcommand="ts", help="测试 slow 标记的测试 (pytest -m slow)", cmd=["pytest", "-m", "slow"])
def test_slow(cwd: Path = Path()) -> None:
    """测试 slow 标记的测试。"""


@fcmd.tool("pymake", subcommand="tox", help="多版本测试 (tox -p auto)", cmd=["uvx", "tox", "-p", "auto"])
def tox_auto(cwd: Path = Path()) -> None:
    """多版本测试。"""


@fcmd.tool(
    "pymake",
    subcommand="git_add_all",
    help="git add -A",
    cmd=["git", "add", "-A"],
    needs=["chk"],
    hidden=True,
)
def _git_add_all(cwd: Path = Path()) -> None:
    """git add -A（内部 job，需先通过类型检查）。"""


@fcmd.tool(
    "pymake",
    subcommand="git_push",
    help="git push",
    cmd=["git", "push"],
    hidden=True,
)
def _git_push(cwd: Path = Path()) -> None:
    """git push（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="git_push_tags",
    help="git push --tags",
    cmd=["git", "push", "--tags"],
    hidden=True,
)
def _git_push_tags(cwd: Path = Path()) -> None:
    """git push --tags（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="sync",
    help="同步开发依赖 (uv sync --extra dev)",
    cmd=["uv", "sync", "--extra", "dev"],
)
def sync(cwd: Path = Path()) -> None:
    """同步开发依赖。"""


@fcmd.tool(
    "pymake",
    subcommand="twine_publish",
    help="twine upload dist/*",
    cmd=["uvx", "twine", "upload", "--disable-progress-bar", "dist/*"],
    hidden=True,
)
def _twine_publish(cwd: Path = Path()) -> None:
    """twine upload（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="push",
    help="推送代码 (清理 + check + push + push tags)",
    needs=["chk", "c", "git_push", "git_push_tags"],
    strategy="thread",
)
def push(cwd: Path = Path()) -> None:
    """推送代码（聚合）。

    依赖 ``chk``（类型检查聚合）+ ``c``（清理工作区）+ ``git_push`` + ``git_push_tags``，
    与 help 文案「清理 + check + push + push tags」一致。
    """


@fcmd.tool(
    "pymake",
    subcommand="upload",
    help="发布到 PyPI (twine upload)",
    needs=["twine_publish"],
)
def publish_pypi(cwd: Path = Path()) -> None:
    """发布到 PyPI（聚合）。"""


@fcmd.main("pymake")
def main() -> None:
    """pymake 主程序."""


if __name__ == "__main__":
    main()
