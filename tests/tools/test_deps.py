"""``fcmd.tools._deps`` 的单元测试。"""

from __future__ import annotations

import pytest

from fcmd.tools import _deps


def test_missing_dependency_prints_hint_and_returns_false(capsys: pytest.CaptureFixture[str]) -> None:
    """可选依赖缺失：提示对应的 extra 安装命令。"""
    assert _deps.missing_dependency("resvg-py", "svg") is False
    assert capsys.readouterr().out.strip() == "未安装 resvg-py 库，请安装: pip install fcmd[svg]"


def test_missing_required_dependency_suggests_reinstall(capsys: pytest.CaptureFixture[str]) -> None:
    """常驻依赖（extra=None）缺失说明安装不完整：提示重装，不指向不存在的 extra。"""
    assert _deps.missing_dependency("Pillow", None) is False
    out = capsys.readouterr().out.strip()
    assert out.startswith("未安装 Pillow 库")
    assert "fcmd[" not in out
    assert "重新安装 fcmd" in out


def test_lazy_import_returns_module_when_available() -> None:
    import json

    assert _deps.lazy_import("json", "json", "core", True) is json


def test_lazy_import_returns_none_when_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    assert _deps.lazy_import("pypdf", "pypdf", "pdf", False) is None
    assert capsys.readouterr().out.strip() == "未安装 pypdf 库，请安装: pip install fcmd[pdf]"
