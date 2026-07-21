"""setenv 工具测试。

验证 ``fcmd.cli.setenv`` 模块：
- 工具注册
- setenv_run 环境变量设置
"""

from __future__ import annotations

import os

import pytest

import fcmd as fx
import fcmd.cli.setenv
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """setenv 工具注册验证。"""

    def test_all_tools_registered(self) -> None:
        """setenv 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("setenv",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_setenv_single_command(self) -> None:
        """setenv 是单命令工具。"""
        assert fx.list_subcommands("setenv") == []


# ============================================================================ #
# setenv 测试
# ============================================================================ #
class TestSetenv:
    """setenv 工具测试。"""

    def test_setenv_overwrite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=False 时覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_SETENV", "old")
        fcmd.cli.setenv.setenv_run("FCMD_TEST_SETENV", "new")
        assert os.environ["FCMD_TEST_SETENV"] == "new"

    def test_setenv_default_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=True 时不覆盖已有值。"""
        monkeypatch.setenv("FCMD_TEST_SETENV", "old")
        fcmd.cli.setenv.setenv_run("FCMD_TEST_SETENV", "new", default=True)
        assert os.environ["FCMD_TEST_SETENV"] == "old"

    def test_setenv_default_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """default=True 且变量未设置时设置值。"""
        monkeypatch.delenv("FCMD_TEST_SETENV", raising=False)
        fcmd.cli.setenv.setenv_run("FCMD_TEST_SETENV", "new", default=True)
        assert os.environ["FCMD_TEST_SETENV"] == "new"

    def test_setenv_via_run_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 run_tool 调用 setenv。"""
        monkeypatch.delenv("FCMD_TEST_SETENV_CLI", raising=False)
        run_tool("setenv", ["FCMD_TEST_SETENV_CLI", "cli_val"])
        assert os.environ["FCMD_TEST_SETENV_CLI"] == "cli_val"
