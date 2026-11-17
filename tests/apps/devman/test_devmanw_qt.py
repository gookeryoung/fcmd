"""devmanw Qt 窗口的离屏冒烟测试。

覆盖默认布局、工具回填、快照渲染、已安装 / 可用选中与按钮态、后台任务事件
流、安装 / 卸载 / 切换 / 升级 / PATH 操作编排、忙碌态、关闭确认与设置落盘。
真实安装逻辑由 ``test_devmanw`` 与 CLI 测试覆盖，这里只验证界面编排。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QMessageBox

from fcmd.apps.devman import _devmanw_qt
from fcmd.apps.devman._devmanw_qt import DevmanWindow, _TaskBridge, _work_task
from fcmd.apps.devman._paths import TOOLS
from fcmd.apps.devman.devmanw import config_path, version_iid
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("devmanw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


def _list_payload() -> dict[str, Any]:
    return {
        "schema": "devman/list/1",
        "root": r"C:\devman",
        "tools": {
            "node": {
                "installed": [
                    {
                        "tool": "node",
                        "name": "22.23.5",
                        "version": "22.23.5",
                        "path": r"C:\devman\node\22.23.5",
                        "status": "ok",
                        "source": "https://nodejs.org/dist",
                        "installed_at": "2026-11-14T10:00:00+08:00",
                    },
                    {
                        "tool": "node",
                        "name": "20.11.1",
                        "version": "20.11.1",
                        "path": r"C:\devman\node\20.11.1",
                        "status": "incomplete",
                        "source": "https://nodejs.org/dist",
                        "installed_at": "",
                    },
                ],
                "active": "22.23.5",
                "incomplete": ["20.11.1"],
            },
            "python": {"installed": [], "active": None, "incomplete": []},
        },
        "shims": {"dir": r"C:\devman\bin", "on_path": True, "index": 2},
        "external": {},
        "markers": {},
        "warnings": [],
    }


def _versions_payload() -> dict[str, Any]:
    return {
        "schema": "devman/versions/1",
        "tool": "node",
        "remote": True,
        "source": "https://nodejs.org/dist/index.json",
        "versions": [
            {"version": "22.23.5", "installed": True, "active": True, "lts": "Jod"},
            {"version": "23.11.0", "installed": False, "active": False, "lts": False},
        ],
        "warnings": [],
    }


def _snapshot() -> dict[str, Any]:
    return {
        "list": _list_payload(),
        "registry": r"C:\devman\bin;C:\Windows",
        "shadowed": {},
        "recorded": [r"C:\devman\bin"],
        "tool_roots": {tool: rf"C:\devman\{tool}" for tool in TOOLS},
    }


# ============================================================================ #
# 构建与设置
# ============================================================================ #
def test_window_builds_with_defaults(home: Path) -> None:
    """默认工具 node、空列表、空闲按钮态与就绪状态。"""
    window = DevmanWindow()
    try:
        assert window.current_tool == "node"
        assert window._installed_tree.columnCount() == 4
        assert window._available_tree.columnCount() == 4
        assert window._available_card.title() == "可用版本"
        assert window._root_label.text() == ""
        assert window.status_text() == "就绪"
        assert window._progress.maximum() == 1
        assert window._buttons["refresh"].isEnabled()
        assert window._buttons["init"].isEnabled()
        assert not window._buttons["install"].isEnabled()
        assert not window._buttons["uninstall"].isEnabled()
        assert not window._buttons["uninit"].isEnabled()
    finally:
        window.deleteLater()


def test_settings_restore_tool(home: Path) -> None:
    """上次工具从设置回填。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tool": "python"}), encoding="utf-8")
    window = DevmanWindow()
    try:
        assert window.current_tool == "python"
    finally:
        window.deleteLater()


def test_custom_settings_path_persists_tool(home: Path, tmp_path: Path) -> None:
    """关闭时把当前工具与 geometry 写回自定义设置路径。"""
    custom = tmp_path / "custom.json"
    window = DevmanWindow(custom)
    try:
        window.current_tool = "go"
        window.close()
    finally:
        window.deleteLater()
    assert custom.is_file()
    assert not config_path().exists()
    raw = json.loads(custom.read_text(encoding="utf-8"))
    assert raw["tool"] == "go"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


# ============================================================================ #
# 快照与选中
# ============================================================================ #
def test_apply_snapshot_builds_trees_and_path(home: Path) -> None:
    """快照渲染分组、root 提示与 PATH 状态。"""
    window = DevmanWindow()
    try:
        window._apply_snapshot(_snapshot())
        assert window._installed_tree.topLevelItemCount() == len(TOOLS)
        node_group = window._installed_tree.topLevelItem(0)
        assert node_group is not None
        assert node_group.text(0) == "node（2 个已安装，活动 22.23.5）"
        assert node_group.childCount() == 2
        assert window._root_label.text() == r"root: C:\devman"
        assert window._path_labels["用户 PATH"].text() == "已写入"
        assert window._path_labels["会遮蔽"].text() == "无"
        assert version_iid("node", "22.23.5") in window._rows
    finally:
        window.deleteLater()


def test_select_installed_updates_details_and_actions(home: Path) -> None:
    """选中已安装版本：详情、活动标记与卸载 / 切换按钮解禁。"""
    window = DevmanWindow()
    try:
        window._apply_snapshot(_snapshot())
        item = window._items[version_iid("node", "22.23.5")]
        window._installed_tree.setCurrentItem(item)
        assert window.current_tool == "node"
        assert window._detail_labels["工具"].text() == "node"
        assert window._detail_labels["活动"].text() == "是"
        assert window._detail_labels["状态"].text() == "完整"
        assert window._buttons["uninstall"].isEnabled()
        assert not window._buttons["use"].isEnabled()
        assert window._buttons["upgrade"].isEnabled()
        assert not window._buttons["install"].isEnabled()

        window._installed_tree.setCurrentItem(window._items[version_iid("node", "20.11.1")])
        assert window._buttons["use"].isEnabled()
        assert window._detail_labels["状态"].text().startswith("半完成")
    finally:
        window.deleteLater()


def test_available_event_and_selection(home: Path) -> None:
    """可用版本事件刷新标题与列表，选中后「安装选中」解禁。"""
    window = DevmanWindow()
    try:
        window._apply_snapshot(_snapshot())
        window._on_event(("available", {"node": _versions_payload()}))
        assert window._available_card.title() == "可用版本（node）"
        assert window._available_tree.topLevelItemCount() == 2
        item = window._items["avail:node:23.11.0"]
        window._available_tree.setCurrentItem(item)
        assert window._detail_labels["状态"].text() == "可安装"
        assert window._buttons["install"].isEnabled()
        assert not window._buttons["uninstall"].isEnabled()
        assert not window._buttons["use"].isEnabled()
    finally:
        window.deleteLater()


def test_selection_survives_snapshot_refresh(home: Path) -> None:
    """刷新快照后尽量恢复原选中的已安装版本。"""
    window = DevmanWindow()
    try:
        window._apply_snapshot(_snapshot())
        iid = version_iid("node", "20.11.1")
        window._installed_tree.setCurrentItem(window._items[iid])
        window._apply_snapshot(_snapshot())
        assert window._installed_selected_iid() == iid
    finally:
        window.deleteLater()


def test_select_group_keeps_actions_conservative(home: Path) -> None:
    """选中工具分组行只允许升级 / PATH 操作，不允许卸载 / 切换。"""
    window = DevmanWindow()
    try:
        window._apply_snapshot(_snapshot())
        group = window._installed_tree.topLevelItem(0)
        assert group is not None
        window._installed_tree.setCurrentItem(group)
        assert window._detail_labels["版本"].text() == "2 个已安装"
        assert not window._buttons["uninstall"].isEnabled()
        assert not window._buttons["use"].isEnabled()
        assert window._buttons["upgrade"].isEnabled()
    finally:
        window.deleteLater()


# ============================================================================ #
# 后台任务事件
# ============================================================================ #
def test_refresh_submits_work(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """刷新提交后台任务：不确定进度、整组按钮禁用。"""
    captured: dict[str, Any] = {}

    def fake_submit(fn: Any, *args: Any, **kwargs: Any) -> None:
        captured["fn"] = fn
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(_devmanw_qt, "submit", fake_submit)
    window = DevmanWindow()
    try:
        window.refresh()
        assert captured["fn"] is _work_task
        assert window._busy is True
        assert window._progress.maximum() == 0
        assert window.status_text() == "刷新…"
        assert not any(widget.isEnabled() for widget in window._buttons.values())
        window._finish("done", "")
        assert window._progress.maximum() == 1
        assert window._buttons["refresh"].isEnabled()
    finally:
        window.deleteLater()


def test_work_task_streams_log_progress_snapshot(home: Path) -> None:
    """worker 的日志 / 进度 / 快照 / 完成事件依次回投界面。"""
    window = DevmanWindow()
    try:
        window._busy = True
        window._label = "刷新"
        window._set_buttons(False)
        bridge = _TaskBridge()
        bridge.event.connect(window._on_event)

        def work(
            log: Any,
            progress: Any,
            emit: Any,
        ) -> str:
            log("读取清单")
            progress(1, 2)
            emit(("snapshot", _snapshot()))
            return ""

        _work_task(work, bridge)
        assert "读取清单" in window._log_view.text()
        assert window._installed_tree.topLevelItemCount() == len(TOOLS)
        assert window._busy is False
        assert window.status_text() == "刷新完成"
        assert window._buttons["refresh"].isEnabled()
    finally:
        window.deleteLater()


def test_progress_event_percent_and_bytes(home: Path) -> None:
    """总量已知显示百分比，未知显示 MB。"""
    window = DevmanWindow()
    try:
        window._label = "安装"
        window._on_event(("progress", 5, 10))
        assert window._progress.value() == 5
        assert window.status_text() == "安装… 50%"
        window._on_event(("progress", 2 * 1024 * 1024, 0))
        assert window.status_text() == "安装… 2 MB"
    finally:
        window.deleteLater()


def test_done_triggers_refresh_after_action(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非「刷新」任务完成后自动刷新列表。"""
    calls: list[bool] = []
    window = DevmanWindow()
    try:
        window._busy = True
        window._label = "安装 node 22.23.5"
        monkeypatch.setattr(window, "refresh", lambda: calls.append(True))
        window._on_event(("done", "已安装"))
        assert calls == [True]
        assert window._busy is False
    finally:
        window.deleteLater()


def test_error_event_shows_critical(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """错误事件：日志、失败状态与错误框。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    window = DevmanWindow()
    try:
        window._busy = True
        window._label = "安装"
        window._on_event(("error", "boom"))
        assert errors == ["boom"]
        assert window.status_text() == "安装失败"
        assert "失败：boom" in window._log_view.text()
        assert window._busy is False
    finally:
        window.deleteLater()


# ============================================================================ #
# 操作编排
# ============================================================================ #
def _select_available(window: DevmanWindow, version: str = "23.11.0") -> None:
    window._apply_snapshot(_snapshot())
    window._on_event(("available", {"node": _versions_payload()}))
    window._available_tree.setCurrentItem(window._items[f"avail:node:{version}"])


def _select_installed(window: DevmanWindow, version: str = "22.23.5") -> None:
    window._apply_snapshot(_snapshot())
    window._installed_tree.setCurrentItem(window._items[version_iid("node", version)])


def test_install_selected_without_selection(home: Path) -> None:
    """未选中可用版本时只在状态栏提示。"""
    window = DevmanWindow()
    try:
        window.install_selected()
        assert window.status_text() == "请先在上方可用版本列表中选择一个版本"
    finally:
        window.deleteLater()


def test_install_selected_builds_spec(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """安装选中把 ``tool@version`` 交给 _ops.install，并回传用户 PATH 变化。"""
    window = DevmanWindow()
    try:
        _select_available(window)
        monkeypatch.setattr(window, "_shadow_gate", lambda: False)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(window, "_start", lambda label, work: captured.update(label=label, work=work))
        window.install_selected()
        assert captured["label"] == "安装 node 23.11.0"

        logs: list[str] = []
        calls: list[str] = []

        def fake_install(_ctx: Any, spec: str, *, allow_shadow: bool, progress: Any) -> dict[str, Any]:
            calls.append(spec)
            progress(1, 1)
            return {
                "message": "已安装 node",
                "path": r"C:\devman\node\23.11.0",
                "user_path": {"changed": True, "position": "prepend", "entry": r"C:\devman\bin"},
            }

        monkeypatch.setattr(_devmanw_qt._ops, "install", fake_install)
        message = captured["work"](logs.append, lambda *_a: None, lambda *_a: None)
        assert calls == ["node@23.11.0"]
        assert "已安装 node" in message
        assert any("用户 PATH 已前置" in text for text in logs)
    finally:
        window.deleteLater()


def test_uninstall_requires_selection(home: Path) -> None:
    """未选中已安装版本时不执行卸载。"""
    window = DevmanWindow()
    try:
        window.uninstall_selected()
        assert window.status_text() == "请先在已安装列表中选择一个版本"
    finally:
        window.deleteLater()


def test_uninstall_confirm_cancel(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """确认框选择否时不提交任务。"""
    calls: list[str] = []
    window = DevmanWindow()
    try:
        _select_installed(window)
        monkeypatch.setattr(_devmanw_qt, "_confirm", lambda *_a, **_k: False)
        monkeypatch.setattr(window, "_start", lambda label, _work: calls.append(label))
        window.uninstall_selected()
        assert calls == []
    finally:
        window.deleteLater()


def test_uninstall_confirm_accepts(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """确认后提交卸载任务并回传移除数量。"""
    window = DevmanWindow()
    try:
        _select_installed(window, "20.11.1")
        monkeypatch.setattr(_devmanw_qt, "_confirm", lambda *_a, **_k: True)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(window, "_start", lambda label, work: captured.update(label=label, work=work))
        window.uninstall_selected()
        assert captured["label"] == "卸载"

        logs: list[str] = []
        monkeypatch.setattr(
            _devmanw_qt._ops,
            "uninstall",
            lambda *_a, **_k: {"removed": [{"version": "20.11.1"}], "user_path": {}},
        )
        message = captured["work"](logs.append, lambda *_a: None, lambda *_a: None)
        assert "20.11.1" in message
    finally:
        window.deleteLater()


def test_use_selected_reports_active(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """切换任务把新活动版本写入结果消息。"""
    window = DevmanWindow()
    try:
        _select_installed(window)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(window, "_start", lambda label, work: captured.update(label=label, work=work))
        window.use_selected()
        assert captured["label"] == "切换"
        monkeypatch.setattr(_devmanw_qt._ops, "use", lambda *_a, **_k: {"active": "22.23.5"})
        message = captured["work"](lambda _t: None, lambda *_a: None, lambda *_a: None)
        assert message == "node 活动版本：22.23.5"
    finally:
        window.deleteLater()


def test_upgrade_tool_reports_target(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """升级任务记录 current → target 并返回目标版本。"""
    window = DevmanWindow()
    try:
        monkeypatch.setattr(window, "_shadow_gate", lambda: False)
        captured: dict[str, Any] = {}
        monkeypatch.setattr(window, "_start", lambda label, work: captured.update(label=label, work=work))
        window.upgrade_tool()
        assert captured["label"] == "升级"
        logs: list[str] = []
        monkeypatch.setattr(
            _devmanw_qt._ops,
            "upgrade",
            lambda *_a, **_k: {"changed": True, "current": "22.23.5", "target": "23.11.0"},
        )
        message = captured["work"](logs.append, lambda *_a: None, lambda *_a: None)
        assert "23.11.0" in message
        assert any("22.23.5 → 23.11.0" in text for text in logs)
    finally:
        window.deleteLater()


def test_path_actions_confirm_and_start(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """写入 / 移除 PATH 集成都先确认再提交任务。"""
    window = DevmanWindow()
    try:
        window._apply_snapshot(_snapshot())
        monkeypatch.setattr(_devmanw_qt, "_confirm", lambda *_a, **_k: True)
        labels: list[str] = []
        monkeypatch.setattr(window, "_start", lambda label, _work: labels.append(label))
        window.init_path()
        window.uninit_path()
        assert labels == ["写入 PATH", "移除 PATH 集成"]
    finally:
        window.deleteLater()


def test_shadow_gate_needs_confirmation(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未注册且存在遮蔽命令时询问；确认返回 True、取消返回 None。"""
    window = DevmanWindow()
    try:
        window.path_state = {"shadowed": {"node": [r"C:\node\node.exe"]}, "registered": False}
        monkeypatch.setattr(_devmanw_qt, "_confirm", lambda *_a, **_k: True)
        assert window._shadow_gate() is True
        monkeypatch.setattr(_devmanw_qt, "_confirm", lambda *_a, **_k: False)
        assert window._shadow_gate() is None
        window.path_state = {"shadowed": {}, "registered": False}
        assert window._shadow_gate() is False
    finally:
        window.deleteLater()


# ============================================================================ #
# 忙碌态与关闭
# ============================================================================ #
def test_confirm_close_when_busy(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """任务进行中关闭需确认。"""
    window = DevmanWindow()
    try:
        window._busy = True
        window._label = "安装"
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
        assert window._confirm_close() is False
        monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        assert window._confirm_close() is True
    finally:
        window.deleteLater()


def test_confirm_close_when_idle(home: Path) -> None:
    """空闲时直接允许关闭。"""
    window = DevmanWindow()
    try:
        assert window._confirm_close() is True
    finally:
        window.deleteLater()


def test_log_event_appends_timestamped_line(home: Path) -> None:
    """后台日志事件写入日志区。"""
    window = DevmanWindow()
    try:
        window._on_event(("log", "hello"))
        text = window._log_view.text()
        assert text
        assert text.rstrip().endswith("hello")
    finally:
        window.deleteLater()
