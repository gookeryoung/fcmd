"""wslmanw Qt 窗口的离屏冒烟测试：列表渲染、详情、状态刷新、设置持久化与对话框。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

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
        assert window.start_button.isEnabled() is False
        assert window.stop_button.isEnabled() is True
        # 行内按钮跟着状态走：运行中显示「停止」，已停止显示「启动」
        assert _distro_row(window, 0).start_stop_button.text() == "停止"
        assert _distro_row(window, 1).start_stop_button.text() == "启动"

        window.distro_list.setCurrentRow(1)
        assert window.start_button.isEnabled() is True
        assert window.stop_button.isEnabled() is False
    finally:
        window._log_view.clear()
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
        assert _image_row(window, 0).entry.name == "Debian"
        assert _image_row(window, 1).entry.name == "Ubuntu"
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
        assert _image_row(window, 0).entry.name == "Debian"
        assert "共 2 个镜像" in window.images_status.fullText()
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
    """侧边栏复用共享导航：四页登记、默认停在发行版页，折叠状态随设置落盘。"""
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.nav.keys() == ("distros", "images", "log", "settings")
        assert window.nav.selected() == "distros"
        assert window.current_page() == "distros"
        assert window.page_title.text() == "发行版"
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
        assert not window._log_view.isVisible()
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
    from fcmd.gui.qt.widgets import PageScroll

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        settings_page = window.page("settings")
        assert settings_page is not None
        # 页面用滚动容器：窗口变矮时滚动，而不是压扁控件
        assert settings_page.findChild(PageScroll) is not None
        # 三项配置都在设置页里
        for widget in (window.download_dir, window.mirror, window.expected_sha):
            assert settings_page.isAncestorOf(widget)
        assert "64" in window.expected_sha.placeholderText()

        # 镜像页只剩列表与下载动作：配置控件 / 折叠高级区都不在它里面
        window.show_page("images")
        images_page = window.page("images")
        assert images_page is not None
        assert not images_page.isAncestorOf(window.download_dir)
        assert not images_page.isAncestorOf(window.mirror)
        assert window.image_filter_buttons  # 筛选胶囊仍在列表页
        assert window.images_refresh.objectName() != ""
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
        window.mirror.setCurrentIndex(window.mirror_ids.index("ustc"))
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["download_dir"] == r"D:\WSL\mirrors"
    assert raw["mirror"] == "ustc"


def test_settings_page_restores_saved_download_dir(home: Path) -> None:
    """启动时把 wslmanw.json 的下载目录 / 下载源回填到设置页。"""
    from fcmd.apps.wslman import wslmanw

    wslmanw.save_settings(
        wslmanw.WslmanwSettings(download_dir=r"E:\WSL", mirror="ustc"),
        _guiconfig.config_path(wslmanw.SETTINGS_NAME),
    )
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.download_dir.value() == r"E:\WSL"
        assert window.mirror_ids[window.mirror.currentIndex()] == "ustc"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_images_page_has_no_advanced_collapsible(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像页保持精简：不再有「高级：下载目录 / 校验」折叠区。"""
    from fcmd.gui.qt.widgets import Collapsible

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


def test_status_messages_follow_their_page(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务消息写回发起任务的分页；切到别的页不会看到它。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("images")
        # 在镜像页发起任务，然后切到日志页：完成提示应停在镜像页
        window._task_page = "images"
        window.show_page("log")
        window._set_task_status("下载完成")
        assert window.status_text() == "就绪"
        assert window.page_status("images") == "下载完成"

        window.show_page("images")
        assert window.status_text() == "下载完成"

        # 设置页的改动只写设置页
        window.show_page("log")
        window._on_download_dir_committed("")
        assert window.page_status("settings").startswith("下载目录")
        assert window.status_text() == "就绪"
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


def test_install_entry_opens_dialog_with_preset_archive(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """行内「安装」把已下载的归档预选进「安装本地镜像」对话框。"""
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
