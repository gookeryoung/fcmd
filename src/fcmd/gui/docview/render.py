"""Markdown → HTML：GFM 风格表格 / 删除线 + 代码块高亮 + 复制链接。

用 ``markdown-it-py`` 而不是 ``QTextBrowser.setMarkdown``：Qt 自带解析器
不暴露 fence 回调，代码块无法高亮、无法单独复制；这里先生成受控 HTML，
再由 :class:`~fcmd.gui.docview.view.HelpView` 交给 QTextDocument 渲染。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from fcmd.gui.theme import Theme

from .highlight import highlight_code

__all__ = ["RenderedDocument", "render_markdown"]


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    """渲染结果：HTML 正文 + 代码块原文（复制链接按下标取用）。"""

    html: str
    code_blocks: tuple[str, ...] = ()
    base_dir: Path | None = None


def _markdown() -> MarkdownIt:
    """构造解析器：禁用原始 HTML，启用表格与删除线（不需要 linkify 依赖）。"""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    md.enable(["table", "strikethrough"])
    return md


def _code_block_html(
    *,
    index: int,
    language: str,
    highlighted: str,
    theme: Theme,
    mono_family: str,
) -> str:
    """艺术化代码块：左侧强调色细条 + 无边框暗色容器 + 轻量语言/复制行。"""
    palette = theme.palette
    mono = mono_family or "monospace"
    label = escape(language or "text")
    label_style = f"color:{palette.accent}; font-family:'{mono}'; font-size:{theme.fonts.small}pt;"
    copy_style = f"color:{palette.muted}; text-decoration:none; font-size:{theme.fonts.small}pt;"
    cell = f"background:{palette.log_bg}; border:none;"
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="margin:10px 0; border:none; border-collapse:collapse;">'
        f'<tr><td rowspan="2" width="3" style="background:{palette.accent}; border:none;"></td>'
        f'<td style="{cell} padding:8px 14px 2px 14px;"><span style="{label_style}">{label}</span></td>'
        f'<td align="right" style="{cell} padding:8px 14px 2px 14px;">'
        f'<a href="fcmd-copy-code:{index}" style="{copy_style}">复制</a></td></tr>'
        f'<tr><td colspan="2" style="{cell} padding:0 14px 12px 14px;">'
        f'<pre style="{cell} margin:0; padding:0;"><code>{highlighted}</code></pre></td></tr></table>\n'
    )


def render_markdown(
    markdown: str,
    *,
    theme: Theme,
    mono_family: str = "",
    base_dir: Path | None = None,
) -> RenderedDocument:
    """渲染 Markdown；代码块带语言栏 / 复制链接，正文由 QTextDocument 渲染。"""
    code_blocks: list[str] = []

    def render_fence(_renderer: Any, tokens: list[Any], idx: int, _options: Any, _env: Any) -> str:
        token = tokens[idx]
        code = str(token.content)
        info = str(token.info or "").strip()
        language = info.split()[0] if info else ""
        index = len(code_blocks)
        code_blocks.append(code)
        highlighted = highlight_code(code, language, theme=theme)
        return _code_block_html(
            index=index,
            language=language,
            highlighted=highlighted,
            theme=theme,
            mono_family=mono_family,
        )

    md = _markdown()
    md.add_render_rule("fence", render_fence)
    return RenderedDocument(html=md.render(markdown), code_blocks=tuple(code_blocks), base_dir=base_dir)
