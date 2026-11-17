"""checksum Qt 窗口的离屏测试：字符串 / 文件摘要、比较着色与设置落盘。"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application
from fcmd.tools.system._checksum_qt import ChecksumQtWindow
from fcmd.tools.system.checksum import calculate_file_checksum, calculate_string_checksum


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ChecksumQtWindow]:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = create_application("checksum-test")
    _qt_theme.apply_theme(application, _guitheme.resolve_theme("dark-blue"))
    win = ChecksumQtWindow(tmp_path / "checksum.json")
    yield win
    win.deleteLater()


def test_compute_string_default_algorithm(window: ChecksumQtWindow) -> None:
    window.string_edit.setText("hello")
    window.compute_string()
    assert window.result.toPlainText() == calculate_string_checksum("hello", "MD5")


def test_algorithm_selection_flows_into_result(window: ChecksumQtWindow) -> None:
    window._algorithms["SHA256"].setChecked(True)
    window.string_edit.setText("hello")
    window.compute_string()
    assert window.algorithm() == "SHA256"
    assert window.result.toPlainText() == calculate_string_checksum("hello", "SHA256")


def test_compare_pass_and_fail(window: ChecksumQtWindow) -> None:
    expected = calculate_string_checksum("hello", "MD5")
    window.compare_enabled.setChecked(True)
    window.compare_edit.setText(expected.upper())
    window.string_edit.setText("hello")
    window.compute_string()
    assert "校验通过" in window.result.toPlainText()

    window.compare_edit.setText("0" * len(expected))
    window.compute_string()
    assert "校验失败" in window.result.toPlainText()


def test_compare_requires_value(window: ChecksumQtWindow) -> None:
    window.compare_enabled.setChecked(True)
    window.string_edit.setText("hello")
    window.compute_string()
    assert window.result.toPlainText() == "请输入比较值"


def test_compute_file(window: ChecksumQtWindow, tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"abc")
    window.file_edit.setText(str(target))
    window.compute_file()
    assert window.result.toPlainText() == calculate_file_checksum(target, "MD5")


def test_missing_file_and_empty_string(window: ChecksumQtWindow) -> None:
    window.compute_file()
    assert window.result.toPlainText() == "请选择有效文件"
    window.compute_string()
    assert window.result.toPlainText() == "请输入字符串"


def test_settings_persisted(window: ChecksumQtWindow, tmp_path: Path) -> None:
    window._algorithms["Blake2b"].setChecked(True)
    window.compare_enabled.setChecked(True)
    window._persist_settings("720x480+10+20")
    raw = json.loads((tmp_path / "checksum.json").read_text(encoding="utf-8"))
    assert raw["algorithm"] == "Blake2b"
    assert raw["compare"] is True
    assert raw["geometry"] == "720x480+10+20"
