"""cli 共享辅助：跨工具复用的常量。

本模块以下划线开头，``_ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

__all__ = ["IGNORE_DIRS", "IGNORE_EXT"]

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
