"""通用帮助窗口：Markdown 正文 + 目录 + 搜索 + 可选示例表。

窗口只依赖 :class:`fcmd.shared.helpdoc.HelpDoc`，不感知业务：正文取自
``HelpDoc.doc_path`` 指向的包内 Markdown，目录由二级 / 三级标题生成，搜索框
过滤目录并在正文中高亮命中。布局采用「顶部导航 + 左侧文档栏 + 右侧正文」：
搜索收进顶部导航右侧，文档来源 / 摘要 / 快捷键说明放进侧边栏，正文只留内容，
避免顶部堆叠标题、标签、摘要与搜索。Markdown 的标题 / 代码 / 表格样式由
``QTextDocument`` 默认样式表按当前主题注入。

菜单装配由 :class:`HelpMenuMixin` 负责：把「使用帮助」（``F1``）插到
「帮助 → 任务日志… → 关于」之前，与 ``LogChannelMixin`` 的日志入口保持同一
顺序；窗口只需在 ``__init__`` 里调用一次 :meth:`HelpMenuMixin.install_help_menu`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.icons import icon, set_icon
from fcmd.gui.style import active_theme
from fcmd.gui.theme import Theme
from fcmd.gui.widgets import (
    Card,
    button,
    hint_label,
    make_splitter,
    make_table,
    make_table_item,
    mono_font,
)
from fcmd.shared.helpdoc import HelpDoc

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["HelpDialog", "HelpExample", "HelpMenuMixin"]

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
_HIGHLIGHT_LIMIT = 200
"""正文命中高亮上限：长文档里避免为极短关键字生成上千个 ExtraSelection。"""


@dataclass(frozen=True)
class HelpExample:
    """帮助窗口示例表的一行：名称 + 一句话说明（双击触发回调）。"""

    name: str
    summary: str = ""


def _outline(markdown: str) -> list[tuple[int, str]]:
    """提取二级 / 三级标题（跳过代码块），返回 ``(level, title)`` 供目录建树。"""
    headings: list[tuple[int, str]] = []
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
            headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def _headings(markdown: str) -> list[str]:
    """提取二级 / 三级标题文本，用于正文定位。"""
    return [title for _level, title in _outline(markdown)]


def _document_stylesheet(theme: Theme, mono_family: str) -> str:
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
    }}
    pre {{
        font-family: "{mono}";
        font-size: {fonts.small}pt;
        color: {palette.log_fg};
        background: {palette.log_bg};
        border: 1px solid {palette.border};
        padding: 8px 10px;
        margin: 8px 0;
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


class HelpDialog(QDialog):  # pragma: no cover - 需要 Qt 事件循环
    """非模态帮助窗口：头部搜索 + 左目录 / 右 Markdown 正文 + 可选示例卡。

    Parameters
    ----------
    doc:
        帮助数据；``doc_path`` 存在时直接渲染包内 Markdown，否则用分段拼正文。
    parent:
        父窗口；关闭后保留对象，便于再次打开时保持滚动位置。
    examples:
        可选示例行；双击某行或点「载入选中示例」调用 ``on_example(row)``。
    on_example:
        示例行回调（参数为行号）。
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
        self.setObjectName("HelpDialog")
        self.doc = doc
        self._markdown = markdown if markdown is not None else doc.read_markdown()
        self._outline = _outline(self._markdown)
        self._headings = [title for _level, title in self._outline]
        self._heading_items: list[QTreeWidgetItem] = []
        self._on_example = on_example
        self._syncing_toc = False
        self._mono_family = mono_font().family()
        self.setWindowTitle(f"{doc.display_title} 使用帮助")
        self.setMinimumSize(760, 520)
        self.resize(960, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_top_nav())

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 14, 12)
        body_layout.setSpacing(10)
        splitter = make_splitter(
            Qt.Orientation.Horizontal,
            sizes=(220, 720),
            parent=body,
            handle_width=6,
        )
        splitter.setObjectName("HelpSplitter")
        splitter.addWidget(self._build_sidebar_panel(splitter))
        splitter.addWidget(self._build_content_panel(splitter, examples))
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.splitter = splitter
        body_layout.addWidget(splitter, 1)
        layout.addWidget(body, 1)

        self._populate_toc()
        self.browser.verticalScrollBar().valueChanged.connect(self._sync_toc_from_scroll)

        search_action = QAction("搜索", self)
        search_action.setShortcut(QKeySequence("Ctrl+F"))
        search_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        search_action.triggered.connect(self._focus_search)
        self.addAction(search_action)

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_top_nav(self) -> QFrame:
        """顶部导航：品牌 / 标题靠左，搜索与文档操作靠右。"""
        nav = QFrame(self)
        nav.setObjectName("HelpTopNav")
        bar = QHBoxLayout(nav)
        bar.setContentsMargins(14, 8, 14, 8)
        bar.setSpacing(10)

        icon_label = QLabel(nav)
        set_icon(icon_label, "circle-help", size=20, tone="accent")
        icon_label.setFixedSize(24, 24)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar.addWidget(icon_label)

        breadcrumb = QLabel("帮助", nav)
        breadcrumb.setObjectName("HelpBreadcrumb")
        bar.addWidget(breadcrumb)
        separator = QLabel("/", nav)
        separator.setObjectName("HelpBreadcrumb")
        bar.addWidget(separator)
        self._title_label = QLabel(self.doc.display_title, nav)
        self._title_label.setObjectName("HelpTitle")
        self._title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        bar.addWidget(self._title_label)
        bar.addStretch(1)

        self.search = QLineEdit(nav)
        self.search.setObjectName("HelpSearch")
        self.search.setPlaceholderText("搜索文档…")
        self.search.setToolTip("搜索目录与正文（Ctrl+F）")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(200)
        self.search.setMaximumWidth(380)
        self.search.addAction(icon("search", size=16), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._find_first)
        bar.addWidget(self.search, 1)
        return nav

    def _build_sidebar_panel(self, parent: QWidget) -> QWidget:
        """左侧目录：无卡片边框，只用一条分隔线与正文区分。"""
        panel = QWidget(parent)
        panel.setObjectName("HelpSidebar")
        panel.setMinimumWidth(200)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 10, 0)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        heading = QLabel("目录", panel)
        heading.setObjectName("HelpSidebarTitle")
        source = "Markdown 文档" if self.doc.doc_path is not None else "内置说明"
        heading.setToolTip(f"{source} · {len(self._headings)} 节")
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(hint_label(f"{len(self._headings)} 节", wrap=False))
        layout.addLayout(header)
        self.toc = QTreeWidget(panel)
        self.toc.setObjectName("HelpToc")
        self.toc.setHeaderHidden(True)
        self.toc.setRootIsDecorated(True)
        self.toc.setIndentation(14)
        self.toc.setAnimated(False)
        self.toc.setExpandsOnDoubleClick(True)
        self.toc.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.toc.currentItemChanged.connect(self._on_toc_item_changed)
        layout.addWidget(self.toc, 1)
        return panel

    def _populate_toc(self) -> None:
        """按二级标题建顶层项、三级标题挂为子项；默认展开，可逐项收起。"""
        self._heading_items = []
        top: QTreeWidgetItem | None = None
        for level, title in self._outline:
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, title)
            if level == 2 or top is None:
                self.toc.addTopLevelItem(item)
                top = item
            else:
                top.addChild(item)
            self._heading_items.append(item)
        self.toc.expandAll()

    def _build_content_panel(self, parent: QWidget, examples: Sequence[HelpExample]) -> QWidget:
        """右侧内容列：正文 Card + 可选示例 Card，示例不再横跨整个窗口。"""
        panel = QWidget(parent)
        panel.setObjectName("HelpContent")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._build_body(panel), 1)
        self.example_table: QTableWidget | None = None
        if examples:
            layout.addWidget(self._build_examples_card(panel, examples))
        return panel

    def _build_body(self, parent: QWidget) -> QTextBrowser:
        """正文：不套卡片，直接吃满右侧内容区，Markdown 样式按主题注入。"""
        self.browser = QTextBrowser(parent)
        self.browser.setObjectName("HelpBody")
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._open_link)
        if self.doc.doc_path is not None:
            self.browser.setSearchPaths([str(self.doc.doc_path.parent)])
        self.browser.document().setDefaultStyleSheet(_document_stylesheet(active_theme(), self._mono_family))
        self.browser.setMarkdown(self._markdown)
        return self.browser

    def _build_examples_card(self, parent: QWidget, examples: Sequence[HelpExample]) -> Card:
        """示例卡：只读表格 + 可选的「载入选中示例」主操作。"""
        card = Card("典型示例", parent)
        self.example_table = make_table(("示例", "说明"), widths=(220, 620), stretch_columns=(1,), parent=card)
        self.example_table.setObjectName("HelpExamples")
        self.example_table.setRowCount(len(examples))
        for row, example in enumerate(examples):
            self.example_table.setItem(row, 0, make_table_item(example.name))
            self.example_table.setItem(row, 1, make_table_item(example.summary))
        self.example_table.setMinimumHeight(90)
        self.example_table.setMaximumHeight(150)
        self.example_table.cellDoubleClicked.connect(self._activate_example)
        card.body.addWidget(hint_label("双击行载入；也可选中后点右上角「载入选中示例」", wrap=False))
        card.body.addWidget(self.example_table)
        if self._on_example is not None:
            load = button("载入选中示例", kind="primary")
            load.clicked.connect(self._load_selected_example)
            card.add_title_action(load)
        return card

    # ------------------------------------------------------------------ #
    # 搜索 / 目录联动
    # ------------------------------------------------------------------ #
    def _focus_search(self) -> None:
        """``Ctrl+F``：聚焦搜索框并全选，便于直接替换关键字。"""
        self.search.setFocus()
        self.search.selectAll()

    def _on_search_changed(self, query: str) -> None:
        """搜索变化：过滤目录、正文高亮并定位第一条可见目录。"""
        self._filter_toc(query)
        self._apply_highlight(query)

    def _visible_headings(self) -> list[str]:
        """当前目录中可见的标题（按树顺序）。"""
        headings: list[str] = []

        def walk(item: QTreeWidgetItem) -> None:
            if item.isHidden():
                return
            headings.append(item.text(0))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.toc.topLevelItemCount()):
            walk(self.toc.topLevelItem(index))
        return headings

    def _filter_toc(self, query: str) -> None:
        """按关键字过滤目录；命中子项时自动展开父项。"""
        needle = query.strip().lower()

        def apply(item: QTreeWidgetItem) -> bool:
            child_visible = False
            for index in range(item.childCount()):
                child_visible = apply(item.child(index)) or child_visible
            match = not needle or needle in item.text(0).lower()
            visible = match or child_visible
            item.setHidden(not visible)
            if child_visible:
                item.setExpanded(True)
            return visible

        for index in range(self.toc.topLevelItemCount()):
            apply(self.toc.topLevelItem(index))
        if not needle:
            self.toc.expandAll()
        visible = self._visible_headings()
        if visible:
            self._scroll_to(visible[0])

    def _find_first(self) -> None:
        """回车：定位到第一条可见目录；没有目录时在正文中查找关键字。"""
        visible = self._visible_headings()
        if visible:
            self._scroll_to(visible[0])
            return
        query = self.search.text().strip()
        if query:
            self._scroll_to(query)

    def _apply_highlight(self, query: str) -> None:
        """在正文中高亮所有命中（上限 :data:`_HIGHLIGHT_LIMIT`）。"""
        needle = query.strip()
        if not needle:
            self.browser.setExtraSelections([])
            return
        theme = active_theme()
        background = QColor(theme.palette.accent)
        background.setAlpha(70)
        fmt = QTextCharFormat()
        fmt.setBackground(background)
        fmt.setForeground(QColor(theme.palette.text))
        selections: list[QTextEdit.ExtraSelection] = []
        cursor = self._document_start()
        while len(selections) < _HIGHLIGHT_LIMIT:
            found = self.browser.document().find(needle, cursor)
            if found.isNull():
                break
            selection = QTextEdit.ExtraSelection()
            selection.cursor = found
            selection.format = fmt
            selections.append(selection)
            cursor = found
        self.browser.setExtraSelections(selections)

    def _document_start(self) -> QTextCursor:
        """正文文档起点的光标（每次查找都从头开始，避免状态互相影响）。"""
        cursor = QTextCursor(self.browser.document())
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        return cursor

    def _on_toc_item_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        """目录选中变化：滚动正文到对应标题。"""
        if self._syncing_toc or current is None:
            return
        self._scroll_to(current.text(0))

    def _scroll_to(self, text: str) -> None:
        """在正文中查找标题（或搜索词）并选中，保证长文档也能定位。"""
        self._syncing_toc = True
        try:
            found = self.browser.document().find(text, self._document_start())
            if not found.isNull():
                self.browser.setTextCursor(found)
                self.browser.ensureCursorVisible()
        finally:
            self._syncing_toc = False

    def _sync_toc_from_scroll(self, _value: int) -> None:
        """正文滚动时把目录高亮同步到当前章节。"""
        if self._syncing_toc or not self._headings:
            return
        anchor = self.browser.cursorForPosition(QPoint(8, 8)).position()
        current = 0
        for index, heading in enumerate(self._headings):
            found = self.browser.document().find(heading, self._document_start())
            if not found.isNull() and found.position() <= anchor:
                current = index
        item = self._heading_items[current] if current < len(self._heading_items) else None
        if item is not None and self.toc.currentItem() is not item:
            self._syncing_toc = True
            try:
                self.toc.setCurrentItem(item)
            finally:
                self._syncing_toc = False

    # ------------------------------------------------------------------ #
    # 示例 / 链接
    # ------------------------------------------------------------------ #
    def _activate_example(self, row: int, _column: int) -> None:
        """双击 / 按钮：把示例行号交给调用方。"""
        if self._on_example is not None:
            self._on_example(row)

    def _load_selected_example(self) -> None:
        """载入当前选中示例；没有选中行时载入第一行。"""
        if self.example_table is None or self.example_table.rowCount() == 0:
            return
        row = self.example_table.currentRow()
        self._activate_example(row if row >= 0 else 0, 0)

    def _open_link(self, url: QUrl) -> None:
        """处理正文链接：外链交给系统浏览器，相对路径按文档目录解析。"""
        scheme = url.scheme().lower()
        if scheme in ("http", "https", "mailto", "file"):
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
