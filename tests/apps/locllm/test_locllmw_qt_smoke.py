"""locllmw Qt 窗口的离屏冒烟测试：七页可构建、主题可切换、设置可落盘。

用 ``QT_QPA_PLATFORM=offscreen`` 在无显示器环境（CI / 本地）跑通整窗构建，
模型扫描与硬件检测替换成空实现，避免测试触盘 / 调 ``nvidia-smi``。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from fcmd.apps.locllm import _locllmw_qt_state as qt_state
from fcmd.apps.locllm._locllmw_qt_window import LocllmWindow, create_application
from fcmd.gui import theme as _guitheme


@pytest.fixture()
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """构建离线窗口：扫描 / 检测 / 采样全部打桩，关闭时自动清理。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    monkeypatch.setattr(qt_state.LocllmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LocllmState, "detect_hardware", lambda self, on_done=None: None)
    create_application([])
    win = LocllmWindow(tmp_path / "ui" / "locllmw.json")
    win.monitor_page.stop()
    yield win
    win.close()


def test_first_page_is_built_immediately(window: Any) -> None:
    """首屏页面必须已经实例化，不能停在空占位控件上（点击已选中项不会触发切换）。"""
    assert window.stack.currentWidget() is window.page("overview")
    assert window.current_page() == "overview"


def test_status_reflects_scan_result(window: Any) -> None:
    """扫描结束后状态栏不能停在「正在读取服务与模型目录…」。"""
    from types import SimpleNamespace

    window._on_models_changed(SimpleNamespace(issue=None, engine_missing=False, port_busy=False))
    status = window.status_text()
    assert "环境就绪" in status
    assert "个模型" in status
    window._on_models_changed(SimpleNamespace(issue="模型目录不存在", engine_missing=True, port_busy=False))
    status = window.status_text()
    assert status.startswith("注意：")
    assert "引擎未安装" in status


def test_window_builds_all_pages(window: Any) -> None:
    keys = window.nav.keys()
    assert keys == ("overview", "models", "run", "chat", "lab", "monitor", "settings")
    for key in keys:
        window.show_page(key)
        assert window.current_page() == key
        assert window.page(key) is not None


def test_generated_pages_expose_core_widgets(window: Any) -> None:
    window.show_page("models")
    models = window.page("models")
    assert models.card.title().startswith("运行模型")
    window.show_page("chat")
    chat = window.page("chat")
    assert chat.service_combo.count() >= 1
    chat.reset()
    assert "新对话" in chat.status.text()
    window.show_page("settings")
    settings_page = window.page("settings")
    settings_page.refresh()
    assert settings_page.models_path.value()


def test_models_page_fills_hardware_column(window: Any) -> None:
    """模型页的硬件列必须按运行页当前口径给出适配状态，而不是一律空白。"""
    from fcmd.apps.locllm.llmbase import ModelEntry

    root = Path("/models")
    window.state.models = [
        ModelEntry(root=root, path=root / "org" / "small.gguf", size=1024**3, profiles=("balanced",), tags=("对话",))
    ]
    window.state.models_root = root
    window.state.running_paths = set()
    window.state.gpu_mib = 32768
    window.state.gpu_free_mib = 32000
    window.show_page("models")
    page = window.page("models")
    page.tree.expand_all()
    group = page.tree.tree.topLevelItem(0)
    assert group is not None
    child = group.child(0)
    assert child.text(4) == "可用"


def test_run_page_form_defaults(window: Any) -> None:
    run = window.run_page
    assert run.profile_combo.currentText() == "balanced"
    assert run.host_edit.text() == "127.0.0.1"
    assert run.port_edit.text() == "8080"
    run.preview_button.click()
    assert "启动计划" in run.plan_view.toPlainText()
    assert run.plan_view.toPlainText().startswith("启动计划:")


def test_theme_switch_persists(window: Any, tmp_path: Path) -> None:
    window.set_theme(_guitheme.resolve_theme("light"))
    assert window.theme().name == "light"
    payload = json.loads((tmp_path / "ui" / "theme.json").read_text(encoding="utf-8"))
    assert payload["theme"] == "light"


def test_close_persists_geometry(window: Any, tmp_path: Path) -> None:
    window.set_status("进行中")
    window.close()
    settings_file = tmp_path / "ui" / "locllmw.json"
    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["geometry"]
    assert "hardware" in payload


def test_sidebar_collapse_updates_menu_and_settings(window: Any, tmp_path: Path) -> None:
    assert not window.nav.is_collapsed()
    window.nav.set_collapsed(True)
    assert window.sidebar_action.isChecked()
    payload = json.loads((tmp_path / "ui" / "locllmw.json").read_text(encoding="utf-8"))
    assert payload["sidebar_collapsed"] is True
    window.sidebar_action.trigger()  # 可勾选动作：再触发一次即展开
    assert not window.nav.is_collapsed()


def test_sidebar_collapsed_state_restored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    monkeypatch.setattr(qt_state.LocllmState, "refresh_models", lambda self, port=None: None)
    monkeypatch.setattr(qt_state.LocllmState, "detect_hardware", lambda self, on_done=None: None)
    settings_path = tmp_path / "ui" / "locllmw.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"sidebar_collapsed": True}), encoding="utf-8")
    create_application([])
    win = LocllmWindow(settings_path)
    win.monitor_page.stop()
    try:
        assert win.nav.is_collapsed()
        assert win.sidebar_action.isChecked()
    finally:
        win.close()


def test_pages_use_scroll_container(window: Any) -> None:
    """长页面必须放进滚动容器：窗口变矮时滚动而不是压扁控件。"""
    from fcmd.gui.qt.widgets import PageScroll

    for key in ("overview", "run", "models", "monitor", "settings", "lab"):
        window.show_page(key)
        page = window.page(key)
        assert page.findChild(PageScroll) is not None, key


def test_run_page_keeps_model_list_height(window: Any) -> None:
    """窗口缩到最小、展开高级参数与模型分组后，模型列表不被压扁，底栏仍可见。"""
    from PySide6.QtWidgets import QApplication

    window.show()
    window.show_page("run")
    window.resize(1040, 780)
    window.run_page.advanced.set_expanded(True)
    window.run_page.tree.expand_all()
    QApplication.processEvents()
    assert window.run_page.tree.tree.height() >= 240
    assert window.run_page.scroll.verticalScrollBar().maximum() > 0
    button = window.run_page.start_button
    assert not button.isHidden()
    assert button.mapTo(window, button.rect().bottomLeft()).y() <= window.height()


def test_chat_input_buttons_share_one_row(window: Any) -> None:
    """对话页输入区按钮横排，避免四个按钮纵向堆叠溢出窗口。"""
    window.show_page("chat")
    chat = window.page("chat")
    buttons = [chat.send_button, chat.stop_button, chat.reset_button, chat.export_button]
    tops = {widget.mapTo(chat, widget.rect().topLeft()).y() for widget in buttons}
    assert len(tops) == 1


def test_scan_result_auto_selects_a_startable_model(window: Any, tmp_path: Path) -> None:
    """列表里有模型但未选择时，自动带入可启动的模型（避免启动报「未找到量化」）。"""
    from fcmd.apps.locllm.llmbase import ModelEntry

    entries = [
        ModelEntry(root=tmp_path, path=tmp_path / "a-Q4_K_XL.gguf", size=10),
        ModelEntry(root=tmp_path, path=tmp_path / "b-Q5_K_M.gguf", size=20),
    ]
    window.state.models = entries
    window.state.models_root = tmp_path
    window.state.selected_model = None
    window.run_page.on_models_changed()
    assert window.state.selected_model == tmp_path / "b-Q5_K_M.gguf"
    assert window.run_page.name_combo.currentText() == "b"
