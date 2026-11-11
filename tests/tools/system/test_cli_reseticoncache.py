"""reseticoncache 工具测试。

验证 ``fcmd reseticoncache``：
- 工具注册（单命令平台命令）
- 平台分支在模块导入期选定：Windows 为 cmd.exe 重置链，其他平台仅打印提示
- 执行走引擎 shell 路径，退出码 0
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool

# win.cmd shell 链关键片段（绝对路径 taskkill 防 PATH 递归调用 fcmd entry）
_TASKKILL_SNIPPET = r"%SystemRoot%\System32\taskkill.exe /f /im explorer.exe"
_DEL_DB_SNIPPET = r'if exist "%LOCALAPPDATA%\IconCache.db" del /a /q "%LOCALAPPDATA%\IconCache.db"'
_DEL_GLOB_SNIPPET = r'if exist "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*" del /a /q "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*"'
_START_SNIPPET = "start explorer.exe"


def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    """构造捕获 cmd 与 kwargs 的 subprocess.run 替身。"""

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


class TestRegistration:
    """reseticoncache 注册为单命令工具。"""

    def test_registered(self) -> None:
        """reseticoncache 已注册且无子命令。"""
        assert "reseticoncache" in list_tools()
        assert list_subcommands("reseticoncache") == []


class TestReseticoncacheRun:
    """``fcmd reseticoncache`` 执行语义。"""

    def test_run_executes_platform_branch(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """执行走引擎 shell 路径：平台分支在注册时选定。"""
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("reseticoncache", []) == 0
        cmd = captured[0][0]
        assert isinstance(cmd, str)  # str cmd → 引擎 shell=True 执行
        assert captured[0][1]["shell"] is True
        if sys.platform == "win32":
            for snippet in (_TASKKILL_SNIPPET, _DEL_DB_SNIPPET, _DEL_GLOB_SNIPPET, _START_SNIPPET):
                assert snippet in cmd
            assert "%LOCALAPPDATA%" in cmd
        else:
            assert cmd == 'echo "reseticoncache: 仅在 Windows 上支持"'
