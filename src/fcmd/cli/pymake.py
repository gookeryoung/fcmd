"""pymake - 项目构建工具入口。

提供构建/测试/清理/检查等子命令。
``pymake <args>`` 与 ``fcmd pymake <args>`` 行为完全一致。

示例
----
    pymake b          # 构建（执行 python --version 模拟）
    pymake t          # 测试
    pymake tc         # 类型检查（聚合：c + pyrefly_check + lint，thread 策略）
"""

from __future__ import annotations

import sys
from pathlib import Path

import fcmd as fx
from fcmd.apis import run_tool

# ============================================================================
# 单任务别名 (cmd 任务)
# ============================================================================


@fx.tool("pymake", subcommand="b", help="构建项目 (python --version)", cmd=["python", "--version"])
def b(cwd: Path = Path()) -> None:
    """构建项目。"""


@fx.tool(
    "pymake",
    subcommand="c",
    help="清理构建产物 (清空 __pycache__)",
    cmd=[
        "python",
        "-c",
        "import shutil, glob; [shutil.rmtree(p, ignore_errors=True) for p in glob.glob('**/__pycache__', recursive=True)]",
    ],
)
def c(cwd: Path = Path()) -> None:
    """清理构建产物。"""


@fx.tool(
    "pymake",
    subcommand="t",
    help="运行测试 (pytest)",
    cmd=["python", "-m", "pytest", "-m", "not slow", "--color=yes"],
)
def t(cwd: Path = Path()) -> None:
    """运行测试。"""


@fx.tool("pymake", subcommand="lint", help="代码检查 (ruff check)", cmd=["python", "-m", "ruff", "check", "--fix"])
def lint(cwd: Path = Path()) -> None:
    """代码检查。"""


# ============================================================================
# 内部 job (hidden, 不暴露为 subcommand)
# ============================================================================


@fx.tool(
    "pymake",
    subcommand="pyrefly_check",
    help="pyrefly 类型检查",
    cmd=["python", "-m", "pyrefly", "check"],
    hidden=True,
)
def pyrefly_check(cwd: Path = Path()) -> None:
    """pyrefly 类型检查（内部 job）。"""


@fx.tool(
    "pymake",
    subcommand="git_add_all",
    help="git add -A",
    cmd=["git", "add", "-A"],
    needs=["c"],
    hidden=True,
)
def git_add_all(cwd: Path = Path()) -> None:
    """git add -A（内部 job）。"""


# ============================================================================
# 聚合 job (有 needs 无 cmd 无函数逻辑)
# ============================================================================


@fx.tool(
    "pymake",
    subcommand="tc",
    help="类型检查 (清理 + pyrefly + lint)",
    needs=["c", "pyrefly_check", "lint"],
    strategy="thread",
)
def tc(cwd: Path = Path()) -> None:
    """类型检查（聚合）。"""


@fx.tool(
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
