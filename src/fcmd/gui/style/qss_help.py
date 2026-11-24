"""帮助窗口专属 QSS：从 ``qss.py`` 拆出，避免主模板超过行数预算。"""

from __future__ import annotations

__all__ = ["HELP_QSS"]

HELP_QSS = """
QDialog#HelpDialog { background: $background; }
/* 帮助窗口：顶部导航 + 左侧文档栏 + 右侧正文，搜索收进导航右侧，
   不再把标题、标签、摘要与搜索堆在同一个头部卡片里。 */
QFrame#HelpTopNav {
    background: $surface;
    border: none;
    border-bottom: 1px solid $border;
}
QLabel#HelpBreadcrumb {
    color: $muted;
    font-size: ${font_small}pt;
}
QLabel#HelpTitle {
    color: $text;
    font-size: ${font_title}pt;
    font-weight: 700;
}
QLineEdit#HelpSearch {
    min-height: $run_control_height;
    max-height: $run_control_height;
    padding: 0 10px;
    background: $background;
    border: 1px solid $border;
    border-radius: 14px;
}
QLineEdit#HelpSearch:focus { border-color: $accent; }
/* 目录和正文都不套卡片：只保留一条可拖动的细分隔线，把宽度留给内容。 */
QSplitter#HelpSplitter::handle {
    background: transparent;
    border-left: 1px solid $border;
}
QSplitter#HelpSplitter::handle:hover { border-left-color: $accent; }
QSplitter#HelpSplitter::handle:pressed { border-left-color: $accent; }
QLabel#HelpSidebarTitle {
    color: $muted;
    font-size: ${font_small}pt;
    font-weight: 600;
}
QTreeWidget#HelpToc {
    background: transparent;
    border: none;
    outline: 0;
    padding: 2px;
}
QTreeWidget#HelpToc::item {
    padding: 6px 8px;
    border-radius: 4px;
    color: $muted;
}
QTreeWidget#HelpToc::item:hover { background: $surface_hover; color: $text; }
QTreeWidget#HelpToc::item:selected {
    background: $accent_soft_strong;
    color: $accent;
    font-weight: 600;
}
QTextBrowser#HelpBody {
    background: $background;
    border: none;
    padding: 0 4px 0 8px;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
}
QTableWidget#HelpExamples { background: transparent; border: none; }
"""
