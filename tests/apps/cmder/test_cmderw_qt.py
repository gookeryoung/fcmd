"""cmderw Qt 窗口的离屏冒烟测试：双面板、标签、传输队列、归档浏览与落盘。

窗口类与 Qt 胶水不追求覆盖率，这里只验证关键交互链路能构建、能跑通。
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._cmder_qt import CmderQtWindow
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path / "home"))
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = create_application("cmderw-test")
    _qt_theme.apply_theme(application, _guitheme.resolve_theme("dark-blue"))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    return tmp_path


def _wait(app: QApplication, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert predicate()


def _panels(tmp_path: Path) -> tuple[Path, Path]:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "a.txt").write_text("hello", encoding="utf-8")
    (left / "sub").mkdir()
    return left, right


def test_window_builds_and_lists_entries(home: Path) -> None:
    app = create_application("cmderw-test")
    left, right = _panels(home)
    window = CmderQtWindow()
    try:
        window.left_panel.set_path(left)
        window.right_panel.set_path(right)
        _wait(app, lambda: len(window.left_panel.entries) >= 2)
        names = [entry.name for entry in window.left_panel.entries]
        assert names[:2] == ["sub", "a.txt"]
        assert window.left_panel.tabs_bar.count() == 1
        window.left_panel.select_name("a.txt")
        assert [entry.name for entry in window.left_panel.selected_entries()] == ["a.txt"]
        assert "选中 1 项" in window.selection_label.text()
    finally:
        window.close()


def test_copy_via_queue(home: Path) -> None:
    app = create_application("cmderw-test")
    left, right = _panels(home)
    window = CmderQtWindow()
    try:
        window.left_panel.set_path(left)
        window.right_panel.set_path(right)
        _wait(app, lambda: len(window.left_panel.entries) >= 2)
        window.left_panel.select_name("a.txt")
        window.transfer_paths([left / "a.txt"], move=False, destination=right)
        _wait(app, lambda: (right / "a.txt").is_file() and not window._queue_running)
        assert (right / "a.txt").read_text(encoding="utf-8") == "hello"
        assert window.queue.tasks[0].status == "done"
    finally:
        window.close()


def test_tabs_and_view_modes(home: Path) -> None:
    create_application("cmderw-test")
    left, right = _panels(home)
    window = CmderQtWindow()
    try:
        window.left_panel.set_path(left)
        window.left_panel.new_tab(str(right))
        assert window.left_panel.tabs_bar.count() == 2
        window.left_panel.close_tab()
        assert window.left_panel.tabs_bar.count() == 1
        window.set_view_mode("brief")
        assert window.left_panel.stack.currentWidget() is window.left_panel.tree
        assert window.left_panel.tree.isColumnHidden(2)
        window.set_view_mode("thumbnails")
        assert window.left_panel.stack.currentWidget() is window.left_panel.thumb_list
        window.set_view_mode("detailed")
        assert not window.left_panel.tree.isColumnHidden(2)
    finally:
        window.close()


def test_archive_browsing(home: Path) -> None:
    app = create_application("cmderw-test")
    left, _right = _panels(home)
    archive = left / "sample.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("dir/one.txt", "data")
        handle.writestr("root.txt", "root")
    window = CmderQtWindow()
    try:
        window.left_panel.set_path(left)
        _wait(app, lambda: any(entry.name == "sample.zip" for entry in window.left_panel.entries))
        assert window.left_panel.open_archive(archive)
        _wait(app, lambda: {entry.name for entry in window.left_panel.entries} >= {"dir", "root.txt"})
        names = {entry.name for entry in window.left_panel.entries}
        assert {"dir", "root.txt"} <= names
        window.left_panel.archive_enter("dir")
        _wait(app, lambda: [entry.name for entry in window.left_panel.entries] == ["one.txt"])
        window.left_panel.exit_archive()
        assert window.left_panel.archive is None
    finally:
        window.close()


def test_filter_and_hidden_toggle(home: Path) -> None:
    app = create_application("cmderw-test")
    left, _right = _panels(home)
    (left / ".hidden").write_text("x", encoding="utf-8")
    window = CmderQtWindow()
    try:
        window.left_panel.set_path(left)
        _wait(app, lambda: len(window.left_panel.entries) >= 2)
        window.left_panel.filter_edit.setText("a.")
        _wait(app, lambda: [entry.name for entry in window.left_panel.entries] == ["a.txt"])
        window.left_panel.filter_edit.clear()
        window.toggle_hidden()
        _wait(app, lambda: any(entry.name == ".hidden" for entry in window.left_panel.entries))
    finally:
        window.close()


def test_restores_right_panel_as_active(home: Path) -> None:
    """保存的 active=right 必须在构造期完成激活，不能踩到尚未创建的 quick_view。"""
    create_application("cmderw-test")
    left, right = _panels(home)
    core.config_path().parent.mkdir(parents=True, exist_ok=True)
    core.config_path().write_text(
        json.dumps(
            {
                "left": {"path": str(left)},
                "right": {"path": str(right)},
                "active": "right",
            }
        ),
        encoding="utf-8",
    )
    window = CmderQtWindow()
    try:
        assert window.active is window.right_panel
        assert window.right_panel.state.path == str(right.resolve())
        assert window.quick_view.isVisible() is False
    finally:
        window.close()


def test_settings_persisted(home: Path) -> None:
    app = create_application("cmderw-test")
    left, right = _panels(home)
    window = CmderQtWindow()
    try:
        window.left_panel.set_path(left)
        window.right_panel.set_path(right)
        _wait(app, lambda: len(window.left_panel.entries) >= 2)
        window.persist_settings()
    finally:
        window.close()
    raw = json.loads(core.config_path().read_text(encoding="utf-8"))
    assert raw["left"]["path"] == str(left.resolve())
    assert raw["right"]["path"] == str(right.resolve())
    assert raw["geometry"]
