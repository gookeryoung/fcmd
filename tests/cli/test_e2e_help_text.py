"""文本 / 数据 / 哈希类工具的 ``--help`` 冒烟。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.cli._e2e_help import assert_help_ok
from tests.conftest import CmdResult

TOOLS = [
    "txttool",
    "padtool",
    "regextool",
    "textdiff",
    "convtool",
    "casetool",
    "colortool",
    "csvtool",
    "inifile",
    "tomltool",
    "xmltool",
    "yamtool",
    "jsontool",
    "jsont",
    "wch",
    "cryptool",
    "hashtool",
    "hashfile",
    "idtool",
]


@pytest.mark.e2e_smoke
@pytest.mark.parametrize("tool", TOOLS)
def test_tool_help_exists(fcmd: Callable[..., CmdResult], tool: str) -> None:
    """工具 --help 不崩溃，退出码 0 或 1。"""
    assert_help_ok(fcmd, tool)
