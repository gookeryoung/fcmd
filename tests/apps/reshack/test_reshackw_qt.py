"""reshackw Qt 窗口的离屏冒烟测试。

覆盖默认布局、设置回填、资源树分组与选中、文本 / 十六进制 / 图像预览、
缩放与适应、导出 / 替换 / 全部导出事件流、加载失败、忙碌态与关闭释放句柄。
真实 PE 解析与写盘由 ``test_reshack`` / ``test_cli_*`` 覆盖，这里用假 PE
对象验证界面编排。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QFileDialog, QMessageBox

from fcmd.apps.reshack import _reshackw_qt
from fcmd.apps.reshack._reshackw_qt import (
    ReshackWindow,
    _TaskBridge,
    _work_export_all,
    _work_load,
    _work_replace,
)
from fcmd.apps.reshack.reshack import PEError, ResourceEntry, export_payload
from fcmd.apps.reshack.reshackw import config_path
from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.app import create_application


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离设置目录并应用默认主题。"""
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    app = create_application("reshackw-test")
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    return base


def _resource(type_id: int, name_id: int, language_id: int = 0, data: bytes = b"") -> ResourceEntry:
    return ResourceEntry(
        type_id=type_id,
        type_name={3: "Icon", 16: "Version", 24: "Manifest"}.get(type_id, f"T{type_id}"),
        name_id=name_id,
        language_id=language_id,
        offset=0x2000,
        size=len(data),
        data=data,
    )


def _info(path: Path) -> Any:
    return SimpleNamespace(path=path, is_64bit=True, is_dll=False, machine="x86-64", subsystem="GUI")


class _FakePE:
    """假 PEFile：只实现窗口用到的方法。"""

    def __init__(self, resources: list[ResourceEntry] | None = None, error: str = "") -> None:
        self._resources = resources or []
        self._error = error
        self.closed = False
        self.replaced: tuple[Any, ...] | None = None

    def open(self, _path: Path) -> None:
        if self._error:
            raise PEError(self._error)

    def file_info(self) -> Any:
        return _info(Path("app.exe"))

    def resources(self) -> list[ResourceEntry]:
        return self._resources

    def close(self) -> None:
        self.closed = True

    def replace_resource(self, *args: Any) -> tuple[int, int, str]:
        self.replaced = args
        return (4, 6, "app.exe.backup")

    def export_all(self, target: Path) -> tuple[int, int, Path]:
        return (2, 2, target)


# ============================================================================ #
# 构建与设置
# ============================================================================ #
def test_window_builds_with_defaults(home: Path) -> None:
    """默认三页签、四列资源树、状态就绪、按钮可用。"""
    window = ReshackWindow()
    try:
        assert window._tree.columnCount() == 4
        assert window._tree.topLevelItemCount() == 0
        assert window._tabs.count() == 3
        assert [window._tabs.tabText(i) for i in range(3)] == ["文本", "十六进制", "图像"]
        assert window._zoom_label.text() == "100%"
        assert window._file_hint.text() == "尚未打开文件"
        assert window._open_button.isEnabled()
        assert window._export_button.isEnabled()
        assert window.status_text() == "就绪"
    finally:
        window.deleteLater()


def test_settings_restored_recent_menu(home: Path) -> None:
    """最近打开菜单按设置回填，空列表显示禁用的「（空）」。"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"recent_files": ["a.exe", "b.exe"]}), encoding="utf-8")
    window = ReshackWindow()
    try:
        labels = [action.text() for action in window._recent_menu.actions()]
        assert labels == ["a.exe", "b.exe"]
    finally:
        window.deleteLater()


def test_custom_settings_path_isolated(home: Path, tmp_path: Path) -> None:
    """自定义设置路径优先于默认 ``ui/reshackw.json``。"""
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({"recent_files": ["x.exe"]}), encoding="utf-8")
    window = ReshackWindow(custom)
    try:
        assert [action.text() for action in window._recent_menu.actions()] == ["x.exe"]
        window.close()
    finally:
        window.deleteLater()
    assert custom.is_file()
    assert not config_path().exists()
    raw = json.loads(custom.read_text(encoding="utf-8"))
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


# ============================================================================ #
# 资源树与预览
# ============================================================================ #
def test_populate_tree_groups_and_selection(home: Path) -> None:
    """按类型分组、分组行不可取资源，叶子行返回 ResourceEntry。"""
    window = ReshackWindow()
    try:
        resources = [
            _resource(24, 1, data=b"hello"),
            _resource(3, 2),
            _resource(3, 1, language_id=1033),
        ]
        window._populate_tree(resources)
        assert window._tree.topLevelItemCount() == 2
        icon_group = window._tree.topLevelItem(0)
        assert icon_group is not None
        assert icon_group.text(0) == "Icon (2)"
        assert icon_group.childCount() == 2
        manifest_group = window._tree.topLevelItem(1)
        assert manifest_group is not None
        assert manifest_group.text(0) == "Manifest (1)"

        window._tree.setCurrentItem(icon_group)
        assert window._selected_resource() is None

        child = icon_group.child(0)
        assert child is not None
        window._tree.setCurrentItem(child)
        selected = window._selected_resource()
        assert isinstance(selected, ResourceEntry)
        assert selected.name_id == 1
    finally:
        window.deleteLater()


def test_show_preview_text_and_hex(home: Path) -> None:
    """文本资源写入文本与十六进制页签，状态栏显示资源标识。"""
    window = ReshackWindow()
    try:
        window._show_preview(_resource(24, 1, data=b"hello"))
        assert window._text_preview.toPlainText() == "hello"
        assert "68 65 6c 6c 6f" in window._hex_preview.toPlainText()
        assert "Manifest #1" in window.status_text()
    finally:
        window.deleteLater()


def test_show_preview_binary_and_image_failure(home: Path) -> None:
    """二进制资源提示切换页签；无法解码的图标在图像页显示失败提示。"""
    window = ReshackWindow()
    try:
        window._show_preview(_resource(3, 1, data=b"not-an-icon"))
        assert "二进制资源" in window._text_preview.toPlainText()
        assert "无法解码图像" in window._image_label.text()
        assert window._preview_image is None
    finally:
        window.deleteLater()


def test_show_image_unsupported_type(home: Path) -> None:
    """非图像资源类型在图像页提示不支持。"""
    window = ReshackWindow()
    try:
        window._show_image(_resource(6, 1, data=b"\x00"))
        assert window._image_label.text() == "此资源类型不支持图像预览"
    finally:
        window.deleteLater()


def test_zoom_buttons_update_label(home: Path) -> None:
    """放大 / 缩小 / 1:1 按 ZOOM_STEP 调整并同步百分比。"""
    image_module = pytest.importorskip("PIL.Image")
    window = ReshackWindow()
    try:
        window._preview_image = image_module.new("RGB", (10, 10), "red")
        window._render_preview_image()
        window._zoom_in_image()
        assert window._zoom_label.text() == "125%"
        window._zoom_out_image()
        assert window._zoom_label.text() == "100%"
        window._zoom_in_image()
        window._reset_zoom_image()
        assert window._zoom_label.text() == "100%"
        assert window._fit_mode is False
    finally:
        window.deleteLater()


def test_fit_image_uses_viewport(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """适应窗口把视口尺寸交给 ``fit_zoom`` 并写回百分比。"""
    image_module = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(_reshackw_qt, "fit_zoom", lambda *_args, **_kwargs: 0.5)
    window = ReshackWindow()
    try:
        window._preview_image = image_module.new("RGB", (10, 10), "red")
        window._fit_image()
        assert window._fit_mode is True
        assert window._zoom_label.text() == "50%"
    finally:
        window.deleteLater()


def test_copy_preview_image_success(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """复制成功时写日志并给出成功状态。"""
    image_module = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(_reshackw_qt, "copy_image_to_clipboard", lambda _image: True)
    window = ReshackWindow()
    try:
        window._preview_image = image_module.new("RGB", (4, 4), "red")
        window._copy_preview_image()
        assert "已复制预览图像到剪贴板" in window._log.text()
        assert window.status_label.objectName() == "Success"
    finally:
        window.deleteLater()


def test_save_preview_image(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """另存为 PNG 写入指定路径。"""
    image_module = pytest.importorskip("PIL.Image")
    target = tmp_path / "preview.png"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), "PNG 图像 (*.png)"))
    window = ReshackWindow()
    try:
        window._shown_resource = _resource(3, 1)
        window._preview_image = image_module.new("RGB", (4, 4), "red")
        window._save_preview_image()
        assert target.is_file()
        assert "已保存预览图像" in window._log.text()
    finally:
        window.deleteLater()


# ============================================================================ #
# 导出 / 替换
# ============================================================================ #
def test_export_selected_writes_payload(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """导出选中资源按 ``export_payload`` 写盘。"""
    resource = _resource(3, 1, data=b"payload")
    target = tmp_path / "Icon_1_0.ico"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), "资源文件 (*.ico)"))
    window = ReshackWindow()
    try:
        monkeypatch.setattr(window, "_selected_resource", lambda: resource)
        window._export_selected()
        assert target.read_bytes() == export_payload(resource)
        assert "已导出 Icon #1" in window._log.text()
    finally:
        window.deleteLater()


def test_export_selected_without_selection(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未选择资源时只提示。"""
    infos: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a: infos.append(text) or None)
    window = ReshackWindow()
    try:
        window._export_selected()
        assert infos == ["请先在左侧选择资源。"]
    finally:
        window.deleteLater()


def test_export_all_without_file(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未打开文件时全部导出只提示。"""
    infos: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a: infos.append(text) or None)
    window = ReshackWindow()
    try:
        window._export_all()
        assert infos == ["请先打开 PE 文件。"]
    finally:
        window.deleteLater()


def test_replace_event_flow(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """替换成功事件刷新资源树、写日志并弹完成提示。"""
    infos: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a: infos.append(text) or None)
    window = ReshackWindow()
    try:
        pe = _FakePE([_resource(3, 1, data=b"new")])
        window._pe = cast(Any, pe)
        window._set_busy(True)
        bridge = _TaskBridge()
        bridge.event.connect(window._on_event)
        _work_replace(cast(Any, pe), 3, 1, b"new", 0, bridge)

        assert pe.replaced == (3, 1, b"new", 0)
        assert window._tree.topLevelItemCount() == 1
        assert "已替换资源" in window._log.text()
        assert window.status_label.objectName() == "Success"
        assert infos and "app.exe.backup" in infos[0]
        assert window._busy is False
    finally:
        window.deleteLater()


def test_export_all_event_flow(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """全部导出事件写日志、状态并弹完成提示。"""
    infos: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _p, _t, text, *a: infos.append(text) or None)
    window = ReshackWindow()
    try:
        pe = _FakePE()
        window._set_busy(True)
        bridge = _TaskBridge()
        bridge.event.connect(window._on_event)
        _work_export_all(cast(Any, pe), tmp_path / "app_resources", bridge)

        assert "已导出 2/2 个资源" in window._log.text()
        assert infos and "2/2" in infos[0]
        assert window._busy is False
    finally:
        window.deleteLater()


# ============================================================================ #
# 加载事件流
# ============================================================================ #
def test_start_load_submits_worker(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """校验通过后切忙碌态并提交后台加载。"""
    captured: dict[str, Any] = {}

    def fake_submit(fn: Any, *args: Any, **kwargs: Any) -> None:
        captured["fn"] = fn
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(_reshackw_qt, "is_pe_file", lambda _path: True)
    monkeypatch.setattr(_reshackw_qt, "submit", fake_submit)
    window = ReshackWindow()
    try:
        window._start_load(Path("app.exe"))
        assert captured["fn"] is _work_load
        assert captured["args"][0] == Path("app.exe")
        assert isinstance(captured["args"][1], _TaskBridge)
        assert captured["kwargs"]["on_error"] == window._on_worker_error
        assert window._busy is True
        assert not window._open_button.isEnabled()
        assert window.status_text() == "正在加载 app.exe…"
    finally:
        window._set_busy(False)
        window.deleteLater()


def test_load_event_flow(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """加载成功回填资源树、标题、文件提示与最近打开。"""
    resources = [_resource(24, 1, data=b"manifest")]
    monkeypatch.setattr(_reshackw_qt, "PEFile", lambda: _FakePE(resources))
    window = ReshackWindow()
    try:
        window._set_busy(True)
        bridge = _TaskBridge()
        bridge.event.connect(window._on_event)
        _work_load(Path("app.exe"), bridge)

        assert window._tree.topLevelItemCount() == 1
        assert "app.exe" in window.windowTitle()
        assert window._file_hint.text() == "app.exe"
        assert "已加载 1 个资源" in window._log.text()
        assert window.status_text() == "app.exe：1 个资源"
        assert [action.text() for action in window._recent_menu.actions()] == ["app.exe"]
        assert window._busy is False
        assert config_path().is_file()
    finally:
        window.deleteLater()


def test_load_event_flow_closes_previous_pe(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """加载新文件时释放上一个 PE 句柄。"""
    old = _FakePE()
    monkeypatch.setattr(_reshackw_qt, "PEFile", _FakePE)
    window = ReshackWindow()
    try:
        window._pe = cast(Any, old)
        bridge = _TaskBridge()
        bridge.event.connect(window._on_event)
        _work_load(Path("app.exe"), bridge)
        assert old.closed is True
        assert window._pe is not None and window._pe is not old
    finally:
        window.deleteLater()


def test_load_pe_error_shows_message(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PEError 转错误事件：恢复按钮、标红状态并弹错误框。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    monkeypatch.setattr(_reshackw_qt, "PEFile", lambda: _FakePE(error="not a PE"))
    window = ReshackWindow()
    try:
        window._set_busy(True)
        bridge = _TaskBridge()
        bridge.event.connect(window._on_event)
        _work_load(Path("bad.exe"), bridge)
        assert errors == ["not a PE"]
        assert window.status_label.objectName() == "Danger"
        assert window._busy is False
        assert "错误：not a PE" in window._log.text()
    finally:
        window.deleteLater()


def test_on_worker_error_direct(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """线程池兜底异常同样恢复界面。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    window = ReshackWindow()
    try:
        window._set_busy(True)
        window._on_worker_error("boom")
        assert errors == ["boom"]
        assert window._busy is False
        assert window.status_label.objectName() == "Danger"
    finally:
        window.deleteLater()


# ============================================================================ #
# 忙碌态与关闭
# ============================================================================ #
def test_busy_disables_buttons(home: Path) -> None:
    """忙碌时禁用四个工具栏按钮，结束后恢复。"""
    window = ReshackWindow()
    try:
        window._set_busy(True)
        assert not window._open_button.isEnabled()
        assert not window._export_button.isEnabled()
        assert not window._replace_button.isEnabled()
        assert not window._export_all_button.isEnabled()
        window._set_busy(False)
        assert window._open_button.isEnabled()
        assert window._export_all_button.isEnabled()
    finally:
        window.deleteLater()


def test_close_releases_pe_and_persists(home: Path) -> None:
    """关闭时释放 PE 句柄，并把最近打开与 geometry 写回设置。"""
    window = ReshackWindow()
    try:
        pe = _FakePE()
        window._pe = cast(Any, pe)
        window._settings.recent_files = ["a.exe", "b.exe"]
        window.close()
        assert pe.closed is True
        assert window._pe is None
    finally:
        window.deleteLater()

    raw = json.loads(config_path().read_text(encoding="utf-8"))
    assert raw["recent_files"] == ["a.exe", "b.exe"]
    assert isinstance(raw["geometry"], str) and "x" in raw["geometry"]


def test_open_recent_invalid_path(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """最近打开里失效的路径弹错误框，不进入加载。"""
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "critical", lambda _p, _t, text, *a: errors.append(text) or None)
    window = ReshackWindow()
    try:
        window._open_recent("missing.exe")
        assert errors and "不是有效的 PE 文件" in errors[0]
    finally:
        window.deleteLater()
