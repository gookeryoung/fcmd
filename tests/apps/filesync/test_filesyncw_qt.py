"""filesyncw Qt 窗口的离屏冒烟测试：默认值、比较渲染、同步、配置持久化。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from fcmd.apps.filesync._core import SYNC_MIRROR, SYNC_TWO_WAY
from fcmd.apps.filesync._filesyncw_qt import (
    FilesyncWindow,
    _work_compare,
    _work_sync,
)
from fcmd.apps.filesync.filesyncw import config_path
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.gui.workers import EventBridge


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("filesyncw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


@pytest.fixture()
def trees(tmp_path: Path) -> tuple[Path, Path]:
    """左右目录：仅左、仅右、内容不同、完全相同各一份。"""
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    (left / "a.txt").write_text("alpha", encoding="utf-8")
    (right / "b.txt").write_text("beta", encoding="utf-8")
    (left / "diff.txt").write_text("one", encoding="utf-8")
    (right / "diff.txt").write_text("two-longer", encoding="utf-8")
    (left / "same.txt").write_text("same", encoding="utf-8")
    (right / "same.txt").write_text("same", encoding="utf-8")
    return left, right


def _prepare(window: FilesyncWindow, left: Path, right: Path) -> None:
    """填目录并切到「内容」比较，结果不受修改时间影响。"""
    window._left_row.set_value(str(left))
    window._right_row.set_value(str(right))
    window._compare.setCurrentText("内容")


def test_window_builds_with_defaults(home: Path) -> None:
    """默认空目录、双向同步、预演开启、结果表为空。"""
    window = FilesyncWindow()
    try:
        assert window._left_row.value() == ""
        assert window._left_row.hint.text() == "未选择"
        assert window._compare.currentText() == "时间与大小"
        assert window._sync.currentText() == "双向"
        assert window._dry_run.isChecked()
        assert window._table.columnCount() == 5
        assert window._table.rowCount() == 0
        assert window.status_text() == "就绪"
    finally:
        window.deleteLater()


def test_settings_restored(home: Path) -> None:
    """启动时回填目录、模式、过滤与复选项。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "left": "L",
                "right": "R",
                "compare_mode": "content",
                "sync_mode": "mirror",
                "include": "*.py",
                "exclude": "tmp",
                "skip_hidden": True,
                "show_equal": True,
            }
        ),
        encoding="utf-8",
    )
    window = FilesyncWindow()
    try:
        assert window._left_row.value() == "L"
        assert window._compare.currentText() == "内容"
        assert window._sync.currentText() == "镜像（左→右）"
        assert window._include.text() == "*.py"
        assert window._exclude.text() == "tmp"
        assert window._skip_hidden.isChecked()
        assert window._show_equal.isChecked()
    finally:
        window.deleteLater()


def test_swap_exchanges_sides(home: Path) -> None:
    """交换左右只换目录，不动其他选项。"""
    window = FilesyncWindow()
    try:
        window._left_row.set_value("L")
        window._right_row.set_value("R")
        window._swap()
        assert window._left_row.value() == "R"
        assert window._right_row.value() == "L"
    finally:
        window.deleteLater()


def test_compare_renders_rows_and_summary(home: Path, trees: tuple[Path, Path]) -> None:
    """比较结果按「仅左 / 仅右 / 不同」渲染，相同项默认隐藏。"""
    left, right = trees
    window = FilesyncWindow()
    try:
        _prepare(window, left, right)
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_compare(left, right, window._collect_options(), SYNC_TWO_WAY, bridge)

        assert window._table.rowCount() == 3
        paths = {window._table.item(row, 1).text() for row in range(3)}  # type: ignore[union-attr]
        assert paths == {"a.txt", "b.txt", "diff.txt"}
        actions = {window._table.item(row, 1).text(): window._table.item(row, 2).text() for row in range(3)}  # type: ignore[union-attr]
        assert actions["a.txt"] == "复制 → 右侧"
        assert actions["b.txt"] == "复制 ← 右侧"
        assert window._table.item(0, 1).toolTip() == window._table.item(0, 1).text()  # type: ignore[union-attr]
        assert window.status_text() == "比较完成"
        assert "比较完成：共 4 项" in window._log.text()
    finally:
        window.deleteLater()


def test_show_equal_toggle_reveals_equal_rows(home: Path, trees: tuple[Path, Path]) -> None:
    """勾选「显示相同项」后相同文件进入结果表。"""
    left, right = trees
    window = FilesyncWindow()
    try:
        _prepare(window, left, right)
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_compare(left, right, window._collect_options(), SYNC_TWO_WAY, bridge)
        window._show_equal.setChecked(True)
        assert window._table.rowCount() == 4
    finally:
        window.deleteLater()


def test_sync_dry_run_writes_nothing(home: Path, trees: tuple[Path, Path]) -> None:
    """预演只统计动作，不落盘。"""
    left, right = trees
    window = FilesyncWindow()
    try:
        _prepare(window, left, right)
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_sync(left, right, window._collect_options(), SYNC_TWO_WAY, True, bridge)
        assert not (right / "a.txt").exists()
        assert (right / "b.txt").exists()
        assert window.status_text() == "同步完成"
        assert "试运行" in window._log.text()
    finally:
        window.deleteLater()


def test_sync_mirror_executes_plan(home: Path, trees: tuple[Path, Path]) -> None:
    """镜像模式实际复制左侧文件并删除右侧多余文件。"""
    left, right = trees
    window = FilesyncWindow()
    try:
        _prepare(window, left, right)
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_sync(left, right, window._collect_options(), SYNC_MIRROR, False, bridge)
        assert (right / "a.txt").read_text(encoding="utf-8") == "alpha"
        assert not (right / "b.txt").exists()
        assert "同步完成：复制" in window._log.text()
    finally:
        window.deleteLater()


def test_help_menu_order(home: Path) -> None:
    """帮助菜单：载入配置…、保存配置…、分隔线、关于。"""
    window = FilesyncWindow()
    try:
        menu = window._menu("帮助")
        texts = [action.text() for action in menu.actions() if not action.isSeparator()]
        assert texts[-3:] == ["载入配置…", "保存配置…", "关于"]
        separators = [action.isSeparator() for action in menu.actions()]
        assert separators[-2] is True
    finally:
        window.deleteLater()


def test_compare_rejects_missing_directories(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """目录不存在时弹窗提示，不进入后台任务。"""
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda _parent, title, text, *args: shown.append(f"{title}:{text}") or None
    )
    window = FilesyncWindow()
    try:
        window._left_row.set_value(str(tmp_path / "missing"))
        window._right_row.set_value(str(tmp_path))
        window._start_compare()
        assert shown and "路径无效" in shown[0]
        assert not window._busy
    finally:
        window.deleteLater()


def test_close_persists_settings(home: Path, tmp_path: Path) -> None:
    """关闭时写回目录、模式、过滤与 geometry。"""
    window = FilesyncWindow()
    try:
        window.show()
        window._left_row.set_value("L")
        window._right_row.set_value("R")
        window._sync.setCurrentText("更新（左→右）")
        window._include.setText("*.py")
        window._skip_hidden.setChecked(True)
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert raw["left"] == "L"
    assert raw["right"] == "R"
    assert raw["sync_mode"] == "update"
    assert raw["include"] == "*.py"
    assert raw["skip_hidden"] is True
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_worker_error_reports_status(home: Path, trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """后台错误走统一收尾：状态变失败、按钮恢复。"""
    left, right = trees
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, title, text, *args: shown.append(text) or None)
    window = FilesyncWindow()
    try:
        _prepare(window, left, right)
        window._on_event(("error", "boom"))
        assert window.status_text() == "操作失败"
        assert not window._busy
        assert shown == ["boom"]
    finally:
        window.deleteLater()


def test_single_line_controls_follow_shared_height() -> None:
    """路径行与动作按钮统一高度（共享基准），不逐个窗口写死。"""
    window = FilesyncWindow()
    try:
        window.show()
        QApplication.processEvents()
        heights: set[int] = {window._compare_button.height(), window._sync_button.height()}
        heights.add(window._left_row.edit.height())
        heights.add(window._compare.height())
        assert len(heights) == 1
    finally:
        window.deleteLater()
