"""文档渲染包：Markdown → Qt 富文本、代码高亮与通用帮助视图。

只依赖 Qt、``shared`` 与标准库；不反向依赖 ``core`` / ``tools`` / ``apps``。
``gui/help.py`` 只负责窗口编排，渲染细节集中在这里，便于其它 GUI 复用。
"""

from __future__ import annotations

from .render import RenderedDocument, render_markdown
from .theme import document_stylesheet, pygments_style
from .view import HelpView

__all__ = [
    "HelpView",
    "RenderedDocument",
    "document_stylesheet",
    "pygments_style",
    "render_markdown",
]
