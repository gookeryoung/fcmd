"""cli 共享辅助：跨工具复用的常量与入口样板。

本模块以下划线开头，``_ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

__all__ = ["IGNORE_DIRS", "IGNORE_EXT", "run_tool_main"]

# 文件遍历时跳过的目录名（跨工具共享）
IGNORE_DIRS: set[str] = {
    ".git",
    "__pycache__",
    ".venv",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    ".pytest_cache",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pyrefly_cache",
    "*.egg-info",
}

# 文件遍历时跳过的扩展名（压缩包等）
IGNORE_EXT: set[str] = {".zip", ".rar", ".7z", ".tar", ".gz", ".pyc", ".pyo"}


def run_tool_main(tool_name: str) -> None:
    """工具模块独立入口：等价于 ``fcmd <tool_name> <args>``。

    供各工具模块的 ``main()`` 调用，统一封装 ``sys.exit(run_tool(...))`` 样板，
    避免 30+ 个模块重复同一段代码。在函数内部局部导入 ``run_tool`` 与 ``sys``，
    不影响模块冷启动导入开销。
    """
    import sys

    from fcmd.apis import run_tool

    sys.exit(run_tool(tool_name, sys.argv[1:]))
