"""pdfcryptw Qt 窗口的离屏冒烟测试：控件可构建、设置可回读 / 落盘。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.gui import config as _guiconfig
from fcmd.gui.app import create_application
from fcmd.tools.pdf._pdfcryptw_qt import PdfCryptWindow


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录，并按需预写设置文件。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("pdfcryptw-test")
    return base


def _write_settings(home: Path, payload: dict[str, Any]) -> None:
    path = _guiconfig.config_path("pdfcryptw")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_window_builds_from_saved_settings(home: Path) -> None:
    """目录 / 线程 / 递归从设置文件回填，密码始终为空。"""
    _write_settings(home, {"directory": "docs", "workers": 8, "recursive": False})
    window = PdfCryptWindow()
    try:
        assert window.windowTitle().startswith("PDF 加密解密")
        assert [action.text() for action in window.menuBar().actions()] == ["文件", "视图", "帮助"]
        assert window._directory.value() == "docs"
        assert window._workers.value() == 8
        assert window._recursive.isChecked() is False
        assert window._password.text() == ""
        assert window._directory_hint.fullText() == "docs"
    finally:
        window.deleteLater()


def test_close_persists_inputs_and_geometry(home: Path) -> None:
    """关闭时把目录 / 线程 / 递归与 geometry 一并写回，且不含密码。"""
    window = PdfCryptWindow()
    try:
        window._directory.set_value("PDF 文件")
        window._workers.setValue(6)
        window._recursive.setChecked(False)
        window._password.setText("secret")
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("pdfcryptw").read_text(encoding="utf-8"))
    assert raw["directory"] == "PDF 文件"
    assert raw["workers"] == 6
    assert raw["recursive"] is False
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]
    assert "secret" not in json.dumps(raw)


def test_long_directory_is_elided_with_tooltip(home: Path) -> None:
    """超长目录在窄窗口里中间省略，完整路径保留在 tooltip。"""
    long_path = "C:/Users/demo/Projects/fcmd/" + "nested/" * 20 + "pdf"
    _write_settings(home, {"directory": long_path})
    expected = str(Path(long_path))
    window = PdfCryptWindow()
    try:
        window.show()
        window._directory_hint.setFixedWidth(160)
        window._directory_hint._refresh()
        assert window._directory_hint.text() != expected
        assert "…" in window._directory_hint.text()
        assert window._directory_hint.toolTip() == expected
    finally:
        window.deleteLater()
