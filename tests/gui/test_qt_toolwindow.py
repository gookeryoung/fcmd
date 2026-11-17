"""``fcmd.gui.qt.toolwindow`` / ``ElidedLabel`` 的离屏测试。

覆盖单页窗口骨架（菜单 / 状态栏 / 几何记忆 / 关闭回调）与长路径省略行为；
Qt 不可用或 CI 无显示环境时使用 ``offscreen`` 平台。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt.app import create_application
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import ElidedLabel
from fcmd.gui.qt.window import parse_geometry

_LONG_PATH = "C:/Users/demo/Projects/fcmd/src/fcmd/apps/locllm/_locllmw_qt_window.py"


@pytest.fixture()
def app() -> QApplication:
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return create_application("test")


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    return base


def _settings(name: str) -> dict[str, object]:
    path = _guiconfig.config_path(name)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def test_window_skeleton_exposes_menu_status_and_body(app: QApplication, home: Path) -> None:
    window = QtToolWindow(title="示例工具", config_name="demo", min_size=(600, 400), default_size=(800, 600))
    try:
        assert window.windowTitle() == "示例工具"
        assert [action.text() for action in window.menuBar().actions()] == ["文件", "视图", "帮助"]
        assert window.status_text() == "就绪"
        window.set_status("完成")
        assert window.status_text() == "完成"
        label = QLabel("内容")
        assert window.add(label) is label
        assert window.body.count() == 1
        assert window.theme().name == _guitheme.load_theme().name
        assert window.settings_path == _guiconfig.config_path("demo")
    finally:
        window.deleteLater()


def test_geometry_is_saved_on_close_and_restored(app: QApplication, home: Path) -> None:
    path = _guiconfig.config_path("geo")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"geometry": "700x500+40+40"}', encoding="utf-8")

    window = QtToolWindow(title="几何", config_name="geo", min_size=(600, 400), default_size=(800, 600))
    try:
        assert (window.width(), window.height()) == (700, 500)
        window.show()
        window.move(60, 50)
        window.resize(720, 520)
        QApplication.processEvents()
        window.close()
    finally:
        window.deleteLater()

    # 保存的是客户区几何，与 restore_geometry 的 setGeometry 同一口径，
    # 不叠加窗口装饰，因此每次启动都能原样恢复。
    saved = _settings("geo")["geometry"]
    assert isinstance(saved, str)
    rect = parse_geometry(saved)
    assert rect is not None
    width, height, x, y = rect
    assert (width, height) == (720, 520)
    assert abs(x - 60) <= 4
    assert abs(y - 50) <= 4

    restored = QtToolWindow(title="几何", config_name="geo", min_size=(600, 400), default_size=(800, 600))
    try:
        assert (restored.width(), restored.height()) == (720, 520)
        assert abs(restored.x() - x) <= 4
        assert abs(restored.y() - y) <= 4
    finally:
        restored.deleteLater()


def test_close_persists_through_callback_without_writing_settings(app: QApplication, home: Path) -> None:
    collected: list[str] = []
    window = QtToolWindow(
        title="回调",
        config_name="callback",
        min_size=(600, 400),
        persist=collected.append,
    )
    try:
        window.close()
    finally:
        window.deleteLater()
    assert len(collected) == 1
    assert "x" in collected[0]
    assert not _guiconfig.config_path("callback").exists()


def test_on_close_can_cancel(app: QApplication, home: Path) -> None:
    calls: list[bool] = []

    def _confirm() -> bool:
        calls.append(True)
        return False

    window = QtToolWindow(title="取消", config_name="cancel", min_size=(600, 400), on_close=_confirm)
    try:
        window.close()
        assert calls == [True]
        assert window.is_closing() is False
        assert not _guiconfig.config_path("cancel").exists()
    finally:
        window.deleteLater()


def test_body_scrolls_instead_of_clipping_when_window_shrinks(app: QApplication, home: Path) -> None:
    """窗口缩到内容最小高度以下时，底部控件完整保留、改为整页滚动。"""
    window = QtToolWindow(title="收缩", config_name="shrink", min_size=(320, 240), default_size=(640, 480))
    tall = QWidget()
    tall.setMinimumHeight(900)
    window.add(tall, 1)
    try:
        window.show()
        window.resize(360, 260)
        QApplication.processEvents()
        assert window.size().height() == 260
        scroll = window.scroll
        assert scroll is not None
        assert scroll.verticalScrollBar().maximum() > 0
        # 内容保持最小高度，没有被布局压扁或裁掉。
        assert tall.height() >= 900
        page = scroll.widget()
        assert page is not None
        assert tall.geometry().bottom() <= page.contentsRect().bottom()
    finally:
        window.deleteLater()


def test_scroll_mode_and_theme_switch(app: QApplication, home: Path) -> None:
    window = QtToolWindow(title="滚动", config_name="scroll", min_size=(600, 400), scroll=True)
    try:
        assert window.scroll is not None
        window.set_theme(_guitheme.resolve_theme("light"))
        assert window.theme().name == "light"
        assert _guitheme.load_theme().name == "light"
        assert window.theme_menu.action("light") is not None
        assert window.theme_menu.action("light").isChecked()  # type: ignore[union-attr]
    finally:
        window.deleteLater()


def test_save_settings_merges_existing_payload(app: QApplication, home: Path) -> None:
    window = QtToolWindow(title="设置", config_name="merge", min_size=(600, 400))
    try:
        window.save_settings({"workers": 4})
        window.save_settings({"recursive": True})
        settings = window.settings()
        assert settings["workers"] == 4
        assert settings["recursive"] is True
        assert _settings("merge")["workers"] == 4
    finally:
        window.deleteLater()


def test_elided_label_shows_middle_ellipsis_and_full_tooltip(app: QApplication, home: Path) -> None:
    label = ElidedLabel(_LONG_PATH)
    try:
        label.resize(140, 24)
        label.show()
        QApplication.processEvents()
        assert label.text() != _LONG_PATH
        assert "…" in label.text()
        assert label.toolTip() == _LONG_PATH
        assert label.fullText() == _LONG_PATH

        label.resize(2000, 24)
        QApplication.processEvents()
        assert label.text() == _LONG_PATH
        assert label.toolTip() == ""
    finally:
        label.deleteLater()


def test_elided_label_set_text_updates_full_text(app: QApplication, home: Path) -> None:
    label = ElidedLabel("short")
    try:
        label.setText(_LONG_PATH)
        assert label.fullText() == _LONG_PATH
    finally:
        label.deleteLater()
