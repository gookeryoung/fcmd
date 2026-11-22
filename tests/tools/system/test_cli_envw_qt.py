"""envw Qt 窗口的离屏测试。

后端读写全部替换为内存字典，避免触碰真实注册表 / profile；覆盖表格过滤、
详情展开、按钮状态、保存落盘、路径编辑对话框的增删去重与极小窗口布局。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.tools.system import _envw_qt
from fcmd.tools.system._envw_qt import EnvQtWindow, PathEditorDialog
from fcmd.tools.system.envw import EnvSettings, load_settings

_SAMPLE = {
    "Alpha": "%Beta%/bin",
    "Beta": "C:/tools",
    "Path": "C:/A;C:/B",
}


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """替换后端读写并返回窗口。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(_envw_qt, "read_scope", lambda _scope, **_kwargs: dict(_SAMPLE))
    app = create_application("envw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    # 样例数据是 Windows 风格（%VAR% 引用、分号路径）：显式注入 win32 平台，
    # 使 Linux CI 与本地走同一套展开语义。
    widget = EnvQtWindow(tmp_path / "envw.json", platform="win32")
    yield widget
    widget.deleteLater()


def _row_names(window: Any) -> list[str]:
    names = []
    for row in range(window._table.rowCount()):
        item = window._table.item(row, 0)
        names.append(str(item.data(Qt.ItemDataRole.UserRole)))
    return names


def _overflowing_children(root: QWidget) -> list[str]:
    problems: list[str] = []

    def walk(widget: QWidget) -> None:
        parent = widget.parentWidget()
        if parent is not None:
            rect = widget.geometry()
            area = parent.contentsRect()
            if rect.bottom() > area.bottom() or rect.right() > area.right():
                problems.append(f"{type(widget).__name__}{rect.getRect()} 超出 {area.getRect()}")
        for child in widget.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if child.isVisible():
                walk(child)

    for child in root.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if child.isVisible():
            walk(child)
    return problems


def test_table_lists_variables_and_keeps_newlines(window: Any) -> None:
    assert _row_names(window) == ["Alpha", "Beta", "Path"]
    assert window._table.rowCount() == 3
    assert window._save_button.isEnabled() is False


def test_search_filters_by_name_and_value(window: Any) -> None:
    window._search_edit.setText("tools")
    assert _row_names(window) == ["Beta"]
    window._search_edit.setText("c:/a")
    assert _row_names(window) == ["Path"]
    window._search_edit.clear()
    assert window._table.rowCount() == 3


def test_selection_fills_detail_and_enables_actions(window: Any) -> None:
    window._select_name("Alpha")
    assert window._detail_name.text() == "Alpha"
    assert window._raw_text.toPlainText() == "%Beta%/bin"
    assert "/tools/bin" in window._expanded_text.toPlainText()
    assert window._edit_button.isEnabled() is True
    assert window._path_button.isEnabled() is False

    window._select_name("Path")
    assert window._path_button.isEnabled() is True


def test_save_writes_backend_and_clears_dirty(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict[str, object] = {}

    def fake_save(scope: str, values: dict[str, str], original: dict[str, str], **_kwargs: object) -> None:
        recorded["scope"] = scope
        recorded["values"] = dict(values)
        recorded["original"] = dict(original)

    monkeypatch.setattr(_envw_qt, "save_scope", fake_save)
    window._variables["Gamma"] = "3"
    window._refresh_table()
    assert window._save_button.isEnabled() is True
    window._save()
    assert recorded["values"] == {"Alpha": "%Beta%/bin", "Beta": "C:/tools", "Path": "C:/A;C:/B", "Gamma": "3"}
    assert window._save_button.isEnabled() is False
    assert window._has_unsaved_changes() is False


def test_persist_writes_scope_and_geometry(window: Any, tmp_path: Path) -> None:
    window._persist_settings("1000x700+1+2")
    settings = load_settings(tmp_path / "envw.json")
    assert settings == EnvSettings(scope=window._scope, geometry="1000x700+1+2")


def test_path_editor_add_move_dedupe(window: Any) -> None:
    dialog = PathEditorDialog(window, ["C:/a", "C:/b", "C:/a"], separator=";", windows=True)
    dialog._deduplicate()
    assert dialog._entries == ["C:/a", "C:/b"]
    dialog._list.setCurrentRow(1)
    dialog._move_up()
    assert dialog._entries == ["C:/b", "C:/a"]
    dialog._entry.setText("C:/c")
    dialog._add()
    assert dialog._entries == ["C:/b", "C:/a", "C:/c"]
    dialog._confirm()
    assert dialog.result == ["C:/b", "C:/a", "C:/c"]
    dialog.deleteLater()


def test_layout_survives_tiny_window(window: Any) -> None:
    window.show()
    window.setMinimumSize(820, 360)
    window.resize(820, 360)
    QApplication.processEvents()
    page = window.scroll.widget()
    assert page is not None
    assert _overflowing_children(page) == []
    assert window._table.height() >= window._table.minimumHeight()
    window.close()
