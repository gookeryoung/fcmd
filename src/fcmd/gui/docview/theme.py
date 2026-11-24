"""帮助文档主题：QTextDocument CSS 与代码高亮配色。

纯函数（只读 :class:`Theme`），不创建控件；``gui/help.py`` 与其它文档窗口共用。
"""

from __future__ import annotations

from fcmd.gui.theme import Theme, is_dark_palette

__all__ = ["document_stylesheet", "pygments_style"]


def pygments_style(theme: Theme) -> str:
    """按主题明暗选择 Pygments 样式名（浅色 ``friendly`` / 深色 ``one-dark``）。"""
    return "monokai" if is_dark_palette(theme.palette) else "friendly"


def document_stylesheet(theme: Theme, mono_family: str) -> str:
    """按主题生成 QTextDocument 默认样式表（标题 / 代码 / 表格 / 链接）。"""
    palette = theme.palette
    fonts = theme.fonts
    mono = mono_family or "monospace"
    return f"""
    body {{ color: {palette.text}; font-size: {fonts.body}pt; }}
    h1 {{
        color: {palette.text};
        font-size: {fonts.title + 4}pt;
        font-weight: 700;
        margin: 2px 0 10px 0;
    }}
    h2 {{
        color: {palette.text};
        font-size: {fonts.title}pt;
        font-weight: 700;
        margin: 18px 0 8px 0;
        padding-bottom: 3px;
        border-bottom: 1px solid {palette.border};
    }}
    h3 {{
        color: {palette.accent};
        font-size: {fonts.heading}pt;
        font-weight: 600;
        margin: 14px 0 6px 0;
    }}
    p {{ margin: 0 0 8px 0; }}
    ul, ol {{ margin: 4px 0 10px 0; }}
    li {{ margin: 2px 0; }}
    a {{ color: {palette.accent}; text-decoration: none; }}
    code {{
        font-family: "{mono}";
        font-size: {fonts.small}pt;
        color: {palette.accent};
        background: {palette.log_bg};
        padding: 1px 4px;
        border-radius: 4px;
    }}
    pre {{
        font-family: "{mono}";
        font-size: {fonts.small}pt;
        color: {palette.log_fg};
        background: {palette.log_bg};
        border: none;
        padding: 8px 10px;
        margin: 0;
    }}
    blockquote {{
        color: {palette.muted};
        border-left: 3px solid {palette.accent};
        margin: 8px 0;
        padding-left: 10px;
    }}
    table {{ border-collapse: collapse; margin: 8px 0; }}
    th {{
        color: {palette.text};
        background: {palette.surface_hover};
        border: 1px solid {palette.border};
        padding: 4px 8px;
    }}
    td {{
        color: {palette.text};
        border: 1px solid {palette.border};
        padding: 4px 8px;
    }}
    hr {{ border: none; border-top: 1px solid {palette.border}; margin: 12px 0; }}
    """
