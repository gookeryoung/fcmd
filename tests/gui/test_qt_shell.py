"""``fcmd.gui.shell`` 窗口外壳 mixin 测试：配置对象、主题按窗口覆盖、共享落盘。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from fcmd.gui import config as _guiconfig
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.app import create_application
from fcmd.gui.shell import (
    WindowGeometryMixin,
    WindowLifecycleMixin,
    WindowSettingsMixin,
    WindowShellMixin,
    WindowSpec,
    WindowThemeMixin,
)
from fcmd.gui.toolwindow import QtToolWindow


@pytest.fixture()
def app() -> QApplication:
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return create_application("shell-test")


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "home"
    monkeypatch.setenv("FCMD_HOME", str(base))
    return base


def test_window_spec_theme_app_falls_back_to_config_name() -> None:
    spec = WindowSpec(title="示例", config_name="llmw")
    assert spec.theme_app == "llmw"
    assert WindowSpec(title="示例", config_name="wslmanw", app_name="custom").theme_app == "custom"


def test_window_shell_mixin_composes_four_responsibilities() -> None:
    for mixin in (WindowSettingsMixin, WindowGeometryMixin, WindowThemeMixin, WindowLifecycleMixin):
        assert issubclass(WindowShellMixin, mixin)


def test_tool_window_uses_shared_shell(app: QApplication, home: Path) -> None:
    window = cast(Any, QtToolWindow(title="共享外壳", config_name="shell-demo", min_size=(400, 300)))
    try:
        assert isinstance(window, QtToolWindow)
        assert window.settings_path == _guiconfig.config_path("shell-demo")
        assert window.settings_value("missing", 7) == 7
        window.save_settings({"workers": 2})
        assert window.settings()["workers"] == 2
        window.set_status("完成")
        assert window.status_text() == "完成"
    finally:
        window.deleteLater()


def test_theme_override_is_scoped_per_window(app: QApplication, home: Path) -> None:
    path = _guitheme.theme_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "theme": "dark-blue",
                "overrides": {"radius": 10},
                "apps": {"shell-scoped": {"accent": "#ff7a00"}},
            }
        ),
        encoding="utf-8",
    )
    window = cast(Any, QtToolWindow(title="覆盖", config_name="shell-scoped", min_size=(400, 300)))
    other = cast(Any, QtToolWindow(title="其它", config_name="shell-other", min_size=(400, 300)))
    try:
        assert window.theme().palette.accent == "#ff7a00"
        assert window.theme().metrics.radius == 10
        assert other.theme().palette.accent == _guitheme.resolve_theme("dark-blue").palette.accent
        # 切主题保留覆盖段：切到 light 后自定义 accent 仍在，QSS 按新主题重建。
        window.set_theme(_guitheme.resolve_theme("light"))
        assert window.theme().name == "light"
        assert _guitheme.load_theme(app="shell-scoped").name == "light"
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["apps"]["shell-scoped"]["accent"] == "#ff7a00"
        assert _qt_theme.active_theme(QApplication.instance()).name == "light"
    finally:
        window.deleteLater()
        other.deleteLater()
