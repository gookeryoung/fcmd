"""mirrortoolw Qt 窗口的离屏冒烟测试。

覆盖默认值、设置回填、模式切换、结果渲染（推荐 / 不可达语义色、搜索过滤）、
探测与应用事件流、关闭落盘。真实网络探测与配置写入由
``test_cli_mirrortoolw`` 覆盖，这里 monkeypatch 掉只验证界面编排。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.gui.workers import EventBridge
from fcmd.tools.dev import _mirrortoolw_qt
from fcmd.tools.dev._mirrortoolw_qt import (
    _THROUGHPUT_COLUMN,
    MirrorToolWindow,
    _work_apply,
    _work_probe,
)
from fcmd.tools.dev.mirrortoolw import (
    DEFAULT_TIMEOUT,
    MODE_SERVICE,
    MODE_SITES,
    SERVICE_LABELS,
    SOURCE_LABELS,
    MirrorResult,
    config_location,
    config_path,
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("mirrortoolw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


def _rows() -> list[MirrorResult]:
    """两条候选：一条可达（推荐）、一条不可达。"""
    return [
        MirrorResult("nju", "https://mirror.nju.edu.cn/pip/simple", True, 8.0, 2000.0),
        MirrorResult("tuna", "https://pypi.tuna.tsinghua.edu.cn/simple", False, 0.0, 0.0, "超时"),
    ]


# ============================================================================ #
# 构建与设置
# ============================================================================ #
def test_window_builds_with_defaults(home: Path) -> None:
    """默认服务模式、pip 服务、默认超时与来源；结果表六列。"""
    window = MirrorToolWindow()
    try:
        assert window._mode_service.isChecked()
        assert not window._mode_sites.isChecked()
        assert window._service_combo.currentText() == SERVICE_LABELS["pip"]
        assert window._timeout_spin.value() == DEFAULT_TIMEOUT
        assert window._source_combo.currentText() == SOURCE_LABELS["auto"]
        assert window._table.columnCount() == 6
        assert not window._table.isColumnHidden(_THROUGHPUT_COLUMN)
        assert window._config_hint.fullText().startswith("配置位置：")
        assert window.status_text() == "就绪"
    finally:
        window.deleteLater()


def test_settings_restored(home: Path) -> None:
    """站点模式、服务、超时、来源与搜索词逐项回填。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mode": MODE_SITES,
                "service": "npm",
                "timeout": 5.5,
                "source": "builtin",
                "search": "ali",
            }
        ),
        encoding="utf-8",
    )
    window = MirrorToolWindow()
    try:
        assert window._mode_sites.isChecked()
        assert window._service_combo.currentText() == SERVICE_LABELS["npm"]
        assert window._timeout_spin.value() == 5.5
        assert window._source_combo.currentText() == SOURCE_LABELS["builtin"]
        assert window._search.text() == "ali"
        assert window._table.isColumnHidden(_THROUGHPUT_COLUMN)
        assert window._config_hint.fullText().startswith("配置位置：教育网站点探测为只读")
    finally:
        window.deleteLater()


def test_custom_settings_path_isolated(home: Path, tmp_path: Path) -> None:
    """自定义设置路径优先于默认 ``ui/mirrortoolw.json``。"""
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({"service": "go", "mode": MODE_SERVICE}), encoding="utf-8")
    window = MirrorToolWindow(custom)
    try:
        assert window._service_combo.currentText() == SERVICE_LABELS["go"]
        window.close()
    finally:
        window.deleteLater()
    assert custom.is_file()
    assert not config_path().exists()


# ============================================================================ #
# 模式切换与结果渲染
# ============================================================================ #
def test_mode_switch_toggles_controls(home: Path) -> None:
    """站点模式隐藏吞吐列、换表头并禁用服务 / 来源 / 应用按钮。"""
    window = MirrorToolWindow()
    try:
        window._mode_sites.setChecked(True)
        assert window._table.isColumnHidden(_THROUGHPUT_COLUMN)
        header = window._table.horizontalHeaderItem(1)
        assert header is not None
        assert header.text() == "站点"
        assert not window._service_combo.isEnabled()
        assert window._source_combo.isEnabled()
        assert not window._apply_selected_button.isEnabled()
        assert not window._apply_recommended_button.isEnabled()

        window._mode_service.setChecked(True)
        assert not window._table.isColumnHidden(_THROUGHPUT_COLUMN)
        header = window._table.horizontalHeaderItem(1)
        assert header is not None
        assert header.text() == "镜像"
        assert window._service_combo.isEnabled()
        assert window._apply_recommended_button.isEnabled()
    finally:
        window.deleteLater()


def test_search_filters_rows(home: Path) -> None:
    """搜索框同时匹配镜像名与地址，大小写不敏感。"""
    window = MirrorToolWindow()
    try:
        window._rows = _rows()
        window._search.setText("NJU")
        assert window._table.rowCount() == 1
        window._search.setText("tsinghua")
        assert window._table.rowCount() == 1
        filtered = window._table.item(0, 1)
        assert filtered is not None
        assert filtered.text() == "tuna"
        window._search.clear()
        assert window._table.rowCount() == 2
    finally:
        window.deleteLater()


def test_rows_mark_recommended_and_unreachable(home: Path) -> None:
    """推荐行用成功色，不可达行用次要色；地址与状态带全文 tooltip。"""
    window = MirrorToolWindow()
    try:
        window._rows = _rows()
        window._search.setText("")
        window._render_rows()
        palette = window.theme().palette
        recommended = window._table.item(0, 0)
        unreachable = window._table.item(1, 0)
        assert recommended is not None
        assert unreachable is not None
        assert recommended.data(Qt.ItemDataRole.UserRole) == "nju"
        assert recommended.foreground().color().name() == QColor(palette.success).name()
        assert unreachable.foreground().color().name() == QColor(palette.muted).name()
        url_cell = window._table.item(0, 2)
        status_cell = window._table.item(1, 3)
        assert url_cell is not None
        assert status_cell is not None
        assert url_cell.toolTip() == "https://mirror.nju.edu.cn/pip/simple"
        assert status_cell.toolTip() == "不可访问（超时）"
    finally:
        window.deleteLater()


def test_set_theme_rerenders_rows(home: Path) -> None:
    """切换主题后结果表重渲染，行色跟随新调色板。"""
    window = MirrorToolWindow()
    try:
        window._rows = _rows()
        window._render_rows()
        light = _guitheme.resolve_theme("light")
        window.set_theme(light)
        item = window._table.item(0, 0)
        assert item is not None
        assert item.foreground().color().name() == QColor(light.palette.success).name()
    finally:
        window.deleteLater()


# ============================================================================ #
# 探测 / 应用事件流
# ============================================================================ #
def test_worker_service_event(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """服务测速事件：记录服务、渲染结果、输出推荐。"""
    monkeypatch.setattr(_mirrortoolw_qt, "probe_service", lambda service, timeout: _rows())
    window = MirrorToolWindow()
    try:
        window._set_busy(True)
        window._start_progress()
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_probe(MODE_SERVICE, "pip", 3.0, "auto", bridge)

        assert window._last_service == "pip"
        assert window._table.rowCount() == 2
        assert window.status_text() == "完成：推荐 nju"
        assert f"{SERVICE_LABELS['pip']} 共 2 个候选镜像，可达 1 个" in window.log_text()
        assert "推荐镜像: nju" in window.log_text()
        assert window._probe_button.isEnabled()
        assert window._progress.value() == 0
    finally:
        window.deleteLater()


def test_worker_sites_event(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """站点探测事件：不记录服务（只读），来源写入日志。"""
    monkeypatch.setattr(_mirrortoolw_qt, "probe_sites", lambda timeout, source: (_rows(), "内置列表"))
    window = MirrorToolWindow()
    try:
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_probe(MODE_SITES, "", 3.0, "auto", bridge)

        assert window._last_service == ""
        assert window.status_text() == "完成：推荐 nju"
        assert "教育网站点（内置列表）共 2 个" in window.log_text()
    finally:
        window.deleteLater()


def test_worker_error_event(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """探测异常：状态置失败、记录日志并弹窗提示。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, text, *args: errors.append(text) or None)

    def _fail(_service: str, _timeout: float) -> list[MirrorResult]:
        raise RuntimeError("boom")

    monkeypatch.setattr(_mirrortoolw_qt, "probe_service", _fail)
    window = MirrorToolWindow()
    try:
        window._set_busy(True)
        window._start_progress()
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_probe(MODE_SERVICE, "pip", 3.0, "auto", bridge)

        assert window.status_text() == "操作失败"
        assert "错误：boom" in window.log_text()
        assert errors == ["boom"]
        assert window._probe_button.isEnabled()
    finally:
        window.deleteLater()


def test_apply_recommended_uses_first_reachable(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「应用推荐」把首个可达镜像交给应用流程。"""
    calls: list[tuple[str, str]] = []
    window = MirrorToolWindow()
    try:
        window._last_service = "pip"
        window._rows = _rows()
        monkeypatch.setattr(window, "_start_apply", lambda service, mirror: calls.append((service, mirror)))
        window._apply_recommended()
        assert calls == [("pip", "nju")]
    finally:
        window.deleteLater()


def test_apply_selected_uses_row_name(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """「应用选中」取当前行 UserRole 记录的镜像名。"""
    calls: list[tuple[str, str]] = []
    window = MirrorToolWindow()
    try:
        window._last_service = "pip"
        window._rows = _rows()
        window._render_rows()
        window._table.selectRow(1)
        monkeypatch.setattr(window, "_start_apply", lambda service, mirror: calls.append((service, mirror)))
        window._apply_selected()
        assert calls == [("pip", "tuna")]
    finally:
        window.deleteLater()


def test_apply_selected_without_selection(home: Path) -> None:
    """未选中行时只记日志，不启动写配置。"""
    window = MirrorToolWindow()
    try:
        window._last_service = "pip"
        window._rows = _rows()
        window._render_rows()
        window._apply_selected()
        assert "请先在结果表中选择一个镜像" in window.log_text()
    finally:
        window.deleteLater()


def test_apply_recommended_without_rows(home: Path) -> None:
    """没有可达镜像时提示先测速或更换来源。"""
    window = MirrorToolWindow()
    try:
        window._last_service = "pip"
        window._rows = []
        window._apply_recommended()
        assert "没有可达镜像可应用" in window.log_text()
    finally:
        window.deleteLater()


def test_apply_event_flow(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """应用完成：回显命令输出、记录日志并更新状态。"""
    monkeypatch.setattr(
        _mirrortoolw_qt,
        "apply_service_mirror",
        lambda service, mirror: "已写入 pip 配置\n第二行",
    )
    window = MirrorToolWindow()
    try:
        window._set_busy(True)
        window._start_progress()
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_apply("pip", "nju", bridge)

        assert window.status_text() == "已应用 nju"
        assert "已写入 pip 配置" in window.log_text()
        assert "第二行" in window.log_text()
        assert f"已应用 {SERVICE_LABELS['pip']} 镜像: nju" in window.log_text()
        assert window._probe_button.isEnabled()
    finally:
        window.deleteLater()


def test_apply_event_error(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """应用失败：走统一失败收尾。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, text, *args: errors.append(text) or None)

    def _fail(_service: str, _mirror: str) -> str:
        raise ValueError("未知 pip 镜像: x")

    monkeypatch.setattr(_mirrortoolw_qt, "apply_service_mirror", _fail)
    window = MirrorToolWindow()
    try:
        bridge = EventBridge()
        bridge.event.connect(window._on_event)
        _work_apply("pip", "x", bridge)

        assert window.status_text() == "操作失败"
        assert errors == ["未知 pip 镜像: x"]
    finally:
        window.deleteLater()


# ============================================================================ #
# 忙碌态 / 关闭落盘
# ============================================================================ #
def test_busy_disables_inputs(home: Path) -> None:
    """探测与应用期间禁用目标控件与动作按钮，结束后按模式恢复。"""
    window = MirrorToolWindow()
    try:
        window._set_busy(True)
        for widget in (
            window._probe_button,
            window._mode_service,
            window._mode_sites,
            window._service_combo,
            window._timeout_spin,
            window._source_combo,
        ):
            assert not widget.isEnabled()
        window._set_busy(False)
        assert window._probe_button.isEnabled()
        assert window._service_combo.isEnabled()
        assert not window._source_combo.isEnabled()
    finally:
        window.deleteLater()


def test_close_persists_settings(home: Path) -> None:
    """关闭时写回模式、服务、超时、来源、搜索词与 geometry。"""
    window = MirrorToolWindow()
    try:
        window.show()
        window._mode_service.setChecked(True)
        window._service_combo.setCurrentText(SERVICE_LABELS["conda"])
        window._timeout_spin.setValue(4.5)
        window._source_combo.setCurrentText(SOURCE_LABELS["builtin"])
        window._search.setText("tuna")
        window.close()
    finally:
        window.deleteLater()

    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert raw["mode"] == MODE_SERVICE
    assert raw["service"] == "conda"
    assert raw["timeout"] == 4.5
    assert raw["source"] == "builtin"
    assert raw["search"] == "tuna"
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_config_location_used_for_hint(home: Path) -> None:
    """配置位置提示直接展示服务落盘路径。"""
    window = MirrorToolWindow()
    try:
        window._service_combo.setCurrentText(SERVICE_LABELS["cargo"])
        assert window._config_hint.fullText() == f"配置位置：{config_location('cargo')}"
    finally:
        window.deleteLater()
