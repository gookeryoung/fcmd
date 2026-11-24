"""Markdown 渲染与代码高亮：代码块、表格、未知语言与超长降级。"""

from __future__ import annotations

from fcmd.gui import theme as _guitheme
from fcmd.gui.docview import render_markdown
from fcmd.gui.docview.highlight import MAX_HIGHLIGHT_LINES, highlight_code
from fcmd.shared.helpdoc import help_headings, help_outline


def test_render_markdown_code_block_highlights_and_copy_link() -> None:
    """代码块带语言标签 / 复制链接，内容被 Pygments 包成 span。"""
    theme = _guitheme.resolve_theme("dark-blue")
    rendered = render_markdown("```python\nprint('hi')\n```", theme=theme)
    assert rendered.code_blocks == ("print('hi')\n",)
    assert "fcmd-copy-code:0" in rendered.html
    assert "<table" in rendered.html and "<pre" in rendered.html
    assert ">python<" in rendered.html
    assert "print" in rendered.html
    assert "<span" in rendered.html


def test_render_markdown_tables_and_strikethrough() -> None:
    """GFM 表格与删除线由 markdown-it 渲染，不依赖 Qt 自带解析器。"""
    theme = _guitheme.resolve_theme("dark-blue")
    rendered = render_markdown("| A | B |\n|---|---|\n| 1 | 2 |\n\n~~旧~~", theme=theme)
    assert "<table>" in rendered.html
    assert "<s>旧</s>" in rendered.html


def test_unknown_language_falls_back_to_escaped_text() -> None:
    """未知语言不高亮，代码中的 HTML 被转义。"""
    theme = _guitheme.resolve_theme("dark-blue")
    rendered = render_markdown("```unknown-lang\n<script>alert(1)</script>\n```", theme=theme)
    assert "&lt;script&gt;" in rendered.html
    assert "<script>" not in rendered.html


def test_long_code_block_skips_highlighting() -> None:
    """超过行数上限的代码块直接转义，不生成 Pygments span。"""
    theme = _guitheme.resolve_theme("dark-blue")
    code = "x\n" * (MAX_HIGHLIGHT_LINES + 1)
    assert "<span" not in highlight_code(code, "python", theme=theme)


def test_help_outline_is_shared_and_skips_code_fences() -> None:
    """目录提取下沉到 shared 后仍跳过代码块里的 #。"""
    markdown = "# 标题\n\n## 用法\n\n```text\n## 不是标题\n```\n\n### 子步骤\n\n## 示例\n"
    assert help_outline(markdown) == ((2, "用法"), (3, "子步骤"), (2, "示例"))
    assert help_headings(markdown) == ("用法", "子步骤", "示例")
