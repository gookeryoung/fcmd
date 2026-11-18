"""regexvalidatew Qt 窗口的离屏冒烟测试：实时校验 / 示例 / 历史 / 设置。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QPlainTextEdit

from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import create_application
from fcmd.tools.text._regexvalidatew_qt import RegexValidateWindow


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    create_application("regexvalidatew-test")
    return base


def test_window_builds_with_empty_pattern(home: Path) -> None:
    """空正则时提示输入，结果区只读且不换行。"""
    window = RegexValidateWindow()
    try:
        assert window._status.text() == "待校验"
        assert window._status.tone() == "muted"
        assert window._result.toPlainText() == "请输入正则表达式"
        assert window._result.isReadOnly()
        assert window._result.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
        assert window._history_combo.count() == 1
        assert window._splitter.count() == 2
    finally:
        window.deleteLater()


def test_startup_refresh_from_settings(home: Path) -> None:
    """启动即有输入时立即校验并显示匹配结果。"""
    path = _guiconfig.config_path("regexvalidate")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pattern": r"\d+", "test_string": "a1b22"}), encoding="utf-8")
    window = RegexValidateWindow()
    try:
        assert window._pattern.text() == r"\d+"
        assert window._status.text() == "✓ 匹配"
        assert window._status.tone() == "success"
        assert "1" in window._result.toPlainText()
    finally:
        window.deleteLater()


def test_invalid_pattern_shows_danger(home: Path) -> None:
    """非法正则显示不匹配状态并输出错误说明。"""
    window = RegexValidateWindow()
    try:
        window._pattern.setText("[")
        assert window._status.tone() == "danger"
        assert window._result.toPlainText()
    finally:
        window.deleteLater()


def test_example_selection_fills_inputs(home: Path) -> None:
    """选择示例后回填正则、文本与标志并校验通过。"""
    window = RegexValidateWindow()
    try:
        window._example_combo.setCurrentIndex(1)
        assert window._pattern.text().startswith("^1[3-9]")
        assert window._text.toPlainText() == "13812345678"
        assert window._status.tone() == "success"
    finally:
        window.deleteLater()


def test_record_history_persists_and_rebuilds_combo(home: Path) -> None:
    """记录历史后下拉可回填，且设置立即落盘。"""
    window = RegexValidateWindow()
    try:
        window._pattern.setText(r"\w+")
        window._text.setPlainText("abc")
        window._record_history()
        assert window._history_combo.count() == 2
        assert window._history_combo.itemText(1).endswith(r"\w+")
        raw = json.loads(_guiconfig.config_path("regexvalidate").read_text(encoding="utf-8"))
        assert raw["history"][0]["pattern"] == r"\w+"
    finally:
        window.deleteLater()


def test_history_selection_restores_inputs(home: Path) -> None:
    """选择历史条目恢复正则、文本与标志。"""
    path = _guiconfig.config_path("regexvalidate")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "history": [
                    {"pattern": "A+", "test_string": "AAA", "ignorecase": True},
                    {"pattern": "b+", "test_string": "bbb"},
                ]
            }
        ),
        encoding="utf-8",
    )
    window = RegexValidateWindow()
    try:
        window._history_combo.setCurrentIndex(1)
        assert window._pattern.text() == "A+"
        assert window._text.toPlainText() == "AAA"
        assert window._ignorecase.isChecked()
        assert window._status.tone() == "success"
    finally:
        window.deleteLater()


def test_help_action_is_in_help_menu(home: Path) -> None:
    """「语法帮助」插在帮助菜单的「关于」之前。"""
    window = RegexValidateWindow()
    try:
        texts = [action.text() for action in window._menu("帮助").actions()]
        assert texts.index("语法帮助") < texts.index("关于")
    finally:
        window.deleteLater()


def test_close_persists_splitter_and_geometry(home: Path) -> None:
    """关闭时写回分栏比例与 geometry。"""
    window = RegexValidateWindow()
    try:
        window.show()
        window._pattern.setText(r"\d")
        window._splitter.setSizes([180, 320])
        expected = window._splitter.sizes()
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(_guiconfig.config_path("regexvalidate").read_text(encoding="utf-8"))
    assert raw["splitter"] == expected
    assert raw["pattern"] == r"\d"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]
