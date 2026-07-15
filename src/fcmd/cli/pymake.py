"""pymake - 项目构建工具入口。

提供构建/测试/清理/检查/格式化/发布等子命令。
``pymake <args>`` 与 ``fcmd pymake <args>`` 行为完全一致。

子命令分组
----------
- 构建：``b`` (uv build)、``sync`` (uv sync)
- 测试：``t`` (pytest)、``tf`` (快速失败)、``cov`` (覆盖率聚合)
- 检查：``tc`` (类型检查聚合)、``lint`` (ruff check)、``fmt`` / ``fmtc`` (ruff format)
- 发布：``bump`` / ``bumpmi`` / ``bumpma`` (版本号)、``p`` (推送)、``pb`` (发布 PyPI)
- 文档：``doc`` (sphinx-build)
- 其他：``tox`` (多版本测试)、``all`` (全套流程)

示例
----
    pymake b          # 构建 (uv build)
    pymake t          # 运行测试
    pymake tc         # 类型检查（聚合：c + pyrefly_check + lint，thread 策略）
    pymake cov        # 测试并生成覆盖率
    pymake bump       # 升级 patch 版本号
    pymake all        # 全套流程（清理 + 构建 + 测试 + 类型检查）
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import fcmd
from fcmd.apis import run_tool

# ============================================================================
# 单任务别名 (cmd 任务)
# ============================================================================


@fcmd.tool("pymake", subcommand="b", help="构建分发包 (uv build)", cmd=["uv", "build"])
def b(cwd: Path = Path()) -> None:
    """构建分发包 (wheel + sdist)。"""


@fcmd.tool("pymake", subcommand="sync", help="同步开发依赖 (uv sync --extra dev)", cmd=["uv", "sync", "--extra", "dev"])
def sync(cwd: Path = Path()) -> None:
    """同步开发依赖。"""


@fcmd.tool(
    "pymake",
    subcommand="c",
    help="清理构建产物与缓存目录",
)
def c(cwd: Path = Path()) -> None:  # noqa: ARG001  cwd 由框架处理，签名保留以驱动 CLI
    """清理构建产物与缓存目录。

    清理目标：build/、dist/、htmlcov/、.tox/、.ruff_cache/、.pyrefly_cache/、
    .mypy_cache/、.pytest_cache/、src/tests 下的 __pycache__ 与 *.egg-info。
    """
    targets = [
        "build",
        "dist",
        "htmlcov",
        ".tox",
        ".ruff_cache",
        ".pyrefly_cache",
        ".mypy_cache",
        ".pytest_cache",
    ]
    for t in targets:
        shutil.rmtree(t, ignore_errors=True)
    for base in ("src", "tests"):
        for p in Path(base).rglob("__pycache__"):
            shutil.rmtree(p, ignore_errors=True)
        for p in Path(base).rglob("*.egg-info"):
            shutil.rmtree(p, ignore_errors=True)


@fcmd.tool(
    "pymake",
    subcommand="t",
    help="运行测试 (pytest)",
    cmd=["pytest", "-m", "not slow", "--color=yes", "--durations=10"],
)
def t(cwd: Path = Path()) -> None:
    """运行测试（不含 slow 标记）。"""


@fcmd.tool(
    "pymake",
    subcommand="tf",
    help="快速测试 (遇到失败立即停止)",
    cmd=["pytest", "-m", "not slow", "--color=yes", "-x", "--durations=10"],
)
def tf(cwd: Path = Path()) -> None:
    """快速测试（首个失败即停止）。"""


@fcmd.tool(
    "pymake",
    subcommand="lint",
    help="代码检查与自动修复 (ruff check --fix)",
    cmd=["ruff", "check", "--fix", "src", "tests"],
)
def lint(cwd: Path = Path()) -> None:
    """代码检查与自动修复。"""


@fcmd.tool(
    "pymake",
    subcommand="fmt",
    help="代码格式化 (ruff format)",
    cmd=["ruff", "format", "src", "tests"],
)
def fmt(cwd: Path = Path()) -> None:
    """代码格式化。"""


@fcmd.tool(
    "pymake",
    subcommand="fmtc",
    help="格式化检查 (ruff format --check，不修改文件)",
    cmd=["ruff", "format", "--check", "src", "tests"],
)
def fmtc(cwd: Path = Path()) -> None:
    """格式化检查（不修改文件）。"""


@fcmd.tool(
    "pymake",
    subcommand="bumpmi",
    help="升级次版本号 (bump-my-version bump minor)",
    cmd=["uvx", "bump-my-version", "bump", "minor", "--tag"],
)
def bumpmi(cwd: Path = Path()) -> None:
    """升级次版本号 (minor)。"""


@fcmd.tool(
    "pymake",
    subcommand="bumpma",
    help="升级主版本号 (bump-my-version bump major)",
    cmd=["uvx", "bump-my-version", "bump", "major", "--tag"],
)
def bumpma(cwd: Path = Path()) -> None:
    """升级主版本号 (major)。"""


@fcmd.tool(
    "pymake",
    subcommand="doc",
    help="构建 Sphinx 文档",
    cmd=["sphinx-build", "-b", "html", "docs", "docs/_build/html"],
)
def doc(cwd: Path = Path()) -> None:
    """构建 Sphinx 文档。"""


@fcmd.tool("pymake", subcommand="tox", help="多版本测试 (tox -p auto)", cmd=["uvx", "tox", "-p", "auto"])
def tox(cwd: Path = Path()) -> None:
    """多版本测试。"""


# ============================================================================
# 内部 job (hidden, 不暴露为 subcommand)
# ============================================================================


@fcmd.tool(
    "pymake",
    subcommand="pyrefly_check",
    help="pyrefly 类型检查",
    cmd=["pyrefly", "check"],
    hidden=True,
)
def pyrefly_check(cwd: Path = Path()) -> None:
    """pyrefly 类型检查（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="test_coverage",
    help="测试并生成覆盖率",
    cmd=["pytest", "-m", "not slow", "--cov=fcmd", "--cov-fail-under=95", "--color=yes", "--durations=10"],
    needs=["c"],
    hidden=True,
)
def test_coverage(cwd: Path = Path()) -> None:
    """测试并生成覆盖率（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="bumpversion",
    help="升级 patch 版本号 (bump-my-version bump patch)",
    cmd=["uvx", "bump-my-version", "bump", "patch", "--tag"],
    needs=["git_add_all"],
    hidden=True,
)
def bumpversion(cwd: Path = Path()) -> None:
    """升级 patch 版本号（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="git_add_all",
    help="git add -A",
    cmd=["git", "add", "-A"],
    needs=["tc"],
    hidden=True,
)
def git_add_all(cwd: Path = Path()) -> None:
    """git add -A（内部 job，需先通过类型检查）。"""


@fcmd.tool("pymake", subcommand="git_push", help="git push", cmd=["git", "push"], hidden=True)
def git_push(cwd: Path = Path()) -> None:
    """git push（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="git_push_tags",
    help="git push --tags",
    cmd=["git", "push", "--tags"],
    hidden=True,
)
def git_push_tags(cwd: Path = Path()) -> None:
    """git push --tags（内部 job）。"""


@fcmd.tool(
    "pymake",
    subcommand="twine_publish",
    help="twine upload dist/*",
    cmd=["uvx", "twine", "upload", "--disable-progress-bar", "dist/*"],
    hidden=True,
)
def twine_publish(cwd: Path = Path()) -> None:
    """twine upload（内部 job）。"""


# ============================================================================
# 聚合 job (有 needs 无 cmd 无函数逻辑)
# ============================================================================


@fcmd.tool(
    "pymake",
    subcommand="tc",
    help="类型检查 (清理 + pyrefly + lint)",
    needs=["c", "pyrefly_check", "lint"],
    strategy="thread",
)
def tc(cwd: Path = Path()) -> None:
    """类型检查（聚合）。"""


@fcmd.tool(
    "pymake",
    subcommand="cov",
    help="测试并生成覆盖率 (清理 + 测试)",
    needs=["test_coverage"],
)
def cov(cwd: Path = Path()) -> None:
    """测试并生成覆盖率（聚合）。"""


@fcmd.tool(
    "pymake",
    subcommand="bump",
    help="升级 patch 版本号 (类型检查 + add + bumpversion)",
    needs=["bumpversion"],
)
def bump(cwd: Path = Path()) -> None:
    """升级 patch 版本号（聚合）。"""


@fcmd.tool(
    "pymake",
    subcommand="p",
    help="推送代码 (清理 + push + push tags)",
    needs=["c", "git_push", "git_push_tags"],
    strategy="thread",
)
def p(cwd: Path = Path()) -> None:
    """推送代码（聚合）。"""


@fcmd.tool(
    "pymake",
    subcommand="pb",
    help="发布到 PyPI (twine upload)",
    needs=["twine_publish"],
)
def pb(cwd: Path = Path()) -> None:
    """发布到 PyPI（聚合）。"""


@fcmd.tool(
    "pymake",
    subcommand="all",
    help="全套流程 (清理 + 构建 + 测试 + 类型检查)",
    needs=["c", "b", "t", "tc"],
    strategy="dependency",
)
def all_(cwd: Path = Path()) -> None:
    """全套流程（聚合）。"""


def main() -> None:
    """``pymake`` 入口：等价于 ``fcmd pymake <args>``。"""
    sys.exit(run_tool("pymake", sys.argv[1:]))


if __name__ == "__main__":
    main()
