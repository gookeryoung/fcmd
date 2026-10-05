"""reseticoncache 工具测试（DSL 声明 commands/reseticoncache.toml）。

验证 ``fcmd reseticoncache`` 的 DSL 迁移语义：
- 工具注册（单命令 DSL 工具，win/unix 双平台分支）
- win.cmd 为 cmd.exe shell 链：taskkill 绝对路径（防递归回归）+ if exist
  守卫删除 + start explorer
- unix 分支仅打印平台提示
- 执行走引擎 shell 路径，退出码 0
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

import fcmd as fx
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.dsl.decl import CommandDecl
from fcmd.dsl.synth import select_platform_cmd

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 reseticoncache）

# win.cmd shell 链关键片段（绝对路径 taskkill 防 PATH 递归调用 fcmd entry）
_TASKKILL_SNIPPET = r"%SystemRoot%\System32\taskkill.exe /f /im explorer.exe"
_DEL_DB_SNIPPET = r'if exist "%LOCALAPPDATA%\IconCache.db" del /a /q "%LOCALAPPDATA%\IconCache.db"'
_DEL_GLOB_SNIPPET = r'if exist "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*" del /a /q "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*"'
_START_SNIPPET = "start explorer.exe"


# ---------------------------------------------------------------------- #
# 测试辅助
# ---------------------------------------------------------------------- #
def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    """构造捕获 cmd 与 kwargs 的 subprocess.run 替身。"""

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


def _builtin_decl() -> CommandDecl:
    """从内置声明中取 reseticoncache 的 CommandDecl（供平台分支直测）。"""
    from fcmd.dsl import builtin_tool_decls

    for tool in builtin_tool_decls():
        if tool.name == "reseticoncache":
            return tool.commands[0]
    raise AssertionError("内置声明中未找到 reseticoncache")  # pragma: no cover


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestReseticoncacheRegistration:
    """reseticoncache 经内置 DSL 注册。"""

    def test_registered_as_dsl(self) -> None:
        """reseticoncache 注册为内置 DSL 单命令工具。"""
        assert "reseticoncache" in _TOOL_REGISTRY
        assert fx.list_subcommands("reseticoncache") == []

    def test_win_cmd_shell_chain(self) -> None:
        """win.cmd 为一条 cmd.exe shell 链：taskkill 绝对路径 + if exist 删除 + start。"""
        win_cmd = select_platform_cmd(_builtin_decl(), "win32")
        assert isinstance(win_cmd, str)
        for snippet in (_TASKKILL_SNIPPET, _DEL_DB_SNIPPET, _DEL_GLOB_SNIPPET, _START_SNIPPET):
            assert snippet in win_cmd
        # 顺序：终止 explorer 在删除之前，start 在最后
        assert win_cmd.index(_TASKKILL_SNIPPET) < win_cmd.index(_DEL_DB_SNIPPET)
        assert win_cmd.index(_DEL_GLOB_SNIPPET) < win_cmd.index(_START_SNIPPET)

    def test_taskkill_uses_absolute_path(self) -> None:
        """回归：taskkill 必须经 %SystemRoot% 绝对路径调用，禁止裸命令名。

        fcmd 自身注册的 ``taskkill`` entry 与系统 taskkill.exe 同名，裸命令名
        经 PATH 查找可能递归调用 fcmd 自身导致进程爆炸。
        """
        win_cmd = select_platform_cmd(_builtin_decl(), "win32")
        assert isinstance(win_cmd, str)
        assert "%SystemRoot%\\System32\\taskkill.exe" in win_cmd
        assert "taskkill" not in win_cmd.replace("%SystemRoot%\\System32\\taskkill.exe", "")

    def test_unix_cmd_prints_hint(self) -> None:
        """unix 分支仅打印平台提示（与原版文案一致）。"""
        unix_cmd = select_platform_cmd(_builtin_decl(), "linux")
        assert unix_cmd == 'echo "reseticoncache: 仅在 Windows 上支持"'


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestReseticoncacheRun:
    """``fcmd reseticoncache`` 执行语义。"""

    def test_run_executes_platform_branch(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """执行走引擎 shell 路径：平台分支在注册时选定，Windows 命中重置链。

        ToolSpec.cmd 在发现注册时按当前平台选定（CI 为 Linux → echo 提示分支；
        Windows → 重置链），两者均为 str cmd shell 执行、退出码 0。
        """
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("reseticoncache", []) == 0
        cmd = captured[0][0]
        assert isinstance(cmd, str)  # str cmd → 引擎 shell=True 执行
        assert captured[0][1]["shell"] is True
        if sys.platform == "win32":
            assert _TASKKILL_SNIPPET in cmd
            # 环境变量由 cmd.exe 运行时展开，Python 侧保持字面量
            assert "%LOCALAPPDATA%" in cmd
        else:
            assert "仅在 Windows 上支持" in cmd
