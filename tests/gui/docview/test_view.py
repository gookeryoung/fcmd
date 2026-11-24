"""HelpView：主题化渲染、搜索高亮、定位与代码块复制。"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.gui.docview import HelpView, render_markdown


@pytest.fixture()
def view() -> Iterator[HelpView]:
    create_application("docview-test")
    theme = _guitheme.resolve_theme("dark-blue")
    widget = HelpView()
    rendered = render_markdown(
        "# 演示\n\n## 用法\n\n```python\nprint('hi')\n```\n\n## 环境变量\n\nFCMD_HOME\n",
        theme=theme,
    )
    widget.set_rendered(rendered, theme=theme, mono_family="TestMono")
    yield widget
    widget.deleteLater()


def test_view_renders_markdown_and_highlights(view: HelpView) -> None:
    theme = _guitheme.resolve_theme("dark-blue")
    assert "FCMD_HOME" in view.toPlainText()
    view.highlight("FCMD_HOME", theme=theme)
    assert view.extraSelections()
    view.highlight("", theme=theme)
    assert not view.extraSelections()


def test_view_scrolls_and_finds_heading(view: HelpView) -> None:
    assert view.find_position("环境变量") >= 0
    assert view.scroll_to("环境变量") is True
    assert view.scroll_to("不存在的标题") is False


def test_view_copies_code_block(view: HelpView) -> None:
    assert view.copy_code(0) is True
    assert QApplication.clipboard().text() == "print('hi')\n"
    assert view.copy_code(99) is False
