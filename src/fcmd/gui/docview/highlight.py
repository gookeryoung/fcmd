"""代码块语法高亮：Pygments → 内联样式的 HTML。

延迟导入 Pygments：帮助窗口打开时才付出成本，``import fcmd`` 冷启动不受影响。
未知语言 / 超长代码块 / 缺少依赖时回退为转义纯文本，不阻塞文档渲染。
"""

from __future__ import annotations

from html import escape

from fcmd.gui.theme import Theme

from .theme import pygments_style

__all__ = ["MAX_HIGHLIGHT_LINES", "highlight_code"]

MAX_HIGHLIGHT_LINES = 2000
"""单代码块高亮上限：超过后直接转义显示，避免打开帮助卡顿。"""


def highlight_code(code: str, language: str, *, theme: Theme) -> str:
    """返回代码块 HTML；``language`` 为空 / 未知 / 超长时返回转义纯文本。"""
    info = language.strip().split()
    if not info or code.count("\n") > MAX_HIGHLIGHT_LINES:
        return escape(code)
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound
    except ImportError:  # pragma: no cover - 主依赖已声明，仅裁剪安装时触发
        return escape(code)
    try:
        lexer = get_lexer_by_name(info[0])
    except ClassNotFound:
        return escape(code)
    formatter = HtmlFormatter(nowrap=True, noclasses=True, style=pygments_style(theme))
    rendered = highlight(code, lexer, formatter)
    return rendered.rstrip("\n")
