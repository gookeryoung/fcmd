"""Qt 绑定版本检测测试：真实绑定、3.10 口径与未安装回退。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from fcmd.gui import compat


def test_binding_version_reports_installed_binding() -> None:
    """测试环境已装 PySide，返回「绑定名 + 版本」。"""
    name, version = compat.binding_version().split(" ", 1)
    assert name in {"PySide2", "PySide6"}
    assert version


def test_binding_name_prefers_pyside2_on_python310(monkeypatch: pytest.MonkeyPatch) -> None:
    """3.10 且装了 PySide2 时绑定为 PySide2，与兼容层的映射口径一致。

    只替换 ``compat`` 自己的 ``importlib`` 引用：直接改 ``importlib.util.find_spec``
    会波及同进程的 coverage（它在 tracing 时也调 ``find_spec``，拿到假对象后
    取 ``.origin`` 崩溃），曾把 xdist worker 打成 INTERNALERROR。
    """
    monkeypatch.setattr(sys, "version_info", (3, 10, 9))
    fake_importlib = SimpleNamespace(util=SimpleNamespace(find_spec=lambda _name: object()))
    monkeypatch.setattr(compat, "importlib", fake_importlib)
    assert compat._binding_name() == "PySide2"


def test_binding_version_reports_missing_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """绑定模块不可导入时返回「未安装」，不抛异常。"""
    monkeypatch.setattr(compat, "_binding_name", lambda: "PySide9")
    assert compat.binding_version() == "未安装"


def test_qt_import_failure_reports_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """绑定找不到时提示装 pip 包，不附带系统库说明。"""
    monkeypatch.setattr(compat, "_binding_name", lambda: "PySide9")
    message = compat.qt_import_failure(ImportError("No module named 'PySide9'"))
    assert message == "未安装 Qt 前端：pip install 'PySide6-Essentials'"


def test_qt_import_failure_reports_load_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """绑定已安装但加载失败时保留底层错误，并给出 Linux 系统库命令。"""
    monkeypatch.setattr(compat, "_binding_name", lambda: "PySide6")
    fake_importlib = SimpleNamespace(util=SimpleNamespace(find_spec=lambda _name: object()))
    monkeypatch.setattr(compat, "importlib", fake_importlib)
    message = compat.qt_import_failure(
        ImportError("libEGL.so.1: cannot open shared object file: No such file or directory")
    )
    assert "Qt 前端已安装但加载失败：libEGL.so.1" in message
    assert "sudo apt install" in message
    assert "libegl1" in message
