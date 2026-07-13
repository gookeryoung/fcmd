"""rich 显示层懒加载。

核心模块（task/graph/executors/command）不直接 import rich，通过本模块统一访问，
确保冷启动时 rich 仅在首次输出时才加载，满足 < 100ms 冷启动目标。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

__all__ = ["get_console", "print_verbose"]

_console: Console | None = None


def get_console() -> Console:
    """获取全局 rich Console 实例（懒加载）。

    首次调用时导入 rich 并创建 Console，后续直接返回缓存实例。
    """
    global _console  # noqa: PLW0603
    if _console is None:
        from rich.console import Console

        _console = Console()
    return _console


def print_verbose(*args: Any, **kwargs: Any) -> None:
    """verbose 模式输出辅助（通过 rich console）。"""
    get_console().print(*args, **kwargs)
