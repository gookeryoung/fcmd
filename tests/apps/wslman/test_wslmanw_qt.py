"""wslmanw Qt 窗口的离屏冒烟测试：列表渲染、详情、状态刷新、设置持久化与对话框。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidget

from fcmd.apps.wslman import _wslmanw_qt, wslmanw
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman._config import WslmanConfig, default_path
from fcmd.apps.wslman._distro import Distro, DistroView
from fcmd.apps.wslman._registry import RegistrySnapshot
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application


def _distro(name: str = "Ubuntu", state: str = "running", *, default: bool = True) -> Distro:
    return Distro(
        name=name,
        state=state,
        state_raw=state.capitalize(),
        version=2,
        default=default,
        base_path=rf"C:\WSL\{name}",
        default_uid=1000,
        registered=True,
    )


def _view(*distros: Distro) -> DistroView:
    snapshot = RegistrySnapshot(
        available=False,
        default_distribution=None,
        default_version=None,
        distros=(),
        error=None,
    )
    return DistroView(distros=distros, default=distros[0].name if distros else None, warnings=(), registry=snapshot)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = create_application("wslmanw-test")
    _qt_theme.apply_theme(application, _guitheme.resolve_theme("dark-blue"))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(_wslmanw_qt, "load_config", lambda: WslmanConfig(path=default_path()))
    monkeypatch.setattr(cli, "_build_environment", object)
    return base


def _text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _wait_idle(window: _wslmanw_qt.WslmanQtWindow, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while window._busy and time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.01)
    if app is not None:
        app.processEvents()
    assert not window._busy


def test_lists_distros_and_details(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    view = _view(_distro("Ubuntu", "running"), _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window._table.rowCount() == 2
        assert _text(window._table, 0, 0) == "Ubuntu"
        assert _text(window._table, 1, 1) == "已停止"
        window._table.selectRow(0)
        assert window.detail_values["名称"].text() == "Ubuntu"
        assert window.detail_values["虚拟磁盘"].text().endswith("ext4.vhdx")
        assert window.hover_label.text().startswith("Ubuntu")
        assert window.start_button.isEnabled() is False
        assert window.stop_button.isEnabled() is True
        window._table.selectRow(1)
        assert window.start_button.isEnabled() is True
        assert window.stop_button.isEnabled() is False
    finally:
        window._log_view.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_status_refresh_only_updates_state_column(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stopped = _view(_distro("Ubuntu", "stopped"))
    running = _view(_distro("Ubuntu", "running"))
    current = {"view": stopped}
    monkeypatch.setattr(cli, "_view", lambda env: current["view"])
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (None, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._table.selectRow(0)
        current["view"] = running
        window._on_status(running)
        assert window._table.rowCount() == 1
        assert _text(window._table, 0, 1) == "运行中"
        assert window._table.currentRow() == 0
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_rows_and_filter(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = wslmanw._images.ImageEntry(
            name="Ubuntu",
            friendly_name="Ubuntu 24.04",
            sources=("cli",),
            downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/ubuntu.wsl"),),
        )
        other = wslmanw._images.ImageEntry(name="Debian", friendly_name="Debian", sources=("cli",))
        window.image_entries = (entry, other)
        window._render_images()
        assert window.image_table.rowCount() == 2
        window.image_search.setText("debian")
        window._render_images()
        assert window.image_table.rowCount() == 1
        assert _text(window.image_table, 0, 0) == "Debian"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_persisted(home: Path) -> None:
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        window.download_dir.setText(r"D:\WSL\downloads")
        window.mirror.setCurrentIndex(window.mirror_ids.index("ustc"))
        window._persist_settings("980x660+10+20")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()
    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["geometry"] == "980x660+10+20"
    assert raw["download_dir"] == r"D:\WSL\downloads"
    assert raw["mirror"] == "ustc"


def test_sidebar_pages_and_collapsed_state_persisted(home: Path) -> None:
    """侧边栏复用共享导航：三页登记、默认停在发行版页，折叠状态随设置落盘。"""
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.nav.keys() == ("distros", "images", "log")
        assert window.nav.selected() == "distros"
        assert window.current_page() == "distros"
        assert window.page_title.text() == "发行版"
        # 三页控件建窗即存在（设置落盘与下载动作都直接读镜像页控件）；
        # 页面本身仍由共享骨架在首次切换时挂进页面栈。
        assert window._table.rowCount() >= 0
        assert window.image_table.columnCount() == 4
        window.nav.set_collapsed(True)
        window._persist_settings("1160x760+0+0")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()
    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["sidebar_collapsed"] is True


def test_close_asks_when_task_running(home: Path) -> None:
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._busy = True
        assert window._on_close() is False
        window._busy = False
        assert window._on_close() is True
    finally:
        window.deleteLater()
