"""通用文档视图：搜索高亮、标题定位、链接与代码块复制。

从 ``gui/help.py`` 抽出的 ``QTextBrowser`` 子类；帮助窗口只负责布局，其它
GUI 需要 Markdown 说明时可直接复用本类。代码块复制通过渲染期写入的
``fcmd-copy-code:<index>`` 链接完成，不依赖 QTextDocument 内部的块属性。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QTextBrowser, QTextEdit, QWidget

from fcmd.gui.theme import Theme

from .render import RenderedDocument
from .theme import document_stylesheet

__all__ = ["HelpView"]

HIGHLIGHT_LIMIT = 200
"""正文命中高亮上限：长文档里避免为极短关键字生成上千个 ExtraSelection。"""


class HelpView(QTextBrowser):  # pragma: no cover - Qt 控件
    """文档正文视图：主题化 Markdown、搜索高亮、标题定位与代码块复制。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._open_link)
        self._code_blocks: tuple[str, ...] = ()
        self._base_dir: Path | None = None

    def set_rendered(self, rendered: RenderedDocument, *, theme: Theme, mono_family: str) -> None:
        """写入渲染结果并注入主题样式 / 相对路径基准目录。"""
        self._code_blocks = rendered.code_blocks
        self._base_dir = rendered.base_dir
        if rendered.base_dir is not None:
            self.setSearchPaths([str(rendered.base_dir)])
        self.document().setDefaultStyleSheet(document_stylesheet(theme, mono_family))
        self.setHtml(rendered.html)

    def highlight(self, text: str, *, theme: Theme) -> None:
        """高亮正文中的全部命中（上限 :data:`HIGHLIGHT_LIMIT`）。"""
        needle = text.strip()
        if not needle:
            self.setExtraSelections([])
            return
        background = QColor(theme.palette.accent)
        background.setAlpha(70)
        fmt = QTextCharFormat()
        fmt.setBackground(background)
        fmt.setForeground(QColor(theme.palette.text))
        selections: list[QTextEdit.ExtraSelection] = []
        cursor = self._document_start()
        while len(selections) < HIGHLIGHT_LIMIT:
            found = self.document().find(needle, cursor)
            if found.isNull():
                break
            selection = QTextEdit.ExtraSelection()
            selection.cursor = found
            selection.format = fmt
            selections.append(selection)
            cursor = found
        self.setExtraSelections(selections)

    def find_position(self, text: str) -> int:
        """返回文本在文档中的起始位置；未命中返回 ``-1``。"""
        found = self.document().find(text, self._document_start())
        return -1 if found.isNull() else found.position()

    def viewport_anchor_position(self) -> int:
        """视口左上角对应的文档位置（目录滚动联动用）。"""
        return self.cursorForPosition(QPoint(8, 8)).position()

    def scroll_to(self, text: str) -> bool:
        """定位文本并选中；未命中返回 ``False``。"""
        found = self.document().find(text, self._document_start())
        if found.isNull():
            return False
        self.setTextCursor(found)
        self.ensureCursorVisible()
        return True

    def copy_code(self, index: int) -> bool:
        """复制第 ``index`` 个代码块的原文；越界返回 ``False``。"""
        if not 0 <= index < len(self._code_blocks):
            return False
        QApplication.clipboard().setText(self._code_blocks[index])
        return True

    def _document_start(self) -> QTextCursor:
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        return cursor

    def _open_link(self, url: QUrl) -> None:
        """处理正文链接：代码复制链接、外链、锚点与相对文件路径。"""
        scheme = url.scheme().lower()
        if scheme == "fcmd-copy-code":
            try:
                self.copy_code(int(url.path() or url.toString().split(":", 1)[1]))
            except (ValueError, IndexError):
                return
            return
        if scheme in ("http", "https", "mailto", "file"):
            QDesktopServices.openUrl(url)
            return
        target = url.toString()
        if target.startswith("#"):
            self.scrollToAnchor(target[1:])
            return
        path = Path(target)
        if not path.is_absolute() and self._base_dir is not None:
            path = self._base_dir / path
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
