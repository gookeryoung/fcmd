"""rich 显示层懒加载。

核心模块（task/graph/executors/command）不直接 import rich，通过本模块统一访问，
确保冷启动时 rich 仅在首次输出时才加载，满足 < 100ms 冷启动目标。

Win7/8 兼容：旧版 Windows conhost 不支持 VT 序列，rich 自动进入 legacy 模式后
宽度为 ``os.get_terminal_size().columns - 1``。但 conhost 最后一列写入触发自动换行
的边界行为，使 rich 减 1 的余量在部分场景仍不足以容纳表格右边框，导致超出窗口。
本模块显式检测 Win7/8 并额外收紧渲染宽度，规避该 conhost 限制。

Win7/8 乱码修复：conhost 默认使用点阵字体（Raster Fonts），不支持 box-drawing
字符（圆角边框、阴影线等），rich 默认的 ROUNDED/SQUARE 边框会渲染为方块/乱码。
rich 的 ``ascii_only`` 自动推断依赖 ``sys.stdout.encoding``，但 Python 3.6+ PEP 528
使 Windows 下 ``sys.stdout.encoding`` 默认为 ``'utf-8'``（WriteConsoleW），导致 rich
误判以为可输出 Unicode box 字符，实际被点阵字体渲染为乱码。本模块在 Win7/8 下
显式传 ``ascii_only=True`` 强制 rich 用 ASCII box 字符（``+``/``-``/``|``）。

旧版 rich 兼容：``ascii_only`` 参数在 rich 13.x 早期版本不存在（Win7 + Python 3.8
环境下 pip 通常只能装到 13.0~13.4）。通过 ``inspect.signature`` 检测 Console 是否
支持该参数；不支持时降级 monkey-patch ``rich.box.BOXES`` 把所有 box 字符表替换为
``box.ASCII``，达到等价效果。
"""

from __future__ import annotations

import inspect
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


def _force_ascii_box() -> None:
    """旧版 rich 降级：把 ``rich.box`` 模块所有 Box 实例常量替换为 ``box.ASCII``。

    ``ascii_only`` 参数在 rich 13.x 早期版本不存在。Win7 conhost 点阵字体
    不支持 box-drawing 字符，直接 monkey-patch ``rich.box`` 模块的所有
    Box 实例常量（ROUNDED/SQUARE/HEAVY/DOUBLE 等），让 Table/Panel 等组件
    渲染时使用 ``box.ASCII`` 的 ``+``/``-``/``|`` 纯 ASCII 字符。

    副作用范围：仅 Win7/8 + 旧版 rich 触发，影响当前进程内所有 rich
    Table/Panel 渲染。fcmd 单进程场景下可接受。
    """
    from rich import box

    ascii_box = box.ASCII
    # 遍历模块级 Box 实例常量（ROUNDED/SQUARE/HEAVY/DOUBLE 等），全部替换为 ASCII
    for name in dir(box):
        if name.startswith("_"):
            continue
        value = getattr(box, name)
        if isinstance(value, box.Box):
            setattr(box, name, ascii_box)


def get_console() -> Console:
    """获取全局 rich Console 实例（懒加载）。

    首次调用时导入 rich 并创建 Console，后续直接返回缓存实例。

    Win7/8 下显式传入：

    - ``legacy_windows=True``：使用 ``SetConsoleTextAttribute`` 着色（非 VT 序列）。
    - ``ascii_only=True``（若 rich 支持）：强制 rich 用 ASCII box 字符。Win7 conhost
      默认点阵字体不支持 box-drawing 字符；rich 的 ``ascii_only`` 自动推断依赖
      ``sys.stdout.encoding``，但 PEP 528 使其在 Windows 下默认为 ``'utf-8'``，
      导致 rich 误判输出 Unicode box 字符被点阵字体渲染为方块/乱码。
    - 旧版 rich 降级：若 ``Console.__init__`` 不接受 ``ascii_only``（rich 13.x 早期），
      调用 ``_force_ascii_box`` monkey-patch ``rich.box.BOXES`` 替换为 ASCII box。
    - ``width=cols-2``：rich 在 legacy 模式下默认渲染宽度为 ``columns - 1``，但
      conhost 最后一列自动换行行为使该余量不足以容纳表格右边框，故额外让 1 列
      （总余量 2 列）避免超出窗口。

    非交互环境（stdout 重定向、IDE 管道等 ``os.get_terminal_size`` 失败的场景）
    不传宽度，由 rich 自行 fallback。
    """
    global _console  # noqa: PLW0603
    if _console is None:
        from rich.console import Console

        kwargs: dict[str, Any] = {}
        if _is_legacy_windows():
            kwargs["legacy_windows"] = True
            # ascii_only 是 rich 13.x 中后期引入的参数，早期版本（13.0~13.4）
            # 不支持，通过签名检测兼容；不支持时降级 monkey-patch box.BOXES
            sig = inspect.signature(Console.__init__)
            if "ascii_only" in sig.parameters:
                kwargs["ascii_only"] = True
            else:
                _force_ascii_box()
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
