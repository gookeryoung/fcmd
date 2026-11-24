"""通用帮助窗口：Markdown 正文 + 目录 + 搜索 + 可选示例表。

窗口骨架只依赖 :class:`fcmd.shared.helpdoc.HelpDoc`，不感知业务：
正文取自 ``HelpDoc.doc_path`` 指向的包内 Markdown，目录由二级 / 三级标题
生成，搜索框同时过滤目录并定位正文。示例表是可选插槽（renamerw 用它把
典型规则写回规则表），没有示例时完全不占空间。

菜单装配由 :class:`HelpMenuMixin` 负责：把「使用帮助」（``F1``）插到
「帮助 → 任务日志… → 关于」之前，与 ``LogChannelMixin`` 的日志入口保持
同一顺序；窗口只需在 ``__init__`` 里调用一次 :meth:`HelpMenuMixin.install_help_menu`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.widgets import button, hint_label, make_table, make_table_item
from fcmd.shared.helpdoc import HelpDoc

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["HelpDialog", "HelpExample", "HelpMenuMixin"]

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


@dataclass(frozen=True)
class HelpExample:
    """帮助窗口示例表的一行：名称 + 一句话说明（双击触发回调）。"""

    name: str
    summary: str = ""


def _headings(markdown: str) -> list[str]:
    """提取二级 / 三级标题（跳过代码块），用于目录与正文定位。"""
    headings: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match is not None:
            headings.append(match.group(2).strip())
    return headings


class HelpDialog(QDialog):  # pragma: no cover - 需要 Qt 事件循环
    """非模态帮助窗口：左目录 / 右 Markdown 正文 / 顶部搜索。

    Parameters
    ----------
    doc:
        帮助数据；``doc_path`` 存在时直接渲染包内 Markdown，否则用分段拼正文。
    parent:
        父窗口；关闭后保留对象，便于再次打开时保持滚动位置。
    examples:
        可选示例行；双击某行调用 ``on_example(row)``。
    on_example:
        示例行双击回调（参数为行号）。
    markdown:
        覆盖正文（测试或调用方已缓存文本时使用）。
    """

    def __init__(
        self,
        doc: HelpDoc,
        *,
        parent: QWidget | None = None,
        examples: Sequence[HelpExample] = (),
        on_example: Callable[[int], None] | None = None,
        markdown: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.doc = doc
        self._markdown = markdown if markdown is not None else doc.read_markdown()
        self._headings = _headings(self._markdown)
        self._on_example = on_example
        self.setWindowTitle(f"{doc.display_title} 使用帮助")
        self.setMinimumSize(760, 520)
        self.resize(900, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        if doc.summary.strip():
            layout.addWidget(hint_label(doc.summary.strip(), wrap=True))

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        search_row.addWidget(QLabel("搜索", self))
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("输入关键字过滤目录，回车定位")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_toc)
        self.search.returnPressed.connect(self._find_first)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.example_table: QTableWidget | None = None
        if examples:
            layout.addWidget(hint_label("典型示例（双击载入）", wrap=False))
            self.example_table = self._build_example_table(examples)
            layout.addWidget(self.example_table)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.toc = QListWidget(splitter)
        self.toc.setMinimumWidth(160)
        self.toc.setMaximumWidth(280)
        self.toc.setAlternatingRowColors(True)
        self.toc.currentRowChanged.connect(self._scroll_to_row)
        splitter.addWidget(self.toc)

        self.browser = QTextBrowser(splitter)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        if doc.doc_path is not None:
            self.browser.setSearchPaths([str(doc.doc_path.parent)])
        self.browser.setMarkdown(self._markdown)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        for heading in self._headings:
            self.toc.addItem(QListWidgetItem(heading))

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        if doc.doc_path is not None:
            open_button = button("打开文档")
            open_button.clicked.connect(self._open_document)
            bar.addWidget(open_button)
        copy_button = button("复制全文")
        copy_button.clicked.connect(self._copy_markdown)
        bar.addWidget(copy_button)
        bar.addStretch(1)
        close_button = button("关闭")
        close_button.setDefault(True)
        close_button.clicked.connect(self.close)
        bar.addWidget(close_button)
        layout.addLayout(bar)

    def _build_example_table(self, examples: Sequence[HelpExample]) -> QTableWidget:
        """只读示例表：名称 + 说明，双击行触发回调。"""
        table = make_table(("示例", "说明"), widths=(220, 620), stretch_columns=(1,), parent=self)
        table.setRowCount(len(examples))
        for row, example in enumerate(examples):
            table.setItem(row, 0, make_table_item(example.name))
            table.setItem(row, 1, make_table_item(example.summary))
        table.setMinimumHeight(110)
        table.cellDoubleClicked.connect(self._activate_example)
        return table

    def _activate_example(self, row: int, _column: int) -> None:
        """双击示例行：交给调用方载入。"""
        if self._on_example is not None:
            self._on_example(row)

    def _visible_headings(self) -> list[str]:
        """当前目录中可见的标题（按列表顺序）。"""
        return [self.toc.item(index).text() for index in range(self.toc.count()) if not self.toc.item(index).isHidden()]

    def _filter_toc(self, query: str) -> None:
        """按关键字过滤目录；清空时恢复全部并定位首项。"""
        needle = query.strip().lower()
        for index in range(self.toc.count()):
            item = self.toc.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())
        visible = [index for index in range(self.toc.count()) if not self.toc.item(index).isHidden()]
        if visible:
            self.toc.setCurrentRow(visible[0])

    def _find_first(self) -> None:
        """回车：定位到第一条可见目录；没有目录时在正文中查找关键字。"""
        visible = self._visible_headings()
        if visible:
            self._scroll_to(visible[0])
            return
        query = self.search.text().strip()
        if query:
            self._scroll_to(query)

    def _scroll_to_row(self, row: int) -> None:
        """目录选中变化：滚动正文到对应标题。"""
        item = self.toc.item(row)
        if item is not None and not item.isHidden():
            self._scroll_to(item.text())

    def _scroll_to(self, text: str) -> None:
        """在正文中查找标题（或搜索词）并选中，保证长文档也能定位。"""
        cursor = self.browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        found = self.browser.document().find(text, cursor)
        if found.isNull():
            return
        self.browser.setTextCursor(found)
        self.browser.ensureCursorVisible()

    def _open_link(self, url: QUrl) -> None:
        """处理正文链接：外链交给系统浏览器，相对路径按文档目录解析。"""
        scheme = url.scheme().lower()
        if scheme in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        if scheme == "file":
            QDesktopServices.openUrl(url)
            return
        target = url.toString()
        if target.startswith("#"):
            self.browser.scrollToAnchor(target[1:])
            return
        path = Path(target)
        if not path.is_absolute() and self.doc.doc_path is not None:
            path = self.doc.doc_path.parent / path
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_document(self) -> None:
        """在系统默认应用中打开包内 Markdown。"""
        if self.doc.doc_path is not None and self.doc.doc_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.doc.doc_path)))

    def _copy_markdown(self) -> None:
        """复制完整 Markdown 正文到剪贴板。"""
        QApplication.clipboard().setText(self._markdown)


class HelpMenuMixin:  # pragma: no cover - 需要 Qt 事件循环
    """窗口帮助菜单混入：``install_help_menu`` 创建 ``F1``「使用帮助」。

    依赖窗口骨架提供的 ``help_menu`` / ``log_action`` / ``about_action``；
    帮助项插在「任务日志…」之前，并把分隔线留在帮助项之后，保证菜单顺序为
    「使用帮助 / 任务日志… / --- / 关于」。
    """

    help_menu: Any = None
    log_action: Any = None
    about_action: Any = None
    _help_dialog: HelpDialog | None = None
    _help_action: Any = None
    _help_separator: Any = None
    _help_doc: HelpDoc | None = None
    _help_examples: tuple[HelpExample, ...] = ()
    _help_on_example: Callable[[int], None] | None = None

    def install_help_menu(
        self,
        doc: HelpDoc,
        *,
        label: str = "使用帮助",
        examples: Sequence[HelpExample] = (),
        on_example: Callable[[int], None] | None = None,
        key: str = "F1",
    ) -> Any:
        """创建并插入帮助动作；重复调用直接复用已有动作。"""
        if self._help_action is not None:
            return self._help_action
        from PySide6.QtGui import QAction, QKeySequence

        action = QAction(label, self)
        action.setShortcut(QKeySequence(key))
        action.triggered.connect(self.show_help)
        menu = self.help_menu
        if menu is not None:
            anchor = self.log_action or self.about_action
            if anchor is not None:
                menu.insertAction(anchor, action)
                menu.insertSeparator(anchor)
                self._help_separator = menu.actions()[menu.actions().index(anchor) - 1]
            else:
                menu.addAction(action)
        self._help_action = action
        self._help_doc = doc
        self._help_examples = tuple(examples)
        self._help_on_example = on_example
        return action

    def show_help(self) -> None:
        """打开 / 前置帮助窗口（非模态，可同时对照操作）。"""
        dialog = self._help_dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return
        if dialog is None:
            doc = self._help_doc
            if doc is None:
                return
            dialog = HelpDialog(
                doc,
                parent=self,  # type: ignore[arg-type] - 混入方为 QWidget
                examples=self._help_examples,
                on_example=self._help_on_example,
            )
            self._help_dialog = dialog
        dialog.show()
