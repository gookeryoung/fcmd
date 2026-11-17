"""ncmconvertw Qt 窗口的离屏冒烟测试。

覆盖默认值、设置回填、文件列表增删、转换事件流（成功 / 失败 / 中止）、
关闭确认与设置落盘。解密本身由 ``test_cli_ncmconvertw`` 覆盖，这里用
monkeypatch 替换 ``decrypt_ncm`` 只验证界面编排。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application
from fcmd.tools.media import _ncmconvertw_qt
from fcmd.tools.media._ncmconvertw_qt import NcmConvertWindow, _ConvertBridge, _work_convert
from fcmd.tools.media.ncmconvertw import NcmError, config_path


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("ncmconvertw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


def test_window_builds_with_defaults(home: Path) -> None:
    """默认空输出目录、空列表、进度归零。"""
    window = NcmConvertWindow()
    try:
        assert window._output_row.value() == ""
        assert window._output_row.hint.fullText() == _ncmconvertw_qt._OUTPUT_HINT
        assert window._list.count() == 0
        assert window._count_label.text() == "共 0 个文件"
        assert window._progress.value() == 0
        assert window.status_text() == "就绪"
        assert window._list.textElideMode() == Qt.TextElideMode.ElideMiddle
    finally:
        window.deleteLater()


def test_settings_restored(home: Path) -> None:
    """启动时回填输出目录与上次导入目录。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"output_dir": "D:/out", "last_input_dir": "D:/in"}),
        encoding="utf-8",
    )
    window = NcmConvertWindow()
    try:
        assert window._output_row.value() == "D:/out"
        assert window._output_row.hint.fullText().endswith("out")
        assert window._last_dir == "D:/in"
    finally:
        window.deleteLater()


def test_add_paths_filters_and_dedupes(home: Path) -> None:
    """只收 .ncm，重复路径不重复入列；非 ncm 记入日志。"""
    window = NcmConvertWindow()
    try:
        window._add_paths([Path("a.ncm"), Path("a.ncm"), Path("b.txt")])
        assert window._list.count() == 1
        item = window._list.item(0)
        assert item is not None
        assert Path(item.text()) == Path("a.ncm")
        assert window._count_label.text() == "共 1 个文件"
        assert "已添加 1 个文件" in window._log.text()
        assert "已忽略 1 个非 .ncm 文件" in window._log.text()
    finally:
        window.deleteLater()


def test_list_items_keep_full_path_tooltip(home: Path) -> None:
    """列表项显示完整路径，中间省略由视图负责，悬停看全路径。"""
    window = NcmConvertWindow()
    try:
        long_path = Path("C:/very/long/directory/name/for/testing/song.ncm")
        window._add_paths([long_path])
        item = window._list.item(0)
        assert item is not None
        assert item.text() == str(long_path)
        assert item.toolTip() == str(long_path)
    finally:
        window.deleteLater()


def test_remove_and_clear(home: Path) -> None:
    """移除选中与清空同步文件列表与计数。"""
    window = NcmConvertWindow()
    try:
        window._add_paths([Path("a.ncm"), Path("b.ncm"), Path("c.ncm")])
        window._list.item(1).setSelected(True)
        window._remove_selected()
        assert [Path(path).name for path in window._files] == ["a.ncm", "c.ncm"]
        assert window._count_label.text() == "共 2 个文件"
        window._clear_files()
        assert window._files == []
        assert window._count_label.text() == "共 0 个文件"
    finally:
        window.deleteLater()


def test_start_requires_files(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有文件时只提示，不进入运行态。"""
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text, *args: warnings.append(text) or None)
    window = NcmConvertWindow()
    try:
        window._start_conversion()
        assert warnings == ["没有输入的文件"]
        assert not window._running
    finally:
        window.deleteLater()


def test_start_rejects_missing_output_dir(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """输出目录不存在时提示错误。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, text, *args: errors.append(text) or None)
    window = NcmConvertWindow()
    try:
        window._add_paths([Path("a.ncm")])
        window._output_row.set_value(str(tmp_path / "missing"))
        window._start_conversion()
        assert errors == ["输出目录不存在"]
        assert not window._running
    finally:
        window.deleteLater()


def test_worker_success_flow(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """成功事件：进度推进、状态恢复、日志逐文件。"""
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, _title, text, *args: shown.append(text) or None)
    monkeypatch.setattr(
        _ncmconvertw_qt,
        "decrypt_ncm",
        lambda path, output_dir: path.with_suffix(".mp3"),
    )
    window = NcmConvertWindow()
    try:
        window._add_paths([tmp_path / "a.ncm"])
        window._set_busy(True)
        bridge = _ConvertBridge()
        bridge.event.connect(window._on_event)
        _work_convert([tmp_path / "a.ncm"], tmp_path / "out", threading.Event(), bridge)

        assert window._progress.value() == 1
        assert window.status_text() == "✓ 全部完成"
        assert "a.ncm → " in window._log.text()
        assert not window._running
        assert window._convert_button.isEnabled()
        assert shown == ["解锁已完成"]
    finally:
        window.deleteLater()


def test_worker_failure_flow(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """解密失败时状态为「完成：成功 0，失败 1」，并弹出警告。"""
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text, *args: warnings.append(text) or None)

    def _fail(_path: Path, _output_dir: Path | None) -> Path:
        raise NcmError("boom")

    monkeypatch.setattr(_ncmconvertw_qt, "decrypt_ncm", _fail)
    window = NcmConvertWindow()
    try:
        window._set_busy(True)
        bridge = _ConvertBridge()
        bridge.event.connect(window._on_event)
        _work_convert([Path("x.ncm")], None, threading.Event(), bridge)
        assert window.status_text() == "完成：成功 0，失败 1"
        assert "失败：x.ncm：boom" in window._log.text()
        assert warnings and "1 个文件失败" in warnings[0]
    finally:
        window.deleteLater()


def test_worker_cancelled_before_start(home: Path) -> None:
    """预置取消标志时只产出中止事件，不报「全部完成」。"""
    cancel = threading.Event()
    cancel.set()
    window = NcmConvertWindow()
    try:
        bridge = _ConvertBridge()
        bridge.event.connect(window._on_event)
        _work_convert([Path("a.ncm")], None, cancel, bridge)
        assert window.status_text() == "已中止：成功 0，失败 0"
        assert "已中止：完成 0/1" in window._log.text()
    finally:
        window.deleteLater()


def test_busy_disables_inputs(home: Path) -> None:
    """运行期间禁用列表维护与输出目录控件。"""
    window = NcmConvertWindow()
    try:
        window._set_busy(True)
        assert not window._convert_button.isEnabled()
        assert not window._add_files_button.isEnabled()
        assert not window._output_row.edit.isEnabled()
        window._set_busy(False)
        assert window._convert_button.isEnabled()
    finally:
        window.deleteLater()


def test_confirm_close_when_running(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """运行中关闭需确认；确认后置取消标志。"""
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: answers.pop(0))
    window = NcmConvertWindow()
    try:
        window._running = True
        assert window._confirm_close() is False
        assert window._confirm_close() is True
        assert window._cancel.is_set()
    finally:
        window.deleteLater()


def test_close_persists_settings(home: Path) -> None:
    """关闭时写回输出目录、上次导入目录与 geometry。"""
    window = NcmConvertWindow()
    try:
        window.show()
        window._output_row.set_value("D:/out")
        window._last_dir = "D:/in"
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert raw["output_dir"] == "D:/out"
    assert raw["last_input_dir"] == "D:/in"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]
