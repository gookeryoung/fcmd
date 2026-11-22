"""Qt 绑定版本检测测试：真实绑定、3.10 口径与未安装回退。"""

from __future__ import annotations

import sys

import pytest

from fcmd.gui import compat


def test_binding_version_reports_installed_binding() -> None:
    """测试环境已装 PySide，返回「绑定名 + 版本」。"""
    name, version = compat.binding_version().split(" ", 1)
    assert name in {"PySide2", "PySide6"}
    assert version


def test_binding_name_prefers_pyside2_on_python310(monkeypatch: pytest.MonkeyPatch) -> None:
    """3.10 且装了 PySide2 时绑定为 PySide2，与兼容层的映射口径一致。"""
    monkeypatch.setattr(sys, "version_info", (3, 10, 9))
    monkeypatch.setattr(compat.importlib.util, "find_spec", lambda _name: object())
    assert compat._binding_name() == "PySide2"


def test_binding_version_reports_missing_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    """绑定模块不可导入时返回「未安装」，不抛异常。"""
    monkeypatch.setattr(compat, "_binding_name", lambda: "PySide9")
    assert compat.binding_version() == "未安装"
