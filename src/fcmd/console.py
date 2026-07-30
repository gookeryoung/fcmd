"""rich 显示层懒加载。

核心模块（task/graph/executors/command）不直接 import rich，通过本模块统一访问，
确保冷启动时 rich 仅在首次输出时才加载，满足 < 100ms 冷启动目标。

Win7/8 兼容：旧版 Windows conhost 不支持 VT 序列，rich 自动进入 legacy 模式后
宽度为 ``os.get_terminal_size().columns - 1``。但 conhost 最后一列写入触发自动换行
的边界行为，使 rich 减 1 的余量在部分场景仍不足以容纳表格右边框，导致超出窗口。
本模块显式检测 Win7/8 并额外收紧渲染宽度，规避该 conhost 限制。
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

__all__ = ["get_console", "print_verbose"]

_console: Console | None = None


def _is_legacy_windows() -> bool:
    """检测是否为旧版 Windows（Win7/8，conhost 不支持 VT 序列）。

    Win10 1607 起 conhost 支持 ANSI VT 序列处理；Win7/8 的 conhost 不支持，
    rich 会自动进入 legacy 模式（``SetConsoleTextAttribute`` + ``file.write``）。
    显式检测 Win7/8 以便 ``get_console`` 应用额外的宽度兼容余量。

    Returns:
        True 表示运行在 Win7/8 conhost 下，需要兼容处理。
    """
    if sys.platform != "win32":
        return False
    try:
        # Win7/8: major < 10；Win10+: major >= 10
        return sys.getwindowsversion().major < 10  # type: ignore[union-attr]
    except AttributeError:
        return False


def get_console() -> Console:
    """获取全局 rich Console 实例（懒加载）。

    首次调用时导入 rich 并创建 Console，后续直接返回缓存实例。

    Win7/8 下显式传入宽度（窗口列数 - 2）：rich 在 legacy 模式下默认渲染宽度为
    ``columns - 1``，但 conhost 最后一列自动换行行为使该余量不足以容纳表格右边框，
    故额外让 1 列（总余量 2 列）避免超出窗口。非交互环境（stdout 重定向、IDE
    管道等 ``os.get_terminal_size`` 失败的场景）不传宽度，由 rich 自行 fallback。
    """
    global _console  # noqa: PLW0603
    if _console is None:
        from rich.console import Console

        kwargs: dict[str, Any] = {}
        if _is_legacy_windows():
            kwargs["legacy_windows"] = True
            try:
                # os.get_terminal_size(1) 返回 srWindow 可视宽度（非 buffer 宽度）
                cols = os.get_terminal_size(1).columns
                # 显式传 width 时 rich 的 size 属性直接使用 _width（不再减 legacy_windows），
                # 故传入 cols-2 等价于默认 cols-1 再额外让 1 列给 conhost 余量
                kwargs["width"] = max(cols - 2, 1)
            except (OSError, ValueError):
                pass
        _console = Console(**kwargs)
    return _console


def print_verbose(*args: Any, **kwargs: Any) -> None:
    """verbose 模式输出辅助（通过 rich console）。"""
    get_console().print(*args, **kwargs)
