"""cli 共享辅助：跨工具复用的常量与入口样板。

本模块以下划线开头，``_ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

__all__ = ["IGNORE_DIRS", "IGNORE_EXT", "_BUILTIN_COMMANDS"]

# 内建命令名（不通过 @fx.tool 注册，由 FcmdApp 直接处理）
# 放在此处供 main.py / _completion_scripts.py 共享，避免循环导入
_BUILTIN_COMMANDS: tuple[str, ...] = ("graph", "info", "completion", "yaml", "env", "doctor", "profiler")

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
