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
from fcmd.apps.filescan._engine import FileHit, RuleMatch, ScanResult, ScanStats
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
        # 扫描页只有任务列表：首项固定是虚线的「添加任务」，空态说明在其下方。
        assert window._tasks == []
        assert window._task_list.count() == 1
        assert not window._task_list.isHidden()
        add_row = window._task_list.itemWidget(window._task_list.item(0))
        assert add_row is not None and add_row.objectName() == "ScanTaskAdd"
        assert window._task_empty is not None and not window._task_empty.isHidden()
        assert not hasattr(window, "_start_button")
        assert not hasattr(window, "_dirs_card")
        assert window._result_task_button.text() == "暂无任务结果  ▾"
        assert window._result_task_button.isEnabled() is False
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
        assert [item.path for item in window._tasks] == ["F:/data"]
        assert window._task_list.count() == 2  # 添加任务行 + 1 个任务
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
    task = filescanw.new_scan_task(str(sample))
    window._tasks = [task]
    window._persist_settings("1111x777+11+22")
    window.deleteLater()
    raw = json.loads(_guiconfig.config_path(filescanw.SETTINGS_NAME).read_text(encoding="utf-8"))
    assert raw["paths"] == [str(sample)]
    assert raw["tasks"][0]["id"] == task.id
    assert raw["tasks"][0]["path"] == str(sample)
    assert raw["geometry"] == "1111x777+11+22"


def test_scan_progress_and_result_are_bound_to_task(home: Path, sample: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描进度写任务行与状态栏；完成后结果页标明所属任务，并能从任务再次进入。"""
    from fcmd.apps.filescan._engine import ScanResult, ScanStats

    window = _window()
    try:
        task = filescanw.new_scan_task(str(sample))
        window._tasks = [task]
        window._refresh_task_rows()
        window._scanning_task_id = task.id
        window._worker_running = True
        window._refresh_task_rows()

        window.show_page("results")
        window.set_page_status("results", "结果是空的")
        window._on_progress(3, 10, str(sample))
        assert "正在扫描 3/10" in window.page_status("scan")
        assert "notes" not in window.page_status("results")
        assert window.page_status("results") == "结果是空的"
        assert window.status_bar.loading_bar.is_running()
        row = window._task_row(task.id)
        assert row is not None
        assert row.status_tag.text() == "扫描 30%"

        # 扫描完成后：结果与任务 id 关联，结果页标题胶囊显示任务名。
        result = ScanResult(roots=(sample,), stats=ScanStats(hit_files=1, hit_count=1))
        window._scan_done((result, ""))
        assert window.current_page() == "results"
        assert window._results[task.id] is result
        assert window._viewing_task_id == task.id
        assert window._result_task_button.text() == f"任务：{task.display_name}  ▾"
        assert window._result_task_button.isEnabled() is True
        assert "命中" in window.page_status("results")
        window._scan_finished()
        row = window._task_row(task.id)
        assert row is not None
        assert row.time_tag.text() == window._tasks[0].last_scanned_at
        assert not row.time_tag.isHidden()
        assert row.hit_tag.text() == "命中 1 文件 / 1 条"
        assert row.hit_tag.tone() == "danger"
        assert not row.hit_tag.isHidden()

        # 从任务行再次进入结果页：即使当前停在扫描页也能切回来。
        window.show_page("scan")
        window._show_task_result(task.id)
        assert window.current_page() == "results"
        assert window._result_task_button.text() == f"任务：{task.display_name}  ▾"

        # 第二个任务有结果后，结果页的任务菜单可以在两者之间切换。
        other = filescanw.new_scan_task(str(sample.parent / "data-2"))
        window._tasks.append(other)
        window._results[other.id] = ScanResult(roots=(sample,), stats=ScanStats())
        window._sync_result_switcher()
        menu = window._result_task_menu()
        assert sorted(action.text() for action in menu.actions()) == sorted([task.display_name, other.display_name])
        other_action = next(action for action in menu.actions() if action.text() == other.display_name)
        other_action.trigger()
        assert window._viewing_task_id == other.id
        assert window._result_task_button.text() == f"任务：{other.display_name}  ▾"
        first_action = next(action for action in menu.actions() if action.text() == task.display_name)
        first_action.trigger()
        assert window._viewing_task_id == task.id
    finally:
        window.deleteLater()


def test_scan_results_restore_after_restart(home: Path, sample: Path) -> None:
    """扫描结果落盘到 filescanw-results.json：重开窗口仍能恢复结果树与任务入口。"""
    from fcmd.apps.filescan._engine import CancelToken
    from fcmd.apps.filescan._filescanw_qt import _scan_worker, _ScanProgress

    window = _window()
    try:
        task = filescanw.new_scan_task(str(sample))
        window._tasks = [task]
        window._refresh_task_rows()
        result, _note = _scan_worker(
            [sample],
            window.ruleset,
            window._scan_options(),
            CancelToken(),
            _ScanProgress(),
            use_cache=False,
            mask=True,
        )
        window._scanning_task_id = task.id
        window._worker_running = True
        window._scan_done((result, ""))
        window._scan_finished()
    finally:
        window.deleteLater()

    assert filescanw.results_path().exists()
    restarted = _window()
    try:
        assert task.id in restarted._results
        assert restarted._viewing_task_id == task.id
        assert restarted.result is not None and restarted.result.hits
        assert restarted._tree.topLevelItemCount() >= 1
        assert restarted._result_task_button.text() == f"任务：{task.display_name}  ▾"
        assert restarted.page_status("results").startswith(task.display_name)
        row = restarted._task_row(task.id)
        assert row is not None and row.result_button.isEnabled()
    finally:
        restarted.deleteLater()


def test_remove_task_clears_persisted_result(home: Path, sample: Path) -> None:
    """移除任务时对应结果从结果文件删掉，重开窗口不会恢复。"""
    from fcmd.apps.filescan._engine import ScanResult, ScanStats

    window = _window()
    try:
        task = filescanw.new_scan_task(str(sample))
        window._tasks = [task]
        window._results[task.id] = ScanResult(roots=(sample,), stats=ScanStats(hit_files=1, hit_count=1))
        window._persist_results()
        window._remove_task(task.id)
    finally:
        window.deleteLater()

    restarted = _window()
    try:
        assert restarted._results == {}
        assert restarted._result_task_button.isEnabled() is False
    finally:
        restarted.deleteLater()


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


def test_tasks_load_from_settings_and_rows_are_built(home: Path, tmp_path: Path) -> None:
    """扫描页只按任务建行卡；每个任务有自己的目录、状态与扫描 / 查看结果按钮。"""
    first, second = tmp_path / "scan-a", tmp_path / "scan-b"
    first.mkdir()
    second.mkdir()
    path = _guiconfig.config_path(filescanw.SETTINGS_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    filescanw.save_settings(filescanw.FilescanSettings(paths=(str(first), str(second))), path)
    window = _window()
    try:
        assert [item.path for item in window._tasks] == [str(first), str(second)]
        assert window._task_list.count() == 3  # 添加任务行 + 2 个任务
        add_row = window._task_list.itemWidget(window._task_list.item(0))
        assert add_row is not None and add_row.objectName() == "ScanTaskAdd"
        rows = [
            window._task_list.itemWidget(window._task_list.item(index)) for index in range(1, window._task_list.count())
        ]
        assert [row.task.path for row in rows] == [str(first), str(second)]
        assert all(row.expand_button.isHidden() is False for row in rows)
        assert all(row.result_button.isEnabled() is False for row in rows)
        assert all(not hasattr(row, "dir_button") for row in rows)
        assert all(not hasattr(row, "remove_button") for row in rows)
        # 扫描按钮是第一个动作且加宽到两倍；扫描时间暂无，标签收起。
        first_row = rows[0]
        assert first_row.header_row.indexOf(first_row.scan_button) < first_row.header_row.indexOf(
            first_row.result_button
        )
        assert first_row.scan_button.minimumWidth() >= 68
        assert first_row.note_row.indexOf(first_row.time_tag) == first_row.note_row.indexOf(first_row.sub_label) + 1
        assert first_row.time_tag.isHidden()
        assert first_row.hit_tag.isHidden()
        assert window._remove_task_button.isEnabled() is True
        assert window._clear_tasks_button.isEnabled() is True
        assert "扫描任务 2 个" in window.page_status("scan")

        # 展开 / 收起：详情就地显示完整目录与最近扫描摘要。
        window.toggle_row("tasks", rows[0])
        assert rows[0].is_expanded() is True
        assert window._task_detail.values["目录"].text() == str(first)
        window.toggle_row("tasks", rows[0])
        assert rows[0].is_expanded() is False
    finally:
        window.deleteLater()


def test_add_remove_and_clear_tasks(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务增删 / 清空只动列表与本次会话结果，不碰磁盘目录；全程不弹确认。"""
    from PySide6.QtWidgets import QFileDialog

    from fcmd.apps.filescan import _filescanw_qt as qt_module

    first = tmp_path / "scan-a"
    first.mkdir()
    window = _window()
    try:
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args, **kwargs: str(first))

        def _no_confirm(*_args: object, **_kwargs: object) -> bool:
            pytest.fail("移除 / 清空扫描任务不应弹确认框")

        monkeypatch.setattr(qt_module, "confirm", _no_confirm)

        window._add_task()
        assert [item.path for item in window._tasks] == [str(first)]
        task = window._tasks[0]
        assert window._task_list.count() == 2  # 添加任务行 + 新任务
        assert task.display_name == "scan-a"
        assert window._remove_task_button.isEnabled() is True
        assert window._clear_tasks_button.isEnabled() is True

        # 顶部「移除」只移除当前选中任务，不删目录。
        window._remove_selected_task()
        assert window._tasks == []
        assert window._task_list.count() == 1
        assert window._task_empty is not None and not window._task_empty.isHidden()
        assert window._remove_task_button.isEnabled() is False
        assert window._clear_tasks_button.isEnabled() is False
        assert first.is_dir()

        # 再加一个任务后从顶部「清空」一次移除全部。
        window._add_task()
        assert len(window._tasks) == 1
        window._clear_tasks()
        assert window._tasks == []
        assert window._task_list.count() == 1
        assert first.is_dir()
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

        # 扫描页只剩「扫描任务」一张卡（列表 + 选项标签 + 添加任务）。
        scan_titles = [card.title() for card in window._scan_page.findChildren(Card)]
        assert scan_titles == ["扫描任务"]
        assert window._scan_page.isAncestorOf(window._task_list)
        assert window._scan_page.isAncestorOf(window._option_tags_host)
        from fcmd.gui.widgets import ProgressBar

        assert window._scan_page.findChildren(ProgressBar) == []
        assert "白名单" in window._option_tags["whitelist"].text()
        assert window._option_tags["whitelist"].tone() == "accent"
        assert window._option_tags["recursive"].tone() == "success"

        # 在设置页改了选项，切回扫描页时标签随之更新（含语义色）。
        window.show_page("settings")
        window._workers.setValue(7)
        window._max_depth.setValue(3)
        window._recursive.setChecked(False)
        window._use_cache.setChecked(False)
        window.show_page("scan")
        assert "深度 3" in window._option_tags["depth"].text()
        assert "并发 7" in window._option_tags["workers"].text()
        assert window._option_tags["recursive"].text() == "不递归"
        assert window._option_tags["recursive"].tone() == "muted"
        assert window._option_tags["cache"].tone() == "muted"
    finally:
        window.deleteLater()


def test_task_scan_button_toggles_and_stops(home: Path, sample: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务行内扫描按钮：空闲扫描，扫描中变停止；停止请求下发后按钮禁用，收尾后恢复。"""
    from fcmd.apps.filescan import _filescanw_qt as qt_module

    window = _window()
    try:
        task = filescanw.new_scan_task(str(sample))
        window._tasks = [task]
        window._refresh_task_rows()
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(qt_module, "submit", lambda *args, **kwargs: calls.append(kwargs))

        window._start_task_scan(task.id)
        assert calls, "扫描应提交到线程池"
        assert window._worker_running is True
        assert window._scanning_task_id == task.id
        row = window._task_row(task.id)
        assert row is not None
        assert row.scan_button.objectName() == "Danger"
        assert row.result_button.isEnabled() is False
        assert window._remove_task_button.isEnabled() is False

        window._cancel_task_scan(task.id)
        assert window._token is not None and window._token.cancelled
        assert window._stopping_task_id == task.id
        row = window._task_row(task.id)
        assert row is not None
        assert row.scan_button.isEnabled() is False

        window._scan_finished()
        row = window._task_row(task.id)
        assert row is not None
        assert window._worker_running is False
        assert row.scan_button.objectName() == "Primary"
        assert row.scan_button.isEnabled() is True
        assert window._remove_task_button.isEnabled() is True
    finally:
        window.deleteLater()


def test_settings_page_changes_feed_scan_options(home: Path, sample: Path) -> None:
    """设置页改的值就是 ``_start_task_scan`` 用的 ScanOptions，不需要额外同步。"""
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


def test_result_context_is_fully_shown_and_match_is_highlighted(home: Path) -> None:
    """结果树上下文列：单元格保留完整（打码）上下文，命中段单独上底色并随列宽换行。"""
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QStyleOptionViewItem

    from fcmd.apps.filescan._engine import FileHit, RuleMatch, ScanResult, ScanStats
    from fcmd.apps.filescan._filescanw_qt import _CONTEXT_ROLE
    from fcmd.apps.filescan._report import render_context

    long_context = "客户联系电话 13800138000 请勿外传，仅限内部核对使用"
    match = RuleMatch(
        rule_id="cn_phone",
        rule_name="手机号",
        line_no=3,
        start=7,
        end=18,
        excerpt="13800138000",
        context=long_context,
        context_start=7,
    )
    result = ScanResult(
        roots=(Path("sample"),),
        stats=ScanStats(hit_files=1, hit_count=1),
        hits=[FileHit(path=Path("sample/notes.txt"), size=120, encoding="utf-8", matches=[match])],
    )
    window = _window()
    try:
        window._render_result(result)
        child = window._tree.topLevelItem(0).child(0)
        assert child.text(2) == render_context(match, False)
        masked = "13" + "*" * 7 + "00"
        assert child.text(2) == long_context.replace("13800138000", masked)
        assert child.data(2, _CONTEXT_ROLE) == ("客户联系电话 ", masked, " 请勿外传，仅限内部核对使用")

        option = QStyleOptionViewItem()
        option.widget = window._tree
        option.font = window._tree.font()
        index = window._tree.indexFromItem(child, 2)
        window._tree.setColumnWidth(2, 120)
        narrow = window._context_delegate.sizeHint(option, index)
        window._tree.setColumnWidth(2, 900)
        wide = window._context_delegate.sizeHint(option, index)
        # 换行后行高随内容增长，不再按固定单行截断。
        assert narrow.height() > wide.height()
        assert wide.height() > window._tree.fontMetrics().height()

        window._tree.setColumnWidth(2, 300)
        document = window._context_delegate._document(index, 292, selected=False, font=window._tree.font())
        assert document is not None
        cursor = QTextCursor(document)
        cursor.setPosition(8)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
        assert cursor.charFormat().background().color().name() == _guitheme.resolve_theme("dark-blue").palette.warning
        cursor.setPosition(0)
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
        assert cursor.charFormat().foreground().color().name() == _guitheme.resolve_theme("dark-blue").palette.text
    finally:
        window.deleteLater()


def _hit_result(
    path: Path,
    excerpt: str = "13800138000",
    context: str = "客户联系电话 13800138000 请勿外传",
) -> tuple[ScanResult, RuleMatch]:
    start = context.index(excerpt)
    match = RuleMatch(
        rule_id="cn_phone",
        rule_name="手机号",
        line_no=3,
        start=start,
        end=start + len(excerpt),
        excerpt=excerpt,
        context=context,
        context_start=start,
    )
    return (
        ScanResult(
            roots=(path.parent,),
            stats=ScanStats(hit_files=1, hit_count=1),
            hits=[FileHit(path=path, size=120, encoding="utf-8", matches=[match])],
        ),
        match,
    )


def test_result_rows_bind_file_and_match_without_duplicate_rule_column(home: Path, tmp_path: Path) -> None:
    """第二列改为命中片段（不再重复规则名），行绑定路径与命中对象供定位 / 复制使用。"""
    from PySide6.QtWidgets import QApplication

    from fcmd.apps.filescan._filescanw_qt import _MATCH_ROLE, _PATH_ROLE
    from fcmd.apps.filescan._report import mask_text

    target = tmp_path / "notes.txt"
    target.write_text("客户联系电话 13800138000 请勿外传", encoding="utf-8")
    result, match = _hit_result(target)
    window = _window()
    try:
        window._render_result(result)
        parent = window._tree.topLevelItem(0)
        child = parent.child(0)
        # 命中内容已在上下文列（带底色）展示，不再单占一列：规则 / 行号 / 上下文 三列。
        assert window._tree.columnCount() == 3
        assert window._tree.headerItem().text(1) == "行号"
        assert child.text(0) == "手机号"
        assert child.text(1) == "3"
        assert child.data(0, _PATH_ROLE) == str(target)
        assert child.data(0, _MATCH_ROLE) is match
        assert window._item_path(child) == target
        assert window._item_matches(child) == [match]
        assert window._item_matches(parent) == [match]

        child.setSelected(True)
        assert window._locate_button.isEnabled() is True
        assert window._copy_match_button.isEnabled() is True
        window._copy_selected_matches()
        assert QApplication.clipboard().text() == mask_text(match.excerpt)
        assert "已打码" in window.page_status("results")

        window._export_raw.setChecked(True)
        window._copy_selected_matches()
        assert QApplication.clipboard().text() == match.excerpt
        window._copy_selected_matches(context=True)
        assert QApplication.clipboard().text() == match.context
        window._copy_selected_paths()
        assert QApplication.clipboard().text() == str(target)

        child.setSelected(False)
        assert window._locate_button.isEnabled() is False
    finally:
        window.deleteLater()


def test_locate_file_opens_file_manager(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定位文件走系统文件管理器命令；双击行同样触发定位。"""
    from fcmd.apps.filescan import _filescanw_qt

    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    result, _match = _hit_result(target)
    calls: list[list[str]] = []

    def fake_popen(command: list[str], **_kwargs: object) -> None:
        calls.append(command)

    monkeypatch.setattr(_filescanw_qt.subprocess, "Popen", fake_popen)
    window = _window()
    try:
        window._render_result(result)
        child = window._tree.topLevelItem(0).child(0)
        child.setSelected(True)
        window._locate_selected_file()
        assert len(calls) == 1
        assert str(target) in " ".join(calls[0])
        assert "已在文件管理器中定位" in window.page_status("results")

        revealed: list[Path] = []
        monkeypatch.setattr(window, "_reveal_file", revealed.append)
        window._result_item_double_clicked(child, 0)
        assert revealed == [target]

        missing = tmp_path / "gone.txt"
        monkeypatch.setattr(_filescanw_qt.subprocess, "Popen", fake_popen)
        window._reveal_file(missing)
        assert str(tmp_path) in " ".join(calls[-1])
    finally:
        window.deleteLater()


def test_reveal_command_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from fcmd.apps.filescan._filescanw_qt import reveal_command

    path = Path("C:/data/notes.txt")
    monkeypatch.setattr(sys, "platform", "win32")
    assert reveal_command(path) == ["explorer", f"/select,{path}"]
    monkeypatch.setattr(sys, "platform", "darwin")
    assert reveal_command(path)[:2] == ["open", "-R"]
    monkeypatch.setattr(sys, "platform", "linux")
    assert reveal_command(path)[0] == "xdg-open"
    assert reveal_command(path)[-1].endswith("data")
