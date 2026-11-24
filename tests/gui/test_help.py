"""通用帮助窗口（``fcmd.gui.help``）的离屏测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

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
        assert dialog.toc.count() == 3
        assert [dialog.toc.item(i).text() for i in range(3)] == ["用法", "环境变量", "常见问题"]
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
        visible = [dialog.toc.item(i).text() for i in range(dialog.toc.count()) if not dialog.toc.item(i).isHidden()]
        assert visible == ["环境变量"]
        dialog.search.setText("")
        visible = [dialog.toc.item(i).text() for i in range(dialog.toc.count()) if not dialog.toc.item(i).isHidden()]
        assert visible == ["用法", "环境变量", "常见问题"]
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
