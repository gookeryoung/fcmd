"""CLI 内置工具会话级注册测试。

验证 conftest 会话级 fixture 的工具发现契约：
- 内置 DSL 命令经 ``ensure_tools_discovered`` 注册（幂等）
- 任一测试文件单跑即可见注册表，不依赖其他文件的导入副作用
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from tests.conftest import CmdResult

pytestmark = [pytest.mark.e2e]


def test_builtin_tools_registered_in_session(fcmd: Callable[..., CmdResult]) -> None:
    """会话级 fixture 已注册内置工具：注册表非空且含 DSL 声明式命令。"""
    assert _TOOL_REGISTRY, "内置工具未注册"
    assert "txttool" in _TOOL_REGISTRY


def test_fcmd_fixture_runs_isolated(fcmd: Callable[..., CmdResult]) -> None:
    """fcmd fixture 单文件可用：子进程执行已知工具成功退出。"""
    fcmd("txttool", "--help").check_ok()
