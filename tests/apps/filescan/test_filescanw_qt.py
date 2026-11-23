"""filescanw Qt 窗口的离屏冒烟测试：默认值、设置回填、白名单与规则操作、扫描。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QTableWidget

from fcmd.apps.filescan import filescanw
from fcmd.apps.filescan._filescanw_qt import FilescanQtWindow
from fcmd.apps.filescan._rules import Matcher, Rule, RuleSet, builtin_ruleset
from fcmd.gui import config as _guiconfig
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = create_application("filescanw-test")
    _qt_theme.apply_theme(application, _guitheme.resolve_theme("dark-blue"))
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    return base


@pytest.fixture()
def sample(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    (root / "notes.txt").write_text("客户电话 13800138000", encoding="utf-8")
    (root / "clean.txt").write_text("nothing here", encoding="utf-8")
    return root


def _text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _window(**kwargs: object) -> FilescanQtWindow:
    window = FilescanQtWindow(**kwargs)  # type: ignore[arg-type]
    return window


def test_window_builds_with_defaults(home: Path) -> None:
    window = _window()
    try:
        # 侧边栏分页与 wslmanw / llmw 同构：扫描 / 结果 / 规则 / 设置，默认停在扫描页。
        assert window.nav.keys() == ("scan", "results", "rules", "settings")
        assert window.nav.selected() == "scan"
        assert window.current_page() == "scan"
        assert window.page_title.text() == "扫描"
        assert window._scan_page.page_key == "scan"
        assert window._result_page.page_key == "results"
        assert window._rules_page.page_key == "rules"
        assert window._settings_page.page_key == "settings"
        assert window._whitelist_table.rowCount() >= 1
        assert window._rules_table.rowCount() == len(builtin_ruleset().rules)
        assert window._start_button.isEnabled()
        assert not window._cancel_button.isEnabled()
        assert window.status_text().startswith("规则")
    finally:
        window.deleteLater()


def test_settings_and_whitelist_restored(home: Path) -> None:
    path = _guiconfig.config_path(filescanw.SETTINGS_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = filescanw.FilescanSettings(
        paths=("F:/data",),
        recursive=False,
        max_depth=3,
        workers=2,
        whitelist_groups=(("我的类型", (".log", ".txt")),),
        filter_rule="cn_phone",
        ruleset=builtin_ruleset(),
    )
    filescanw.save_settings(settings, path)
    window = _window()
    try:
        assert window._paths == ["F:/data"]
        assert not window._recursive.isChecked()
        assert window._max_depth.value() == 3
        assert window._whitelist_table.rowCount() == 1
        assert _text(window._whitelist_table, 0, 0) == "我的类型"
        assert _text(window._whitelist_table, 0, 1) == ".log,.txt"
        assert window._filter_rule.currentText() == "cn_phone"
    finally:
        window.deleteLater()


def test_whitelist_delete_keeps_single_group(home: Path) -> None:
    window = _window()
    try:
        window._whitelist_table.setRowCount(1)
        from fcmd.gui.widgets import make_table_item

        window._whitelist_table.setItem(0, 0, make_table_item("只有一组"))
        window._whitelist_table.setItem(0, 1, make_table_item(".txt"))
        window._delete_whitelist_row()
        assert window._whitelist_table.rowCount() == 1
    finally:
        window.deleteLater()


def test_rules_enable_and_expression(home: Path) -> None:
    window = _window()
    try:
        window._rules_table.selectRow(0)
        window._set_rules_enabled(False)
        assert window.ruleset.rules[0].enabled is False
        window._rules_table.selectRow(0)
        window._set_rules_enabled(True)
        assert window.ruleset.rules[0].enabled is True
        window._expression.setText("cn_phone or cn_id")
        window._apply_expression()
        assert window.ruleset.expression == "cn_phone or cn_id"
        window._expression.setText("and (")
        window._apply_expression()
        assert window.ruleset.expression == "cn_phone or cn_id"
    finally:
        window.deleteLater()


def test_scan_worker_masks_context(home: Path, sample: Path) -> None:
    from fcmd.apps.filescan._engine import CancelToken, ScanOptions
    from fcmd.apps.filescan._filescanw_qt import _scan_worker, _ScanProgress

    ruleset = RuleSet(
        rules=(
            Rule(
                id="cn_phone",
                name="手机号",
                matcher=Matcher(kind="regex", pattern=r"1\d{10}"),
                group="PII",
                enabled=True,
            ),
        ),
        expression="",
    )
    bridge = _ScanProgress()
    result, note = _scan_worker(
        [sample],
        ruleset,
        ScanOptions(recursive=True),
        CancelToken(),
        bridge,
        use_cache=False,
        mask=True,
    )
    assert note == ""
    assert result.stats.hit_files == 1
    from fcmd.apps.filescan._report import render_context

    rendered = str([render_context(match, False) for hit in result.hits for match in hit.matches])
    assert "13800138000" not in rendered
    assert "*" in rendered


def test_settings_persisted_on_close(home: Path, sample: Path) -> None:
    window = _window()
    window._paths = [str(sample)]
    window._persist_settings("1111x777+11+22")
    window.deleteLater()
    raw = json.loads(_guiconfig.config_path(filescanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["paths"] == [str(sample)]
    assert raw["geometry"] == "1111x777+11+22"


def test_scan_progress_and_result_status_stay_on_their_pages(home: Path, sample: Path) -> None:
    """状态栏按页隔离：切到结果页后，扫描进度仍写在扫描页，结果摘要写结果页。"""
    from fcmd.apps.filescan._engine import ScanResult

    window = _window()
    try:
        window.show_page("results")
        window.set_page_status("results", "结果是空的")
        window._on_progress(3, 10, str(sample))
        assert window.page_status("scan").startswith("正在扫描 3/10")
        assert window.page_status("results") == "结果是空的"

        # 扫描完成自动切到结果页，两页各自留下本次结论。
        window.show_page("scan")
        window._scan_done((ScanResult(roots=(sample,)), ""))
        assert window.current_page() == "results"
        assert "命中" in window.page_status("results")
        assert window.page_status("scan") == window.page_status("results")
    finally:
        window.deleteLater()


def test_sidebar_collapsed_persists(home: Path) -> None:
    """侧边栏折叠状态写进 filescanw.json，旧设置文件（无该字段）仍按展开处理。"""
    window = _window()
    try:
        window.nav.set_collapsed(True)
    finally:
        window.deleteLater()
    saved = filescanw.load_settings()
    assert saved.sidebar_collapsed is True


def test_old_settings_without_sidebar_field_load_unexpanded(home: Path) -> None:
    path = _guiconfig.config_path(filescanw.SETTINGS_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"paths": ["F:/data"]}), encoding="utf-8")
    assert filescanw.load_settings().sidebar_collapsed is False
    window = _window()
    try:
        assert window.nav.is_collapsed() is False
    finally:
        window.deleteLater()


def test_scan_dirs_reuse_shared_storage_card(home: Path, tmp_path: Path) -> None:
    """扫描目录走与 wslmanw「工作目录」/ llmw「存储」同一套 StorageCard。"""
    from fcmd.gui.widgets import DiskTile

    first, second = tmp_path / "scan-a", tmp_path / "scan-b"
    path = _guiconfig.config_path(filescanw.SETTINGS_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    filescanw.save_settings(filescanw.FilescanSettings(paths=(str(first), str(second))), path)
    window = _window()
    try:
        card = window._dirs_card
        assert card.title() == "扫描目录"
        assert card.roots() == [first, second]
        tiles = card.grid.findChildren(DiskTile)
        assert [tile.location.display_text for tile in tiles] == [str(first), str(second)]
        # 扫描没有「主目录」概念：卡片上不显示主目录胶囊，也不出「设为主目录」按钮。
        from PySide6.QtWidgets import QPushButton

        from fcmd.gui.widgets import StatusTag

        assert card.findChildren(StatusTag) == []
        buttons = [child.text() for child in card.findChildren(QPushButton)]
        assert "设为主目录" not in buttons
        assert buttons.count("移除") == 2

        window._on_dirs_changed([Path(str(first))])
        assert window._paths == [str(first)]
        assert "目录 1 个" in window.page_status("scan")

        window._clear_directories()
        assert window._paths == []
        assert card.roots() == []
    finally:
        window.deleteLater()


def test_settings_page_holds_options_whitelist_and_cache(home: Path, sample: Path) -> None:
    """选项 / 白名单 / 缓存配置在设置页；扫描页只留目录、动作与只读摘要。"""
    from fcmd.gui.widgets import Card

    window = _window()
    try:
        settings_titles = [card.title() for card in window._settings_page.findChildren(Card)]
        assert settings_titles == ["扫描范围", "后缀白名单", "缓存与配置"]
        # 关键控件都在设置页里
        for widget in (
            window._recursive,
            window._max_depth,
            window._include_ext,
            window._modified_after,
            window._whitelist_table,
            window._use_cache,
        ):
            assert window._settings_page.isAncestorOf(widget), widget.objectName()

        # 扫描页只有「扫描目录」与「开始扫描」两张卡，且带当前选项摘要。
        scan_titles = [card.title() for card in window._scan_page.findChildren(Card)]
        assert scan_titles == ["扫描目录", "开始扫描"]
        assert window._scan_page.isAncestorOf(window._start_button)
        assert window._scan_page.isAncestorOf(window._progress)
        assert "白名单" in window._options_note.text()

        # 在设置页改了选项，切回扫描页时摘要随之更新。
        window.show_page("settings")
        window._workers.setValue(7)
        window._max_depth.setValue(3)
        window.show_page("scan")
        assert "深度 3" in window._options_note.text()
        assert "并发 7" in window._options_note.text()
    finally:
        window.deleteLater()


def test_settings_page_changes_feed_scan_options(home: Path, sample: Path) -> None:
    """设置页改的值就是 ``_start_scan`` 用的 ScanOptions，不需要额外同步。"""
    window = _window()
    try:
        window.show_page("settings")
        window._recursive.setChecked(False)
        window._follow.setChecked(True)
        window._max_depth.setValue(2)
        window._min_size.setValue(10)
        window._max_size.setValue(2048)
        window._workers.setValue(3)
        window._context_chars.setValue(12)
        window._include_ext.setText(".txt")
        window._use_cache.setChecked(False)
        options = window._scan_options()
        assert options.recursive is False
        assert options.follow_symlinks is True
        assert options.max_depth == 2
        assert options.min_size == 10
        assert options.max_size == 2048
        assert options.workers == 3
        assert options.context_chars == 12
        assert options.include_exts == (".txt",)
        assert window._use_cache.isChecked() is False
        assert window._current_settings().use_cache is False
    finally:
        window.deleteLater()
