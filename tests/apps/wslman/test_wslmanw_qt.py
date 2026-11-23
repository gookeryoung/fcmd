"""wslmanw Qt 窗口的离屏冒烟测试：列表渲染、详情、状态刷新、设置持久化与对话框。"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from fcmd.apps.wslman import _initenv, _open, _sshfiles, _wslmanw_qt, wslmanw
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman._config import WslmanConfig, default_path
from fcmd.apps.wslman._distro import Distro, DistroView
from fcmd.apps.wslman._errors import WslmanError
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


_REAL_HAS_DISTRO_FILES = wslmanw.has_distro_files


@pytest.fixture(autouse=True)
def _assume_fake_distro_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """大多数窗口用例用 ``C:\\WSL\\...`` 假路径构造 view；默认按磁盘文件存在处理。

    专门验证失效实例剔除的用例再恢复 :func:`wslmanw.has_distro_files` 的真实实现。
    """
    monkeypatch.setattr(wslmanw, "has_distro_files", bool)


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


def _b64(text: str) -> str:
    """探测脚本里文件名走 base64。"""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _wait_idle(window: _wslmanw_qt.WslmanQtWindow, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    # 先让 ``_task_finished`` 里 singleShot(0) 排队的刷新（轻量状态 / 完整重读）落地，
    # 再等它完成；只等 ``_busy`` 会在状态刷新线程仍在跑时提前返回，读到旧行卡。
    # 连续两拍空闲才收手：收尾的那一拍可能刚好只把 timer 排进队列，下一拍才真正启动。
    settled = 0
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if window._busy or window._status_busy or window._reload_pending:
            settled = 0
        else:
            settled += 1
            if settled >= 2:
                break
        time.sleep(0.01)
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
        # 与 llmw 模型行同构：副标题只放版本 + 所属工作目录，右侧数值放磁盘占用。
        # 该实例的 BasePath 不在任何已配置目录下，如实标注而不是留空。
        assert _distro_row(window, 0).sub_label.text() == "v2 · 未在已配置目录"
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
        # 顶栏不再重复启动 / 停止；行内按钮跟着状态走，只显示图标，中文在 tooltip。
        assert not hasattr(window, "start_button")
        assert not hasattr(window, "stop_button")
        running_row = _distro_row(window, 0)
        stopped_row = _distro_row(window, 1)
        assert _icon_name(running_row.start_stop_button) == "stop"
        assert running_row.start_stop_button.text() == ""
        assert "停止" in running_row.start_stop_button.toolTip()
        assert _icon_name(stopped_row.start_stop_button) == "run"
        assert stopped_row.start_stop_button.text() == ""
        assert "启动" in stopped_row.start_stop_button.toolTip()

        window.distro_list.setCurrentRow(1)
        assert stopped_row.start_stop_button.isEnabled() is True
    finally:
        window._log_lines.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_actions_and_distro_rows_all_carry_icons(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """实例动作与行卡都有图标：重启 / 配置不再缺图标，发行版按名称区分。"""
    view = _view(
        _distro("Ubuntu-24.04", "running"),
        _distro("Debian", "stopped", default=False),
        _distro("kali-linux", "stopped", default=False),
        _distro("Devuan", "stopped", default=False),
    )
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        # 工具栏：每个动作都有图标、不显示中文，文案进 tooltip。
        distro_buttons = [
            window._button_by_text[name]
            for name in (
                "打开终端",
                "用 IDE 打开",
                "用 PI 打开",
                "重启",
                "刷新",
                "修复注册",
                "安装",
                "导出",
                "迁移",
                "复制",
                "删除实例",
                "关闭全部",
            )
        ]
        assert all(_icon_name(item) for item in distro_buttons)
        assert all(item.text() == "" for item in distro_buttons)
        assert all(item.toolTip() for item in distro_buttons)
        # 顶栏不再重复启动 / 停止：这两个动作只在行卡上。
        assert "启动" not in window._button_by_text
        assert "停止" not in window._button_by_text
        # 用户口径：重启用修复注册原来的 reset 图标，关闭全部用重启原来的 power，
        # 修复注册改用扳手。
        assert _icon_name(window.restart_button) == "reset"
        assert _icon_name(window._button_by_text["修复注册"]) == "wrench"
        assert _icon_name(window._button_by_text["关闭全部"]) == "power"
        assert _icon_name(window._button_by_text["用 IDE 打开"]) == "code"
        assert _icon_name(window._button_by_text["用 PI 打开"]) == "bot"
        assert _icon_name(window._button_by_text["安装"]) == "download"
        assert _icon_name(window._button_by_text["复制"]) == "copy"
        labels = list(window._button_by_text)
        assert labels.index("安装") < labels.index("导出")

        assert _icon_name(_distro_row(window, 0).config_button) == "settings"
        assert _distro_row(window, 0).config_button.text() == ""
        assert _icon_name(_distro_row(window, 0).start_stop_button) == "stop"
        assert _distro_row(window, 0).start_stop_button.text() == ""
        assert _icon_name(_distro_row(window, 0).icon_label) == "distro-ubuntu"
        assert _icon_name(_distro_row(window, 1).icon_label) == "distro-debian"
        assert _icon_name(_distro_row(window, 2).icon_label) == "distro-kali"
        # 识别不出的发行版回落通用图标，不留空。
        assert _icon_name(_distro_row(window, 3).icon_label) == "distro"
        for index in range(window.distro_list.count()):
            icon = _distro_row(window, index).icon_label
            assert icon.pixmap().isNull() is False

        # 文件页的两个入口也用各自的图标，而不是都落到「打开」。
        assert _icon_name(window.files_open_ide) == "code"
        assert _icon_name(window.files_open_agent) == "bot"
        # 文件页按钮同样只显示图标，中文名在 tooltip。
        file_buttons = (
            window.files_up,
            window.files_home,
            window.files_refresh,
            window.files_open_ide,
            window.files_open_agent,
            window.files_upload,
            window.files_download,
            window.files_mkdir,
            window.files_rename,
            window.files_delete,
        )
        assert all(item.text() == "" for item in file_buttons)
        assert all(item.toolTip() for item in file_buttons)
        assert all(_icon_name(item) for item in file_buttons)
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_restart_button_sits_between_start_and_stop_and_terminates_first(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """重启排在启动 / 停止之间：只对运行中的实例可用，先 terminate 再拉起。"""
    from types import SimpleNamespace

    running = _distro("Ubuntu", "running")
    view = _view(running, _distro("Debian", "stopped", default=False))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    calls: list[tuple[str, object]] = []
    try:
        _wait_idle(window)
        texts = list(window._button_by_text)
        assert "重启" in texts
        assert "启动" not in texts and "停止" not in texts

        window.distro_list.setCurrentRow(0)
        assert window.restart_button.isEnabled() is True
        window.distro_list.setCurrentRow(1)
        assert window.restart_button.isEnabled() is False
        window.distro_list.setCurrentRow(0)

        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
        monkeypatch.setattr(cli, "_build_environment", lambda *args, **kwargs: SimpleNamespace())
        monkeypatch.setattr(cli, "_require_target", lambda env, name: running)
        monkeypatch.setattr(cli, "_run_step", lambda env, argv, **kwargs: calls.append(("run", tuple(argv))))
        monkeypatch.setattr(window, "_launch_keepalive", lambda env, name: calls.append(("launch", name)))
        window._restart_selected()
        # 任务一开始就把按钮置忙（与其他动作同一套忙碌态）
        assert window._busy is True
        assert window.restart_button.isEnabled() is False
        _wait_idle(window)
        assert calls == [("run", ("--terminate", "Ubuntu")), ("launch", "Ubuntu")]
        assert window.page_status("distros") == "重启完成"
    finally:
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
        assert _icon_name(running.start_stop_button) == "stop"
        assert running.start_stop_button.text() == ""
        assert "停止" in running.start_stop_button.toolTip()
        assert running.start_stop_button.objectName() == "Danger"
        assert running.start_stop_button.property("fcmdIconTone") == "danger"
        assert _icon_name(stopped.start_stop_button) == "run"
        assert stopped.start_stop_button.text() == ""
        assert "启动" in stopped.start_stop_button.toolTip()
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
        assert _icon_name(stopped.start_stop_button) == "stop"
        assert "停止" in stopped.start_stop_button.toolTip()
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
        assert _icon_name(row.start_stop_button) == "stop"
        assert window.distro_list.currentRow() == 0
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_status_refresh_skips_unchanged_rows(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询结果与当前行完全一致时不碰行内控件，避免每 3 秒闪一下。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        row = _distro_row(window, 0)
        calls: list[tuple[object, ...]] = []
        monkeypatch.setattr(row, "update_distro", lambda *args: calls.append(args))

        window._on_status(view)
        assert calls == []
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


def test_image_row_shows_wslui_mirror_sources(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """镜像行折叠时只显示源数量，完整名单与 URL 在 tooltip / 展开区。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = _image_entry("Ubuntu-24.04")
        catalog = _wslmanw_qt._wslui.WsluiCatalog(
            (
                _wslmanw_qt._wslui.WsluiDistro(
                    "Ubuntu",
                    "24.04",
                    (
                        _wslmanw_qt._wslui.WsluiSource(
                            "https://mirrors.ustc.edu.cn/ubuntu/rootfs.tar.xz", "ustc", "tar.xz"
                        ),
                    ),
                ),
            )
        )
        window._apply_images((_catalog((entry,)), (entry,), catalog))
        window._render_images()
        row = _image_row(window, 0)
        assert row.mirror_label.isHidden() is False
        # 折叠行只报数量（WSLUI 源 + 官方源）；名单与 URL 收进 tooltip / 展开区。
        assert row.mirror_label.fullText() == "镜像源：2 个"
        assert "中科大 USTC" in row.mirror_label.toolTip()
        assert "mirrors.ustc.edu.cn" in row.mirror_label.toolTip()

        # 匹配不到 WSLUI 源时只剩官方源，数量仍然是算出来的而不是写死的。
        other = _image_entry("Custom")
        window._apply_images((_catalog((other,)), (other,), catalog))
        window._render_images()
        assert _image_row(window, 0).mirror_label.fullText() == "镜像源：1 个"

        # 展开后才给明细：详情区「镜像源」字段与 tooltip 同一份名单。
        window._apply_images((_catalog((entry,)), (entry,), catalog))
        window._render_images()
        window._on_image_toggle(_image_row(window, 0))
        assert window.image_detail_view.values["镜像源"].text().splitlines() == [
            "中科大 USTC · https://mirrors.ustc.edu.cn/ubuntu/rootfs.tar.xz",
            f"官方源 · {entry.downloads[0].url}",
        ]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_auto_download_uses_fastest_wslui_source(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """自动策略先对全部候选测速，WSLUI 最快的排前面，官方源仍作为兜底。"""
    from types import SimpleNamespace

    from fcmd.apps.wslman import _source

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    monkeypatch.setattr(cli, "_preflight_for", lambda *args, **kwargs: SimpleNamespace(ok=True, detail=""))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = _image_entry("Ubuntu-24.04")
        slow = _wslmanw_qt._wslui.WsluiSource("https://slow/rootfs.tar.xz", "slow", "tar.xz")
        fast = _wslmanw_qt._wslui.WsluiSource("https://fast/rootfs.tar.xz", "fast", "tar.xz")
        catalog = _wslmanw_qt._wslui.WsluiCatalog((_wslmanw_qt._wslui.WsluiDistro("Ubuntu", "24.04", (slow, fast)),))
        window._apply_images((_catalog((entry,)), (entry,), catalog))

        def fake_rank(candidates: Any, **_kwargs: Any) -> Any:
            return tuple(sorted(candidates, key=lambda candidate: 0 if "fast" in candidate.url else 1))

        monkeypatch.setattr(_wslmanw_qt._mirrors, "rank_candidates", fake_rank)
        seen: list[tuple[str, ...]] = []

        def fake_download(target: Any, *, candidates: Any, **_kwargs: Any) -> Any:
            seen.append(tuple(candidate.url for candidate in candidates))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x")
            return SimpleNamespace(size=1, source=candidates[0].ident, sha256="x", path=target)

        monkeypatch.setattr(_source, "download_mirrored", fake_download)
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_refresh_image_states", lambda self: None)
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_render_images", lambda self: None)
        monkeypatch.setattr(
            _wslmanw_qt,
            "submit",
            lambda fn, *args, on_done=None, on_error=None, on_finished=None, **kwargs: (
                on_done(fn(*args)) if on_done else None
            ),
        )
        window._download_payload(entry)
        assert seen == [("https://fast/rootfs.tar.xz", "https://slow/rootfs.tar.xz", "https://e/ubuntu-24.04.wsl")]
        assert any("来源 fast" in line for line in window._log_lines)
        assert any("Ubuntu-24.04-x86_64.tar.xz" in line for line in window._log_lines)
        assert any("自动测速择优" in line for line in window._log_lines)
    finally:
        window._downloads.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_download_progress_lands_on_its_own_row(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """并发下载的进度按镜像名落到各自行：行内给百分比 / 已下载量，状态行给并发与排队。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = _image_entry("Ubuntu")
        window._apply_images((_catalog((entry,)), (entry,)))
        task = _wslmanw_qt._DownloadTask(entry)
        task.state = "running"
        window._downloads[entry.name] = task
        window._render_images()

        window._on_download_event((entry.name, 512, 1024))
        row = _image_row(window, 0)
        assert row.metrics_label is not None
        assert row.metrics_label.text().startswith("下载 50%")
        assert "正在下载 1 项" in window.page_status("images")
        assert "50%" in window.page_status("images")
        assert "下载 50%" in window.loading_bar.text_label.text()
        assert window.loading_bar.bar.value() > 0
        assert "排队" not in window.page_status("images")

        # 总量未知（服务器没给长度）时只给已下载量。
        window._on_download_event((entry.name, 2048, None))
        assert row.metrics_label.text().startswith("下载 2.0 KiB")
        assert window.loading_bar.bar.maximum() == 0  # 总量未知回到忙碌态
    finally:
        window._downloads.clear()
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
        # 「仅安装」已合并：没有直链归档的条目在「全部」里，不再单独成筛选。
        window.image_filter_buttons["all"].click()
        assert window.image_list.count() == 2

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
    assert raw["work_dir"] == ""
    # 旧版下载 / 备份目录字段保留读入兼容，但界面不再写入
    assert raw["download_dir"] == ""
    assert raw["backup_dir"] == ""
    assert raw["mirror"] == "ustc"
    assert raw["image_filter"] == "installed"
    assert raw["image_source"] == "manifest"
    assert raw["image_sort"] == "status"


def test_sidebar_pages_and_collapsed_state_persisted(home: Path) -> None:
    """侧边栏复用共享导航：四页登记（日志已移到帮助菜单）、默认停在发行版页。"""
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.nav.keys() == ("distros", "files", "images", "settings")
        assert window.nav.selected() == "distros"
        assert window.current_page() == "distros"
        assert window.page_title.text() == "实例"
        # 四页控件建窗即存在（设置落盘与下载动作会直接读设置页 / 镜像页控件）；
        # 页面本身仍由共享骨架在首次切换时挂进页面栈。
        assert window.distro_list.count() == 0
        assert window.image_list.count() == 0
        assert window.files_table.rowCount() == 0
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
        # 状态行也要能读到原因，不能只有一条「启动失败」。
        assert "启动失败" in window.status_text()
        assert "启动后状态仍为" in window.status_text()
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


def test_instance_toolbar_opens_ide_at_default_cwd(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """实例页「用 IDE 打开」：按实例默认终端目录 + UNC 路径启动设置里选中的 IDE。"""
    from types import SimpleNamespace

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu", "running")))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    config = WslmanConfig(path=default_path(), terminal_program="conhost")
    monkeypatch.setattr(cli, "_build_environment", lambda: SimpleNamespace(config=config))
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    monkeypatch.setattr(_wslmanw_qt._files, "pick_unc_root", lambda name: r"\\wsl.localhost\Ubuntu")
    monkeypatch.setattr(_wslmanw_qt.shutil, "which", lambda name: r"C:\tools\code.exe")
    spawned: list[tuple[str, ...]] = []
    monkeypatch.setattr(_wslmanw_qt, "spawn_gui", lambda argv: spawned.append(tuple(argv)) or 1)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        window.settings = wslmanw.with_terminal_cwd(window.settings, "Ubuntu", "/home/me/proj")
        window._open_selected_ide()
        _wait_idle(window)
        assert spawned == [("code", "--remote", "wsl+Ubuntu", "/home/me/proj")]
        assert "用 VSCode 打开完成" in window.page_status("distros")
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
    """下载源 / 校验留在设置页；镜像页不再承担配置表单。"""
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
        for widget in (window.mirror, window.expected_sha, window.download_limit_combo, window.work_grid):
            assert settings_page.isAncestorOf(widget)
        assert "64" in window.expected_sha.placeholderText()

        # 镜像页只剩列表与下载动作：配置控件 / 折叠高级区都不在它里面
        window.show_page("images")
        images_page = window.page("images")
        assert images_page is not None
        assert not images_page.isAncestorOf(window.mirror)
        assert window.image_filter_buttons  # 筛选胶囊仍在列表页
        assert window.images_refresh.objectName() != ""
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_min_width_keeps_action_text_and_settings_inside_page(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口收到最小宽度：实例动作按钮不被压扁，设置页内容不超出视口。

    回归：动作行原先用 ``QHBoxLayout``，10 个按钮挤在一行时每个只剩 ~59px，
    「打开终端 / 修复注册 / 删除实例 / 关闭全部」都被截断；设置页的打开方式
    下拉框固定 220px，把卡片最小宽度撑出滚动视口，右侧内容被裁掉。
    """
    from fcmd.gui.widgets import PageScroll

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu-24.04")))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show()
        window.resize(wslmanw.MIN_WIDTH, wslmanw.MIN_HEIGHT)
        QApplication.processEvents()

        squeezed = [
            item.text() for item in window._buttons if item.isVisible() and item.width() < item.sizeHint().width()
        ]
        assert squeezed == []

        window.show_page("settings")
        QApplication.processEvents()
        settings_page = window.page("settings")
        assert settings_page is not None
        scroll = settings_page.findChild(PageScroll)
        assert scroll is not None
        # 内容不宽于视口：否则横向滚动条被关掉后，右侧文字会被窗口裁掉。
        assert scroll.widget().minimumSizeHint().width() <= scroll.viewport().width()
        assert scroll.widget().width() <= scroll.viewport().width()
    finally:
        window.hide()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_page_has_no_config_file_card(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """简化后不再展示「配置文件」卡片与单独的下载 / 备份目录。"""
    from fcmd.gui.widgets import Card

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        page = window.page("settings")
        assert page is not None
        assert "配置文件" not in [card.title() for card in page.findChildren(Card)]
        assert not hasattr(window, "download_dir")
        assert not hasattr(window, "backup_dir")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_work_dir_is_first_card_wrapping_multi_disk_grid(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """工作目录是设置页第一张卡：外层与其他设置项一致，里面仍是多磁盘卡。"""
    from fcmd.gui.widgets import Card, DiskGrid

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        page = window.page("settings")
        assert page is not None
        cards = page.findChildren(Card)
        # 设置页第一张卡就是「工作目录」，后面才是下载 / 打开方式。
        assert cards[0].title() == "工作目录"
        assert cards[0].findChild(DiskGrid) is window.work_grid
        assert page.findChildren(DiskGrid) == [window.work_grid]
        buttons = window.work_grid.findChildren(_wslmanw_qt.QPushButton)
        texts = [item.text() for item in buttons]
        assert "打开" in texts
        assert texts.count("添加目录") == 1
        assert "更改工作目录…" not in texts
        # 只有一个目录时不需要「设为主目录」/「移除」。
        assert "设为主目录" not in texts
        assert "移除" not in texts
        assert not hasattr(window, "work_derived")
        # 推演结果仍按默认工作目录生效（只是不展示）。
        root = home / "wsl"
        assert window._local_start_dir() == str(root / "download")
        assert window._install_dir_path() == root / "install"
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


def test_settings_page_persists_work_dir_and_mirror(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """添加工作目录与下载源改动都立即落盘；默认目录仍排在首位。"""
    chosen = tmp_path / "workspace"
    chosen.mkdir()
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(chosen))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("settings")
        # 「添加目录」是磁盘卡自身的虚线卡，点了走目录选择框。
        add = next(
            item for item in window.work_grid.findChildren(_wslmanw_qt.QPushButton) if item.objectName() == "DiskAdd"
        )
        add.click()
        settings = cast(wslmanw.WslmanwSettings, window.settings)
        assert settings.work_dir == str(home / "wsl")
        assert [Path(item) for item in settings.work_dirs] == [home / "wsl", chosen]
        window.mirror.setCurrentIndex(window.mirror_ids.index("ustc"))
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path(wslmanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["work_dir"] == str(home / "wsl")
    assert raw["work_dirs"] == [str(home / "wsl"), str(chosen)]
    assert raw["download_dir"] == ""
    assert raw["backup_dir"] == ""
    assert raw["mirror"] == "ustc"


def test_distro_rows_show_configured_work_dir(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """实例按注册表 BasePath 归到已配置工作目录；不在任何目录下如实标注。"""
    root = tmp_path / "work"
    inside = replace(_distro("Ubuntu"), base_path=str(root / "install" / "Ubuntu"))
    outside = _distro("Debian", "stopped", default=False)
    wslmanw.save_settings(
        wslmanw.WslmanwSettings(work_dir=str(root), work_dirs=(str(root),)),
        _guiconfig.config_path(wslmanw.SETTINGS_NAME),
    )
    monkeypatch.setattr(cli, "_view", lambda env: _view(inside, outside))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert _distro_row(window, 0).sub_label.text() == "v2 · work"
        assert str(root) in _distro_row(window, 0).toolTip()
        assert _distro_row(window, 1).sub_label.text() == "v2 · 未在已配置目录"
        assert "未在已配置目录" in _distro_row(window, 1).toolTip()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def _two_root_window(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    state: str = "stopped",
) -> tuple[Any, Path, Path, Distro]:
    """构造一个有两个工作目录、实例位于第一个目录下的窗口。"""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    distro = replace(_distro("Ubuntu", state), base_path=str(first / "install" / "Ubuntu"))
    wslmanw.save_settings(
        wslmanw.WslmanwSettings(work_dir=str(first), work_dirs=(str(first), str(second))),
        _guiconfig.config_path(wslmanw.SETTINGS_NAME),
    )
    monkeypatch.setattr(cli, "_view", lambda env: _view(distro))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    _wait_idle(window)
    window.distro_list.setCurrentRow(0)
    return window, first, second, distro


def test_migrate_refuses_running_instance(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """运行中的实例直接拦下并提示先停止，不替用户停。"""
    window, _first, _second, _distro_item = _two_root_window(home, monkeypatch, tmp_path, state="running")
    started: list[str] = []
    shown: list[str] = []
    try:
        monkeypatch.setattr(window, "_start", lambda label, work: started.append(label))
        monkeypatch.setattr(_wslmanw_qt.QMessageBox, "information", lambda _p, title, text, *a: shown.append(text))
        window._migrate_selected()
        assert started == []
        assert "先点「停止」" in shown[0]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_migrate_requires_another_work_dir(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """只有一个工作目录时提示先到设置页添加，不弹迁移确认。"""
    root = tmp_path / "only"
    distro = replace(_distro("Ubuntu", "stopped"), base_path=str(root / "install" / "Ubuntu"))
    wslmanw.save_settings(
        wslmanw.WslmanwSettings(work_dir=str(root), work_dirs=(str(root),)),
        _guiconfig.config_path(wslmanw.SETTINGS_NAME),
    )
    monkeypatch.setattr(cli, "_view", lambda env: _view(distro))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    started: list[str] = []
    shown: list[str] = []
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        monkeypatch.setattr(window, "_start", lambda label, work: started.append(label))
        monkeypatch.setattr(_wslmanw_qt.QMessageBox, "information", lambda _p, title, text, *a: shown.append(text))
        window._migrate_selected()
        assert started == []
        assert "添加另一个工作目录" in shown[0]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_migrate_selection_targets_install_subdir(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """选中目标工作目录后，迁移到它的 install/<实例>，并把目标交给后台任务。"""
    window, _first, second, _distro_item = _two_root_window(home, monkeypatch, tmp_path)
    started: list[tuple[str, str]] = []
    calls: list[tuple[Path, Path]] = []
    try:
        monkeypatch.setattr(
            _wslmanw_qt.QInputDialog,
            "getItem",
            lambda *a, **k: (f"second · {second}", True),
        )
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *a, **k: True)
        monkeypatch.setattr(
            window, "_migrate_worker", lambda distro, target_root, target: calls.append((target_root, target)) or "ok"
        )

        def run(label: str, work: Any) -> None:
            started.append((label, work(threading.Event())))

        monkeypatch.setattr(window, "_start", run)
        window._migrate_selected()
        assert started == [("迁移", "ok")]
        assert calls == [(second, second / "install" / "Ubuntu")]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_migrate_worker_exports_unregisters_and_imports(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """成功路径：导出到目标卷 → 删除旧实例 → 导入新目录；临时包清理并写明新位置。"""
    window, _first, second, distro_item = _two_root_window(home, monkeypatch, tmp_path)
    target = second / "install" / "Ubuntu"
    temp = second / ".Ubuntu-migrate.tar"
    commands: list[list[str]] = []
    moved = replace(distro_item, base_path=str(target))
    try:
        monkeypatch.setattr(cli, "_require_target", lambda env, name: distro_item)
        monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
        monkeypatch.setattr(
            cli, "_preflight_for", lambda env, root, need, estimated=False: SimpleNamespace(ok=True, detail="")
        )
        monkeypatch.setattr(cli, "part_path", lambda path: Path(f"{path}.part"))
        monkeypatch.setattr(cli, "atomic_replace", lambda part, path: Path(path).write_text("tar", encoding="utf-8"))
        monkeypatch.setattr(_wslmanw_qt, "distro_export_bytes", lambda *args: (512, False))
        monkeypatch.setattr(cli, "_try_view", lambda env: _view(moved))

        def run_step(env: Any, args: list[str], **kwargs: Any) -> None:
            commands.append(list(args))
            if args[0] == "--export":
                Path(args[2]).write_text("tar", encoding="utf-8")

        monkeypatch.setattr(cli, "_run_step", run_step)
        message = window._migrate_worker(distro_item, second, target)
        assert commands[0][:2] == ["--export", "Ubuntu"]
        assert commands[1] == ["--unregister", "Ubuntu"]
        assert commands[2] == ["--import", "Ubuntu", str(target), str(temp), "--version", "2"]
        assert str(target) in message
        assert not temp.exists()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_migrate_worker_rolls_back_when_import_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """导入失败时把旧实例导回原位置，不让原实例不可用；临时包保留备查。"""
    window, first, second, distro_item = _two_root_window(home, monkeypatch, tmp_path)
    target = second / "install" / "Ubuntu"
    original = Path(distro_item.base_path or "")
    temp = second / ".Ubuntu-migrate.tar"
    commands: list[list[str]] = []
    try:
        monkeypatch.setattr(cli, "_require_target", lambda env, name: distro_item)
        monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
        monkeypatch.setattr(
            cli, "_preflight_for", lambda env, root, need, estimated=False: SimpleNamespace(ok=True, detail="")
        )
        monkeypatch.setattr(cli, "part_path", lambda path: Path(f"{path}.part"))
        monkeypatch.setattr(cli, "atomic_replace", lambda part, path: Path(path).write_text("tar", encoding="utf-8"))
        monkeypatch.setattr(_wslmanw_qt, "distro_export_bytes", lambda *args: (512, False))

        def run_step(env: Any, args: list[str], **kwargs: Any) -> None:
            commands.append(list(args))
            if args[0] == "--export":
                Path(args[2]).write_text("tar", encoding="utf-8")
            if args[0] == "--import" and args[2] == str(target):
                raise _wslmanw_qt.runtime_error("导入失败：磁盘已满")

        monkeypatch.setattr(cli, "_run_step", run_step)
        with pytest.raises(WslmanError) as caught:
            window._migrate_worker(distro_item, second, target)
        assert "已回滚到原位置" in str(caught.value)
        assert ["--import", "Ubuntu", str(original), str(temp), "--version", "2"] in commands
        assert "已经回滚" in str(caught.value) or "已回滚" in str(caught.value)
        assert temp.exists()
        assert first.exists()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_migrate_worker_rejects_insufficient_space(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """目标可用空间不足时明确提示，且不会先导出。"""
    window, _first, second, distro_item = _two_root_window(home, monkeypatch, tmp_path)
    commands: list[list[str]] = []
    try:
        monkeypatch.setattr(cli, "_require_target", lambda env, name: distro_item)
        monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
        monkeypatch.setattr(
            cli,
            "_preflight_for",
            lambda env, root, need, estimated=False: SimpleNamespace(ok=False, detail="需要 8 GiB，可用 1 GiB"),
        )
        monkeypatch.setattr(cli, "_run_step", lambda env, args, **kwargs: commands.append(list(args)))
        monkeypatch.setattr(_wslmanw_qt, "distro_export_bytes", lambda *args: (512, False))
        with pytest.raises(WslmanError) as caught:
            window._migrate_worker(distro_item, second, second / "install" / "Ubuntu")
        assert "可用空间不足" in str(caught.value)
        assert commands == []
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_page_ignores_legacy_download_dirs(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧设置里的下载 / 备份目录按新规则忽略，并在日志里留一句说明。"""
    from fcmd.apps.wslman import wslmanw

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    wslmanw.save_settings(
        wslmanw.WslmanwSettings(download_dir=r"E:\WSL", backup_dir=r"E:\Backups", mirror="ustc"),
        _guiconfig.config_path(wslmanw.SETTINGS_NAME),
    )
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window._local_start_dir() == str(home / "wsl" / "download")
        assert window._backup_dir_path() == home / "wsl" / "backup"
        assert window.mirror_ids[window.mirror.currentIndex()] == "ustc"
        assert any("旧设置里的下载 / 备份目录已忽略" in line for line in window._log_lines)
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
        assert window.log_action is not None
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
        window._on_mirror_changed(window.mirror.currentIndex())
        assert window.page_status("settings").startswith("下载源")
        assert window.status_text() == window.page_status("distros")
        assert not window.status_text().startswith("下载源")
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
        # 「安装」没有关键词命中，靠行内按钮显式给下载图标，不留下空图标。
        assert _icon_name(row_downloaded.download_button) == "download"
        assert _icon_name(row_plain.download_button) == "download"
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


def test_image_row_cancel_button_cancels_its_download(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行内「取消」只作用于这一项下载：设置该项的取消标记，不触发新的下载请求。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = _image_entry("Ubuntu")
        started: list[str] = []
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_download_entry", lambda self, name: started.append(name))
        window._apply_images((_catalog((entry,)), (entry,)))
        task = _wslmanw_qt._DownloadTask(entry)
        task.state = "running"
        window._downloads[entry.name] = task
        window._render_images()

        row = _image_row(window, 0)
        assert row.download_button is not None
        assert row.download_button.text() == "取消"
        row.download_button.click()

        assert task.cancel.is_set()
        assert started == []
        assert "取消中" in window.page_status("images")
    finally:
        window._downloads.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_download_queue_respects_limit_and_queues_extra(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """并发上限内立刻启动，超出的排队；状态行给出并发数、上限与排队数，排队项可单独取消。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entries = [_image_entry(name) for name in ("Ubuntu", "Debian", "Kali")]
        window._apply_images((_catalog(tuple(entries)), tuple(entries)))
        cast(Any, window).settings = wslmanw.with_max_downloads(cast(wslmanw.WslmanwSettings, window.settings), 2)
        launched: list[str] = []
        monkeypatch.setattr(
            _wslmanw_qt.WslmanQtWindow,
            "_launch_download",
            lambda self, task: launched.append(task.entry.name),
        )

        for entry in entries:
            window._download_payload(entry)

        # 前两项按顺序占用槽位，第三项排队。
        assert launched == ["Ubuntu", "Debian"]
        assert window._downloads["Kali"].state == "queued"
        status = window.page_status("images")
        assert "正在下载 2 项" in status
        assert "排队 1 项" in status

        # 排队项取消只移除它自己，不碰正在跑的两项。
        window._cancel_download("Kali")
        assert "Kali" not in window._downloads
        assert window._downloads["Ubuntu"].cancel.is_set() is False
        assert "排队" not in window.page_status("images")
    finally:
        window._downloads.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_download_failure_releases_slot_without_stopping_others(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """单项失败 / 取消只释放自己的槽位，排队的下一项继续启动。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entries = [_image_entry(name) for name in ("Ubuntu", "Debian")]
        window._apply_images((_catalog(tuple(entries)), tuple(entries)))
        cast(Any, window).settings = wslmanw.with_max_downloads(cast(wslmanw.WslmanwSettings, window.settings), 1)
        launched: list[str] = []
        monkeypatch.setattr(
            _wslmanw_qt.WslmanQtWindow,
            "_launch_download",
            lambda self, task: launched.append(task.entry.name),
        )
        # 收尾会重扫磁盘 / 重绘列表，这里只验证调度，不触发真实 IO。
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_refresh_image_states", lambda self: None)
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_render_images", lambda self: None)

        window._download_payload(entries[0])
        window._download_payload(entries[1])
        assert launched == ["Ubuntu"]

        first = window._downloads["Ubuntu"]
        window._download_done(first, ("failed", "网络中断"))
        window._download_finished(first)

        assert "Ubuntu" not in window._downloads
        assert launched == ["Ubuntu", "Debian"]
    finally:
        window._downloads.clear()
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_heavy_task_does_not_turn_download_rows_into_cancel(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """安装 / 迁移等重操作只禁用无下载的行，不会把整列误标成「正在下载」。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = _image_entry("Ubuntu")
        window._apply_images((_catalog((entry,)), (entry,)))
        window._busy = True
        window._set_buttons(False)

        row = _image_row(window, 0)
        assert row.download_button is not None
        assert row.download_button.text() == "下载"
        assert not row.download_button.isEnabled()

        task = _wslmanw_qt._DownloadTask(entry)
        task.state = "running"
        window._downloads[entry.name] = task
        window._set_buttons(False)
        assert row.download_button.text() == "取消"
        assert row.download_button.isEnabled()
    finally:
        window._busy = False
        window._downloads.clear()
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


def test_install_archive_copies_vhdx_and_uses_import_in_place(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """直接选 .vhdx：复制到 install/<名称>/ext4.vhdx，再走 --import-in-place。"""
    source = tmp_path / "download" / "kali.ext4.vhdx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"disk")
    target = home / "wsl" / "install" / "kali-linux"

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
    steps: list[list[str]] = []
    monkeypatch.setattr(cli, "_run_step", lambda _env, argv, **_kwargs: steps.append(list(argv)))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._install_archive(source, "kali-linux", target, replace=False, backup=None)
        _wait_idle(window)
        disk = target / "ext4.vhdx"
        assert disk.read_bytes() == b"disk"
        assert steps == [["--import-in-place", "kali-linux", str(disk)]]
        assert "安装完成" in window.status_text()
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
        assert row.config_button.text() == ""
        assert "配置 Ubuntu" in row.config_button.toolTip()
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
                uv="0.5.14",
                node="20.11.0",
                nvm="yes",
                bun="1.2.0",
                rust="1.80.0",
                pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
                uv_index="https://pypi.tuna.tsinghua.edu.cn/simple",
                npm_registry="https://registry.npmmirror.com/",
                bun_registry="https://registry.npmmirror.com",
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
        # 仅 Python 就绪：它勾选 + 禁用 + 绿标，其余可操作并显示红色「未初始化」。
        assert section._done == {"python"}
        assert section.run_button.isEnabled() is True
        assert section.collapsed_hint.text() == "初始化状态："
        assert section.checks["python"].isEnabled() is False
        assert section.checks["python"].isChecked() is True
        assert section.status_tags["python"].isHidden() is False
        assert section.status_tags["python"].text() == "已初始化"
        assert section.status_tags["python"].tone() == "success"
        for key in ("mirror", "ssh", "uv", "node", "bun", "rust"):
            assert section.checks[key].isEnabled() is True
            assert section.checks[key].isChecked() is True
            assert section.status_tags[key].isHidden() is False
            assert section.status_tags[key].text() == "未初始化"
            assert section.status_tags[key].tone() == "danger"
        section._set_all(False)
        assert section.checks["python"].isChecked() is True
        assert not any(section.checks[key].isChecked() for key in ("mirror", "ssh", "uv", "node", "bun", "rust"))

        section._apply_status(_initenv.EnvStatus())
        assert section.copy_key.isEnabled() is False
        # 检测结果为「全部未初始化」：每个项目都要有红色标签，不能隐藏。
        assert all(not tag.isHidden() for tag in section.status_tags.values())
        assert all(tag.text() == "未初始化" and tag.tone() == "danger" for tag in section.status_tags.values())
        assert section.collapsed_hint.text() == "未初始化："
        section.toggle_collapsed(True)
        assert all(not tag.isHidden() for tag in section._collapsed_tags.values())
        assert all(tag.tone() == "danger" for tag in section._collapsed_tags.values())
        QApplication.clipboard().setText("sentinel")
        section._copy_ssh_key()
        assert QApplication.clipboard().text() == "sentinel"

        calls: list[list[str]] = []

        class _FakeRunner:
            def run(
                self,
                argv: Any,
                *,
                timeout: float | None = None,
                env: Any = None,
                stdin: bytes | None = None,
            ) -> CommandResult:
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
            def run(
                self,
                argv: Any,
                *,
                timeout: float | None = None,
                env: Any = None,
                stdin: bytes | None = None,
            ) -> CommandResult:
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


def test_init_env_section_shows_cached_status_without_running(home: Path) -> None:
    """带缓存打开配置：直接回显上次结果，不执行 wsl -d 启动实例。"""
    cached = _initenv.EnvStatus(user="me", home="/home/me", python="3.12.3", uv="0.5.14")
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu", "stopped"), status=cached)
    try:
        assert section._status == cached
        assert section.checks["python"].isChecked() is True
        assert section.checks["python"].isEnabled() is False
        assert "Python 3.12.3" in section.summary.fullText()
        assert "上次检测结果" in section.log.toPlainText()
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_detect_prompts_before_starting_stopped_instance(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """手动检测前先确认：实例停止时 wsl -d 会启动它。"""
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu", "stopped"))
    submitted: list[Any] = []
    monkeypatch.setattr(_wslmanw_qt, "submit_daemon", lambda *args, **kwargs: submitted.append(args))
    monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: False)
    try:
        section.refresh_status()
        assert submitted == []
        assert "已取消检测" in section.summary.fullText()
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_detection_done_persists_cache(home: Path) -> None:
    """检测完成后通过回调把结果交给窗口持久化，下次打开直接回显。"""
    owner = QWidget()
    seen: list[_initenv.EnvStatus] = []
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"), on_detected=seen.append)
    try:
        status = _initenv.EnvStatus(user="me", python="3.12.3")
        section._detection_done(status)
        assert seen == [status]
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_smart_detect_manual_force_prompts_before_starting_stopped_instance(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实例停止时手动点智能检测也不能绕过确认：取消后不提交后台任务。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    submitted: list[Any] = []
    try:
        _wait_idle(window)
        monkeypatch.setattr(_wslmanw_qt, "submit", lambda *args, **kwargs: submitted.append(args))
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: False)
        window._detect_openers(force=True)
        assert submitted == []
        assert window._detecting is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_smart_detect_reuses_cached_paths_without_starting_stopped_instance(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实例未运行时智能检测只读缓存，不执行任何 wsl 命令。"""
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_opener_paths(
            cast(wslmanw.WslmanwSettings, window.settings),
            "Ubuntu",
            {"opencode": "/usr/bin/opencode"},
        )
        monkeypatch.setattr(cli, "_build_environment", lambda: pytest.fail("不应启动 WSL"))
        monkeypatch.setattr(_open, "detect_host", lambda key: _open.Detection(key, _open.HOST_WINDOWS))
        _host, wsl, warning, fresh = window._detect_worker("Ubuntu", include_wsl=False)
        assert wsl["opencode"].path == "/usr/bin/opencode"
        assert wsl["opencode"].found is True
        assert fresh is None
        assert "复用上次检测结果" in warning
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_sections_use_live_state_after_instance_started(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """初始化环境 / 外部启动实例后，状态提供者返回 running，后续扫描不再弹启动确认。"""
    owner = QWidget()
    submitted: list[Any] = []
    monkeypatch.setattr(_wslmanw_qt, "submit_daemon", lambda *args, **kwargs: submitted.append(args))
    monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: pytest.fail("实例已在运行，不应再确认"))
    init = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu", "stopped"), state_provider=lambda: "running")
    ssh = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu", "stopped"), state_provider=lambda: "running")
    try:
        init.refresh_status()
        ssh.refresh()
        assert len(submitted) == 2
    finally:
        init.deleteLater()
        ssh.deleteLater()
        owner.deleteLater()


def test_ssh_scan_prompts_before_starting_stopped_instance(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SSH 迁移扫描同样不能在实例停止时静默启动。"""
    owner = QWidget()
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu", "stopped"))
    submitted: list[Any] = []
    monkeypatch.setattr(_wslmanw_qt, "submit_daemon", lambda *args, **kwargs: submitted.append(args))
    monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: False)
    try:
        section.refresh()
        assert submitted == []
        assert "已取消扫描" in section.file_hint.fullText()
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_detection_keeps_started_instance_alive(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """为检测而启动的实例要用看护进程留在 Running，不能命令结束就自动停。"""
    kept: list[str] = []

    class _Runner:
        def run(self, argv: Any, **_kwargs: Any) -> CommandResult:
            return CommandResult(tuple(argv), 0, b"WSLMAN_USER=me\n", b"", 1)

    class _FakeEnv:
        runner = _Runner()

    monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    monkeypatch.setattr(_wslmanw_qt, "_ensure_running", lambda env, name: kept.append(name))
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu", "stopped"))
    try:
        section._keepalive = True
        status = section._detect()
        assert status.user == "me"
        assert kept == ["Ubuntu"]
        assert section._keepalive is False
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
        assert seen == [("安装 Node.js 依赖", "安装 nvm / Node.js")]
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_package_mirror_selection_reaches_run_steps(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """软件包源独立于系统镜像源：选择后初始化步骤直接使用指定 PyPI。"""
    owner = QWidget()
    saved: list[str] = []
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"), on_package_mirror_changed=saved.append)
    try:
        assert section.package_mirror_key() == "auto"
        index = section.package_mirror.findData("mirrors.ustc.edu.cn")
        assert index >= 0
        section.package_mirror.setCurrentIndex(index)
        assert saved == ["mirrors.ustc.edu.cn"]
        assert section.package_mirror_key() == "mirrors.ustc.edu.cn"

        section._apply_status(_initenv.EnvStatus())
        section._set_all(False)
        section.checks["uv"].setChecked(True)
        seen: list[tuple[tuple[str, str], ...]] = []

        def fake_submit(fn: Any, *, on_done: Any = None, on_error: Any = None, on_finished: Any = None) -> None:
            if on_done is not None:
                on_done(fn())
            if on_finished is not None:
                on_finished()

        monkeypatch.setattr(_wslmanw_qt, "submit_daemon", fake_submit)
        monkeypatch.setattr(section, "refresh_status", lambda: None)
        monkeypatch.setattr(
            section,
            "_execute",
            lambda steps: seen.append(tuple((step.label, step.script) for step in steps)) or "初始化完成",
        )
        section._run_selected()
        assert len(seen) == 1 and len(seen[0]) == 3
        # 先同步软件包源（只写 pip / uv 配置，不重装 uv），再跑勾选的 uv 安装。
        assert seen[0][0][0] == "配置软件包镜像"
        assert "index='https://mirrors.ustc.edu.cn/pypi/simple'" in seen[0][0][1]
        assert "uv_toml.tmp" in seen[0][0][1]
        assert "pip install --user" not in seen[0][0][1]
        assert "/etc/apt/sources.list" not in seen[0][0][1]
        assert "index='https://mirrors.ustc.edu.cn/pypi/simple'" in seen[0][2][1]
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_init_env_package_mirror_mismatch_reruns_config_only(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """软件包源已改、但 Python / uv 已就绪时，仍要单独更新 pip / uv 配置。"""
    owner = QWidget()
    section = _wslmanw_qt._InitEnvSection(owner, _distro("Ubuntu"), package_mirror="mirrors.ustc.edu.cn")
    try:
        section._apply_status(
            _initenv.EnvStatus(
                mirror="mirrors.aliyun.com",
                ssh_public_key="ssh-ed25519 AAAA",
                python="3.12.3",
                uv="0.5.14",
                node="20.11.0",
                nvm="yes",
                bun="1.2.0",
                rust="1.80.0",
                pip_index="https://pypi.tuna.tsinghua.edu.cn/simple",
                uv_index="https://pypi.tuna.tsinghua.edu.cn/simple",
                npm_registry="https://registry.npmmirror.com/",
                bun_registry="https://registry.npmmirror.com",
            )
        )
        assert section._package_mirror_dirty is True
        assert section.run_button.isEnabled() is True
        assert "软件包源已修改" in section.run_button.toolTip()
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
        assert seen == [("配置软件包镜像",)]
        assert section._package_mirror_dirty is False
        assert section.run_button.isEnabled() is False
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_distro_config_embeds_init_env_section(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配置对话框内嵌初始化环境：一层可见、打开不自动检测、忙时确认关闭。"""
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
        assert calls == []  # 打开配置不启动实例，也不自动检测
        dialog.hide()
        dialog.show()
        assert calls == []

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


def test_ssh_migrate_section_scans_confirms_and_writes(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """迁移 SSH 卡片：扫描来源 / 实例现状、冲突确认、内容走 stdin、写入后校验权限。"""
    source = tmp_path / "ssh"
    source.mkdir()
    (source / "id_ed25519").write_bytes(b"PRIVATE KEY DATA")
    (source / "id_ed25519.pub").write_bytes(b"ssh-ed25519 AAAA me")
    (source / "known_hosts").write_bytes(b"github.com ssh-ed25519 AAAA")
    owner = QWidget()
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu"))
    try:
        assert section.collapsed is True
        assert section.details.isHidden() is True
        assert section.migrate_button.isEnabled() is False
        assert section.source.value() == str(_sshfiles.default_source_dir())
        section.source.set_value(str(source))

        target = _sshfiles.SshTarget(
            user="me",
            home="/home/me",
            dir="/home/me/.ssh",
            dir_mode="700",
            files=(_sshfiles.RemoteFile("known_hosts", "644"),),
        )
        monkeypatch.setattr(section, "refresh", lambda: None)
        files = _sshfiles.scan_source(source)
        windows = _sshfiles.SshSource(_sshfiles.SOURCE_WINDOWS, "", str(source))
        section._scan_done((files, target, windows))
        assert set(section._boxes) == {"id_ed25519", "id_ed25519.pub", "known_hosts"}
        assert all(box.isChecked() for box in section._boxes.values())
        assert section.migrate_button.isEnabled() is True
        assert "已有 1 个文件" in section.target_hint.text()
        assert section.summary.fullText() == "Windows 宿主 3 个文件 · 实例内 1 个文件"
        # 标签由扫描结果推导：实例里只有不同大小的 known_hosts，算未迁移。
        assert section.state_tag.isHidden() is False
        assert section.state_tag.text() == "未迁移"
        assert section.state_tag.tone() == "muted"

        matched = _sshfiles.SshTarget(
            user="me",
            home="/home/me",
            dir="/home/me/.ssh",
            dir_mode="700",
            files=tuple(_sshfiles.RemoteFile(item.name, item.mode_text, item.size) for item in files),
        )
        section._scan_done((files, matched, windows))
        assert section.state_tag.text() == "已完成迁移"
        assert section.state_tag.tone() == "success"
        assert "均已存在" in section.state_tag.toolTip()

        partial = _sshfiles.SshTarget(
            dir="/home/me/.ssh",
            files=(_sshfiles.RemoteFile("id_ed25519", "644", files[0].size),),
        )
        section._scan_done((files, partial, windows))
        assert section.state_tag.text() == "部分迁移"
        assert section.state_tag.tone() == "warning"

        section._scan_error("boom")
        assert section.state_tag.isHidden() is True
        assert section.state_tag.toolTip() == ""
        assert section._boxes == {}
        # 扫描失败清空旧结果；重新扫描后继续验证迁移。
        section._scan_done((files, target, windows))

        # 取消覆盖确认：不发起任何写入。
        scheduled: list[Any] = []
        monkeypatch.setattr(_wslmanw_qt, "submit_daemon", lambda fn, **kwargs: scheduled.append(fn))
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: False)
        section._migrate_selected()
        assert scheduled == []

        # 确认后逐个写入：stdin 带内容，命令行里不出现私钥内容。
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
        seen: list[tuple[list[str], bytes | None]] = []

        class _Runner:
            def run(
                self,
                argv: Any,
                *,
                timeout: float | None = None,
                env: Any = None,
                stdin: bytes | None = None,
            ) -> CommandResult:
                seen.append((list(argv), stdin))
                mode = "600" if len(seen) == 1 else "644"
                return CommandResult(
                    argv=tuple(argv),
                    returncode=0,
                    stdout=f"WSLSSH_WRITTEN={mode}|me\n".encode(),
                    stderr=b"",
                    duration_ms=1,
                )

        class _FakeEnv:
            runner = _Runner()

        monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
        monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
        refreshed: list[bool] = []
        monkeypatch.setattr(section, "refresh", lambda: refreshed.append(True))
        names = list(section._boxes)
        assert section._migrate(names, set()) == "已迁移 3 个文件"
        assert [stdin for _argv, stdin in seen] == [(source / name).read_bytes() for name in names]
        assert all(argv[:4] == ["wsl.exe", "-d", "Ubuntu", "--exec"] for argv, _stdin in seen)
        assert 'cat > "$tmp"' in seen[0][0][-1]
        assert "PRIVATE KEY DATA" not in seen[0][0][-1]
        assert refreshed == []
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_ssh_migrate_section_surfaces_write_failures(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """覆盖未确认 / 权限不符都要明确报错，不能静默当成功。"""
    source = tmp_path / "ssh"
    source.mkdir()
    (source / "id_ed25519").write_bytes(b"KEY")
    owner = QWidget()
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu"))
    try:
        section.source.set_value(str(source))
        section._files = {"id_ed25519": _sshfiles.SshFile("id_ed25519", 3, "private")}

        class _Runner:
            def __init__(self, result: CommandResult) -> None:
                self.result = result

            def run(
                self,
                argv: Any,
                *,
                timeout: float | None = None,
                env: Any = None,
                stdin: bytes | None = None,
            ) -> CommandResult:
                return self.result

        def _install(result: CommandResult) -> None:
            class _FakeEnv:
                runner = _Runner(result)

            monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
            monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")

        _install(
            CommandResult(
                argv=("wsl.exe",),
                returncode=3,
                stdout=b"",
                stderr="目标已存在且未确认覆盖：/home/me/.ssh/id_ed25519".encode(),
                duration_ms=1,
            )
        )
        with pytest.raises(WslmanError) as conflict:
            section._migrate(["id_ed25519"], set())
        assert conflict.value.code == "RUNTIME_ERROR"
        assert "扫描后才出现" in (conflict.value.hint or "")

        _install(
            CommandResult(
                argv=("wsl.exe",),
                returncode=0,
                stdout=b"WSLSSH_WRITTEN=644|me\n",
                stderr=b"",
                duration_ms=1,
            )
        )
        with pytest.raises(WslmanError) as wrong_mode:
            section._migrate(["id_ed25519"], set())
        assert wrong_mode.value.code == "RUNTIME_ERROR"
        assert "644" in wrong_mode.value.message
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_ssh_migrate_from_another_instance_streams_files(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """跨实例来源：扫描经 wsl.exe 探测，读取走 cat 文件流，内容不进入命令行。"""
    probe_debian = (
        "WSLSSH_USER=me\nWSLSSH_HOME=/home/me\nWSLSSH_DIR=/home/me/.ssh\nWSLSSH_DIRMODE=700\n"
        f"WSLSSH_FILE={_b64('id_ed25519')}|600|16\n"
    )
    probe_ubuntu = "WSLSSH_USER=me\nWSLSSH_HOME=/home/me\nWSLSSH_DIR=/home/me/.ssh\nWSLSSH_DIRMODE=700\n"
    calls: list[tuple[list[str], bytes | None]] = []

    class _Runner:
        def run(
            self,
            argv: Any,
            *,
            timeout: float | None = None,
            env: Any = None,
            stdin: bytes | None = None,
        ) -> CommandResult:
            calls.append((list(argv), stdin))
            distro = argv[2]
            if argv[4] == "cat":
                return CommandResult(tuple(argv), 0, b"PRIVATE KEY DATA", b"", 1)
            script = argv[-1]
            if "WSLSSH_WRITTEN=" in script:
                return CommandResult(tuple(argv), 0, b"WSLSSH_WRITTEN=600|me\n", b"", 1)
            text = probe_debian if distro == "Debian" else probe_ubuntu
            return CommandResult(tuple(argv), 0, text.encode(), b"", 1)

    class _FakeEnv:
        runner = _Runner()

    monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    owner = QWidget()
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu"), instances=("Debian",))
    try:
        assert section.source_instance.isHidden() is True
        section.source_kind.setCurrentIndex(section.source_kind.findData(_sshfiles.SOURCE_WSL))
        assert section.source_instance.isHidden() is False
        assert section.source.isHidden() is True
        assert "Debian:~/.ssh" in section.source_remote.fullText()

        files, target, source = section._scan()
        assert [item.name for item in files] == ["id_ed25519"]
        assert files[0].size == 16
        assert target.dir == "/home/me/.ssh"
        assert source.display_path() == "Debian:/home/me/.ssh"
        # 先探测来源实例，再探测当前实例。
        assert [call[0][2] for call in calls] == ["Debian", "Ubuntu"]

        section._scan_done((files, target, source))
        assert section.summary.fullText() == "WSL 实例 · Debian 1 个文件 · 实例内 0 个文件"
        assert section.source_remote.fullText() == "Debian:/home/me/.ssh"

        calls.clear()
        assert section._migrate(["id_ed25519"], set()) == "已迁移 1 个文件"
        read_argv, read_stdin = calls[0]
        assert read_argv == ["wsl.exe", "-d", "Debian", "--exec", "cat", "--", "/home/me/.ssh/id_ed25519"]
        assert read_stdin is None
        write_argv, write_stdin = calls[1]
        assert write_argv[:4] == ["wsl.exe", "-d", "Ubuntu", "--exec"]
        assert write_argv[4] == "bash"
        assert write_stdin == b"PRIVATE KEY DATA"
        assert "PRIVATE KEY DATA" not in " ".join(write_argv)
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_ssh_migrate_reports_unreachable_source_instance(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """来源实例读不到时明确报错并提示先启动，不当成「没有文件」。"""

    class _Runner:
        def run(
            self,
            argv: Any,
            *,
            timeout: float | None = None,
            env: Any = None,
            stdin: bytes | None = None,
        ) -> CommandResult:
            return CommandResult(
                tuple(argv),
                1,
                b"",
                b"There is no distribution with the supplied name.",
                1,
            )

    class _FakeEnv:
        runner = _Runner()

    monkeypatch.setattr(cli, "_build_environment", _FakeEnv)
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    owner = QWidget()
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu"), instances=("Debian",))
    try:
        section.source_kind.setCurrentIndex(section.source_kind.findData(_sshfiles.SOURCE_WSL))
        with pytest.raises(WslmanError) as failure:
            section._scan()
        assert "Debian" in failure.value.message
        assert "启动" in (failure.value.hint or "")
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_distro_config_embeds_ssh_migrate_and_confirms_close(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """迁移卡片默认收起、首次展开扫描；迁移进行中关窗要先确认。"""
    owner = QWidget()
    dialog = _wslmanw_qt._DistroConfigDialog(
        owner,  # type: ignore[arg-type]
        _distro("Ubuntu"),
        "~",
        instances=("Debian",),
    )
    try:
        monkeypatch.setattr(dialog.init_env, "refresh_status", lambda: None)
        assert isinstance(dialog.ssh_migrate, _wslmanw_qt._SshMigrateSection)
        assert dialog.ssh_migrate.parent() is dialog
        assert dialog.ssh_migrate.collapsed is True
        assert dialog.ssh_migrate._instances == ("Debian",)
        assert dialog.ssh_migrate.source_instance.itemText(0) == "Debian"
        scans: list[str] = []
        monkeypatch.setattr(dialog.ssh_migrate, "refresh", lambda: scans.append("scan"))
        dialog.ssh_migrate.toggle_collapsed(False)
        assert scans == ["scan"]
        dialog.ssh_migrate.toggle_collapsed(True)
        assert scans == ["scan"]

        titles: list[str] = []

        def _deny(_parent: Any, title: str, _text: str) -> bool:
            titles.append(title)
            return False

        monkeypatch.setattr(_wslmanw_qt, "confirm", _deny)
        dialog.ssh_migrate._busy = True
        dialog.reject()
        assert titles == ["SSH 迁移仍在进行"]
        assert dialog.ssh_migrate._closing is False
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
        dialog.reject()
        assert dialog.ssh_migrate._closing is True
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


def test_reload_button_rereads_registry_and_recomputes_work_dir_size(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「刷新」重读注册信息：实例搬家后归属目录与占用都按新 BasePath 重算。"""
    first = home / "wsl" / "install" / "Ubuntu"
    second = home / "other" / "install" / "Ubuntu"
    state = {"base": str(first)}
    sizes = {str(first): (2048, False), str(second): (4096, True)}
    monkeypatch.setattr(
        cli,
        "_view",
        lambda env: _view(replace(_distro("Ubuntu", "stopped"), base_path=state["base"])),
    )
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda base, version: sizes.get(str(base), (None, False)))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_work_dirs(
            cast(wslmanw.WslmanwSettings, window.settings),
            [home / "wsl", home / "other"],
            home / "wsl",
        )
        window._on_reload_distros()
        _wait_idle(window)
        row = _distro_row(window, 0)
        assert row.work_dir == home / "wsl"
        metrics = row.metrics_label
        assert metrics is not None and metrics.text() == "2.0 KiB"

        state["base"] = str(second)  # 实例被整体移动到第二个工作目录
        window._on_reload_distros()
        _wait_idle(window)

        row = _distro_row(window, 0)
        assert row.distro.base_path == str(second)
        assert row.work_dir == home / "other"
        assert "other" in row.sub_label.text()
        metrics = row.metrics_label
        assert metrics is not None and metrics.text() == "≈4.0 KiB"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_reload_keeps_size_when_instance_is_outside_configured_dirs(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """实例不在任何已配置目录：归属如实标注，占用仍按 BasePath 的 vhdx 解析。"""
    outside = home / "external" / "Ubuntu"
    monkeypatch.setattr(cli, "_view", lambda env: _view(replace(_distro("Ubuntu", "stopped"), base_path=str(outside))))
    monkeypatch.setattr(
        _wslmanw_qt,
        "distro_disk_bytes",
        lambda base, version: (8192, False) if str(base) == str(outside) else (None, False),
    )
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._on_reload_distros()
        _wait_idle(window)

        row = _distro_row(window, 0)
        assert row.work_dir is None
        assert row.sub_label.text().endswith("未在已配置目录")
        metrics = row.metrics_label
        assert metrics is not None and metrics.text() == "8.0 KiB"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_reload_button_while_busy_runs_full_reload_after_finish(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """重操作中点的「刷新」不丢：收尾后完整重读，归属与占用按新位置重算。"""
    first = home / "wsl" / "install" / "Ubuntu"
    second = home / "other" / "install" / "Ubuntu"
    state = {"base": str(first)}
    sizes = {str(first): (2048, False), str(second): (8192, False)}
    monkeypatch.setattr(
        cli,
        "_view",
        lambda env: _view(replace(_distro("Ubuntu", "stopped"), base_path=state["base"])),
    )
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda base, version: sizes.get(str(base), (None, False)))
    window = _wslmanw_qt.WslmanQtWindow()
    loads: list[str] = []
    real_load = _wslmanw_qt.WslmanQtWindow._load

    def counting_load(self: Any, cancel: threading.Event) -> str:
        loads.append("load")
        return real_load(self, cancel)

    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load", counting_load)
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_work_dirs(
            cast(wslmanw.WslmanwSettings, window.settings),
            [home / "wsl", home / "other"],
            home / "wsl",
        )
        state["base"] = str(second)
        loads.clear()
        window._busy = True
        window._button_by_text["刷新"].click()

        assert loads == []
        assert window._reload_pending is True
        assert "结束后自动" in window.page_status("distros")

        window._busy = False
        window._task_finished()
        QApplication.processEvents()
        _wait_idle(window)

        assert loads == ["load"]
        row = _distro_row(window, 0)
        assert row.work_dir == home / "other"
        metrics = row.metrics_label
        assert metrics is not None and metrics.text() == "8.0 KiB"
    finally:
        window._busy = False
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_refresh_keeps_stale_instance_with_repair_tag(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """注册表 BasePath 失效时仍列出实例，但标「需修复」、禁用启动并启用修复按钮。"""
    monkeypatch.setattr(wslmanw, "has_distro_files", _REAL_HAS_DISTRO_FILES)
    stale = home / "wsl" / "install" / "Ubuntu"
    moved = home / "other" / "install" / "Ubuntu"
    moved.mkdir(parents=True)
    (moved / "ext4.vhdx").write_bytes(b"x" * 16)
    monkeypatch.setattr(cli, "_view", lambda env: _view(replace(_distro("Ubuntu", "stopped"), base_path=str(stale))))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_work_dirs(
            cast(wslmanw.WslmanwSettings, window.settings),
            [home / "wsl", home / "other"],
            home / "wsl",
        )
        window._button_by_text["刷新"].click()
        _wait_idle(window)

        assert window.distro_list.count() == 1
        row = _distro_row(window, 0)
        assert row.distro.needs_repair is True
        assert row.distro.repair_vhd == str(moved / "ext4.vhdx")
        assert row.repair_tag.isHidden() is False
        assert row.start_stop_button.isEnabled() is False
        assert "需先修复注册" in row.start_stop_button.toolTip()
        assert window.repair_button.isEnabled() is True
        assert "需修复注册" in window.page_status("distros")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_repair_registrations_unregisters_and_imports_in_place(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「修复注册」先 unregister 失效登记，再 import-in-place 扫描到的 ext4.vhdx。"""
    monkeypatch.setattr(wslmanw, "has_distro_files", _REAL_HAS_DISTRO_FILES)
    stale = home / "wsl" / "install" / "Ubuntu"
    moved = home / "other" / "install" / "Ubuntu"
    moved.mkdir(parents=True)
    vhd = moved / "ext4.vhdx"
    vhd.write_bytes(b"x" * 16)
    state = {"view": _view(replace(_distro("Ubuntu", "stopped"), base_path=str(stale)))}
    monkeypatch.setattr(cli, "_view", lambda env: state["view"])
    calls: list[tuple[str, ...]] = []

    class _Runner:
        def run(self, argv: Any, **_kwargs: Any) -> CommandResult:
            calls.append(tuple(str(part) for part in argv))
            if "--import-in-place" in argv:
                state["view"] = _view(replace(_distro("Ubuntu", "stopped"), base_path=str(moved)))
            return CommandResult(argv=tuple(argv), returncode=0, stdout=b"", stderr=b"", duration_ms=1)

    config = WslmanConfig(path=default_path(), terminal_program="conhost")
    monkeypatch.setattr(
        cli,
        "_build_environment",
        lambda *args, **kwargs: SimpleNamespace(runner=_Runner(), config=config),
    )
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")
    monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_work_dirs(
            cast(wslmanw.WslmanwSettings, window.settings),
            [home / "wsl", home / "other"],
            home / "wsl",
        )
        window._button_by_text["刷新"].click()
        _wait_idle(window)
        assert _distro_row(window, 0).distro.needs_repair is True

        started: dict[str, Any] = {}
        monkeypatch.setattr(window, "_start", lambda label, work: started.update(label=label, work=work))
        window.repair_button.click()
        assert started["label"] == "修复注册"
        message = started["work"](threading.Event())
        assert "成功 1 个" in message
        rendered = [" ".join(argv) for argv in calls]
        assert any("--unregister Ubuntu" in item for item in rendered)
        assert any(f"--import-in-place Ubuntu {vhd}" in item for item in rendered)
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_refresh_marks_instance_when_disk_files_are_gone(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """磁盘文件被删后，刷新保留实例但标「需修复」，并提示没有可自动导入的磁盘。"""
    monkeypatch.setattr(wslmanw, "has_distro_files", _REAL_HAS_DISTRO_FILES)
    current = home / "wsl" / "install" / "Ubuntu"
    current.mkdir(parents=True)
    (current / "ext4.vhdx").write_bytes(b"x" * 16)
    monkeypatch.setattr(cli, "_view", lambda env: _view(replace(_distro("Ubuntu", "stopped"), base_path=str(current))))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        assert window.distro_list.count() == 1

        # 目录被移除：磁盘文件与安装目录都不在了。
        (current / "ext4.vhdx").unlink()
        current.rmdir()
        window._button_by_text["刷新"].click()
        _wait_idle(window)

        assert window.distro_list.count() == 1
        row = _distro_row(window, 0)
        assert row.distro.needs_repair is True
        assert row.distro.repair_vhd is None
        assert row.repair_tag.isHidden() is False
        assert window.repair_button.isEnabled() is False
        assert "需修复注册" in window.page_status("distros")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_move_task_finishes_with_full_reload(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """迁移收尾的轻量刷新发现 BasePath 变了：静默完整重读，归属与占用立刻更新。"""
    outside = home / "external" / "Ubuntu"
    second = home / "other" / "install" / "Ubuntu"
    state = {"base": str(outside)}
    sizes = {str(outside): (2048, False), str(second): (8192, False)}
    monkeypatch.setattr(
        cli,
        "_view",
        lambda env: _view(replace(_distro("Ubuntu", "stopped"), base_path=state["base"])),
    )
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda base, version: sizes.get(str(base), (None, False)))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_work_dirs(
            cast(wslmanw.WslmanwSettings, window.settings),
            [home / "wsl", home / "other"],
            home / "wsl",
        )
        assert _distro_row(window, 0).work_dir is None

        state["base"] = str(second)
        window._busy = True
        window._task_done("迁移", ("ok", "已迁移到 other"))
        window._task_finished()
        QApplication.processEvents()
        _wait_idle(window)

        row = _distro_row(window, 0)
        assert row.work_dir == home / "other"
        metrics = row.metrics_label
        assert metrics is not None and metrics.text() == "8.0 KiB"
        # 自动重读是静默的：任务结果不被泛化的「刷新完成」覆盖。
        assert window.page_status("distros") == "迁移完成"
    finally:
        window._busy = False
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_status_poll_reloads_rows_when_base_path_changes(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """轮询发现 BasePath 变了：完整重读，归属与占用跟着换，不用等手动点「刷新」。"""
    first = home / "wsl" / "install" / "Ubuntu"
    second = home / "other" / "install" / "Ubuntu"
    sizes = {str(first): (2048, False), str(second): (8192, False)}
    current = {"view": _view(replace(_distro("Ubuntu", "stopped"), base_path=str(first)))}
    monkeypatch.setattr(cli, "_view", lambda env: current["view"])
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda base, version: sizes.get(str(base), (None, False)))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        cast(Any, window).settings = wslmanw.with_work_dirs(
            cast(wslmanw.WslmanwSettings, window.settings),
            [home / "wsl", home / "other"],
            home / "wsl",
        )
        window._on_reload_distros()
        _wait_idle(window)
        assert _distro_row(window, 0).work_dir == home / "wsl"

        current["view"] = _view(replace(_distro("Ubuntu", "stopped"), base_path=str(second)))
        window._on_status(current["view"])
        QApplication.processEvents()
        _wait_idle(window)

        row = _distro_row(window, 0)
        assert row.work_dir == home / "other"
        metrics = row.metrics_label
        assert metrics is not None and metrics.text() == "8.0 KiB"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_start_selected_reprobes_state_instead_of_trusting_stale_row(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行卡停在过期的「运行中」时点启动：按新环境重新探测并真正拉起。"""
    from types import SimpleNamespace

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu", "running")))
    monkeypatch.setattr(cli, "_build_environment", lambda: SimpleNamespace(config=WslmanConfig(path=default_path())))
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cli,
        "_require_target",
        lambda env, name: calls.append(("probe", name)) or _distro("Ubuntu", "stopped"),
    )
    monkeypatch.setattr(
        _wslmanw_qt.WslmanQtWindow,
        "_launch_keepalive",
        lambda self, env, name: calls.append(("launch", name)),
    )
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        window._start_selected()
        _wait_idle(window)

        assert calls == [("probe", "Ubuntu"), ("launch", "Ubuntu")]
        assert window.page_status("distros") == "启动完成"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_work_dir_change_during_busy_defers_row_rebuild(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """工作目录在任务中变化：先标记待刷新，收尾后自动按新配置重建行卡。"""
    installed = str(home / "wsl" / "install" / "Ubuntu")
    monkeypatch.setattr(cli, "_view", lambda env: _view(replace(_distro(), base_path=installed)))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        rebuilt: list[str] = []
        monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_refresh_rows", lambda self: rebuilt.append("rows"))
        other = home / "other"
        window._busy = True
        window._store_work_dirs([other, home / "wsl"], other, "默认工作目录已切换")

        assert rebuilt == []
        assert window._list_pending is True
        assert "列表待刷新" in window.page_status("distros")

        window._busy = False
        window._task_finished()
        assert rebuilt == ["rows"]
        assert window._list_pending is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


# ---------------------------------------------------------------------- #
# 文件页：家目录浏览 + 上传下载 / 增删改
# ---------------------------------------------------------------------- #
def _wait_files(window: _wslmanw_qt.WslmanQtWindow, timeout: float = 5.0) -> None:
    """等文件页的目录读取 / 文件任务全部落地。"""
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if not window._files_loading and not window._files_busy:
            break
        time.sleep(0.01)
    if app is not None:
        app.processEvents()
    assert not window._files_loading
    assert not window._files_busy


def _files_row(window: _wslmanw_qt.WslmanQtWindow, name: str) -> int:
    names = [window.files_table.item(row, 0).text() for row in range(window.files_table.rowCount())]
    for row, text in enumerate(names):
        if text == name:
            return row
    raise AssertionError(f"文件页没有 {name!r}：{names}")


def _enable_files_page(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """把文件页的 WSL 探测与 UNC 根钉到临时目录。"""
    from types import SimpleNamespace

    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro("Ubuntu", "running")))
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    monkeypatch.setattr(_wslmanw_qt._files, "pick_unc_root", lambda name: str(root))

    class _Runner:
        def run(
            self,
            argv: Any,
            *,
            timeout: float | None = None,
            env: Any = None,
            stdin: bytes | None = None,
        ) -> CommandResult:
            return CommandResult(
                argv=tuple(argv),
                returncode=0,
                stdout=b"WSLFILES_USER=me\nWSLFILES_HOME=/home/me\n",
                stderr=b"",
                duration_ms=1,
            )

    monkeypatch.setattr(cli, "_build_environment", lambda *args, **kwargs: SimpleNamespace(runner=_Runner()))
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")


def test_files_page_browses_and_edits_home(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """默认进家目录；双击进入子目录、上级返回；新建 / 上传 / 下载 / 重命名 / 删除可用。"""
    root = tmp_path / "unc"
    home_dir = root / "home" / "me"
    home_dir.mkdir(parents=True)
    (home_dir / "docs").mkdir()
    (home_dir / "notes.txt").write_text("hello", encoding="utf-8")
    source = tmp_path / "local-upload.txt"
    source.write_text("upload", encoding="utf-8")
    downloaded = tmp_path / "downloaded.txt"
    _enable_files_page(monkeypatch, root)

    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("files")
        _wait_files(window)
        assert window.files_distro.currentText() == "Ubuntu"
        assert window.files_path.text() == "~"
        assert [window.files_table.item(row, 0).text() for row in range(2)] == ["docs", "notes.txt"]
        assert window.files_table.item(0, 1).text() == "文件夹"
        assert window.files_table.item(1, 2).text() == "5 B"
        assert window.files_status.fullText().startswith("Ubuntu · me")

        # 双击文件夹进入，再回到上级
        docs_row = _files_row(window, "docs")
        window.files_table.selectRow(docs_row)
        assert window.files_rename.isEnabled() is True
        window._files_open_row(window.files_table.item(docs_row, 0))
        _wait_files(window)
        assert window.files_path.text() == "~/docs"
        window._files_go_up()
        _wait_files(window)
        assert window.files_path.text() == "~"

        # 新建文件夹
        monkeypatch.setattr(_wslmanw_qt.QInputDialog, "getText", lambda *args, **kwargs: ("newdir", True))
        window._files_new_folder()
        _wait_files(window)
        assert (home_dir / "newdir").is_dir()
        assert _files_row(window, "newdir") >= 0

        # 上传
        monkeypatch.setattr(
            _wslmanw_qt.QFileDialog,
            "getOpenFileNames",
            lambda *args, **kwargs: ([str(source)], ""),
        )
        window._files_upload()
        _wait_files(window)
        assert (home_dir / "local-upload.txt").read_text(encoding="utf-8") == "upload"

        # 下载
        notes_row = _files_row(window, "notes.txt")
        window.files_table.selectRow(notes_row)
        monkeypatch.setattr(
            _wslmanw_qt.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(downloaded), ""),
        )
        window._files_download()
        _wait_files(window)
        assert downloaded.read_text(encoding="utf-8") == "hello"

        # 重命名
        notes_row = _files_row(window, "notes.txt")
        window.files_table.selectRow(notes_row)
        monkeypatch.setattr(_wslmanw_qt.QInputDialog, "getText", lambda *args, **kwargs: ("renamed.txt", True))
        window._files_rename()
        _wait_files(window)
        assert (home_dir / "renamed.txt").exists()
        assert not (home_dir / "notes.txt").exists()

        # 删除（确认后递归删除文件夹）
        newdir_row = _files_row(window, "newdir")
        window.files_table.selectRow(newdir_row)
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: True)
        window._files_delete()
        _wait_files(window)
        assert not (home_dir / "newdir").exists()
        assert _files_row(window, "renamed.txt") >= 0
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_reports_unc_failure(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """UNC 不可访问时给可操作提示，表格保持为空，动作按钮不可用。"""
    _enable_files_page(monkeypatch, tmp_path)

    def boom(name: str) -> str:
        raise _wslmanw_qt.runtime_error("无法访问 Ubuntu 的文件系统", hint="确认实例已启动")

    monkeypatch.setattr(_wslmanw_qt._files, "pick_unc_root", boom)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.show_page("files")
        _wait_files(window)
        assert window.files_table.rowCount() == 0
        assert "无法访问" in window.files_status.fullText()
        assert window.files_upload.isEnabled() is False
        assert window.files_mkdir.isEnabled() is False
        assert window.files_download.isEnabled() is False
        assert window.files_open_ide.isEnabled() is False
        assert window.files_open_agent.isEnabled() is False
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


# ---------------------------------------------------------------------- #
# 打开方式：设置卡片 + 文件页「用 IDE / AI Agent 打开当前目录」
# ---------------------------------------------------------------------- #
def _prepare_openers_page(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    probe: tuple[int, bytes] = (0, b"/usr/bin/opencode\n"),
) -> None:
    """文件页探测照旧；另外让「WSL 内启动」拿得到 config 与可记录的 runner。"""
    from types import SimpleNamespace

    _enable_files_page(monkeypatch, root)

    class _Runner:
        def run(
            self,
            argv: Any,
            *,
            timeout: float | None = None,
            env: Any = None,
            stdin: bytes | None = None,
        ) -> CommandResult:
            if any("command -v" in str(part) for part in argv):
                return CommandResult(
                    argv=tuple(argv),
                    returncode=probe[0],
                    stdout=probe[1],
                    stderr=b"" if probe[0] == 0 else b"not found\n",
                    duration_ms=1,
                )
            return CommandResult(
                argv=tuple(argv),
                returncode=0,
                stdout=b"WSLFILES_USER=me\nWSLFILES_HOME=/home/me\n",
                stderr=b"",
                duration_ms=1,
            )

    config = WslmanConfig(path=default_path(), terminal_program="conhost")
    monkeypatch.setattr(
        cli,
        "_build_environment",
        lambda *args, **kwargs: SimpleNamespace(runner=_Runner(), config=config),
    )
    monkeypatch.setattr(cli, "_require_wsl", lambda env: "wsl.exe")


def _open_window(home: Path, monkeypatch: pytest.MonkeyPatch, root: Path) -> _wslmanw_qt.WslmanQtWindow:
    """建窗并进入文件页的项目目录；返回已就绪的窗口。"""
    project = root / "home" / "me" / "proj"
    project.mkdir(parents=True, exist_ok=True)
    window = _wslmanw_qt.WslmanQtWindow()
    _wait_idle(window)
    window.show_page("files")
    _wait_files(window)
    window._files_load("Ubuntu", "/home/me/proj")
    _wait_files(window)
    assert window.files_path.text() == "~/proj"
    return window


def test_settings_openers_card_lists_builtins_and_persists(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """设置页列出内置预设；选中项立即写盘，重启后沿用。"""
    _enable_files_page(monkeypatch, tmp_path)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        ide = window.opener_combos[_open.KIND_IDE]
        assert [ide.itemText(index) for index in range(ide.count())] == [
            "VSCode（内置）",
            "Trae（内置）",
            "TRAE CN（国内版）（内置）",
            "Qoder（内置）",
            "QODER CN（国内版）（内置）",
            "WorkBuddy（内置）",
        ]
        agent = window.opener_combos[_open.KIND_AGENT]
        assert [agent.itemText(index) for index in range(agent.count())] == [
            "PI（内置）",
            "OpenCode（内置）",
            "DeepSeek Harness（内置）",
            "OpenCode（Windows）（内置）",
            "DeepSeek Harness（Windows）（内置）",
        ]
        assert window.opener_tags[_open.KIND_IDE].text() == _open.HOST_LABELS[_open.HOST_WINDOWS]
        assert window.opener_tags[_open.KIND_AGENT].text() == _open.HOST_LABELS[_open.HOST_WSL]

        ide.setCurrentIndex(1)
        saved = wslmanw.load_settings()
        assert saved.ide_opener == "trae"
        assert [item.key for item in saved.ide_openers] == [
            "vscode",
            "trae",
            "trae-cn",
            "qoder",
            "qoder-cn",
            "workbuddy",
        ]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_opens_ide_with_wsl_remote_path(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """VSCode 走 Windows 宿主机：argv 带 ``--remote wsl+<实例>`` 与实例内 POSIX 路径。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root)
    spawned: list[tuple[str, ...]] = []
    monkeypatch.setattr(_wslmanw_qt, "spawn_gui", lambda argv: spawned.append(tuple(argv)) or 1)
    monkeypatch.setattr(_wslmanw_qt.shutil, "which", lambda name: r"C:\tools\code.exe")
    window = _open_window(home, monkeypatch, root)
    try:
        assert window.files_open_ide.isEnabled() is True
        window._files_open_with(_open.KIND_IDE)
        assert spawned == [("code", "--remote", "wsl+Ubuntu", "/home/me/proj")]
        assert "VSCode" in window.files_status.fullText()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_opens_workbuddy_with_unc_path(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """只认 UNC 的 Windows 程序拿到当前目录的 UNC 路径。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root)
    spawned: list[tuple[str, ...]] = []
    monkeypatch.setattr(_wslmanw_qt, "spawn_gui", lambda argv: spawned.append(tuple(argv)) or 1)
    monkeypatch.setattr(_wslmanw_qt.shutil, "which", lambda name: r"C:\tools\workbuddy.exe")
    window = _open_window(home, monkeypatch, root)
    try:
        settings = cast(wslmanw.WslmanwSettings, window.settings)
        cast(Any, window).settings = wslmanw.with_openers(
            settings,
            _open.KIND_IDE,
            wslmanw.openers_for(settings, _open.KIND_IDE),
            "workbuddy",
        )
        window._sync_files_actions()
        window._files_open_with(_open.KIND_IDE)
        expected = str(_wslmanw_qt._files.to_local(str(root), "/home/me/proj"))
        assert spawned == [("workbuddy", expected)]
        # Windows 根（UNC / 盘符）拼出反斜杠路径；非 Windows 的 POSIX 根按本地分隔符拼接，
        # UNC → 反斜杠的转换本身由 test_posix_unc_mapping_round_trip 覆盖。
        if os.name == "nt":
            assert expected.endswith("\\home\\me\\proj")
        else:
            assert expected.endswith("/home/me/proj")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_instance_toolbar_opens_pi_at_default_cwd(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """实例页「用 PI 打开」：固定选内置 PI，并在实例默认目录经终端启动。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root)
    window = _wslmanw_qt.WslmanQtWindow()
    launched: list[Any] = []
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        window.settings = wslmanw.with_terminal_cwd(window.settings, "Ubuntu", "/home/me/proj")

        class _Recorder:
            def launch(self, command: Any, **_kwargs: Any) -> int:
                launched.append(command)
                return 1

        window.launcher = cast(Any, _Recorder())
        window._open_selected_pi()
        _wait_idle(window)
        assert len(launched) == 1
        assert launched[0].argv == (
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--cd",
            "/home/me/proj",
            "--exec",
            "bash",
            "-lc",
            "pi",
        )
        assert "用 PI 打开完成" in window.page_status("distros")
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_reports_missing_windows_tool(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Windows 侧命令不在 PATH 时明确报错，不静默失败。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root)
    monkeypatch.setattr(_wslmanw_qt.shutil, "which", lambda name: None)
    errors: list[Any] = []
    monkeypatch.setattr(_wslmanw_qt.QMessageBox, "critical", lambda *args, **kwargs: errors.append(args))
    window = _open_window(home, monkeypatch, root)
    try:
        window._files_open_with(_open.KIND_IDE)
        assert errors
        assert "没有找到 code" in window.files_status.fullText()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_starts_agent_inside_distro(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """AI Agent 走实例内：先探测命令存在，再用终端 ``--cd 当前目录`` 启动。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root)
    window = _open_window(home, monkeypatch, root)
    try:
        launched: list[Any] = []

        class _Recorder:
            def launch(self, command: Any, **_kwargs: Any) -> int:
                launched.append(command)
                return 1

        window.launcher = cast(Any, _Recorder())
        window._files_open_with(_open.KIND_AGENT)
        _wait_files(window)
        assert len(launched) == 1
        assert launched[0].argv == (
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--cd",
            "/home/me/proj",
            "--exec",
            "bash",
            "-lc",
            "opencode",
        )
        assert "OpenCode" in window.files_status.fullText()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_reports_missing_agent_in_distro(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """实例内命令不存在时给出安装 / 改模板的提示。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root, probe=(127, b""))
    errors: list[Any] = []
    monkeypatch.setattr(_wslmanw_qt.QMessageBox, "critical", lambda *args, **kwargs: errors.append(args))
    window = _open_window(home, monkeypatch, root)
    try:
        window._files_open_with(_open.KIND_AGENT)
        _wait_files(window)
        assert errors
        assert "找不到 opencode" in window.files_status.fullText()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_files_page_prompts_when_no_opener_configured(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """条目被删光后按钮禁用；直接调用时弹提示而不是静默返回。"""
    root = tmp_path / "unc"
    _prepare_openers_page(monkeypatch, root)
    window = _open_window(home, monkeypatch, root)
    try:
        cast(Any, window).settings = wslmanw.with_openers(
            cast(wslmanw.WslmanwSettings, window.settings), _open.KIND_IDE, (), ""
        )
        window._sync_files_actions()
        assert window.files_open_ide.isEnabled() is False
        prompts: list[Any] = []
        monkeypatch.setattr(_wslmanw_qt.QMessageBox, "information", lambda *args, **kwargs: prompts.append(args))
        window._files_open_with(_open.KIND_IDE)
        assert prompts
        assert window.files_open_agent.isEnabled() is True
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_smart_detect_fills_builtins_but_keeps_user_edits(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """检测补全内置条目，手改过的命令保持不变；选择与补全都落盘。"""
    _enable_files_page(monkeypatch, tmp_path)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        settings = cast(wslmanw.WslmanwSettings, window.settings)
        openers = wslmanw.openers_for(settings, _open.KIND_IDE)
        edited = _open.Opener(
            "vscode",
            "VSCode",
            _open.KIND_IDE,
            _open.HOST_WINDOWS,
            'my-code "{dir}"',
            True,
            "note",
            _open.ORIGIN_USER,
        )
        cast(Any, window).settings = wslmanw.with_openers(settings, _open.KIND_IDE, (edited, *openers[1:]), "trae")
        host = {
            "vscode": _open.Detection(
                "vscode", _open.HOST_WINDOWS, r"C:\code.cmd", r'C:\code.cmd --remote wsl+{distro} "{dir}"'
            ),
            "trae": _open.Detection(
                "trae", _open.HOST_WINDOWS, r"C:\trae.exe", r'C:\trae.exe --remote wsl+{distro} "{dir}"'
            ),
        }
        window._apply_detections((host, {}), log=False)

        saved = {
            item.key: item
            for item in wslmanw.openers_for(cast(wslmanw.WslmanwSettings, window.settings), _open.KIND_IDE)
        }
        assert saved["vscode"].command == 'my-code "{dir}"'
        assert saved["vscode"].origin == _open.ORIGIN_USER
        assert saved["trae"].command == r'C:\trae.exe --remote wsl+{distro} "{dir}"'
        assert saved["trae"].origin == _open.ORIGIN_DETECTED
        assert wslmanw.load_settings().ide_opener == "trae"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_smart_detect_tags_reflect_host_and_wsl_state(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """每条标签区分宿主机 / WSL 内是否已安装；两侧都没有时明确「未安装」。"""
    _enable_files_page(monkeypatch, tmp_path)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window._opener_detections["Ubuntu"] = (
            {"vscode": _open.Detection("vscode", _open.HOST_WINDOWS, r"C:\code.cmd", "code")},
            {"opencode": _open.Detection("opencode", _open.HOST_WSL, "/usr/bin/opencode", "opencode")},
        )
        window._reload_opener_combo(_open.KIND_IDE)
        assert window.opener_tags[_open.KIND_IDE].text() == "Windows 宿主机已安装"
        assert r"C:\code.cmd" in window.opener_tags[_open.KIND_IDE].toolTip()
        window._reload_opener_combo(_open.KIND_AGENT)
        assert window.opener_tags[_open.KIND_AGENT].text() == "WSL 实例内已安装"
        assert "/usr/bin/opencode" in window.opener_tags[_open.KIND_AGENT].toolTip()

        settings = cast(wslmanw.WslmanwSettings, window.settings)
        openers = wslmanw.openers_for(settings, _open.KIND_IDE)
        cast(Any, window).settings = wslmanw.with_openers(settings, _open.KIND_IDE, openers, "trae")
        window._reload_opener_combo(_open.KIND_IDE)
        assert window.opener_tags[_open.KIND_IDE].text() == "未安装"

        # 条目的运行位置在宿主机，但只在 WSL 内找到：提示可能改运行位置。
        window._opener_detections["Ubuntu"] = (
            {"vscode": _open.Detection("vscode", _open.HOST_WINDOWS)},
            {"vscode": _open.Detection("vscode", _open.HOST_WSL, "/usr/bin/code", "code")},
        )
        cast(Any, window).settings = wslmanw.with_openers(settings, _open.KIND_IDE, openers, "vscode")
        window._reload_opener_combo(_open.KIND_IDE)
        tag = window.opener_tags[_open.KIND_IDE]
        assert tag.text() == "仅WSL 内已安装"
        assert "可编辑条目切换运行位置" in tag.toolTip()

        # 检测进行中且还没有结果：标签显示「检测中」。
        window._opener_detections.clear()
        window._detecting = True
        window._reload_opener_combo(_open.KIND_IDE)
        assert window.opener_tags[_open.KIND_IDE].text() == "检测中"
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_settings_page_detects_once_per_instance(home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """进设置页触发一次检测；已有该实例缓存时只刷新标签，不重复扫描。"""
    _enable_files_page(monkeypatch, tmp_path)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        calls: list[bool] = []
        monkeypatch.setattr(window, "_detect_openers", lambda force=False: calls.append(force))
        window.show_page("settings")
        assert calls == [False]
        assert window._opener_detections == {}

        empty_host: dict[str, _open.Detection] = {}
        empty_wsl: dict[str, _open.Detection] = {}
        window._opener_detections["Ubuntu"] = (empty_host, empty_wsl)
        window.show_page("settings")
        assert calls == [False]
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def _ssh_snapshot() -> Any:
    """一份可直接回显的 SSH 迁移状态（模拟上次扫描结果）。"""
    return _sshfiles.build_snapshot(
        (_sshfiles.SshFile("id_ed25519", 10, "private"),),
        _sshfiles.SshTarget(dir="/home/me/.ssh", files=(_sshfiles.RemoteFile("id_ed25519", "600", 10),)),
        _sshfiles.SshSource(_sshfiles.SOURCE_WINDOWS, "", r"C:\Users\me\.ssh"),
        stamp=1700000000.0,
    )


def test_ssh_migrate_section_shows_cached_status_without_scanning(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """打开配置直接回显上次扫描状态：不启动实例、不重扫来源。"""
    owner = QWidget()
    snapshot = _ssh_snapshot()
    monkeypatch.setattr(_wslmanw_qt, "submit_daemon", lambda *args, **kwargs: pytest.fail("回显缓存不应扫描"))
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu", "stopped"), status=snapshot)
    try:
        assert section.state_tag.isHidden() is False
        assert section.state_tag.text() == "已完成迁移"
        assert section.state_tag.tone() == "success"
        assert "上次扫描" in section.state_tag.toolTip()
        assert section.summary.fullText() == "Windows 宿主 1 个文件 · 实例内 1 个文件"
        assert section.collapsed_hint.text() == "Windows 宿主 1 个文件"
        assert "上次扫描结果" in section.file_hint.fullText()
        assert "显示上次扫描结果" in section.log.toPlainText()
        # 只回显状态，不恢复文件清单：迁移前必须重新扫描确认冲突与权限。
        assert section.migrate_button.isEnabled() is False
        assert section._boxes == {}
        assert section._target is None
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_ssh_migrate_section_reports_scan_result_for_persisting(home: Path) -> None:
    """每次扫描结束把结果交给回调（窗口据此写进 wslmanw.json）。"""
    owner = QWidget()
    seen: list[Any] = []
    section = _wslmanw_qt._SshMigrateSection(owner, _distro("Ubuntu"), on_scanned=seen.append)
    try:
        source = _sshfiles.SshSource(_sshfiles.SOURCE_WINDOWS, "", "/home/me/.ssh-win")
        files = (_sshfiles.SshFile("config", 5, "config"),)
        target = _sshfiles.SshTarget(dir="/home/me/.ssh", files=())
        section._scan_done((files, target, source))
        assert len(seen) == 1
        snapshot = seen[0]
        assert snapshot.state.label == "未迁移"
        assert (snapshot.source_count, snapshot.target_count) == (1, 0)
        assert snapshot.source_path == "/home/me/.ssh-win"
        assert snapshot.target_summary
    finally:
        section.deleteLater()
        owner.deleteLater()


def test_ssh_status_cache_survives_other_settings_saves(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SSH 迁移状态写盘后不被其它设置保存冲掉（回归：白名单式构造会丢缓存字段）。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        snapshot = _ssh_snapshot()
        window._save_ssh_status("Ubuntu", snapshot)
        window._save_env_status("Ubuntu", _initenv.EnvStatus(python="3.12.3"))
        window._persist_settings(window.geometry_text())

        saved = wslmanw.load_settings()
        assert wslmanw.ssh_status_for(saved, "Ubuntu") == snapshot
        assert wslmanw.ssh_status_for(saved, "Debian") is None
        cached_env = wslmanw.env_status_for(saved, "Ubuntu")
        assert cached_env is not None and cached_env.python == "3.12.3"

        # 打开配置对话框时直接回显缓存，不发起任何扫描。
        monkeypatch.setattr(_wslmanw_qt, "submit_daemon", lambda *args, **kwargs: pytest.fail("回显缓存不应扫描"))
        dialog = _wslmanw_qt._DistroConfigDialog(window, _distro("Ubuntu", "stopped"), "~")
        try:
            assert dialog.ssh_migrate.state_tag.text() == "已完成迁移"
            assert dialog.ssh_migrate.state_tag.isHidden() is False
        finally:
            dialog.deleteLater()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_filter_chips_have_hints_and_no_redundant_pair(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """筛选项精简为四个互不重叠的口径，每个都在 tooltip 里写清含义。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        labels = [label for _key, label in wslmanw.IMAGE_FILTERS]
        assert labels == ["全部", "可下载", "已安装", "已备份"]
        assert "仅安装" not in labels
        assert set(window.image_filter_buttons) == {"all", "download", "installed", "backed_up"}
        for key, chip in window.image_filter_buttons.items():
            assert chip.toolTip() == wslmanw.IMAGE_FILTER_HINTS[key]
            assert chip.toolTip()
        assert "备份目录" in window.image_filter_buttons["backed_up"].toolTip()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_image_row_tags_explain_installed_and_backed_up(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行内「已安装 / 已备份」胶囊要有 tooltip，说明判定来源（备份目录里的导出包）。"""
    monkeypatch.setattr(cli, "_view", lambda env: _view(_distro()))
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        entry = replace(_image_entry("Ubuntu"), installed=True, backed_up=True)
        window._apply_images((_catalog((entry,)), (entry,)))
        row = _image_row(window, 0)
        tags = [tag for tag in row.header.findChildren(_wslmanw_qt.StatusTag) if tag.isVisibleTo(row)]
        texts = {tag.text(): tag.toolTip() for tag in tags}
        assert "备份目录" in texts["已备份"]
        assert "WSL 实例" in texts["已安装"]
        # 详情区也写清两个胶囊的含义。
        window.distro_list.setCurrentRow(0) if window.distro_list.count() else None
        window.focus_row("images", row)
        hints = " ".join(label.text() for label in window.image_detail_view.findChildren(_wslmanw_qt.QLabel))
        assert "已备份" in hints
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_backup_action_exports_to_backup_dir(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """实例页「备份」：直接导出到备份目录的 <实例>.tar，供镜像页「已备份」识别。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    monkeypatch.setattr(_wslmanw_qt, "distro_export_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        distro = view.find("Ubuntu")
        assert distro is not None
        backup = window._backup_dir_path() / "Ubuntu.tar"
        started: list[tuple[str, Any]] = []

        monkeypatch.setattr(cli, "_require_target", lambda env, name: distro)
        monkeypatch.setattr(cli, "_guard_running_export", lambda *args, **kwargs: None)
        monkeypatch.setattr(cli, "_preflight_for", lambda *args, **kwargs: SimpleNamespace(ok=True, detail=""))
        monkeypatch.setattr(cli, "_export_parts", lambda path, mode: (None,))

        def fake_stream(env: Any, name: str, path: Any, parts: Any, **kwargs: Any) -> tuple[int, str]:
            target = Path(str(path))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"tar")
            return 3, "deadbeef"

        monkeypatch.setattr(cli, "_export_stream", fake_stream)
        monkeypatch.setattr(window, "_start", lambda label, work, **kwargs: started.append((label, work)))

        window.distro_list.setCurrentRow(0)
        assert window._button_by_text["备份"].toolTip()
        window._backup_selected()
        assert [label for label, _work in started] == ["备份"]
        message = started[0][1](threading.Event())
        assert backup.read_bytes() == b"tar"
        assert "已备份" in message
        assert str(backup) in message
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_backup_action_keeps_existing_file_unless_confirmed(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """备份文件已存在时先确认；取消则不发起任何任务。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt, "distro_disk_bytes", lambda *args: (2048, False))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        window.distro_list.setCurrentRow(0)
        window._backup_dir_path().mkdir(parents=True, exist_ok=True)
        (window._backup_dir_path() / "Ubuntu.tar").write_bytes(b"old")
        started: list[str] = []
        monkeypatch.setattr(window, "_start", lambda label, work, **kwargs: started.append(label))
        monkeypatch.setattr(_wslmanw_qt, "confirm", lambda *args, **kwargs: False)
        window._backup_selected()
        assert started == []
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_instance_and_image_backup_tags_use_gui_backup_dir(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「已备份」以 GUI 工作目录的 backup 子目录为准：实例行有标签，镜像页也能命中。"""
    view = _view(_distro("Ubuntu", "stopped"))
    monkeypatch.setattr(cli, "_view", lambda env: view)
    monkeypatch.setattr(_wslmanw_qt.WslmanQtWindow, "_load_images", lambda self, force=False: None)
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        backup_dir = window._backup_dir_path()
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "Ubuntu.tar").write_bytes(b"tar")

        assert window._local_backup_names() == ("ubuntu",)
        window._load(threading.Event())
        window._refresh_rows()
        row = window.distro_list.itemWidget(window.distro_list.item(0))
        assert isinstance(row, _wslmanw_qt._DistroItemRow)
        assert row.backup_tag.isHidden() is False
        assert row.backup_tag.text() == "已备份"
        assert "备份" in row.backup_tag.toolTip()

        entry = _image_entry("Ubuntu")
        marked = _wslmanw_qt._images.mark_local_state((entry,), installed=(), backups=window._local_backup_names())
        assert marked[0].backed_up is True
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_copy_dialog_auto_defaults_name_and_target(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """复制对话框默认名 <源>-copy、默认目录 <工作目录>/install/<新名称>，改名时目录跟随。"""
    source = _distro("Ubuntu", "stopped")
    monkeypatch.setattr(cli, "_view", lambda env: _view(source))
    window = _wslmanw_qt.WslmanQtWindow()
    try:
        _wait_idle(window)
        dialog = _wslmanw_qt._CopyDistroDialog(window, source, (2048, False), ("Ubuntu",))
        assert dialog.name.text() == "Ubuntu-copy"
        assert Path(dialog.target.text()) == home / "wsl" / "install" / "Ubuntu-copy"
        dialog.name.setText("Ubuntu-clone")
        assert Path(dialog.target.text()) == home / "wsl" / "install" / "Ubuntu-clone"
        dialog._start()
        assert dialog.payload() == ("Ubuntu-clone", home / "wsl" / "install" / "Ubuntu-clone")
        dialog.deleteLater()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_copy_worker_exports_and_imports_without_touching_source(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """复制成功：导出源实例 → 导入新名称到目标目录；不删除源实例，临时包清理。"""
    window, first, second, distro_item = _two_root_window(home, monkeypatch, tmp_path)
    target = second / "install" / "Ubuntu-copy"
    temp = target.parent / ".Ubuntu-copy-copy.tar"
    commands: list[list[str]] = []
    imported = replace(distro_item, name="Ubuntu-copy", base_path=str(target))
    try:
        monkeypatch.setattr(cli, "_require_target", lambda env, name: distro_item)
        monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
        monkeypatch.setattr(
            cli, "_preflight_for", lambda env, root, need, estimated=False: SimpleNamespace(ok=True, detail="")
        )
        monkeypatch.setattr(cli, "part_path", lambda path: Path(f"{path}.part"))
        monkeypatch.setattr(cli, "atomic_replace", lambda part, path: Path(path).write_text("tar", encoding="utf-8"))
        monkeypatch.setattr(_wslmanw_qt, "distro_export_bytes", lambda *args: (512, False))
        monkeypatch.setattr(cli, "_try_view", lambda env: _view(imported))

        def run_step(env: Any, args: list[str], **kwargs: Any) -> None:
            commands.append(list(args))
            if args[0] == "--export":
                Path(args[2]).write_text("tar", encoding="utf-8")

        monkeypatch.setattr(cli, "_run_step", run_step)
        message = window._copy_worker(distro_item, "Ubuntu-copy", target, False)
        assert commands[0][:2] == ["--export", "Ubuntu"]
        assert commands[1] == ["--import", "Ubuntu-copy", str(target), str(temp), "--version", "2"]
        assert not any(args[0] == "--unregister" for args in commands)
        assert str(target) in message and "已复制" in message
        assert not temp.exists()
        assert first.exists()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()


def test_copy_worker_cleans_new_instance_when_import_fails(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """导入失败只回收新建实例与目标目录；源实例不受影响。"""
    window, first, second, distro_item = _two_root_window(home, monkeypatch, tmp_path)
    target = second / "install" / "Ubuntu-copy"
    commands: list[list[str]] = []
    try:
        monkeypatch.setattr(cli, "_require_target", lambda env, name: distro_item)
        monkeypatch.setattr(cli, "_dir_problem", lambda path: None)
        monkeypatch.setattr(
            cli, "_preflight_for", lambda env, root, need, estimated=False: SimpleNamespace(ok=True, detail="")
        )
        monkeypatch.setattr(cli, "part_path", lambda path: Path(f"{path}.part"))
        monkeypatch.setattr(cli, "atomic_replace", lambda part, path: Path(path).write_text("tar", encoding="utf-8"))
        monkeypatch.setattr(_wslmanw_qt, "distro_export_bytes", lambda *args: (512, False))

        def run_step(env: Any, args: list[str], **kwargs: Any) -> None:
            commands.append(list(args))
            if args[0] == "--export":
                Path(args[2]).write_text("tar", encoding="utf-8")
            if args[0] == "--import":
                Path(args[2]).mkdir(parents=True, exist_ok=True)
                (Path(args[2]) / "ext4.vhdx").write_bytes(b"disk")
                raise _wslmanw_qt.runtime_error("导入失败")

        monkeypatch.setattr(cli, "_run_step", run_step)
        with pytest.raises(WslmanError) as caught:
            window._copy_worker(distro_item, "Ubuntu-copy", target, False)
        assert "复制失败" in str(caught.value)
        assert "源实例未受影响" in (caught.value.hint or "")
        assert commands[1][0] == "--import"
        assert ["--unregister", "Ubuntu-copy"] in commands
        assert not target.exists()
        assert first.exists()
    finally:
        window._poll.stop()  # type: ignore[union-attr]
        window.deleteLater()
