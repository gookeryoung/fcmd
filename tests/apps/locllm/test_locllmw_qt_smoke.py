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
