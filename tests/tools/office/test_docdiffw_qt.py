"""docdiffw Qt 窗口的离屏冒烟测试：控件可构建、设置可回读 / 落盘。"""

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
from fcmd.tools.office._docdiffw_qt import DocDiffWindow


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("docdiffw-test")
    return base


def _write_settings(home: Path, payload: dict[str, Any]) -> None:
    path = _guiconfig.config_path("docdiffw")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_window_restores_saved_values(home: Path) -> None:
    """文档路径 / 标题 / 模式 / 显示修订从设置文件回填。"""
    _write_settings(
        home,
        {
            "old_doc": "old.docx",
            "new_doc": "new.docx",
            "output": "out.docx",
            "title": "合同对比",
            "show_changes": False,
            "compare_mode": "revised",
        },
    )
    window = DocDiffWindow()
    try:
        assert window.windowTitle().startswith("文档对比工具")
        assert window._old_row.value() == "old.docx"
        assert window._new_row.value() == "new.docx"
        assert window._output_row.value() == "out.docx"
        assert window._title.text() == "合同对比"
        assert window._compare_mode.currentText() == "revised"
        assert window._show_changes.isChecked() is False
        assert window._old_row.hint.fullText() == str(Path("old.docx"))
    finally:
        window.deleteLater()


def test_close_persists_values_and_geometry(home: Path) -> None:
    """关闭时写回文档 / 标题 / 模式 / 显示修订与 geometry。"""
    window = DocDiffWindow()
    try:
        window._old_row.set_value("a.docx")
        window._new_row.set_value("b.docx")
        window._output_row.set_value("dist/c.docx")
        window._title.setText("周报")
        window._compare_mode.setCurrentText("revised")
        window._show_changes.setChecked(False)
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("docdiffw").read_text(encoding="utf-8"))
    assert raw["old_doc"] == "a.docx"
    assert raw["new_doc"] == "b.docx"
    assert raw["output"] == "dist/c.docx"
    assert raw["title"] == "周报"
    assert raw["compare_mode"] == "revised"
    assert raw["show_changes"] is False
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_invalid_compare_mode_falls_back_on_close(home: Path) -> None:
    """下拉框被写入未知模式时，关闭仍回落 original（不放宽设置校验）。"""
    window = DocDiffWindow()
    try:
        window._compare_mode.setCurrentText("ultra")
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("docdiffw").read_text(encoding="utf-8"))
    assert raw["compare_mode"] == "original"


def test_long_document_path_is_elided_with_tooltip(home: Path) -> None:
    """超长文档路径中间省略，完整路径保留在 tooltip。"""
    long_path = "C:/Users/demo/Documents/" + "合同/" * 20 + "旧版本.docx"
    _write_settings(home, {"old_doc": long_path})
    expected = str(Path(long_path))
    window = DocDiffWindow()
    try:
        window.show()
        window._old_row.hint.setFixedWidth(160)
        window._old_row.hint._refresh()
        assert window._old_row.hint.text() != expected
        assert "…" in window._old_row.hint.text()
        assert window._old_row.hint.toolTip() == expected
    finally:
        window.deleteLater()
