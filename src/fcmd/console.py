"""rich 显示层懒加载。

核心模块（task/graph/executors/command）不直接 import rich，通过本模块统一访问，
确保冷启动时 rich 仅在首次输出时才加载，满足 < 100ms 冷启动目标。

Win7/8 兼容：旧版 Windows conhost 不支持 VT 序列，rich 自动进入 legacy 模式后
宽度为 ``os.get_terminal_size().columns - 1``。但 conhost 最后一列写入触发自动换行
的边界行为，使 rich 减 1 的余量在部分场景仍不足以容纳表格右边框，导致超出窗口。
本模块显式检测 Win7/8 并额外收紧渲染宽度，规避该 conhost 限制。

Win7/8 乱码修复：conhost 默认使用点阵字体（Raster Fonts），不支持 box-drawing
字符（圆角边框 ``╭─╮``、阴影线 ``░▒▓``、双线 ``═╣`` 等），rich 默认的
ROUNDED/SQUARE 边框会渲染为方块/乱码。rich 的 ``ascii_only`` 自动推断依赖
``sys.stdout.encoding``，但 Python 3.6+ PEP 528 使 Windows 下
``sys.stdout.encoding`` 默认为 ``'utf-8'``（WriteConsoleW），导致 rich 误判以为
可输出 Unicode box 字符，实际被点阵字体渲染为乱码。

修复策略（三层兜底，跨所有 rich 版本）：

1. ``legacy_windows=True``：切到 ``SetConsoleTextAttribute`` 着色路径。
2. ``ascii_only=True``（若 rich 支持）：rich 13.x 中后期引入的参数，通过
   ``inspect.signature`` 检测；旧版 rich 不支持时跳过。
3. ``file=_AsciiBoxStream(sys.stdout)``：包装 stdout 拦截 ``write``，把所有
   box-drawing 字符 replace 为 ASCII（``+``/``-``/``|``）。对 rich 透明，
   不依赖 rich 内部 API，是旧版 rich（box monkey-patch 失效）的最终兜底。
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


# box-drawing 字符到 ASCII 的映射表（str.translate 用）。
# 覆盖 Unicode "Box Drawing" (U+2500-U+257F) 和 "Block Elements" (U+2580-U+259F)
# 以及 rich 常用的装饰字符。Win7 conhost 点阵字体不支持这些字符。
_BOX_TO_ASCII = str.maketrans(
    {
        # 圆角边框 ROUNDED
        "╭": "+",
        "╮": "+",
        "╰": "+",
        "╯": "+",
        # 直角边框 SQUARE
        "┌": "+",
        "┐": "+",
        "└": "+",
        "┘": "+",
        # T 型连接
        "├": "+",
        "┤": "+",
        "┬": "+",
        "┴": "+",
        "┼": "+",
        # 水平/垂直线
        "─": "-",
        "│": "|",
        # 双线 DOUBLE
        "═": "=",
        "║": "|",
        "╔": "+",
        "╗": "+",
        "╚": "+",
        "╝": "+",
        "╠": "+",
        "╣": "+",
        "╦": "+",
        "╩": "+",
        "╬": "+",
        # 粗线 HEAVY
        "━": "=",
        "┃": "|",
        "┏": "+",
        "┓": "+",
        "┗": "+",
        "┛": "+",
        "┣": "+",
        "┫": "+",
        "┳": "+",
        "┻": "+",
        "╋": "+",
        # 双线圆角
        "╓": "+",
        "╖": "+",
        "╙": "+",
        "╜": "+",
        "╟": "+",
        "╢": "+",
        "╤": "+",
        "╧": "+",
        "╨": "+",
        "╥": "+",
        "╞": "+",
        "╡": "+",
        "╪": "+",
        "╫": "+",
        # 虚线/点线
        "╌": "-",
        "╍": "=",
        "╎": "|",
        "╏": "|",
        "┄": "-",
        "┅": "=",
        "┆": "|",
        "┇": "|",
        "┈": "-",
        "┉": "=",
        "┊": "|",
        "┋": "|",
        # Block Elements 阴影块
        "░": " ",
        "▒": " ",
        "▓": "#",
        "█": "#",
        "▀": "#",
        "▄": "#",
        "▌": "#",
        "▐": "#",
        "▖": "#",
        "▗": "#",
        "▘": "#",
        "▝": "#",
        "▙": "#",
        "▚": "#",
        "▛": "#",
        "▜": "#",
        "▞": "#",
        "▟": "#",
        # 其他装饰字符
        "•": "*",
        "·": ".",
        "●": "*",
        "○": "o",
        "■": "#",
        "□": "#",
        "◆": "*",
        "◇": "*",
        "►": ">",
        "◄": "<",
        "▲": "^",
        "▼": "v",
    }
)


class _AsciiBoxStream:
    """拦截 stream ``write``，把 box-drawing 字符替换为 ASCII。

    Win7 conhost 默认点阵字体不支持 box-drawing 字符。rich 在 legacy_windows
    模式下直接 ``file.write`` 输出 Unicode box 字符，``ascii_only`` 参数和
    ``box`` 模块 monkey-patch 在旧版 rich 上可能失效。本类包装 stdout，
    在 ``write`` 时用 ``str.translate`` 把 box 字符 replace 为 ASCII，
    对 rich 透明，跨所有 rich 版本生效。

    保留中文等非 box Unicode 字符，仅替换 box-drawing 区块。
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, text: Any) -> int:
        """写入文本，box-drawing 字符被替换为 ASCII 后转发给底层 stream。"""
        if isinstance(text, str) and text:
            text = text.translate(_BOX_TO_ASCII)
        return self._stream.write(text)

    def flush(self) -> Any:
        return self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def fileno(self) -> int:
        return self._stream.fileno()

    @property
    def encoding(self) -> str:
        return self._stream.encoding

    @property
    def errors(self) -> str:
        return self._stream.errors

    @property
    def mode(self) -> str:
        return self._stream.mode

    @property
    def buffer(self) -> Any:
        return self._stream.buffer

    @property
    def line_buffering(self) -> bool:
        return self._stream.line_buffering

    @property
    def newlines(self) -> Any:
        return self._stream.newlines

    def writable(self) -> bool:
        return self._stream.writable()

    def readable(self) -> bool:
        return self._stream.readable()

    def seekable(self) -> bool:
        return self._stream.seekable()

    def __getattr__(self, name: str) -> Any:
        """其他属性/方法委托底层 stream。"""
        return getattr(self._stream, name)


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

    Win7/8 下显式传入：

    - ``legacy_windows=True``：使用 ``SetConsoleTextAttribute`` 着色（非 VT 序列）。
    - ``file=_AsciiBoxStream(sys.stdout)``：包装 stdout 拦截 box-drawing 字符，
      replace 为 ASCII。对 rich 透明，跨所有 rich 版本，是旧版 rich 的最终兜底。
    - ``ascii_only=True``（若 rich 支持）：rich 13.x 中后期参数，通过
      ``inspect.signature`` 检测；与 stdout 包装形成双保险。
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
            # 包装 stdout 拦截 box 字符：跨所有 rich 版本的最终兜底
            kwargs["file"] = _AsciiBoxStream(sys.stdout)
            # ascii_only 是 rich 13.x 中后期引入的参数，旧版不支持；
            # 通过签名检测兼容，不支持时跳过（stdout 包装已兜底）
            sig = inspect.signature(Console.__init__)
            if "ascii_only" in sig.parameters:
                kwargs["ascii_only"] = True
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
