"""``fcmd.tools._deps`` 的单元测试。"""

from __future__ import annotations

import pytest

from fcmd.tools import _deps


def test_missing_dependency_prints_hint_and_returns_false(capsys: pytest.CaptureFixture[str]) -> None:
    assert _deps.missing_dependency("Pillow", "img") is False
    assert capsys.readouterr().out.strip() == "未安装 Pillow 库，请安装: pip install fcmd[img]"
