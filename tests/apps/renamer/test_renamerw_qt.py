"""renamerw 的 Qt 离屏测试。

覆盖默认布局、设置回填与落盘、目标添加 / 目录展开、规则表单显隐、规则增删改
与排序、预览渲染与状态色、执行 / 撤销、规则文件导入导出、帮助窗口与主题切换。
真实重命名语义由 ``test_renamerw`` 与 CLI 测试覆盖，这里只验证界面编排。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from fcmd.apps.renamer import _examples
from fcmd.apps.renamer._core import RenameRule
from fcmd.apps.renamer._renamerw_qt import RenamerWindow
from fcmd.apps.renamer.renamerw import RenamerSettings, load_settings, rules_to_text, save_settings
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("renamerw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


@pytest.fixture()
def window(home: Path) -> Any:
    """构建默认窗口，测试结束关闭。"""
    win = RenamerWindow()
    yield win
    win.close()


def _make_files(tmp_path: Path, *names: str) -> list[Path]:
    paths = []
    for name in names:
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    return paths


def _settings_for(targets: list[Path], rules: list[RenameRule]) -> RenamerSettings:
    return RenamerSettings(targets=[str(path) for path in targets], rules_json=rules_to_text(rules))


def test_window_defaults(window: Any) -> None:
    assert window._file_table.columnCount() == 3
    assert window._rule_table.columnCount() == 4
    assert window._file_table.rowCount() == 0
    assert window.status_text() == "请先添加文件或目录"
    assert window._show_old_check.isChecked()
    assert window._recursive_check.isChecked() is False


def test_restores_saved_targets_and_rules(home: Path, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt", "b.txt")
    path = home / "ui" / "renamerw.json"
    save_settings(_settings_for(files, [RenameRule(pattern="^a", replacement="A")]), path)

    win = RenamerWindow(path)
    try:
        assert len(win._selected) == 2
        assert len(win._rules) == 1
        assert win._file_table.rowCount() == 2
        assert "可执行 1" in win.status_text()
    finally:
        win.close()


def test_persist_on_close(home: Path, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt")
    path = tmp_path / "settings.json"
    win = RenamerWindow(path)
    win._append_targets(files)
    win._pattern_edit.setText("^a")
    win._replacement_edit.setText("A")
    win._add_rule()

    assert win.close()

    settings = load_settings(path)
    assert settings.targets == [str(files[0])]
    assert settings.rules_json == rules_to_text([RenameRule(pattern="^a", replacement="A")])
    assert settings.geometry


def test_add_files_dedupes(monkeypatch: pytest.MonkeyPatch, window: Any, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt", "b.txt")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        staticmethod(lambda *args, **kwargs: ([str(files[0]), str(files[0]), str(files[1])], "")),
    )
    window._add_files()
    assert [str(path) for path in window._selected] == [str(files[0]), str(files[1])]
    assert window._file_table.rowCount() == 2


def test_add_dir_expands_recursively(monkeypatch: pytest.MonkeyPatch, window: Any, tmp_path: Path) -> None:
    nested = tmp_path / "dir" / "sub"
    nested.mkdir(parents=True)
    (tmp_path / "dir" / "one.txt").write_text("x", encoding="utf-8")
    (nested / "two.txt").write_text("x", encoding="utf-8")
    window._recursive_check.setChecked(True)
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *args, **kwargs: str(tmp_path / "dir")),
    )
    window._add_dir()
    assert sorted(path.name for path in window._selected) == ["one.txt", "two.txt"]


def test_rule_form_switches_fields(window: Any) -> None:
    assert not window._occurrence_spin.isHidden()
    assert window._mode_combo.isHidden()
    assert window._text_edit.isHidden()

    window._kind_combo.setCurrentText("计数器")
    assert not window._start_spin.isHidden()
    assert window._pattern_edit.isHidden()
    assert not window._text_edit.isHidden()
    assert window._anchor_combo.count() == 2

    window._kind_combo.setCurrentText("插入")
    assert not window._anchor_text_edit.isHidden()
    assert not window._text_edit.isHidden()
    assert window._start_spin.isHidden()


def test_anchor_states_follow_selection(window: Any) -> None:
    window._kind_combo.setCurrentText("搬移")
    window._anchor_combo.setCurrentText("指定位置")
    assert not window._anchor_text_edit.isEnabled()
    assert window._position_spin.isEnabled()
    window._anchor_combo.setCurrentText("锚点前")
    assert window._anchor_text_edit.isEnabled()
    assert not window._position_spin.isEnabled()


def test_rule_from_form_uses_defaults_for_inactive_fields(window: Any) -> None:
    window._kind_combo.setCurrentText("大小写")
    window._occurrence_spin.setValue(5)
    window._start_spin.setValue(9)
    window._digits_spin.setValue(6)
    rule = window._rule_from_form()
    assert rule.occurrence == 0
    assert rule.start == 1
    assert rule.step == 1
    assert rule.digits == 3
    assert rule.mode == "lower"


def test_add_update_remove_and_move_rules(window: Any) -> None:
    window._pattern_edit.setText("^a")
    window._replacement_edit.setText("A")
    window._add_rule()
    window._pattern_edit.setText("b")
    window._replacement_edit.setText("B")
    window._add_rule()
    assert [rule.pattern for rule in window._rules] == ["^a", "b"]
    assert window._rule_table.rowCount() == 2

    window._rule_table.setCurrentCell(0, 0)
    window._load_selected_rule()
    assert window._pattern_edit.text() == "^a"
    window._replacement_edit.setText("AA")
    window._update_rule()
    assert window._rules[0].replacement == "AA"

    window._move_rule(1)
    assert [rule.pattern for rule in window._rules] == ["b", "^a"]

    window._rule_table.setCurrentCell(1, 0)
    window._remove_rule()
    assert [rule.pattern for rule in window._rules] == ["b"]


def test_invalid_rule_shows_error(monkeypatch: pytest.MonkeyPatch, window: Any) -> None:
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *args, **kwargs: shown.append(args[2])))
    window._add_rule()
    assert shown and "pattern" in shown[0]
    assert window._rules == []


def test_preview_marks_statuses_and_colors(window: Any, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt", "zzz.txt")
    window._append_targets(files)
    window._pattern_edit.setText("^a")
    window._replacement_edit.setText("A")
    window._add_rule()

    assert window._file_table.rowCount() == 2
    statuses = [window._file_table.item(row, 2).text() for row in range(2)]
    assert statuses == ["可执行", "未变化"]
    assert window._file_table.item(1, 2).data(Qt.ItemDataRole.ForegroundRole) is not None
    assert window._file_table.item(0, 2).data(Qt.ItemDataRole.ForegroundRole) is None


def test_preview_conflict_with_existing_target(window: Any, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt", "b.txt")
    window._append_targets(files)
    window._pattern_edit.setText("a")
    window._replacement_edit.setText("b")
    window._add_rule()
    assert "冲突 1" in window.status_text()


def test_preview_reports_rule_error(window: Any, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt")
    window._append_targets(files)
    window._rules = [RenameRule(pattern="[")]
    window._refresh_preview()
    assert window.status_text().startswith("规则错误：")
    assert window._preview is not None and window._preview.error


def test_execute_cancel_leaves_files(
    monkeypatch: pytest.MonkeyPatch,
    window: Any,
    tmp_path: Path,
) -> None:
    files = _make_files(tmp_path, "a.txt")
    window._append_targets(files)
    window._pattern_edit.setText("a")
    window._replacement_edit.setText("b")
    window._add_rule()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))
    window._execute()
    assert files[0].exists()


def test_execute_then_undo(monkeypatch: pytest.MonkeyPatch, window: Any, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt")
    window._append_targets(files)
    window._pattern_edit.setText("a")
    window._replacement_edit.setText("b")
    window._add_rule()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *args, **kwargs: None))

    window._execute()
    renamed = tmp_path / "b.txt"
    assert renamed.exists()
    assert window._selected == [renamed]
    assert window._last_report is not None and window._last_report.completed

    window._undo()
    assert files[0].exists()
    assert window._selected == [files[0]]
    assert window._last_report is None


def test_execute_without_changes_shows_message(
    monkeypatch: pytest.MonkeyPatch,
    window: Any,
    tmp_path: Path,
) -> None:
    files = _make_files(tmp_path, "a.txt")
    window._append_targets(files)
    window._pattern_edit.setText("zzz")
    window._replacement_edit.setText("x")
    window._add_rule()
    messages: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *args, **kwargs: messages.append(args[2])))
    window._execute()
    assert messages and "没有" in messages[0]


def test_rules_file_roundtrip(monkeypatch: pytest.MonkeyPatch, window: Any, tmp_path: Path) -> None:
    window._rules = [RenameRule(pattern="^a", replacement="A")]
    window._refresh_rule_tree()
    target = tmp_path / "rules.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *args, **kwargs: (str(target), "")))
    window._save_rules_file()
    assert target.exists()

    window._rules.clear()
    window._refresh_rule_tree()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args, **kwargs: (str(target), "")))
    window._load_rules_file()
    assert [rule.pattern for rule in window._rules] == ["^a"]


def test_help_dialog_loads_example(window: Any) -> None:
    window._show_help()
    assert window._help_dialog is not None
    assert window._help_table is not None
    assert window._help_table.rowCount() == len(_examples.EXAMPLES)

    window._help_table.setCurrentCell(0, 0)
    window._load_example_row(window._help_table)
    assert window._rules == list(_examples.EXAMPLES[0].rules)
    window._help_dialog.close()


def test_toggle_old_column(window: Any) -> None:
    window._toggle_old_column(False)
    assert window._file_table.isColumnHidden(0)
    window._toggle_old_column(True)
    assert not window._file_table.isColumnHidden(0)


def test_theme_switch_keeps_preview(window: Any, tmp_path: Path) -> None:
    files = _make_files(tmp_path, "a.txt")
    window._append_targets(files)
    window._pattern_edit.setText("a")
    window._replacement_edit.setText("b")
    window._add_rule()
    window.set_theme(_guitheme.resolve_theme("light"))
    assert window._file_table.rowCount() == 1


def test_help_menu_entries(window: Any) -> None:
    menus = {action.text(): action.menu() for action in window.menuBar().actions()}
    help_menu = menus["帮助"]
    assert help_menu is not None
    texts = [action.text() for action in help_menu.actions()]
    assert texts[-1] == "关于"
    assert {"使用帮助", "载入规则…", "保存规则…"} <= set(texts)
