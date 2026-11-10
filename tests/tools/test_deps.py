"""``fcmd.tools._deps`` 的单元测试。"""

from __future__ import annotations

import pytest

from fcmd.tools import _deps


def test_missing_dependency_prints_hint_and_returns_false(capsys: pytest.CaptureFixture[str]) -> None:
    assert _deps.missing_dependency("Pillow", "img") is False
    assert capsys.readouterr().out.strip() == "未安装 Pillow 库，请安装: pip install fcmd[img]"


def test_lazy_import_returns_module_when_available() -> None:
    import json

    assert _deps.lazy_import("json", "json", "core", True) is json


def test_lazy_import_returns_none_when_unavailable(capsys: pytest.CaptureFixture[str]) -> None:
    assert _deps.lazy_import("pypdf", "pypdf", "pdf", False) is None
    assert capsys.readouterr().out.strip() == "未安装 pypdf 库，请安装: pip install fcmd[pdf]"
