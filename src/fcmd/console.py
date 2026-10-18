"""显示层：``rich`` 的薄封装。

项目原先自实现 Console + Table（686 行）以规避外部依赖，现由 rich 承担：

- markup 解析、颜色降级（非 tty 自动转纯文本）、Windows VT 启用
- 表格排版（CJK 宽度、截断、对齐、边框样式）
- 进度条 / 语法高亮等后续能力可直接从 rich 取用

本模块只保留三件必须自己决定的事：

1. **单例与动态流绑定**：``file=None`` 时 rich 每次写入都重新解析
   ``sys.stdout``，因此 pytest ``capsys`` / 管道重定向都能正确捕获。
2. **box 语义映射**：``"ascii"`` / ``"round"`` / ``"auto"`` / ``None`` →
   rich ``box``，其中 ``"auto"`` 按终端编码能力探测（:func:`supports_unicode`）。
3. **markup 关闭开关**：``print(..., markup=False)`` 用于 mermaid 等含
   ``[...]`` 的正文。

冷启动：rich 只在 CLI 显示路径导入，``import fcmd`` 仍为 ~3ms。
"""

from __future__ import annotations

import sys
from typing import Any

from rich import box as rich_box
from rich.console import Console as RichConsole
from rich.table import Table as RichTable

__all__ = ["Console", "Table", "get_console", "print_error", "print_verbose", "supports_unicode"]

_console: Console | None = None

#: 项目内使用的 box 别名 → rich box
_BOXES: dict[str, rich_box.Box] = {
    "ascii": rich_box.ASCII,
    "round": rich_box.ROUNDED,
    "square": rich_box.SQUARE,
    "heavy": rich_box.HEAVY,
}


def supports_unicode(file: Any = None) -> bool:
    """检测输出流能否渲染 Unicode 制表符（圆角 box / 树形字符）。

    流无 ``encoding`` 属性（如 ``io.StringIO``）时按支持处理；用 ``╭``
    （U+256D，ascii / cp437 等窄编码不含该字形）试编码，失败则返回 False。

    Parameters
    ----------
    file:
        待探测的输出流；``None`` 使用 ``sys.stdout``。
    """
    out = file if file is not None else sys.stdout
    encoding = getattr(out, "encoding", None)
    if not encoding:
        return True
    try:
        "\u256d".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _resolve_box(box: Any) -> rich_box.Box | None:
    """把项目 box 别名解析为 rich ``Box``（``None`` 表示无边框）。"""
    if box is None:
        return None
    if box == "auto":
        return rich_box.ROUNDED if supports_unicode() else rich_box.ASCII
    if isinstance(box, str):
        return _BOXES.get(box, rich_box.ASCII)
    return box


class Table(RichTable):
    """rich ``Table`` 的项目默认值包装。

    与 rich 的差异仅在构造默认值：``box`` 接受 ``"ascii"``（默认）/
    ``"round"`` / ``"auto"`` / ``None`` 别名，其余参数与列选项直接透传。
    """

    def __init__(  # noqa: PLR0913 rich Table 签名透传
        self,
        *,
        title: str | None = None,
        show_header: bool = True,
        header_style: str | None = None,
        show_lines: bool = False,
        box: Any = "ascii",
        width: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            title=title,
            show_header=show_header,
            header_style=header_style,
            show_lines=show_lines,
            box=_resolve_box(box),
            width=width,
            **kwargs,
        )


class Console:
    """rich ``Console`` 封装，保留项目既有的 ``print`` 调用约定。

    - ``file=None``：每次写入动态解析 ``sys.stdout``（测试捕获友好）。
    - ``print(*args, end=..., sep=..., style=..., markup=...)``：未识别的
      rich 关键字直接忽略，避免调用点因版本差异报错。
    - 传入 :class:`Table` 时作为 renderable 输出。
    """

    def __init__(
        self,
        *,
        file: Any = None,
        width: int | None = None,
        legacy_windows: bool = False,  # noqa: ARG002 保留签名兼容
        ascii_only: bool = False,  # noqa: ARG002 保留签名兼容
        **kwargs: Any,
    ) -> None:
        self._rich = RichConsole(file=file, width=width, **kwargs)

    @property
    def rich(self) -> RichConsole:
        """底层 rich Console（需要进度条等高级用法时使用）。"""
        return self._rich

    def print(self, *args: Any, **kwargs: Any) -> None:
        """输出到 console，支持 rich markup。

        支持的关键字：``end`` / ``sep`` / ``style`` / ``markup`` /
        ``justify`` / ``soft_wrap`` / ``crop``；其余忽略。
        """
        allowed = ("end", "sep", "style", "justify", "soft_wrap", "crop", "no_wrap")
        opts: dict[str, Any] = {k: kwargs.pop(k) for k in allowed if k in kwargs}
        markup = kwargs.pop("markup", True)
        # 默认不折行：CLI 输出（长路径、单行日志）按原样写出，与终端宽度无关。
        opts.setdefault("soft_wrap", True)
        self._rich.print(*args, markup=markup, highlight=False, **opts)
        # 显式 flush：管道/打包场景下确保内容及时写出（rich 仅在 tty 下刷新）
        file = getattr(self._rich, "file", None)
        flush = getattr(file, "flush", None)
        if callable(flush):
            flush()


def get_console() -> Console:
    """获取全局 Console 实例（懒加载单例）。"""
    global _console  # noqa: PLW0603
    if _console is None:
        _console = Console()
    return _console


def print_error(message: str) -> None:
    """输出统一格式的错误行（``错误: {message}``，前缀为红色）。"""
    get_console().print(f"[red]错误:[/red] {message}")


def print_verbose(*args: Any, **kwargs: Any) -> None:
    """verbose 模式输出辅助（委托全局 Console）。"""
    get_console().print(*args, **kwargs)
