"""轻量显示层：自实现 Console + Table，替代 rich。

核心模块（task/graph/executors/command）不直接依赖外部显示库，通过本模块
统一访问，确保冷启动时零外部依赖、< 100ms 冷启动目标。

支持能力（覆盖项目内全部调用点）：

- ``Console.print(*args, **kwargs)``：解析 rich 风格 markup 子集
  （``[cyan]``/``[red]``/``[green]``/``[yellow]``/``[bold]``/``[dim]``/
  ``[magenta]``/``[bold cyan]`` 等），按当前环境着色输出。
- ``Table``：``add_column`` / ``add_row``，支持三种边框——ASCII
  （``+``/``-``/``|``，默认）、圆角 Unicode 制表符（``box="round"``）、
  无边框（``box=None``）；``box="auto"`` 经 :func:`supports_unicode` 按
  终端编码能力自动降级。支持 ``style`` / ``justify`` 列选项与
  ``no_wrap`` 弹性列（超宽截断）、``show_lines`` 行间分隔线。

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
import shutil
import sys
import unicodedata
from typing import Any

__all__ = ["Console", "Table", "get_console", "print_verbose", "supports_unicode"]

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
# Unicode 渲染能力探测与 box 字符集
# ---------------------------------------------------------------------- #


def supports_unicode(file: Any = None) -> bool:
    """检测输出流是否支持 Unicode 制表符（round box / 树形字符）。

    判定顺序：

    - legacy Windows（Win7/8 conhost 不支持宽字符渲染）：返回 False。
    - 流无 ``encoding`` 属性（如 ``io.StringIO``）：按支持处理。
    - 用 ``╭``（U+256D，ascii / cp437 等窄编码不包含该字形；GBK /
      UTF-8 均包含）试编码，失败则返回 False。

    Parameters
    ----------
    file:
        待探测的输出流；``None`` 使用 ``sys.stdout``。
    """
    if _is_legacy_windows():
        return False
    out = file if file is not None else sys.stdout
    encoding = getattr(out, "encoding", None)
    if not encoding:
        return True
    try:
        "\u256d".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


# 边框字符集：ascii 为纯 ASCII（Win7/8 conhost 安全）；round 为圆角 Unicode
# 制表符（``box="auto"`` 经 :func:`supports_unicode` 探测后选用）。
_BOX_CHARS: dict[str, dict[str, str]] = {
    "ascii": {
        "h": "-",
        "v": "|",
        "top_l": "+",
        "top_m": "+",
        "top_r": "+",
        "mid_l": "+",
        "mid_m": "+",
        "mid_r": "+",
        "bot_l": "+",
        "bot_m": "+",
        "bot_r": "+",
    },
    "round": {
        "h": "─",
        "v": "│",
        "top_l": "╭",
        "top_m": "┬",
        "top_r": "╮",
        "mid_l": "├",
        "mid_m": "┼",
        "mid_r": "┤",
        "bot_l": "╰",
        "bot_m": "┴",
        "bot_r": "╯",
    },
}


# ---------------------------------------------------------------------- #
# Table
# ---------------------------------------------------------------------- #


class Table:
    """轻量表格，兼容 rich ``Table`` 的常用子集。

    支持的构造参数（与 rich ``Table`` 签名兼容）：

    - ``title``：表格标题（可选）。
    - ``show_header``：是否显示表头行（默认 True）。
    - ``header_style``：表头样式（如 ``"bold"``）。
    - ``show_lines``：是否显示数据行间分隔线（默认 False）。
    - ``box``：边框样式——``None`` 无边框（对齐输出）；``"ascii"`` 纯
      ASCII 边框（默认，Win7/8 安全）；``"round"`` 圆角 Unicode 制表符；
      ``"auto"`` 经 :func:`supports_unicode` 探测自动选用 round / ascii。
    - ``width``：渲染宽度预算（列宽截断依据）；``None`` 用终端宽度。

    列选项（``add_column``）：``style``（列样式）、``justify``（对齐：
    left/center/right）、``no_wrap``（表格总宽超预算时该列截断并以
    ``…`` 结尾，其余列保持完整）。
    """

    def __init__(  # noqa: PLR0913 rich Table 签名兼容，参数数量对齐
        self,
        *,
        title: str | None = None,
        show_header: bool = True,
        header_style: str | None = None,
        show_lines: bool = False,
        box: Any = "ascii",
        width: int | None = None,
    ) -> None:
        self.title = title
        self.show_header = show_header
        self.header_style = header_style or ""
        self.show_lines = show_lines
        self.box = box
        self.width = width
        self._columns: list[dict[str, Any]] = []
        self._rows: list[tuple[str, ...]] = []

    def add_column(
        self,
        header: str,
        *,
        style: str | None = None,
        no_wrap: bool = False,
        justify: str = "left",
    ) -> None:
        """添加列定义。

        ``no_wrap=True`` 表示该列在表格总宽超过渲染预算时截断（``…`` 结尾），
        为长文本列（说明 / 别名等）预留弹性空间；其余列始终完整显示。
        """
        self._columns.append({"header": header, "style": style, "justify": justify, "no_wrap": no_wrap})

    def add_row(self, *cells: Any) -> None:
        """添加一行数据（自动转 str）。"""
        self._rows.append(tuple(str(c) for c in cells))

    def _resolve_box(self) -> str:
        """解析实际使用的边框字符集键（``auto`` 按终端能力探测）。"""
        if self.box == "auto":
            return "round" if supports_unicode() else "ascii"
        if isinstance(self.box, str) and self.box in _BOX_CHARS:
            return self.box
        return "ascii"

    def _render_width(self) -> int:
        """渲染宽度预算：显式 ``width`` 优先，否则取终端宽度。"""
        if self.width is not None:
            return self.width
        return shutil.get_terminal_size().columns

    def _col_widths(self) -> list[int]:
        """计算每列最终宽度（含表头；no_wrap 列超预算时收缩）。"""
        widths = []
        for i, col in enumerate(self._columns):
            w = _display_width(_strip_markup(col["header"]))
            for row in self._rows:
                if i < len(row):
                    w = max(w, _display_width(_strip_markup(row[i])))
            widths.append(w)
        widths = self._shrink_no_wrap(widths)
        return widths

    def _shrink_no_wrap(self, widths: list[int]) -> list[int]:
        """表格总宽超预算时按自然宽度比例收缩 no_wrap 列（单列下限 4）。"""
        flex = {i for i, col in enumerate(self._columns) if col["no_wrap"]}
        if not flex:
            return widths
        if self.box is None:
            overhead = 2 * (len(widths) - 1)
        else:
            overhead = 3 * len(widths) + 1
        total = overhead + sum(widths)
        limit = self._render_width()
        if total <= limit:
            return widths
        fixed = sum(w for i, w in enumerate(widths) if i not in flex)
        budget = max(4 * len(flex), limit - overhead - fixed)
        flex_total = sum(widths[i] for i in flex)
        scale = budget / flex_total
        if scale >= 1:
            return widths
        return [min(w, max(4, int(w * scale))) if i in flex else w for i, w in enumerate(widths)]

    def _prepare_cell(self, i: int, text: str, width: int) -> str:
        """渲染前处理单元格：no_wrap 列超宽时截断（丢失 markup 样式）。"""
        if self._columns[i]["no_wrap"] and _display_width(_strip_markup(text)) > width:
            return self._truncate_visible(text, width)
        return text

    @staticmethod
    def _truncate_visible(text: str, width: int) -> str:
        """按显示宽度截断纯文本，超出部分以 ``…`` 结尾。"""
        if width <= 1:
            return "…"
        used = 0
        out: list[str] = []
        for ch in _strip_markup(text):
            w = _display_width(ch)
            if used + w > width - 1:
                break
            out.append(ch)
            used += w
        return "".join(out) + "…"

    def _pad(self, text: str, width: int, justify: str) -> str:
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

    def _row_cells(self, row: tuple[str, ...], widths: list[int]) -> list[str]:
        """把一行数据按列宽预处理（截断）并对齐填充。"""
        parts = []
        for i, col in enumerate(self._columns):
            cell = self._prepare_cell(i, row[i] if i < len(row) else "", widths[i])
            parts.append(self._pad(cell, widths[i], col["justify"]))
        return parts

    def _header_cells(self, widths: list[int]) -> list[str]:
        """按列宽对齐填充表头（应用 header_style）。"""
        parts = []
        for i, col in enumerate(self._columns):
            header = (
                f"[{self.header_style}]{col['header']}[/{self.header_style}]" if self.header_style else col["header"]
            )
            parts.append(self._pad(header, widths[i], col["justify"]))
        return parts

    def _render_no_box(self) -> str:
        """无边框渲染：列间两空格分隔。"""
        widths = self._col_widths()
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
            lines.append("")
        if self.show_header:
            lines.append("  ".join(self._header_cells(widths)))
        for row in self._rows:
            lines.append("  ".join(self._row_cells(row, widths)))
        return "\n".join(lines)

    def _render_boxed(self) -> str:
        """带边框渲染：ascii（``+``/``-``/``|``）或 round（圆角制表符）。"""
        b = _BOX_CHARS[self._resolve_box()]
        widths = self._col_widths()
        sep_top = b["top_l"] + b["top_m"].join(b["h"] * (w + 2) for w in widths) + b["top_r"]
        sep_mid = b["mid_l"] + b["mid_m"].join(b["h"] * (w + 2) for w in widths) + b["mid_r"]
        sep_bot = b["bot_l"] + b["bot_m"].join(b["h"] * (w + 2) for w in widths) + b["bot_r"]
        lines: list[str] = []
        if self.title:
            lines.append(self.title)
            lines.append("")
        lines.append(sep_top)
        if self.show_header:
            lines.append(b["v"] + " " + f" {b['v']} ".join(self._header_cells(widths)) + " " + b["v"])
            lines.append(sep_mid)
        for idx, row in enumerate(self._rows):
            lines.append(b["v"] + " " + f" {b['v']} ".join(self._row_cells(row, widths)) + " " + b["v"])
            if self.show_lines and idx < len(self._rows) - 1:
                lines.append(sep_mid)
        lines.append(sep_bot)
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

    @staticmethod
    def _flush(out: Any) -> None:
        """安全 flush 输出流。

        PyInstaller/fspacker 打包后通过管道调用时，stdout 为全缓冲；
        若不在每次 print 后 flush，缓冲区内容在进程退出前不会写出，
        导致管道接收端看不到任何输出（``fcmd ... | less`` 等场景显示为空）。
        rich Console 默认每次 print 后 flush，自实现需对齐此行为。
        """
        flush = getattr(out, "flush", None)
        if callable(flush):
            flush()

    def print(self, *args: Any, **kwargs: Any) -> None:
        """输出到 console，支持 rich 风格 markup。

        支持的关键字参数：``end``（默认 ``\\n``）、``sep``（默认空格）、
        ``style``（整体样式）、``markup``（默认 True；False 表示纯文本
        原样输出，不做 markup 解析，用于 mermaid 等含 ``[...]`` 字符的
        内容）。其他 rich 关键字签名兼容但忽略。

        若首个参数是 :class:`Table` 实例，渲染表格后输出。
        """
        end = kwargs.pop("end", "\n")
        sep = kwargs.pop("sep", " ")
        style = kwargs.pop("style", None)
        markup = kwargs.pop("markup", True)
        # 其余 kwargs（highlight/justify/soft_wrap/overflow/no_wrap 等）忽略

        if len(args) == 1 and isinstance(args[0], Table):
            text = str(args[0])  # 渲染结果仍含表头/单元格 markup，走正常解析
        else:
            text = sep.join(str(a) for a in args)
            if style and markup:
                text = f"[{style}]{text}[/{style}]"

        out = self._out
        if not markup:
            # 纯文本路径：legacy 与 ANSI 模式均原样写出（文本中无样式标签）
            out.write(text + end)
        elif self._legacy and self._color_enabled:
            self._write_legacy(text, end)
        else:
            out.write(self._render_text(text) + end)
        # 对齐 rich 默认行为：每次 print 后 flush，避免管道场景下输出丢失。
        self._flush(out)


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
