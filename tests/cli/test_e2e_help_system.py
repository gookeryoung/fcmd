"""系统 / 网络 / 构建类工具的 ``--help`` 冒烟。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.cli._e2e_help import assert_help_ok
from tests.conftest import CmdResult

TOOLS = [
    "sysinfo",
    "which",
    "iptool",
    "nettool",
    "portcheck",
    "bumpversion",
    "packtool",
    "pymake",
    "autofmt",
    "writefile",
    "taskk",
    "gitt",
]


@pytest.mark.e2e_smoke
@pytest.mark.parametrize("tool", TOOLS)
def test_tool_help_exists(fcmd: Callable[..., CmdResult], tool: str) -> None:
    """工具 --help 不崩溃，退出码 0 或 1。"""
    assert_help_ok(fcmd, tool)
