"""通用帮助窗口（``fcmd.gui.help``）的离屏测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QWidget

from fcmd.gui.app import create_application
from fcmd.gui.help import HelpDialog, HelpExample, HelpMenuMixin
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.shared.helpdoc import HelpDoc, HelpSection
from tests.helpers import destroy_qt_top_levels, qt_top_levels

_MARKDOWN = """# 演示工具

一句话说明。

## 用法

前置条件。

## 环境变量

FCMD_HOME  配置目录。

## 常见问题

- 问题一
"""


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离窗口设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("help-test")
    return base


def _doc() -> HelpDoc:
    return HelpDoc(
        tool="demo",
        title="演示工具",
        summary="一句话说明。",
        sections=(HelpSection("env", lines=["FCMD_HOME  配置目录。"]),),
    )


def test_dialog_builds_toc_and_markdown(home: Path) -> None:
    """目录来自二级标题，正文渲染 Markdown。"""
    stale = qt_top_levels()
    dialog = HelpDialog(_doc(), markdown=_MARKDOWN)
    try:
        assert dialog.windowTitle() == "演示工具 使用帮助"
        assert dialog.toc.topLevelItemCount() == 3
        assert [dialog.toc.topLevelItem(i).text(0) for i in range(3)] == ["用法", "环境变量", "常见问题"]
        assert "FCMD_HOME" in dialog.browser.toPlainText()
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


def test_search_filters_toc(home: Path) -> None:
    """搜索框按标题过滤目录；清空后恢复。"""
    stale = qt_top_levels()
    dialog = HelpDialog(_doc(), markdown=_MARKDOWN)
    try:
        dialog.search.setText("环境")
        assert dialog._visible_headings() == ["环境变量"]
        dialog.search.setText("")
        assert dialog._visible_headings() == ["用法", "环境变量", "常见问题"]
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


_NESTED_MARKDOWN = """# 演示工具

## 用法

### 子步骤

1. 第一步

## 环境变量

FCMD_HOME  配置目录。
"""


def test_toc_tree_supports_expand_and_collapse(home: Path) -> None:
    """二级标题是顶层项，三级标题挂为子项，可展开 / 收起。"""
    stale = qt_top_levels()
    dialog = HelpDialog(_doc(), markdown=_NESTED_MARKDOWN)
    try:
        assert dialog.toc.topLevelItemCount() == 2
        usage = dialog.toc.topLevelItem(0)
        assert usage.text(0) == "用法"
        assert usage.childCount() == 1
        assert usage.child(0).text(0) == "子步骤"
        assert usage.isExpanded()
        usage.setExpanded(False)
        assert not usage.isExpanded()

        dialog.search.setText("子步骤")
        assert not usage.isHidden()
        assert not usage.child(0).isHidden()
        assert "子步骤" in dialog._visible_headings()
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


def test_example_table_callback(home: Path) -> None:
    """示例表双击把行号交给调用方。"""
    stale = qt_top_levels()
    picked: list[int] = []
    dialog = HelpDialog(
        _doc(),
        markdown=_MARKDOWN,
        examples=[HelpExample("示例一", "说明一"), HelpExample("示例二", "说明二")],
        on_example=picked.append,
    )
    try:
        assert dialog.example_table is not None
        assert dialog.example_table.rowCount() == 2
        dialog._activate_example(1, 0)
        assert picked == [1]
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


class _HelpWindow(HelpMenuMixin, QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """测试用窗口：验证帮助菜单装配与复用。"""

    def __init__(self) -> None:
        super().__init__(title="帮助测试", config_name="helptest")


def test_help_menu_is_inserted_before_log_and_about(home: Path) -> None:
    """「使用帮助」排在任务日志与关于之前，重复安装复用同一动作。"""
    stale = qt_top_levels()
    window = _HelpWindow()
    try:
        action = window.install_help_menu(_doc())
        texts = [item.text() for item in window.help_menu.actions()]
        assert texts.index("使用帮助") < texts.index(window.log_action.text())
        assert texts[-1] == "关于"
        assert window.install_help_menu(_doc()) is action
        window.show_help()
        QApplication.processEvents()
        assert window._help_dialog is not None
        assert window._help_dialog.isVisible()
        window.show_help()
        assert window._help_dialog is not None
    finally:
        window.close()
        destroy_qt_top_levels(stale)


def test_dialog_uses_shared_visual_components(home: Path) -> None:
    """头部 / 目录 / 正文使用共享控件与对象名，便于主题 QSS 接管。"""
    stale = qt_top_levels()
    dialog = HelpDialog(_doc(), markdown=_MARKDOWN)
    try:
        assert dialog.objectName() == "HelpDialog"
        assert dialog._title_label.objectName() == "HelpTitle"
        assert dialog.search.objectName() == "HelpSearch"
        assert dialog.toc.objectName() == "HelpToc"
        assert dialog.browser.objectName() == "HelpBody"
        assert dialog.findChild(QFrame, "HelpTopNav") is not None
        assert dialog.findChild(QWidget, "HelpSidebar") is not None
        assert dialog.findChild(QWidget, "HelpContent") is not None
        heading = dialog.findChild(QLabel, "HelpSidebarTitle")
        assert heading is not None and heading.text() == "目录"
        assert [action.shortcut().toString() for action in dialog.actions() if not action.shortcut().isEmpty()] == [
            "Ctrl+F"
        ]
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


def test_search_highlights_body_matches(home: Path) -> None:
    """搜索框除过滤目录外，还在正文里生成命中高亮。"""
    stale = qt_top_levels()
    dialog = HelpDialog(_doc(), markdown=_MARKDOWN)
    try:
        dialog.search.setText("环境")
        assert dialog.browser.extraSelections()
        dialog.search.setText("")
        assert not dialog.browser.extraSelections()
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


def test_example_primary_action_loads_selected_row(home: Path) -> None:
    """示例卡右上角主操作按当前行触发回调。"""
    stale = qt_top_levels()
    picked: list[int] = []
    dialog = HelpDialog(
        _doc(),
        markdown=_MARKDOWN,
        examples=[HelpExample("示例一", "说明一"), HelpExample("示例二", "说明二")],
        on_example=picked.append,
    )
    try:
        assert dialog.example_table is not None
        dialog.example_table.setCurrentCell(1, 0)
        dialog._load_selected_example()
        assert picked == [1]
        buttons = [button.text() for button in dialog.findChildren(QPushButton)]
        assert "载入选中示例" in buttons
    finally:
        dialog.close()
        destroy_qt_top_levels(stale)


def test_document_stylesheet_uses_theme_tokens(home: Path) -> None:
    """Markdown 默认样式表取当前主题的强调色与等宽字体。"""
    from fcmd.gui.help import _document_stylesheet
    from fcmd.gui.style import active_theme

    theme = active_theme()
    css = _document_stylesheet(theme, "TestMono")
    assert "TestMono" in css
    assert theme.palette.accent in css
    assert "h2" in css and "pre" in css and "table" in css
