"""clr 工具测试。

验证 ``fcmd clr``：
- 工具注册（单命令平台命令）
- Windows 使用 ``cls`` shell 命令，其他平台使用 ``clear`` 列表命令
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool


def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    """构造捕获 cmd 与 kwargs 的 subprocess.run 替身。"""

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


class TestRegistration:
    """clr 注册为单命令工具。"""

    def test_registered(self) -> None:
        """clr 已注册且无子命令。"""
        assert "clr" in list_tools()
        assert list_subcommands("clr") == []


class TestClrRun:
    """``fcmd clr`` 执行语义。"""

    def test_run_executes_platform_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """平台分支在模块导入期选定：Windows 走 shell，其他平台直接执行 clear。"""
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("clr", []) == 0
        cmd, kwargs = captured[0]
        if sys.platform == "win32":
            assert cmd == "cls"
            assert kwargs["shell"] is True
        else:
            assert cmd == ["clear"]
            assert not kwargs.get("shell", False)
