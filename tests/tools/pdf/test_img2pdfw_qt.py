"""img2pdfw Qt 窗口的离屏冒烟测试：控件可构建、设置可回读 / 落盘。"""

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
from fcmd.tools.pdf._img2pdfw_qt import Img2PdfWindow


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("img2pdfw-test")
    return base


def _write_settings(home: Path, payload: dict[str, Any]) -> None:
    path = _guiconfig.config_path("img2pdfw")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_window_restores_saved_values(home: Path) -> None:
    """输入 / 输出 / 页面尺寸 / DPI / EXIF 校正从设置文件回填。"""
    _write_settings(
        home,
        {
            "input_path": "pics",
            "output_path": "out.pdf",
            "page_size": "a4",
            "dpi": 300,
            "normalize": False,
        },
    )
    window = Img2PdfWindow()
    try:
        assert window.windowTitle().startswith("图片转 PDF 转换器")
        assert window._input.text() == "pics"
        assert window._output.text() == "out.pdf"
        assert window._page_size.currentText() == "A4"
        assert window._dpi.value() == 300
        assert window._normalize.isChecked() is False
        assert window._input_hint.fullText() == str(Path("pics"))
    finally:
        window.deleteLater()


def test_close_persists_values_and_geometry(home: Path) -> None:
    """关闭时写回输入 / 输出 / 页面尺寸 / DPI / 校正与 geometry。"""
    window = Img2PdfWindow()
    try:
        window._input.setText("照片")
        window._output.setText("dist/照片.pdf")
        window._page_size.setCurrentText("A3")
        window._dpi.setValue(200)
        window._normalize.setChecked(False)
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("img2pdfw").read_text(encoding="utf-8"))
    assert raw["input_path"] == "照片"
    assert raw["output_path"] == "dist/照片.pdf"
    assert raw["page_size"] == "a3"
    assert raw["dpi"] == 200
    assert raw["normalize"] is False
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_long_paths_are_elided_with_tooltip(home: Path) -> None:
    """输入 / 输出长路径中间省略，完整路径保留在 tooltip。"""
    long_input = "C:/Users/demo/Pictures/" + "album/" * 20 + "cover.png"
    long_output = "C:/Users/demo/Pictures/" + "album/" * 20 + "cover.pdf"
    _write_settings(home, {"input_path": long_input, "output_path": long_output})
    expected_input = str(Path(long_input))
    window = Img2PdfWindow()
    try:
        window.show()
        window._input_hint.setFixedWidth(160)
        window._input_hint._refresh()
        assert "…" in window._input_hint.text()
        assert window._input_hint.toolTip() == expected_input
        assert "…" in window._output_hint.text() or len(window._output_hint.text()) < len(long_output)
    finally:
        window.deleteLater()
