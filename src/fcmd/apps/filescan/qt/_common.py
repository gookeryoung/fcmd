"""filescan 对话框共用的小工具：多行输入与单行压平。"""

from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit

__all__ = ["_collapse_multiline", "_list_text", "_multiline_editor"]


def _collapse_multiline(text: str) -> str:
    """把多行输入合并为单行：换行只用于排版，行首缩进一并去掉。"""
    return "".join(part.strip() for part in text.splitlines()).strip()


def _list_text(text: str) -> str:
    """把多行列表合并为以分号分隔的单行：这里换行是列表分隔符。"""
    return ";".join(part.strip() for part in text.splitlines() if part.strip())


def _multiline_editor(
    text: str = "",
    placeholder: str = "",
    *,
    min_height: int = 64,
    max_height: int = 96,
) -> QPlainTextEdit:
    """「单行语义 / 多行排版」输入框：按宽度折行，保存时再压成一行或按分隔符解析。

    用于可能很长的配置文字（跳过目录、后缀列表、正则表达式、组合式表达式）：单行
    ``QLineEdit`` 会把内容藏在水平滚动后面，这里固定高度内自动换行。
    """
    editor = QPlainTextEdit()
    editor.setPlainText(text)
    editor.setPlaceholderText(placeholder)
    editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
    editor.setMinimumHeight(min_height)
    editor.setMaximumHeight(max_height)
    editor.setTabChangesFocus(True)
    return editor
