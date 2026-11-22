"""wslmanw Qt 窗口的离屏冒烟测试：列表渲染、详情、状态刷新、设置持久化与对话框。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from fcmd.apps.wslman import _initenv, _wslmanw_qt, wslmanw
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman._config import WslmanConfig, default_path
from fcmd.apps.wslman._distro import Distro, DistroView
from fcmd.apps.wslman._registry import RegistrySnapshot
from fcmd.apps.wslman._runner import CommandResult
from fcmd.gui import config as _guiconfig
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.gui.icons import ICON_NAME_PROPERTY as _ICON_NAME_PROPERTY


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


def _distro_row(window: _wslmanw_qt.WslmanQtWindow, index: int) -> _wslmanw_qt._DistroItemRow:
    item = window.distro_list.item(index)
    assert item is not None
    widget = window.distro_list.itemWidget(item)
    assert isinstance(widget, _wslmanw_qt._DistroItemRow)
    return widget


def _image_row(window: _wslmanw_qt.WslmanQtWindow, index: int) -> _wslmanw_qt._ImageItemRow:
    item = window.image_list.item(index)
    assert item is not None
    widget = window.image_list.itemWidget(item)
    assert isinstance(widget, _wslmanw_qt._ImageItemRow)
    return widget


def _icon_name(widget: Any) -> Any:
    """控件当前挂的图标名（``set_icon`` 写入的属性）。"""
    return widget.property(_ICON_NAME_PROPERTY)


def _wait_idle(window: _wslmanw_qt.WslmanQtWindow, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    # 先让 ``_task_finished`` 里 singleShot(0) 排队的轻量状态刷新落地，再等它完成；
    # 只等 ``_busy`` 会在状态刷新线程仍在跑时提前返回，读到旧行卡。
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if not window._busy and not window._status_busy:
            break
        time.sleep(0.01)
    if app is not None:
        app.processEvents()
    assert not window._busy


def test_lists_distros_as_rows_with_toolbar_actions(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """发行版以行卡呈现：选中行驱动工具栏启停按钮与状态栏摘要。"""
    view = _view(_distro("Ubuntu", "running"), _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.distro_list.count() == 2
        assert _distro_row(window, 0).distro.name == "Ubuntu"
        assert isinstance(_distro_row(window, 0), _wslmanw_qt.ItemRow)
        assert _distro_row(window, 1).state_tag.text() == "已停止"
        # 与 llmw 模型行同构：副标题只放版本，右侧数值放磁盘占用。
        assert _distro_row(window, 0).sub_label.text() == "v2"
        metrics = _distro_row(window, 0).metrics_label
        assert metrics is not None and metrics.text() == "2.0 KiB"

        window.distro_list.setCurrentRow(0)
        assert window.status_detail_text().startswith("Ubuntu")
        # 上下两行的数值列宽度一致：状态标签与启动按钮前的分隔线不会左右跳动。
        first_metrics = _distro_row(window, 0).metrics_label
        second_metrics = _distro_row(window, 1).metrics_label
        assert first_metrics is not None and second_metrics is not None
        assert first_metrics.width() == second_metrics.width()
        assert not _distro_row(window, 0).action_divider.isHidden()
        assert window.start_button.isEnabled() is False
        assert window.stop_button.isEnabled() is True
        # 行内按钮跟着状态走：运行中显示「停止」，已停止显示「启动」
        assert _distro_row(window, 0).start_stop_button.text() == "停止"
        assert _distro_row(window, 1).start_stop_button.text() == "启动"

        window.distro_list.setCurrentRow(1)
        assert window.start_button.isEnabled() is True
        assert window.stop_button.isEnabled() is False
    finally:
        window._log_lines.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_running_distro_row_uses_red_stop_button(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """运行中的发行版给红色危险按钮「停止」；待机仍是主按钮「启动」。"""
    view = _view(_distro("Ubuntu", "running"), _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        running, stopped = _distro_row(window, 0), _distro_row(window, 1)
        assert running.start_stop_button.text() == "停止"
        assert running.start_stop_button.objectName() == "Danger"
        assert running.start_stop_button.property("fcmdIconTone") == "danger"
        assert stopped.start_stop_button.text() == "启动"
        assert stopped.start_stop_button.objectName() == "Primary"
        assert stopped.start_stop_button.property("fcmdIconTone") == "on_accent"

        # 任务进行中禁用：变体不丢，图标色调退回 muted（禁用底色变浅后仍看得清）。
        running.set_busy(True)
        assert running.start_stop_button.isEnabled() is False
        assert running.start_stop_button.objectName() == "Danger"
        assert running.start_stop_button.property("fcmdIconTone") == "muted"
        running.set_busy(False)
        assert running.start_stop_button.isEnabled() is True
        assert running.start_stop_button.property("fcmdIconTone") == "danger"

        # 状态原地变化时按钮变体跟着切（轮询不重建行）。
        stopped.update_distro(_distro("Debian", "running", default=False), 2048, False)
        assert stopped.start_stop_button.text() == "停止"
        assert stopped.start_stop_button.objectName() == "Danger"
        stopped.update_distro(_distro("Debian", "stopped", default=False), 2048, False)
        assert stopped.start_stop_button.objectName() == "Primary"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_row_selects_when_clicking_title_and_tags(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """点标题文字 / 状态胶囊也要能选中整行（行内文字不抢鼠标）。"""
    view = _view(_distro("Ubuntu", "running"), _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        row = _distro_row(window, 1)
        window.distro_list.setCurrentRow(0)
        QApplication.processEvents()
        assert window.distro_list.currentRow() == 0
        # 点到行的标题文字上（行卡里面积最大的位置）
        QTest.mouseClick(row.title_label, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(6, 6))
        QApplication.processEvents()
        assert window.distro_list.currentRow() == 1
        # 点到状态胶囊上
        window.distro_list.setCurrentRow(0)
        QApplication.processEvents()
        QTest.mouseClick(row.state_tag, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(4, 4))
        QApplication.processEvents()
        assert window.distro_list.currentRow() == 1
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_row_expands_inline_details(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """详情在行内展开（与 llmw 模型行一致），不再占用下方分栏。"""
    view = _view(_distro("Ubuntu", "running"), _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        first, second = _distro_row(window, 0), _distro_row(window, 1)
        first.expand_button.click()
        assert first.is_expanded() is True
        assert not window.distro_detail_view.isHidden()
        assert window.distro_detail_view.values["名称"].text() == "Ubuntu"
        assert window.distro_detail_view.values["虚拟磁盘"].text().endswith("ext4.vhdx")

        # 展开另一行时先收起旧行；详情控件跟着换行
        second.expand_button.click()
        assert first.is_expanded() is False
        assert second.is_expanded() is True
        assert window.distro_detail_view.values["名称"].text() == "Debian"

        second.expand_button.click()
        assert second.is_expanded() is False
        assert window.distro_detail_view.isHidden()
        # 双击行等价于点展开
        window._on_distro_double_clicked(window.distro_list.item(0))
        assert first.is_expanded() is True
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_status_refresh_updates_rows_in_place(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询只更新行内状态，不重建列表：选中行与行卡实例保持不变。"""
    stopped = _view(_distro("Ubuntu", "stopped"))
    running = _view(_distro("Ubuntu", "running"))
    current = {"view": stopped}
    monkeypatch.setattr(cli, "_view", lambda env: current["view"])
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (None, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        row = _distro_row(window, 0)
        assert row.state_tag.text() == "已停止"

        current["view"] = running
        window._on_status(running)
        assert window.distro_list.count() == 1
        assert _distro_row(window, 0) is row  # 同一个行卡实例，未被重建
        assert row.state_tag.text() == "运行中"
        assert row.start_stop_button.text() == "停止"
        assert window.distro_list.currentRow() == 0
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_start_button_recovers_after_initial_load(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """首屏刷新结束后行内启动按钮立刻回到主按钮态，不等下一次状态轮询。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        row = _distro_row(window, 0)
        # 刷新任务进行中行按钮被禁用；任务一结束必须恢复（此前要等 3 秒轮询，
        # 期间启动按钮一直是禁用态的灰底，看不出是主操作）。
        assert window._busy is False
        assert row.start_stop_button.isEnabled() is True
        assert row.start_stop_button.objectName() == "Primary"
        assert row.start_stop_button.property("fcmdIconTone") == "on_accent"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_image_note_shares_the_subtitle_line(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「镜像 …」说明在副标题行（与版本同排），不和名称 / 状态胶囊挤在一起。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = wslmanw._images.ImageEntry(name="Ubuntu", friendly_name="Ubuntu 24.04", sources=("cli",))
        window.image_entries = (entry,)
        window._refresh_distro_images()
        row = _distro_row(window, 0)
        assert row.image_label.fullText() == "镜像 Ubuntu 24.04"
        assert row.image_label.isHidden() is False
        # 副标题行：版本 + 镜像说明 + 拉伸 + 状态胶囊；标题行只有名称。
        assert row.note_row.indexOf(row.image_label) >= 0
        assert row.title_row.indexOf(row.image_label) == -1
        assert row.title_row.indexOf(row.title_label) >= 0
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_export_progress_lands_in_status_bar(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """导出进度写实例页状态栏：文案带百分比 / 已写 / 估算总量，右侧进度条同步。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        bar = window.statusBar()
        window._busy = True
        window._task_page = "distros"
        window._on_export_progress("Ubuntu", 512, 1024)
        text = window.page_status("distros")
        assert text.startswith("导出 Ubuntu · 50% · ")
        assert bar.loading_bar.bar.maximum() == 1000
        assert bar.loading_bar.bar.value() == 500
        assert bar.loading_bar.text_label.text().startswith("导出 50% · ")

        # 任务结束后迟到的进度信号不再把进度条叫起来。
        window._busy = False
        window._task_finished()
        window._on_export_progress("Ubuntu", 1024, 1024)
        assert bar.loading_bar.is_running() is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_download_progress_lands_in_status_bar(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载进度写状态栏：文案带百分比 / 速度，右侧进度条切确定进度；任务结束收起。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        bar = window.statusBar()
        assert bar.loading_bar.is_running() is False

        window._busy = True
        window._active_entry = "Ubuntu"
        window._task_page = "images"
        window._on_download_progress(512, 1024)
        text = window.page_status("images")
        assert text.startswith("下载 Ubuntu · 50% · ")
        assert bar.loading_bar.bar.maximum() == 1000
        assert bar.loading_bar.bar.value() == 500
        assert bar.loading_bar.is_running() is True
        # 右侧进度条只放短文案（百分比 + 速度），长文案留给左侧状态文字。
        assert bar.loading_bar.text_label.text().startswith("下载 50% · ")

        # 总量未知（服务器没给长度）时回到忙碌动画，文案只给已下载字节。
        window._on_download_progress(2048, None)
        assert "下载 Ubuntu · 2.0 KiB · " in window.page_status("images")
        assert bar.loading_bar.bar.maximum() == 0

        window._busy = False
        window._active_entry = ""
        window._task_finished()
        assert bar.loading_bar.is_running() is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_task_shows_status_bar_loading_until_finished(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任何后台任务进行中状态栏右侧都有加载条，任务结束自动收起。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        bar = window.statusBar()
        window._start("导出", lambda cancel: "已完成")
        assert bar.loading_bar.is_running() is True
        _wait_idle(window)
        assert bar.loading_bar.is_running() is False
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
            installed=True,
        )
        other = wslmanw._images.ImageEntry(name="Debian", friendly_name="Debian", sources=("store",))
        window.image_entries = (entry, other)
        window._render_images()
        assert window.image_list.count() == 2
        # 已安装的 Ubuntu 置顶，即使默认按名称排序（Debian < Ubuntu）。
        assert _image_row(window, 0).entry.name == "Ubuntu"
        assert _image_row(window, 1).entry.name == "Debian"
        # 行卡骨架来自共享 ItemRow（与 llmw 模型行同源）。
        assert isinstance(_image_row(window, 0), _wslmanw_qt.ItemRow)
        assert window.image_list.currentRow() == 0
        window.image_search.setText("Ubuntu 24")
        window._render_images()
        assert window.image_list.count() == 1
        assert _image_row(window, 0).entry.name == "Ubuntu"

        # 筛选按钮：只重绘，不重新拉取清单。
        window.image_search.clear()
        window.image_filter_buttons["installed"].click()
        assert window.image_list.count() == 1
        assert _image_row(window, 0).entry.name == "Ubuntu"
        window.image_filter_buttons["install"].click()
        assert window.image_list.count() == 1
        assert _image_row(window, 0).entry.name == "Debian"
        window.image_filter_buttons["all"].click()

        # 来源筛选与排序下拉。
        window.image_source.setCurrentIndex(window.image_source.findData("store"))
        assert window.image_list.count() == 1
        assert _image_row(window, 0).entry.name == "Debian"
        window.image_source.setCurrentIndex(window.image_source.findData("all"))
        window.image_sort.setCurrentIndex(window.image_sort.findData("friendly"))
        # 已安装的 Ubuntu 在任何排序下都置顶，friendly 只影响未安装的同档位条目。
        assert _image_row(window, 0).entry.name == "Ubuntu"
        assert _image_row(window, 1).entry.name == "Debian"
        assert "共 2 个镜像" in window.images_status.fullText()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_rows_pin_installed_and_downloaded(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像列表把已安装 / 已下载固定置顶，切换排序口径不影响置顶顺序。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        installed = wslmanw._images.ImageEntry(name="Ubuntu", friendly_name="Zulu", sources=("cli",), installed=True)
        downloaded = wslmanw._images.ImageEntry(name="Kali", friendly_name="Beta", sources=("cli",), downloaded=True)
        online = wslmanw._images.ImageEntry(name="Debian", friendly_name="Alpha", sources=("manifest",))
        window.image_entries = (online, downloaded, installed)
        window._render_images()
        assert [_image_row(window, index).entry.name for index in range(3)] == ["Ubuntu", "Kali", "Debian"]

        # 按友好名排序（Alpha / Beta / Zulu）：置顶档位不变，只在同档位内比较。
        window.image_sort.setCurrentIndex(window.image_sort.findData("friendly"))
        assert [_image_row(window, index).entry.name for index in range(3)] == ["Ubuntu", "Kali", "Debian"]
        window.image_sort.setCurrentIndex(window.image_sort.findData("name"))
        assert [_image_row(window, index).entry.name for index in range(3)] == ["Ubuntu", "Kali", "Debian"]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_row_selects_when_clicking_title(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像行同理：点标题文字即可选中整行（行内文字不抢鼠标）。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        first = wslmanw._images.ImageEntry(name="Debian", friendly_name="Debian", sources=("manifest",))
        second = wslmanw._images.ImageEntry(name="Ubuntu", friendly_name="Ubuntu", sources=("cli",))
        window.image_entries = (first, second)
        window._render_images()
        row = _image_row(window, 1)
        window.image_list.setCurrentRow(0)
        QApplication.processEvents()
        QTest.mouseClick(row.title_label, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(6, 6))
        QApplication.processEvents()
        assert window.image_list.currentRow() == 1
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_rows_expand_inline_detail(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像行参考 llmw 模型行：详情在行内展开，同一时间只展开一行，不再占用右侧面板。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        first = wslmanw._images.ImageEntry(
            name="Debian",
            friendly_name="Debian GNU/Linux",
            sources=("manifest",),
            downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/debian.wsl", "a" * 64),),
        )
        second = wslmanw._images.ImageEntry(name="Ubuntu", friendly_name="Ubuntu", sources=("cli",))
        window.image_entries = (first, second)
        window._render_images()
        row = _image_row(window, 0)
        row.expand_button.click()
        assert row.is_expanded() is True
        assert not window.image_detail_view.isHidden()
        assert window.image_detail_view.values["名称"].text() == "Debian"
        assert "debian.wsl" in window.image_detail_view.url.toPlainText()

        # 展开另一行时先收起旧行。
        other = _image_row(window, 1)
        other.expand_button.click()
        assert row.is_expanded() is False
        assert other.is_expanded() is True
        assert window.image_detail_view.values["名称"].text() == "Ubuntu"

        other.expand_button.click()
        assert other.is_expanded() is False
        assert window.image_detail_view.isHidden()
        # 双击行与点「展开」等价。
        row.expand_button.click()
        assert row.is_expanded() is True
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_persisted(home: Path) -> None:
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        window.download_dir.set_value(r"D:\WSL\downloads")
        window.mirror.setCurrentIndex(window.mirror_ids.index("ustc"))
        window.image_filter_buttons["installed"].click()
        window.image_source.setCurrentIndex(window.image_source.findData("manifest"))
        window.image_sort.setCurrentIndex(window.image_sort.findData("status"))
        window._persist_settings("980x660+10+20")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()
    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["geometry"] == "980x660+10+20"
    assert raw["download_dir"] == r"D:\WSL\downloads"
    assert raw["mirror"] == "ustc"
    assert raw["image_filter"] == "installed"
    assert raw["image_source"] == "manifest"
    assert raw["image_sort"] == "status"


def test_sidebar_pages_and_collapsed_state_persisted(home: Path) -> None:
    """侧边栏复用共享导航：三页登记（日志已移到帮助菜单）、默认停在发行版页。"""
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.nav.keys() == ("distros", "images", "settings")
        assert window.nav.selected() == "distros"
        assert window.current_page() == "distros"
        assert window.page_title.text() == "实例"
        # 四页控件建窗即存在（设置落盘与下载动作会直接读设置页 / 镜像页控件）；
        # 页面本身仍由共享骨架在首次切换时挂进页面栈。
        assert window.distro_list.count() == 0
        assert window.image_list.count() == 0
        window.nav.set_collapsed(True)
        window._persist_settings("1160x760+0+0")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()
    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["sidebar_collapsed"] is True


def test_prebuilt_pages_stay_hidden_until_shown(home: Path) -> None:
    """提前构造的页面在进入页面栈前必须隐藏，否则会以 (0,0) 画到菜单栏上。"""
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        window.show()
        _wait_idle(window)
        QApplication.processEvents()

        bar = window.menuBar()
        assert bar.geometry().top() == 0
        assert bar.geometry().height() > 0
        assert not window.image_list.isVisible()
        # 日志不占页面：未打开前没有日志窗口
        assert window._log_dialog is None
        hit = window.childAt(30, bar.height() // 2)
        node = hit
        while node is not None and node is not window:
            if node is bar:
                break
            node = node.parentWidget()
        assert node is bar, f"菜单栏区域被 {hit!r} 覆盖"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.close()
        window.deleteLater()


def test_image_catalog_load_uses_daemon_thread(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像页拉清单走守护线程：关窗时不能被 ``wsl --list --online`` / 网络下载卡住进程退出。

    回归 ``QThreadPool`` 版本：窗口能关，但进程要等任务结束才退（网络卡住时等同退不出）。
    """
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    started = threading.Event()
    release = threading.Event()

    def slow_collect(**_kwargs: object) -> object:
        started.set()
        release.wait(5)
        raise RuntimeError("模拟断网")

    monkeypatch.setattr(_wslmanw_qt._images, "collect_images", slow_collect)
    before = {id(thread) for thread in threading.enumerate()}
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("images")
        assert started.wait(5)
        new_threads = [thread for thread in threading.enumerate() if id(thread) not in before]
        assert any(thread.name == "fcmd-daemon-task" and thread.daemon for thread in new_threads)
        # 放行后台任务，等回调跑完再销毁窗口，避免信号投递到已析构的控件。
        release.set()
        deadline = time.monotonic() + 5
        app = QApplication.instance()
        while window._images_busy and time.monotonic() < deadline:
            if app is not None:
                app.processEvents()
            time.sleep(0.01)
        assert window._images_busy is False
    finally:
        release.set()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


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


# ---------------------------------------------------------------------- #
# 镜像页：缓存秒开 / 手动更新 / 底部动作行不被压扁
# ---------------------------------------------------------------------- #
def _image_entry(name: str = "Ubuntu", *, downloads: bool = True) -> Any:
    from fcmd.apps.wslman._images import ImageDownload, ImageEntry

    return ImageEntry(
        name=name,
        friendly_name=f"{name} 24.04",
        sources=("cli", "manifest"),
        downloads=(ImageDownload("x86_64", f"https://e/{name.casefold()}.wsl", "a" * 64),) if downloads else (),
    )


def _catalog(entries: tuple[Any, ...], *, from_cache: bool = False, stale: bool = False) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        entries=entries,
        sources=("cli", "manifest"),
        fetched_at=time.time(),
        from_cache=from_cache,
        stale=stale,
        warnings=(),
    )


def test_start_failure_dialog_only_after_waiting_for_running(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """启动失败只在「等够时间仍不是 Running」时报错，不在刚 spawn 完就弹窗误报。"""
    from types import SimpleNamespace

    stopped = _distro("Ubuntu", "stopped")
    monkeypatch.setattr(cli, "_view", lambda env: _view(stopped))
    monkeypatch.setattr(cli, "_build_environment", lambda: SimpleNamespace(config=WslmanConfig(path=default_path())))
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    monkeypatch.setattr(_wslmanw_qt, "spawn_detached", lambda argv: 1)
    waited: list[float] = []

    def fake_wait(_env: Any, name: str, **kwargs: Any) -> Any:
        waited.append(float(kwargs.get("timeout", cli.START_TIMEOUT_S)))
        return stopped

    monkeypatch.setattr(cli, "wait_until_running", fake_wait)
    messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *_args: messages.append(str(_args[2])) or QMessageBox.StandardButton.Ok,
    )
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        window._start_selected()
        _wait_idle(window)
        assert waited  # 先等待，再判定失败
        assert messages and "启动后状态仍为" in messages[0]
        assert "已等待" in messages[0]
        assert "启动失败" in window.status_text()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_start_selected_spawns_detached_keepalive(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """启动实例用脱离父进程的看护循环：wslmanw 退出后实例仍在运行。"""
    from types import SimpleNamespace

    stopped = _distro("Ubuntu", "stopped")
    running = _distro("Ubuntu", "running")
    states = iter([stopped, running])
    monkeypatch.setattr(cli, "_view", lambda env: _view(stopped))
    monkeypatch.setattr(cli, "_build_environment", lambda: SimpleNamespace(config=WslmanConfig(path=default_path())))
    monkeypatch.setattr(cli, "_require_target", lambda env, name: next(states))
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    spawned: list[list[str]] = []

    def fake_spawn(argv: Any) -> int:
        spawned.append(list(argv))
        return 4321

    monkeypatch.setattr(_wslmanw_qt, "spawn_detached", fake_spawn)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        window._start_selected()
        _wait_idle(window)
        assert spawned == [
            ["wsl.exe", "-d", "Ubuntu", "--exec", "/bin/sh", "-c", "while :; do sleep 3600; done"],
        ]
        assert "启动完成" in window.status_text()
        assert "[启动] 已启动 Ubuntu" in "\n".join(window._log_lines)
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_closing_window_does_not_stop_instances(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """退出 wslmanw 不结束任何实例：关闭流程不发 terminate / shutdown。"""
    view = _view(_distro("Ubuntu", "running"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_step", lambda env, argv, **_kwargs: calls.append(list(argv)))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window._on_close() is True
        assert calls == []
        assert window._poll is not None and window._poll.isActive() is False
    finally:
        window.deleteLater()


def test_open_selected_starts_in_user_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「打开终端」进入 Linux 用户主目录（``--cd ~``），而不是 Windows 当前目录的 /mnt 映射。"""
    from types import SimpleNamespace

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    config = WslmanConfig(path=default_path(), terminal_program="conhost")
    monkeypatch.setattr(cli, "_build_environment", lambda: SimpleNamespace(config=config))
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        recorded: list[Any] = []

        class _Recorder:
            def launch(self, command: Any, **_kwargs: Any) -> int:
                recorded.append(command)
                return 1

        window.launcher = cast(Any, _Recorder())
        window._open_selected()
        assert len(recorded) == 1
        assert recorded[0].argv == ("wsl.exe", "-d", "Ubuntu", "--cd", "~")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_images_page_restores_cache_then_refreshes(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """进镜像页先铺过期缓存（秒开），随后仍后台刷新一次（陈旧回显）。"""
    from fcmd.apps.wslman import _images

    stale_at = time.time() - 2 * _images.CACHE_TTL_SECONDS
    _images.save_cache((_image_entry("Ubuntu"), _image_entry("Debian")), stale_at)
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    calls: list[bool] = []
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: calls.append(force))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("images")
        assert window.image_list.count() == 2
        assert "本地缓存" in window.images_status.fullText()
        # 缓存回显之后必须继续刷新，否则过期数据会一直停在界面上。
        assert calls == [False]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_images_page_fresh_cache_skips_refresh(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """30 分钟内的缓存直接当结果用：不重复请求，要刷新点「更新列表」。"""
    from fcmd.apps.wslman import _images

    _images.save_cache((_image_entry("Ubuntu"),), time.time())
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    calls: list[bool] = []
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: calls.append(force))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("images")
        assert window.image_list.count() == 1
        assert calls == []
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_images_update_button_forces_refresh(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「更新列表」是手动更新入口：force=True 且更新期间禁用按钮。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    calls: list[bool] = []
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: calls.append(force))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.images_refresh.text() == "更新列表"
        window.images_refresh.click()
        assert calls == [True]

        window._set_images_busy(True)
        assert window.images_refresh.isEnabled() is False
        assert window.images_refresh.text() == "更新中…"
        window.images_refresh.click()
        assert calls == [True]  # 更新中重复点击被忽略

        window._set_images_busy(False)
        assert window.images_refresh.isEnabled() is True
        assert window.images_refresh.text() == "更新列表"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_images_note_text_covers_online_and_cache() -> None:
    """状态行备注区分「在线更新 / 本地缓存 / 过期缓存」三种口径。"""
    from fcmd.apps.wslman._wslmanw_qt import _images_note_text, _images_stamp

    stamp = _images_stamp(time.time())
    assert "更新于" in _images_note_text(_catalog((), from_cache=False))
    assert "本地缓存" in _images_note_text(_catalog((), from_cache=True))
    assert "过期缓存" in _images_note_text(_catalog((), from_cache=True, stale=True))
    assert isinstance(stamp, str) and stamp


def test_images_page_has_no_bottom_action_bar(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像页不再有底部动作条：下载 / 安装 / 取消都在行内，窗口压到最小也不重叠。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    # 布局用例不碰网络：切页时的后台刷新交给专门的两个用例覆盖。
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entries = tuple(_image_entry(f"Distro{i}") for i in range(30))
        window._apply_images((_catalog(entries), entries))
        window.resize(1040, 600)
        window.show()
        window.show_page("images")
        QApplication.processEvents()

        # 底部「下载选中 / 取消 / 下载后立即安装」已移除
        for name in ("_download_button", "_cancel_button", "install_after"):
            assert not hasattr(window, name), name
        assert window.image_list.height() >= 200
        assert window.images_status.height() == window.images_status.sizeHint().height()
        for widget in (window.image_install_local, window.images_refresh):
            assert widget.height() >= widget.sizeHint().height(), widget.objectName() or widget
        # 行内动作按钮同样不被压扁
        for index in range(window.image_list.count()):
            row = _image_row(window, index)
            if row.download_button is not None:
                assert row.download_button.height() >= row.download_button.sizeHint().height()
    finally:
        window.hide()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


# ---------------------------------------------------------------------- #
# 设置页：下载配置从镜像页搬过来
# ---------------------------------------------------------------------- #
def test_settings_page_hosts_download_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """下载目录 / 下载源 / 校验移入设置页；镜像页不再承担配置表单。"""
    from fcmd.gui.widgets import PageScroll

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        settings_page = window.page("settings")
        assert settings_page is not None
        # 页面用滚动容器：窗口变矮时滚动，而不是压扁控件
        assert settings_page.findChild(PageScroll) is not None
        # 配置项都在设置页里（含新增的备份目录）
        for widget in (window.download_dir, window.backup_dir, window.mirror, window.expected_sha):
            assert settings_page.isAncestorOf(widget)
        assert "64" in window.expected_sha.placeholderText()

        # 镜像页只剩列表与下载动作：配置控件 / 折叠高级区都不在它里面
        window.show_page("images")
        images_page = window.page("images")
        assert images_page is not None
        assert not images_page.isAncestorOf(window.download_dir)
        assert not images_page.isAncestorOf(window.backup_dir)
        assert not images_page.isAncestorOf(window.mirror)
        assert window.image_filter_buttons  # 筛选胶囊仍在列表页
        assert window.images_refresh.objectName() != ""
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_work_dir_card_lists_subfolders(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """设置页「工作目录」卡片：根目录 + 下载 / 安装 / 备份三个用途子目录。"""
    from fcmd.gui.widgets import Card

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        page = window.page("settings")
        assert page is not None
        titles = [card.title() for card in page.findChildren(Card)]
        assert "工作目录" in titles
        work = next(card for card in page.findChildren(Card) if card.title() == "工作目录")
        values = [label.fullText() for label in work.findChildren(_wslmanw_qt.ElidedLabel)]
        root = home / "wsl"
        assert str(root) in values
        assert str(root / "download") in values
        assert str(root / "install") in values
        assert str(root / "backup") in values
        # 下载 / 备份目录默认值分别指向工作目录的 download / backup
        assert window.download_dir.value() == ""
        assert window.backup_dir.value() == ""
        assert window._local_start_dir() == str(root / "download")
        assert window._backup_dir_path() == root / "backup"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_install_dialog_defaults_into_work_dir(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """「安装镜像」默认把发行版装到工作目录的 install/<名称>。"""
    from fcmd.apps.wslman import _config

    archive = tmp_path / "download" / "Ubuntu-24.04.wsl"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"x")
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        dialog = _wslmanw_qt._InstallLocalDialog(window, (), preset=archive)
        try:
            QApplication.processEvents()
            dialog.table.selectRow(0)
            dialog._on_select()
            assert dialog.name.text() == "Ubuntu-24.04"
            assert Path(dialog.target.text()) == home / "wsl" / "install" / "Ubuntu-24.04"
            assert _config.default_install_root() == home / "wsl" / "install"
        finally:
            dialog.close()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_page_persists_download_dir_and_mirror(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """设置页改动立即落盘：下载目录（回车提交）与下载源（切换即保存）。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        window.download_dir.set_value(r"D:\WSL\mirrors")
        window.download_dir.committed.emit(r"D:\WSL\mirrors")
        window.backup_dir.set_value(r"D:/WSL/backups")
        window.backup_dir.committed.emit(r"D:/WSL/backups")
        window.mirror.setCurrentIndex(window.mirror_ids.index("ustc"))
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["download_dir"] == r"D:\WSL\mirrors"
    assert raw["backup_dir"] == r"D:/WSL/backups"
    assert raw["mirror"] == "ustc"


def test_settings_page_restores_saved_download_dir(home: Path) -> None:
    """启动时把 wslmanw.json 的下载目录 / 下载源回填到设置页。"""
    from fcmd.apps.wslman import wslmanw

    wslmanw.save_settings(
        wslmanw.WslmanwSettings(download_dir=r"E:\WSL", backup_dir=r"E:\Backups", mirror="ustc"),
        _guiconfig.config_path(wslmanw.SETTINGS_NAME),
    )
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.download_dir.value() == r"E:\WSL"
        assert window.backup_dir.value() == r"E:\Backups"
        assert window._backup_dir_path() == Path(r"E:\Backups")
        assert window.mirror_ids[window.mirror.currentIndex()] == "ustc"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_images_page_has_no_advanced_collapsible(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像页保持精简：不再有「高级：下载目录 / 校验」折叠区。"""
    from fcmd.gui.widgets import Collapsible

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("images")
        page = window.page("images")
        assert page is not None
        assert page.findChildren(Collapsible) == []
        assert not hasattr(window, "advanced")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


# ---------------------------------------------------------------------- #
# 底部状态栏：单行高度 + 消息归属发起任务的分页
# ---------------------------------------------------------------------- #
def test_statusbar_is_single_line(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态栏恒为一行高：选中摘要、清单状态、加载条都不会把它撑高。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu-24.04")))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.resize(1040, 600)
        window.show()
        QApplication.processEvents()
        single = window.statusBar().sizeHint().height()
        assert window.statusBar().height() == single

        # 发行版页：选中摘要走「页内补充」，不换行、不撑高
        window.distro_list.setCurrentRow(0)
        QApplication.processEvents()
        assert window.status_detail_text()
        assert window.statusBar().height() == single

        # 镜像页：长状态 + 加载条同样保持一行
        window.show_page("images")
        window.set_status("共 120 个镜像 · 显示 120 个 · 已安装 12 · cli+manifest · 更新于 12:00")
        window.show_loading("正在获取镜像清单…")
        QApplication.processEvents()
        assert window.loading_bar.is_running()
        assert window.statusBar().height() == single
    finally:
        window.hide()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_rows_note_matching_image(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """发行版行标注对应的在线镜像；自定义导入给「本地导入」，清单未加载时留空。"""
    from fcmd.apps.wslman import _images

    ubuntu = wslmanw._images.ImageEntry(
        name="Ubuntu-24.04",
        friendly_name="Ubuntu 24.04 LTS",
        sources=("cli", "manifest"),
        downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/ubuntu.wsl"),),
    )
    _images.save_cache((ubuntu,), time.time())
    view = _view(_distro("Ubuntu-24.04"), _distro("MyImport", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        rows = {_distro_row(window, index).distro.name: _distro_row(window, index) for index in range(2)}
        # 缓存里有的镜像：启动即回显，行内直接给出友好名
        assert rows["Ubuntu-24.04"].image_label.fullText() == "镜像 Ubuntu 24.04 LTS"
        assert "CLI" in rows["Ubuntu-24.04"].image_label.toolTip()
        # 清单里没有的发行版：标「本地导入」
        assert rows["MyImport"].image_label.fullText() == "本地导入"

        # 在线清单更新后就地刷新说明，不重建行卡
        row = rows["MyImport"]
        got = wslmanw._images.ImageEntry(name="MyImport", friendly_name="My Import", sources=("cli",))
        window._apply_images((_catalog((ubuntu, got)), (ubuntu, got)))
        after = {_distro_row(window, i).distro.name: _distro_row(window, i) for i in range(2)}
        assert after["MyImport"] is row
        assert row.image_label.fullText() == "镜像 My Import"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_row_image_note_hidden_without_catalog(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有镜像清单（首次启动、断网且无缓存）时不写「本地导入」误导用户。"""
    from fcmd.apps.wslman import _images

    monkeypatch.setattr(_images, "load_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu")))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        row = _distro_row(window, 0)
        assert row.image_label.text() == ""
        assert row.image_label.isVisible() is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_log_moves_to_help_menu(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务日志不占页面：「帮助 → 任务日志…」打开非模态窗口，回放缓冲并实时追加。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        texts = [action.text() for action in window.help_menu.actions() if not action.isSeparator()]
        assert texts[-2:] == ["任务日志…", "关于"]
        assert window.log_action.shortcut().toString() == "Ctrl+Shift+L"
        assert window._log_dialog is None

        window._log("启动前日志")
        window.show_log()
        QApplication.processEvents()
        dialog = window._log_dialog
        assert dialog is not None
        assert dialog.isVisible()
        assert "启动前日志" in dialog.log_view.text()

        # 打开后继续实时追加
        window._log("运行中日志")
        QApplication.processEvents()
        assert "运行中日志" in dialog.log_view.text()

        # 清空同时清缓冲：再次打开不会回放已清掉的行
        dialog.log_view.clear_button.click()
        QApplication.processEvents()
        assert dialog.log_view.text() == ""
        assert window._log_lines == []
        window.show_log()
        QApplication.processEvents()
        assert dialog.log_view.text() == ""
        dialog.close()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_log_buffer_is_capped(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """日志缓冲有上限：长时间运行不会无限吃内存。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        for index in range(_wslmanw_qt._LOG_LINES_MAX + 5):
            window._log(f"line {index}")
        assert len(window._log_lines) == _wslmanw_qt._LOG_LINES_MAX
        assert window._log_lines[-1].endswith("line 1004")
        assert not any(line.endswith("line 0") for line in window._log_lines)
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_status_messages_follow_their_page(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务消息写回发起任务的分页；切到别的页不会看到它。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("images")
        # 在镜像页发起任务，然后切到别的页：完成提示应停在镜像页
        window._task_page = "images"
        window.show_page("distros")
        window._set_task_status("下载完成")
        assert window.page_status("images") == "下载完成"
        # 发行版页只显示自己的状态（刷新完成），不会串到镜像页的消息
        assert window.status_text() == window.page_status("distros") != "下载完成"

        window.show_page("images")
        assert window.status_text() == "下载完成"

        # 设置页的改动只写设置页
        window.show_page("distros")
        window._on_download_dir_committed("")
        assert window.page_status("settings").startswith("下载目录")
        assert window.status_text() == window.page_status("distros")
        assert not window.status_text().startswith("下载目录")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_status_poll_does_not_leak_distro_summary_to_other_pages(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：切到镜像页后，3 秒状态轮询不能把发行版摘要写进镜像页状态栏。

    轮询在任何页都会触发 `_show_details()`，摘要必须固定写在「发行版」页的页内补充里，
    否则底部会一直挂着「Ubuntu · 已停止」。
    """
    running = _view(_distro("Ubuntu", "running"))
    stopped = _view(_distro("Ubuntu", "stopped"))
    current = {"view": running}
    monkeypatch.setattr(cli, "_view", lambda env: current["view"])
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        QApplication.processEvents()
        assert window.status_detail_text().startswith("Ubuntu")

        window.show_page("images")
        QApplication.processEvents()
        assert window.status_detail_text() == ""
        assert window.status_text() != ""
        # 模拟 3 秒轮询：状态变了但用户仍停在镜像页
        current["view"] = stopped
        window._apply_status(stopped)
        QApplication.processEvents()
        assert window.status_detail_text() == ""  # 镜像页底部不出现发行版摘要
        assert "Ubuntu" not in window.status_text()

        # 回到发行版页：摘要（已按新状态更新）恢复
        window.show_page("distros")
        QApplication.processEvents()
        assert window.status_detail_text().startswith("Ubuntu")
        assert "已停止" in window.status_detail_text()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_summary_only_on_distros_page(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """选中发行版的摘要不跨页残留：切走后页内补充清空。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu")))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        QApplication.processEvents()
        assert window.status_detail_text().startswith("Ubuntu")
        window.show_page("images")
        assert window.status_detail_text() == ""
        window.show_page("distros")
        assert window.status_detail_text().startswith("Ubuntu")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


# ---------------------------------------------------------------------- #
# 行内动作：下载 / 安装 / 已安装（底部动作条已移除）
# ---------------------------------------------------------------------- #
def test_image_row_actions_follow_state(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行内按钮随状态变：未下载「下载」→ 已下载「安装」→ 已安装无按钮。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        plain = _image_entry("Ubuntu")
        downloaded = wslmanw._images.ImageEntry(
            name="Debian",
            friendly_name="Debian",
            sources=("cli", "manifest"),
            downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/debian.wsl"),),
            downloaded=True,
            archive=r"D:\WSL\debian.wsl",
        )
        installed = wslmanw._images.ImageEntry(
            name="Alpine",
            friendly_name="Alpine",
            sources=("store",),
            installed=True,
        )
        # 行内按钮的槽在渲染时绑定，先把窗口级动作换成记录器，避免真的开始下载。
        downloads: list[str] = []
        installs: list[str] = []
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_download_entry", lambda self, name: downloads.append(name))
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_install_entry", lambda self, name: installs.append(name))
        entries = (plain, downloaded, installed)
        window._apply_images((_catalog(entries), entries))

        rows = {_image_row(window, index).entry.name: _image_row(window, index) for index in range(3)}
        row_plain, row_downloaded, row_installed = rows["Ubuntu"], rows["Debian"], rows["Alpine"]
        assert row_plain.download_button is not None
        assert row_plain.download_button.text() == "下载"
        assert row_downloaded.download_button is not None
        assert row_downloaded.download_button.text() == "安装"
        assert row_installed.download_button is None

        # 行内标签：已下载 / 已安装
        marks = [tag.text() for tag in row_downloaded.findChildren(_wslmanw_qt.StatusTag)]
        assert "已下载" in marks
        installed_marks = [tag.text() for tag in row_installed.findChildren(_wslmanw_qt.StatusTag)]
        assert "已安装" in installed_marks
        assert "已下载" not in installed_marks

        # 点击分别走「下载」与「安装」两条路径
        row_plain.download_button.click()
        row_downloaded.download_button.click()
        assert downloads == ["Ubuntu"]
        assert installs == ["Debian"]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_install_archive_creates_missing_target_parents(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """安装到还不存在的工作目录（``install/<名称>``）时先建目录，避免 wsl 报「找不到路径」。"""
    archive = tmp_path / "download" / "kali-linux.wsl"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"x" * 16)
    target = home / "wsl" / "install" / "kali-linux"
    assert not target.parent.exists()

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    steps: list[list[str]] = []

    def fake_step(_env: Any, argv: list[str], **_kwargs: Any) -> Any:
        steps.append(list(argv))
        assert target.is_dir(), "导入前必须先建好目标目录"
        return None

    monkeypatch.setattr(cli, "_run_step", fake_step)
    monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._install_archive(archive, "kali-linux", target, replace=False, backup=None)
        _wait_idle(window)
        assert target.is_dir()
        assert steps and steps[0][:2] == ["--import", "kali-linux"]
        assert steps[0][2] == str(target)
        assert steps[0][3] == str(archive)
        assert window.status_text().startswith("安装完成") or "已安装" in window.status_text()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_install_archive_reports_unwritable_target(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """建目录失败时给出可读错误，而不是把 wsl 的「找不到路径」直接抛给用户。"""
    archive = tmp_path / "kali.wsl"
    archive.write_bytes(b"x")
    target = home / "wsl" / "install" / "kali-linux"

    real_mkdir = Path.mkdir

    def boom(self: Path, *args: Any, **kwargs: Any) -> None:
        if self == target:
            raise OSError("access denied")
        real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
    monkeypatch.setattr(_wslmanw_qt.Path, "mkdir", boom)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._install_archive(archive, "kali-linux", target, replace=False, backup=None)
        _wait_idle(window)
        assert "安装失败" in window.status_text()
        assert "无法创建安装目录" in "".join(window._log_lines)
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_install_entry_opens_dialog_with_preset_archive(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """行内「安装」把已下载的归档预选进「安装镜像」对话框。"""
    archive = tmp_path / "ubuntu.wsl"
    archive.write_bytes(b"x")
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = wslmanw._images.ImageEntry(
            name="Ubuntu",
            friendly_name="Ubuntu",
            sources=("cli", "manifest"),
            downloads=(wslmanw._images.ImageDownload("x86_64", "https://e/ubuntu.wsl"),),
            downloaded=True,
            archive=str(archive),
        )
        window._apply_images((_catalog((entry,)), (entry,)))
        seen: list[Path | None] = []

        class _FakeDialog:
            def __init__(self, _owner: Any, _entries: Any, *, preset: Path | None = None) -> None:
                seen.append(preset)

            def exec(self) -> int:  # 取消：不发起安装，只验证预选路径
                return 0

        monkeypatch.setattr(_wslmanw_qt, "_InstallLocalDialog", _FakeDialog)
        window._install_entry("Ubuntu")
        assert seen == [archive]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_distro_config_button_dialog_and_persisted_cwd(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行内启停右侧有「配置」：设为默认 + 终端默认目录，保存后写进窗口设置。"""
    view = _view(_distro("Ubuntu", "stopped"), _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        row = _distro_row(window, 0)
        assert row.config_button.text() == "配置"
        row.set_busy(True)
        assert row.config_button.isEnabled() is False
        row.set_busy(False)
        assert row.config_button.isEnabled() is True

        dialog = _wslmanw_qt._DistroConfigDialog(window, row.distro, "~")
        try:
            assert dialog.cwd.text() == "~"
            assert dialog.make_default.isEnabled() is False
            dialog.cwd.setText("/srv/app")
            assert dialog.payload() == (False, "/srv/app")
        finally:
            dialog.deleteLater()

        other = _distro_row(window, 1)
        dialog = _wslmanw_qt._DistroConfigDialog(window, other.distro, "~")
        try:
            dialog.make_default.setChecked(True)
            dialog.cwd.setText("/srv/debian")
            assert dialog.payload() == (True, "/srv/debian")
        finally:
            dialog.deleteLater()

        window._save_terminal_cwd("Debian", "/srv/debian")
        assert wslmanw.terminal_cwd(cast(wslmanw.WslmanwSettings, window.settings), "debian") == "/srv/debian"
        assert wslmanw.load_settings().distro_terminal_dirs == {"Debian": "/srv/debian"}
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_init_env_section_defaults_status_copy_and_execute(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """初始化环境卡片：已就绪勾选 + 禁用 + 绿色标签、复制公钥、root 步骤加 -u root。"""
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc me@host"
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"))
    try:
        assert set(section.checks) == {task.key for task in _initenv.TASKS}
        assert set(section.status_tags) == {task.key for task in _initenv.TASKS}
        assert all(box.isChecked() for box in section.checks.values())
        assert all(box.isEnabled() for box in section.checks.values())
        assert all(tag.isHidden() for tag in section.status_tags.values())
        # 默认收起：只显示已初始化情况与展开按钮；勾选 / 状态 / 日志隐藏。
        assert section.collapsed is True
        assert section.details.isHidden() is True
        assert section.collapsed_hint.isHidden() is False
        assert _icon_name(section.toggle_button) == "chevron-down"
        assert section.toggle_button.toolTip() == "展开详情"
        assert all(tag.isHidden() for tag in section._collapsed_tags.values())
        # 三个动作不随详情收起：收起态下也能检测环境 / 复制 SSH / 开始初始化。
        assert section.detect_button.isHidden() is False
        assert section.run_button.isHidden() is False
        assert section.copy_key.isHidden() is False
        # 状态直接铺在卡片里，不再藏进可展开的详情。
        assert all(not label.isHidden() for label in section._status_values.values())

        section.toggle_collapsed()
        assert section.collapsed is False
        assert section.details.isHidden() is False
        assert _icon_name(section.toggle_button) == "chevron-up"
        assert section.toggle_button.toolTip() == "收起详情"
        assert section.collapsed_hint.isHidden() is True
        # 收起按钮的 clicked 会带 checked 参数：点第二下必须真的收起。
        section.toggle_button.click()
        assert section.collapsed is True
        assert section.details.isHidden() is True
        assert _icon_name(section.toggle_button) == "chevron-down"
        assert section.toggle_button.toolTip() == "展开详情"

        section._apply_status(
            _initenv.EnvStatus(
                user="me",
                home="/home/me",
                os_name="Ubuntu 24.04",
                mirror="mirrors.aliyun.com",
                ssh_public_key=public_key,
                python="3.12.3",
                node="20.11.0",
                rust="1.80.0",
                pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
                npm_registry="https://registry.npmmirror.com/",
            )
        )
        # 全部就绪：保持勾选但禁用，绿色「已初始化」标签可见。
        assert all(box.isChecked() for box in section.checks.values())
        assert not any(box.isEnabled() for box in section.checks.values())
        for tag in section.status_tags.values():
            assert tag.isHidden() is False
            assert tag.text() == "已初始化"
            assert tag.tone() == "success"
        section._set_all(False)
        assert all(box.isChecked() for box in section.checks.values())
        assert section.copy_key.isEnabled() is True
        # 全部项目已初始化：开始初始化直接禁用。
        assert section.run_button.isEnabled() is False
        assert "已初始化" in section.run_button.toolTip()
        assert "Python 3.12.3" in section.summary.fullText()
        # 摘要行会省略，完整文案同步进 tooltip；展开区说明也收在卡片 tooltip 里。
        assert section.summary.toolTip() == section.summary.fullText()
        assert "检测与执行都会启动该实例" in section.toolTip()
        assert section._status_values["SSH 密钥"].text().startswith("ssh-ed25519 ")

        section._copy_ssh_key()
        assert QApplication.clipboard().text() == public_key

        # 收起后只留已初始化标签：全就绪时 5 个短标签全部可见。
        section.toggle_collapsed(True)
        assert section.details.isHidden() is True
        assert section.collapsed_hint.text() == "已初始化："
        assert all(not tag.isHidden() for tag in section._collapsed_tags.values())
        assert section._collapsed_tags["node"].text() == "Node.js"
        section.toggle_collapsed(False)
        assert section.details.isHidden() is False
        assert all(tag.isHidden() for tag in section._collapsed_tags.values())

        section._apply_status(_initenv.EnvStatus(python="3.12.3"))
        # 仅 Python 就绪：只有它勾选 + 禁用 + 绿标，其余仍可操作。
        assert section._done == {"python"}
        assert section.run_button.isEnabled() is True
        assert section.collapsed_hint.text() == "已初始化："
        assert section.checks["python"].isEnabled() is False
        assert section.checks["python"].isChecked() is True
        assert section.status_tags["python"].isHidden() is False
        for key in ("mirror", "ssh", "node", "rust"):
            assert section.checks[key].isEnabled() is True
            assert section.checks[key].isChecked() is True
            assert section.status_tags[key].isHidden() is True
        section._set_all(False)
        assert section.checks["python"].isChecked() is True
        assert not any(section.checks[key].isChecked() for key in ("mirror", "ssh", "node", "rust"))

        section._apply_status(_initenv.EnvStatus())
        assert section.copy_key.isEnabled() is False
        assert all(tag.isHidden() for tag in section.status_tags.values())
        assert section.collapsed_hint.text() == "尚未检测到已初始化的项目。"
        assert all(tag.isHidden() for tag in section._collapsed_tags.values())
        QApplication.clipboard().setText("sentinel")
        section._copy_ssh_key()
        assert QApplication.clipboard().text() == "sentinel"

        calls: list[list[str]] = []

        class _FakeRunner:
            def run(self, argv: Any, *, timeout: float | None = None, env: Any = None) -> CommandResult:
                calls.append(list(argv))
                return CommandResult(argv=tuple(argv), returncode=0, stdout=b"done\n", stderr=b"", duration_ms=1)

        class _FakeEnv:
            runner = _FakeRunner()

        monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
        monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
        assert section._execute(_initenv.steps_for(["ssh"])) == "初始化完成"
        assert calls[0][:6] == ["wsl.exe", "-d", "Ubuntu", "-u", "root", "--exec"]
        assert calls[1][:4] == ["wsl.exe", "-d", "Ubuntu", "--exec"]
        assert "-u" not in calls[1]
        assert calls[0][-1] == _initenv.steps_for(["ssh"])[0].script
        assert calls[1][-1] == _initenv.steps_for(["ssh"])[1].script
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_section_restores_saved_selection(home: Path) -> None:
    """初始化勾选是「已设置参数」：有保存值时按保存值勾选，探测后也不被默认勾选覆盖。"""
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"), selected=("python", "rust"))
    try:
        assert section.checks["python"].isChecked() is True
        assert section.checks["rust"].isChecked() is True
        assert section.checks["node"].isChecked() is False
        assert section.selected_keys() == ("python", "rust")

        section._apply_status(_initenv.EnvStatus(python="3.12.3"))
        # 已就绪的 python 被强制勾选并禁用，但不再计入待执行选择。
        assert section.checks["python"].isChecked() is True
        assert section.checks["python"].isEnabled() is False
        assert section.checks["rust"].isChecked() is True
        assert section.checks["node"].isChecked() is False
        assert section.selected_keys() == ("rust",)
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_section_detect_parses_probe_output(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"))
    try:
        calls: list[list[str]] = []

        class _FakeRunner:
            def run(self, argv: Any, *, timeout: float | None = None, env: Any = None) -> CommandResult:
                calls.append(list(argv))
                return CommandResult(
                    argv=tuple(argv),
                    returncode=0,
                    stdout=b"WSLMAN_USER=me\nWSLMAN_MIRROR=mirrors.aliyun.com\nWSLMAN_PYTHON=3.12.3\n",
                    stderr=b"",
                    duration_ms=1,
                )

        class _FakeEnv:
            runner = _FakeRunner()

        monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
        monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
        status = section._detect()
        assert status.user == "me"
        assert status.mirror == "mirrors.aliyun.com"
        assert status.python == "3.12.3"
        assert calls == [["wsl.exe", "-d", "Ubuntu", "--exec", "bash", "-lc", _initenv.probe_script()]]
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_section_run_skips_ready_items(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """开始初始化只跑「未就绪且勾选」的项目，已就绪的不重复执行。"""
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"))
    try:
        section._apply_status(_initenv.EnvStatus(python="3.12.3"))
        section._set_all(False)
        section.checks["node"].setChecked(True)
        section.checks["python"].setChecked(True)  # 已就绪，禁用后即使勾着也不执行
        seen: list[tuple[str, ...]] = []

        def fake_submit(fn: Any, *, on_done: Any = None, on_error: Any = None, on_finished: Any = None) -> None:
            if on_done is not None:
                on_done(fn())
            if on_finished is not None:
                on_finished()

        monkeypatch.setattr(_wslmanw_qt, "submit_daemon", fake_submit)
        monkeypatch.setattr(section, "refresh_status", lambda: None)
        monkeypatch.setattr(
            section, "_execute", lambda steps: seen.append(tuple(step.label for step in steps)) or "初始化完成"
        )
        section._run_selected()
        assert seen == [("安装 Node.js 软件包", "配置 npm 镜像")]
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_distro_config_embeds_init_env_section(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置对话框直接内嵌初始化环境卡片：一层可见、首次显示自动检测、忙时确认关闭。"""
    owner = QWidget()
    dialog = _wslmanw_qt._DistroConfigDialog(owner, _distro("Ubuntu"), "~")  # type: ignore[arg-type]
    try:
        assert isinstance(dialog.init_env, _wslmanw_qt._InitEnvSection)
        assert dialog.init_env.parent() is dialog
        assert dialog.init_env.details.isHidden() is True
        assert _icon_name(dialog.init_env.toggle_button) == "chevron-down"
        for name in ("detect_button", "run_button", "copy_key"):
            assert dialog.isAncestorOf(getattr(dialog.init_env, name))

        calls: list[str] = []
        monkeypatch.setattr(dialog.init_env, "refresh_status", lambda: calls.append("detect"))
        dialog.show()
        assert calls == ["detect"]
        dialog.hide()
        dialog.show()
        assert calls == ["detect"]  # 只自动检测一次

        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: False)
        dialog.init_env._busy = True
        dialog.reject()
        assert dialog.init_env._closing is False
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
        dialog.reject()
        assert dialog.init_env._closing is True
    finally:
        dialog.deleteLater()
        owner.deleteLater()


def test_distro_config_collapses_back_to_initial_height(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """展开再收起后对话框高度要回到初始值（延误一拍 adjustSize 才能避开旧 minimumSizeHint）。"""
    app = QApplication.instance()
    assert app is not None
    owner = QWidget()
    dialog = _wslmanw_qt._DistroConfigDialog(owner, _distro("Ubuntu"), "~")  # type: ignore[arg-type]
    monkeypatch.setattr(dialog.init_env, "refresh_status", lambda: None)
    try:
        dialog.show()
        app.processEvents()
        initial = dialog.height()
        dialog.init_env.toggle_collapsed(False)
        app.processEvents()
        expanded = dialog.height()
        assert expanded > initial
        dialog.init_env.toggle_collapsed(True)
        app.processEvents()
        assert dialog.init_env.details.isHidden() is True
        assert dialog.height() == initial
    finally:
        dialog.deleteLater()
        owner.deleteLater()


def test_dialog_geometry_persists_and_restores(home: Path) -> None:
    """配置窗口的位置 / 尺寸写进设置文件，下次打开在原处还原；初始化勾选一并持久化。"""
    app = QApplication.instance()
    assert app is not None
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        dialog = _wslmanw_qt._DistroConfigDialog(window, _distro("Ubuntu"), "~", selected=("node",))
        dialog.show()
        app.processEvents()
        dialog.setGeometry(40, 30, 700, 520)
        app.processEvents()
        expected = dialog.geometry()
        window._save_dialog_geometry("distro_config", dialog)
        window._save_init_tasks("Ubuntu", dialog.init_env.selected_keys())
        window._persist_settings(window.geometry_text())
        dialog.deleteLater()

        raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
        assert raw["dialog_geometry"]["distro_config"] == _wslmanw_qt.format_geometry(
            expected.width(), expected.height(), expected.x(), expected.y()
        )
        assert raw["distro_init_tasks"] == {"Ubuntu": ["node"]}

        restored = _wslmanw_qt._DistroConfigDialog(
            window, _distro("Ubuntu"), "~", selected=wslmanw.init_task_keys(wslmanw.load_settings(), "Ubuntu")
        )
        window._restore_dialog_geometry("distro_config", restored)
        try:
            assert restored.init_env.checks["node"].isChecked() is True
            assert restored.init_env.checks["python"].isChecked() is False
            assert restored.geometry() == expected
        finally:
            restored.deleteLater()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_manager_exports_deletes_and_cleans_cache(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """管理镜像：列出完整包 / 未完成 .part，支持导出、删除与清理缓存。"""
    from fcmd.apps.wslman import _images

    download = tmp_path / "download"
    download.mkdir()
    archive = download / "ubuntu.wsl"
    archive.write_bytes(b"image")
    partial = download / "debian.wsl.part"
    partial.write_bytes(b"part")
    cache = _images.cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(tmp_path / "exported"))
    (tmp_path / "exported").mkdir()
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        dialog = _wslmanw_qt._ImageManagerDialog(window, download, ())
        try:
            rows = {item[0].name: index for index, item in enumerate(dialog._items)}
            assert rows == {"debian.wsl.part": 0, "ubuntu.wsl": 1}
            assert dialog._items[0][3] is False
            assert dialog._items[1][3] is True

            dialog.table.selectRow(rows["ubuntu.wsl"])
            dialog._export_selected()
            assert (tmp_path / "exported" / "ubuntu.wsl").read_bytes() == b"image"

            dialog._delete_selected()
            assert archive.exists() is False
            assert dialog.changed is True

            dialog._clean_cache()
            assert partial.exists() is False
            assert cache.exists() is False
            assert dialog.cache_cleared is True
        finally:
            dialog.deleteLater()
        assert window.image_install_local.text() == "安装镜像…"
        assert window.image_manage.text() == "管理镜像…"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_refresh_requested_during_task_runs_after_finish(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务收尾前发起的刷新不能丢：设为默认后默认胶囊立刻换行，不等 3 秒轮询。"""
    current = {"view": _view(_distro("Ubuntu", default=True), _distro("Debian", "stopped", default=False))}
    monkeypatch.setattr(cli, "_view", lambda env: current["view"])
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert _distro_row(window, 0).default_tag.isHidden() is False
        assert _distro_row(window, 1).default_tag.isHidden() is True

        current["view"] = _view(_distro("Ubuntu", default=False), _distro("Debian", "stopped", default=True))
        window._busy = True
        window.refresh()
        assert window._refresh_pending is True
        window._task_finished()
        QApplication.processEvents()
        _wait_idle(window)

        assert window._refresh_pending is False
        assert _distro_row(window, 0).default_tag.isHidden() is True
        assert _distro_row(window, 1).default_tag.isHidden() is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()
