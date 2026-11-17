"""img2icow Qt 窗口的离屏冒烟测试：控件可构建、设置可回读 / 落盘。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import create_application
from fcmd.tools.media._img2icow_qt import Img2IcoWindow


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("img2icow-test")
    return base


def _write_settings(home: Path, payload: dict[str, Any]) -> None:
    path = _guiconfig.config_path("img2icow")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_window_restores_saved_inputs_and_options(home: Path) -> None:
    """输入 / 输出 / 尺寸 / 质量从设置文件回填。"""
    _write_settings(
        home,
        {"input_path": "a.png", "output_path": "out.ico", "sizes": "16,32", "quality": "low"},
    )
    window = Img2IcoWindow()
    try:
        assert window.windowTitle().startswith("图像转 ICO 转换器")
        assert window._input.text() == "a.png"
        assert window._output.text() == "out.ico"
        assert window._sizes.text() == "16,32"
        assert window._quality.currentText() == "low"
        assert window._input_hint.fullText() == str(Path("a.png"))
    finally:
        window.deleteLater()


def test_close_persists_inputs_and_geometry(home: Path) -> None:
    """关闭时写回输入 / 输出 / 尺寸 / 质量与 geometry。"""
    window = Img2IcoWindow()
    try:
        window._input.setText("icons")
        window._output.setText("dist/icon.ico")
        window._sizes.setText("32,64")
        window._quality.setCurrentText("medium")
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("img2icow").read_text(encoding="utf-8"))
    assert raw["input_path"] == "icons"
    assert raw["output_path"] == "dist/icon.ico"
    assert raw["sizes"] == "32,64"
    assert raw["quality"] == "medium"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_long_input_is_elided_with_tooltip(home: Path) -> None:
    """超长输入路径中间省略，完整路径保留在 tooltip。"""
    long_path = "C:/Users/demo/Projects/fcmd/" + "assets/" * 20 + "logo.svg"
    _write_settings(home, {"input_path": long_path})
    expected = str(Path(long_path))
    window = Img2IcoWindow()
    try:
        window.show()
        window._input_hint.setFixedWidth(160)
        window._input_hint._refresh()
        assert window._input_hint.text() != expected
        assert "…" in window._input_hint.text()
        assert window._input_hint.toolTip() == expected
    finally:
        window.deleteLater()
