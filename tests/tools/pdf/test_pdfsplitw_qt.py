"""pdfsplitw Qt 窗口的离屏冒烟测试：模式切换 / 提示 / 设置回写。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.gui import config as _guiconfig
from fcmd.gui.app import create_application
from fcmd.tools.pdf._pdfsplitw_qt import PdfSplitWindow


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("pdfsplitw-test")
    return base


def test_window_builds_with_mode_controls(home: Path) -> None:
    """默认 parts 模式：份数可编辑、页码范围隐藏、路径提示为占位。"""
    window = PdfSplitWindow()
    try:
        assert window._parts.isEnabled()
        assert not window._size.isEnabled()
        assert not window._pages.isEnabled()
        assert window._output_file_row.isHidden()
        assert window._input_hint.fullText() == "未选择"
        assert "输入文件所在目录" in window._output_dir_hint.fullText()
        assert window.status_text() == "就绪"
    finally:
        window.deleteLater()


def test_mode_switch_toggles_controls(home: Path) -> None:
    """切到页码范围模式后显示提取文件行并启用页码输入。"""
    window = PdfSplitWindow()
    try:
        window.show()
        window._mode_buttons["range"].setChecked(True)
        assert window._pages.isEnabled()
        assert not window._parts.isEnabled()
        assert window._output_file_row.isVisible()
        window._mode_buttons["size"].setChecked(True)
        assert window._size.isEnabled()
        assert not window._output_file_row.isVisible()
    finally:
        window.deleteLater()


def test_input_hint_keeps_full_path(home: Path, tmp_path: Path) -> None:
    """长路径在提示标签里保留完整文本（显示层省略、数据不截断）。"""
    long_dir = tmp_path / ("very-long-directory-name-" * 4)
    long_file = long_dir / "sample.pdf"
    window = PdfSplitWindow()
    try:
        window._input_row.set_value(str(long_file))
        assert window._input_hint.fullText() == str(long_file)
        assert window._output_file_row.hint.fullText().endswith("sample_extracted.pdf")
    finally:
        window.deleteLater()


def test_settings_restored_for_range_mode(home: Path) -> None:
    """启动时回填模式、参数与页面路径。"""
    path = _guiconfig.config_path("pdfsplitw")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "input_path": "in.pdf",
                "output_dir": "out",
                "output_path": "picked.pdf",
                "mode": "range",
                "parts": 3,
                "size": 5,
                "pages": "1-3",
                "geometry": "880x700+10+20",
            }
        ),
        encoding="utf-8",
    )
    window = PdfSplitWindow()
    try:
        assert window._mode_buttons["range"].isChecked()
        assert window._pages.text() == "1-3"
        assert window._parts.value() == 3
        assert window._size.value() == 5
        assert window._input_row.value() == "in.pdf"
        assert window._output_file_row.value() == "picked.pdf"
    finally:
        window.deleteLater()


def test_close_persists_mode_and_geometry(home: Path) -> None:
    """关闭时写回模式、参数、路径与 geometry。"""
    window = PdfSplitWindow()
    try:
        window.show()
        window._mode_buttons["range"].setChecked(True)
        window._input_row.set_value("in.pdf")
        window._pages.setText("2-4")
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("pdfsplitw").read_text(encoding="utf-8"))
    assert raw["mode"] == "range"
    assert raw["pages"] == "2-4"
    assert raw["input_path"] == "in.pdf"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]
