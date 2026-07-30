"""轻量显示层：自实现 Console + Table，替代 rich。

核心模块（task/graph/executors/command）不直接依赖外部显示库，通过本模块
统一访问，确保冷启动时零外部依赖、< 100ms 冷启动目标。

支持能力（覆盖项目内全部调用点）：

- ``Console.print(*args, **kwargs)``：解析 rich 风格 markup 子集
  （``[cyan]``/``[red]``/``[green]``/``[yellow]``/``[bold]``/``[dim]``/
  ``[magenta]``/``[bold cyan]`` 等），按当前环境着色输出。
- ``Table``：``add_column`` / ``add_row``，ASCII 边框（``+``/``-``/``|``）
  或无边框（``box=None``）渲染，支持 ``style`` / ``justify`` / ``no_wrap``
  列选项（``no_wrap`` 当前忽略，仅签名兼容）。

着色策略：

- 非 tty（重定向、IDE 管道、测试 capsys）：纯文本输出，无颜色码。
- 非 Windows tty：ANSI 转义码。
- Win10+ tty：启用 VT 处理后用 ANSI 转义码。
- Win7/8 conhost（不支持 VT 序列）：``ctypes SetConsoleTextAttribute`` 16色。

Win7/8 兼容性：移除 rich 后，box-drawing 字符乱码问题自动消失（自实现
Table 仅用 ASCII ``+``/``-``/``|``）。保留 ``_is_legacy_windows`` 用于
颜色路径切换（VT vs SetConsoleTextAttribute）。
"""

from __future__ import annotations

import ctypes
import re
import sys
import unicodedata
from typing import Any

__all__ = ["Console", "Table", "get_console", "print_verbose"]

_console: Console | None = None


# ---------------------------------------------------------------------- #
# markup 解析
# ---------------------------------------------------------------------- #

# 匹配 [tag] 或 [/tag] 或 [/]
_TAG_RE = re.compile(r"\[(/?)\s*([^\]]+?)\s*\]")

# ANSI 前景色码（标准 8 色 + bright 变体）
_ANSI_FG = {
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "bright_black": "90",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_blue": "94",
    "bright_magenta": "95",
    "bright_cyan": "96",
    "bright_white": "97",
}

# ANSI 属性码
_ANSI_ATTR = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "underline": "4",
    "blink": "5",
}

# Win16 前景色位掩码（FOREGROUND_RED/GREEN/BLUE/INTENSITY）
_WIN_FG = {
    "black": 0,
    "red": 4,
    "green": 2,
    "yellow": 6,
    "blue": 1,
    "magenta": 5,
    "cyan": 3,
    "white": 7,
}

_ANSI_RESET = "\033[0m"


def _parse_markup(text: str) -> list[tuple[str, frozenset[str]]]:
    """解析 rich 风格 markup，返回 ``[(text_segment, styles_set), ...]``。

    标签语法：

    - ``[cyan]text[/cyan]``：应用 cyan 颜色
    - ``[bold cyan]text[/bold cyan]``：叠加 bold + cyan
    - ``[/]``：闭合最近一个开标签

    样式栈模型：开标签压入样式集合，闭标签弹出。当前生效样式 = 栈中所有
    集合的并集（支持 ``[bold][cyan]...[/cyan][/bold]`` 嵌套叠加）。

    闭标签按栈顺序弹出（不按名称精确匹配），对项目内成对使用的调用点足够。
    """
    pos = 0
    stack: list[frozenset[str]] = []
    current: frozenset[str] = frozenset()
    out: list[tuple[str, frozenset[str]]] = []

    for m in _TAG_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], current))
        pos = m.end()
        is_close = m.group(1) == "/"
        tag = m.group(2).strip()
        if is_close:
            if stack:
                stack.pop()
                current = frozenset().union(*stack) if stack else frozenset()
        else:
            new_styles = frozenset(tag.split())
            stack.append(new_styles)
            current = current | new_styles

    if pos < len(text):
        out.append((text[pos:], current))
    return out


def _strip_markup(text: str) -> str:
    """移除所有 markup 标签，返回纯文本（用于计算可见宽度）。"""
    return _TAG_RE.sub("", text)


def _display_width(s: str) -> int:
    """计算字符串在终端的显示宽度。

    East Asian Wide(W)/Fullwidth(F)/Ambiguous(A) 字符占 2 列，其余 1 列。
    漏掉 F 会导致全角字符（如 ``（）``）宽度计算偏小，表格右边框错位。
    """
    width = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F", "A"):
            width += 2
        else:
            width += 1
    return width


def _styles_to_ansi(styles: frozenset[str]) -> str:
    """把样式集合转为 ANSI 转义码（如 ``\\033[1;36m`` 表示 bold+cyan）。"""
    codes: list[str] = []
    for s in styles:
        if s in _ANSI_FG:
            codes.append(_ANSI_FG[s])
        elif s in _ANSI_ATTR:
            codes.append(_ANSI_ATTR[s])
    if not codes:
        return ""
    return f"\033[{';'.join(codes)}m"


def _styles_to_win_attr(styles: frozenset[str]) -> int:
    """把样式集合转为 Win16 前景色位掩码（FOREGROUND_* 或运算结果）。

    返回值：颜色位（低 4 bit 的 RGB 部分）与 FOREGROUND_INTENSITY(8) 的或运算
    结果。无样式时返回 7（默认白前景）以支持恢复默认颜色。
    """
    attr = 7  # 默认白前景
    for s in styles:
        if s in _WIN_FG:
            # 替换颜色位（低 3 bit RGB），保留强度位
            attr = (attr & 8) | _WIN_FG[s]
        elif s == "bold":
            attr |= 8  # FOREGROUND_INTENSITY
        # dim/italic/underline 等在 Win16 无对应，忽略
    return attr


# ---------------------------------------------------------------------- #
# Win7/8 legacy 检测
# ---------------------------------------------------------------------- #


def _is_legacy_windows() -> bool:
    """检测是否为旧版 Windows（Win7/8，conhost 不支持 VT 序列）。

    Win10 1607 起 conhost 支持 ANSI VT 序列处理；Win7/8 的 conhost 不支持，
    需要通过 ``SetConsoleTextAttribute`` 着色。

    Returns:
        True 表示运行在 Win7/8 conhost 下，需要 legacy 着色路径。
    """
    if sys.platform != "win32":
        return False
    try:
        # Win7/8: major < 10；Win10+: major >= 10
        return sys.getwindowsversion().major < 10  # type: ignore[union-attr]
    except AttributeError:
        return False


def _enable_vt_mode() -> bool:
    """Win10+ 启用 console VT 处理，返回是否成功。

    非Windows 平台返回 True（无需启用）。失败时返回 False，调用方回退到
    无颜色输出。
    """
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        if not kernel32.SetConsoleMode(handle, new_mode):
            return False
    except (OSError, AttributeError):
        return False
    else:
        return True


# ---------------------------------------------------------------------- #
# Table
# ---------------------------------------------------------------------- #


class Table:
    """轻量 ASCII 表格，兼容 rich ``Table`` 的常用子集。

    支持的构造参数（与 rich ``Table`` 签名兼容）：

    - ``title``：表格标题（可选）。
    - ``show_header``：是否显示表头行（默认 True）。
    - ``header_style``：表头样式（如 ``"bold"``）。
    - ``show_lines``：是否显示行间分隔线（当前固定不显示，签名兼容）。
    - ``box``：边框样式，``None`` 表示无边框（对齐输出），非 None 表示
      ASCII 边框（``+``/``-``/``|``）。

    列选项（``add_column``）：``style``（列样式）、``justify``（对齐：
    left/center/right）、``no_wrap``（当前忽略）。
    """

    def __init__(
        self,
        *,
        title: str | None = None,
        show_header: bool = True,
        header_style: str | None = None,
        show_lines: bool = False,  # noqa: ARG002 签名兼容，当前不绘制行间分隔
        box: Any = "ascii",
    ) -> None:
        self.title = title
        self.show_header = show_header
        self.header_style = header_style or ""
        self.box = box
        self._columns: list[dict[str, Any]] = []
        self._rows: list[tuple[str, ...]] = []

    def add_column(
        self,
        header: str,
        *,
        style: str | None = None,
        no_wrap: bool = False,  # noqa: ARG002 签名兼容，当前忽略
        justify: str = "left",
    ) -> None:
        """添加列定义。"""
        self._columns.append({"header": header, "style": style, "justify": justify})

    def add_row(self, *cells: Any) -> None:
        """添加一行数据（自动转 str）。"""
        self._rows.append(tuple(str(c) for c in cells))

    def _col_widths(self) -> list[int]:
        """计算每列最大可见宽度（含表头）。"""
        widths = []
        for i, col in enumerate(self._columns):
            w = _display_width(_strip_markup(col["header"]))
            for row in self._rows:
                if i < len(row):
                    w = max(w, _display_width(_strip_markup(row[i])))
            widths.append(w)
        return widths

    @staticmethod
    def _pad(text: str, width: int, justify: str) -> str:
        """按显示宽度填充对齐（保留 markup 标签，按纯文本宽度计算）。"""
        visible = _strip_markup(text)
        pad = width - _display_width(visible)
        if pad <= 0:
            return text
        if justify == "right":
            return " " * pad + text
        if justify == "center":
            left = pad // 2
            right = pad - left
            return " " * left + text + " " * right
        return text + " " * pad  # left

    def _render_no_box(self) -> str:
        """无边框渲染：列间两空格分隔。"""
        widths = self._col_widths()
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
            lines.append("")
        if self.show_header:
            parts = [
                self._pad(
                    f"[{self.header_style}]{col['header']}[/{self.header_style}]"
                    if self.header_style
                    else col["header"],
                    widths[i],
                    col["justify"],
                )
                for i, col in enumerate(self._columns)
            ]
            lines.append("  ".join(parts))
        for row in self._rows:
            parts = [
                self._pad(row[i] if i < len(row) else "", widths[i], self._columns[i]["justify"])
                for i in range(len(self._columns))
            ]
            lines.append("  ".join(parts))
        return "\n".join(lines)

    def _render_boxed(self) -> str:
        """ASCII 边框渲染：``+``/``-``/``|``。"""
        widths = self._col_widths()
        sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
            lines.append("")
        lines.append(sep)
        if self.show_header:
            parts = [
                self._pad(
                    f"[{self.header_style}]{col['header']}[/{self.header_style}]"
                    if self.header_style
                    else col["header"],
                    widths[i],
                    col["justify"],
                )
                for i, col in enumerate(self._columns)
            ]
            lines.append("| " + " | ".join(parts) + " |")
            lines.append(sep)
        for row in self._rows:
            parts = [
                self._pad(row[i] if i < len(row) else "", widths[i], self._columns[i]["justify"])
                for i in range(len(self._columns))
            ]
            lines.append("| " + " | ".join(parts) + " |")
        lines.append(sep)
        return "\n".join(lines)

    def __str__(self) -> str:
        """渲染表格为字符串。"""
        if not self._columns:
            return self.title or ""
        if self.box is None:
            return self._render_no_box()
        return self._render_boxed()


# ---------------------------------------------------------------------- #
# Console
# ---------------------------------------------------------------------- #


class Console:
    """轻量 Console，支持 rich 风格 markup 子集着色。

    构造参数（与 rich ``Console`` 部分签名兼容，便于平滑迁移）：

    - ``legacy_windows``：强制使用 ``SetConsoleTextAttribute`` 着色（Win7/8）。
    - ``ascii_only``：签名兼容，当前忽略（自实现 Table 仅用 ASCII，无 box-drawing）。
    - ``width``：渲染宽度（当前忽略，由终端决定）。
    - ``file``：输出流，默认 ``sys.stdout``。

    ``print`` 方法接受 ``end`` / ``sep`` / ``style`` 关键字，其他 rich
    关键字（``highlight`` / ``justify`` / ``soft_wrap`` / ``overflow`` /
    ``no_wrap`` 等）签名兼容但当前忽略。
    """

    def __init__(
        self,
        *,
        legacy_windows: bool = False,
        ascii_only: bool = False,  # noqa: ARG002 签名兼容，当前忽略
        width: int | None = None,  # noqa: ARG002 签名兼容，当前忽略
        file: Any = None,
    ) -> None:
        # file=None 表示运行时动态解析 sys.stdout，确保 pytest capsys 能捕获
        self._file = file
        self._explicit_file = file is not None
        self._legacy = legacy_windows
        self._color_enabled = self._detect_color()
        self._kernel32: Any = None
        if self._legacy and self._color_enabled:
            try:
                self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            except (OSError, AttributeError):
                self._color_enabled = False

    @property
    def _out(self) -> Any:
        """当前输出流：显式传入时用传入值，否则动态取 sys.stdout。

        动态解析确保 pytest capsys 等运行时替换 stdout 的场景能正确捕获输出，
        而非持有构造时的旧 stdout 引用。
        """
        return self._file if self._explicit_file else sys.stdout

    def _detect_color(self) -> bool:
        """检测是否应启用颜色输出。"""
        out = self._out
        try:
            is_tty = bool(out.isatty())
        except (AttributeError, ValueError):
            is_tty = False
        if not is_tty:
            return False
        if sys.platform != "win32":
            return True
        if self._legacy:
            return True  # Win7/8 用 SetConsoleTextAttribute
        return _enable_vt_mode()

    def _apply_win_color(self, styles: frozenset[str]) -> None:
        """Win7/8 legacy 模式：调用 SetConsoleTextAttribute 切换颜色。"""
        if self._kernel32 is None:
            return
        attr = _styles_to_win_attr(styles)
        try:
            handle = self._kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            self._kernel32.SetConsoleTextAttribute(handle, attr)
        except (OSError, AttributeError):
            pass

    def _render_text(self, text: str) -> str:
        """解析 markup 并按 ANSI 模式渲染（返回带转义码的字符串）。

        legacy 模式下由 ``_write_legacy`` 直接处理样式切换；此方法仅用于
        非 legacy 路径。
        """
        if not self._color_enabled:
            return _strip_markup(text)
        segments = _parse_markup(text)
        buf: list[str] = []
        prev: frozenset[str] = frozenset()
        for seg_text, styles in segments:
            if styles != prev:
                code = _styles_to_ansi(styles)
                if code:
                    buf.append(code)
                elif prev:
                    buf.append(_ANSI_RESET)
                prev = styles
            buf.append(seg_text)
        if prev:
            buf.append(_ANSI_RESET)
        return "".join(buf)

    def _write_legacy(self, text: str, end: str) -> None:
        """Win7/8 legacy 模式输出：逐段切换 SetConsoleTextAttribute。"""
        segments = _parse_markup(text)
        prev: frozenset[str] = frozenset()
        out = self._out
        for seg_text, styles in segments:
            if styles != prev:
                self._apply_win_color(styles)
                prev = styles
            if seg_text:
                out.write(seg_text)
        # 恢复默认颜色
        if prev:
            self._apply_win_color(frozenset())
        out.write(end)

    def print(self, *args: Any, **kwargs: Any) -> None:
        """输出到 console，支持 rich 风格 markup。

        支持的关键字参数：``end``（默认 ``\\n``）、``sep``（默认空格）、
        ``style``（整体样式）。其他 rich 关键字签名兼容但忽略。

        若首个参数是 :class:`Table` 实例，渲染表格后输出。
        """
        end = kwargs.pop("end", "\n")
        sep = kwargs.pop("sep", " ")
        style = kwargs.pop("style", None)
        # 其余 kwargs（highlight/justify/soft_wrap/overflow/no_wrap 等）忽略

        if len(args) == 1 and isinstance(args[0], Table):
            text = str(args[0])
        else:
            text = sep.join(str(a) for a in args)
            if style:
                text = f"[{style}]{text}[/{style}]"

        if self._legacy and self._color_enabled:
            self._write_legacy(text, end)
        else:
            self._out.write(self._render_text(text) + end)


# ---------------------------------------------------------------------- #
# 模块级 API
# ---------------------------------------------------------------------- #


def get_console() -> Console:
    """获取全局 Console 实例（懒加载单例）。

    Win7/8 下使用 ``SetConsoleTextAttribute`` 着色（legacy_windows=True）；
    其他平台用 ANSI 转义码。
    """
    global _console  # noqa: PLW0603
    if _console is None:
        kwargs: dict[str, Any] = {}
        if _is_legacy_windows():
            kwargs["legacy_windows"] = True
        _console = Console(**kwargs)
    return _console


def print_verbose(*args: Any, **kwargs: Any) -> None:
    """verbose 模式输出辅助（委托全局 Console）。"""
    get_console().print(*args, **kwargs)
