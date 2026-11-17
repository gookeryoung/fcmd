"""``fcmd.gui.qt.sidebar`` 的离屏测试：宽度铺满、折叠 / 展开、快捷键与提示。

Qt 不可用时整体跳过（CI 只装 ``--extra test``，不含 PySide6）。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QSizePolicy

from fcmd.gui.qt.sidebar import COLLAPSED_WIDTH, EXPANDED_WIDTH, SidebarNav


@pytest.fixture()
def nav() -> Any:
    QApplication.instance() or QApplication([])
    widget = SidebarNav(title="locllm")
    widget.add("overview", "概览", hint="服务状态与快捷入口")
    widget.add("run", "运行", hint="档位、上下文与启动参数")
    widget.add("settings", "设置", hint="存储、引擎与关于")
    yield widget
    widget.deleteLater()


def test_pages_are_registered_in_order(nav: Any) -> None:
    assert nav.keys() == ("overview", "run", "settings")
    assert nav.selected() == "overview"
    assert nav.button("overview") is not None and nav.button("overview").isChecked()


def test_buttons_expand_to_fill_sidebar_width(nav: Any) -> None:
    """页按钮横向铺满侧栏；纵向高度交给共享样式统一，不在按钮上单独写死。"""
    button = nav.button("run")
    assert button is not None
    assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert button.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    assert button.minimumHeight() == 0
    assert nav.width() == EXPANDED_WIDTH


def test_collapse_and_expand(nav: Any) -> None:
    events: list[bool] = []
    nav.collapsedChanged.connect(events.append)
    nav.set_collapsed(True)
    assert nav.is_collapsed()
    assert nav.width() == COLLAPSED_WIDTH
    assert nav.title_label.isHidden()
    assert nav.hint_label.isHidden()
    for key, label in (("overview", "概览"), ("run", "运行"), ("settings", "设置")):
        button = nav.button(key)
        assert button is not None
        assert button.text() == label[:1]
        assert label in button.toolTip()
    nav.set_collapsed(True)  # 幂等，不重复发信号
    nav.set_collapsed(False)
    assert not nav.is_collapsed()
    assert nav.width() == EXPANDED_WIDTH
    assert not nav.title_label.isHidden()
    assert nav.button("run").text() == "运行"
    assert events == [True, False]


def test_toggle_flips_state(nav: Any) -> None:
    nav.toggle()
    assert nav.is_collapsed()
    nav.toggle()
    assert not nav.is_collapsed()


def test_add_while_collapsed_uses_short_label(nav: Any) -> None:
    nav.set_collapsed(True)
    nav.add("models", "模型", hint="GGUF 模型")
    assert nav.button("models").text() == "模"
    assert "GGUF 模型" in nav.button("models").toolTip()


def test_select_and_relative_cycle(nav: Any) -> None:
    changed: list[str] = []
    nav.changed.connect(changed.append)
    assert nav.select("run") == "run"
    assert nav.select_relative(1) == "settings"
    assert nav.select_relative(1) == "overview"
    assert nav.select_relative(-1) == "settings"
    assert changed[-1] == "settings"
    assert nav.select(0) == "overview"


def test_select_unknown_key_raises(nav: Any) -> None:
    with pytest.raises(KeyError):
        nav.select("missing")
    with pytest.raises(KeyError):
        nav.select(99)


def test_add_duplicate_key_raises(nav: Any) -> None:
    with pytest.raises(KeyError):
        nav.add("run", "重复")


def test_set_hint_updates_current_page(nav: Any) -> None:
    nav.set_hint("overview", "新的提示")
    assert nav.hint("overview") == "新的提示"
    assert nav.hint_label.text() == "新的提示"
