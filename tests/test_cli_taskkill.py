"""taskkill 工具测试。

验证 ``fcmd.cli.taskkill`` 模块：
- 工具注册
- 进程终止（跨平台）
- CLI 调度
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.taskkill
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.taskkill import kill_process, taskkill_run


# ============================================================================ #
# 测试辅助：创建 fake subprocess.run 函数（避免 lambda ARG005）
# ============================================================================ #
def _recording_subprocess_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``subprocess.run`` 函数。"""

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args[0])
        return subprocess.CompletedProcess(args[0], 0, "", "")

    return run


def _subprocess_run_success(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """总是返回成功结果的 fake ``subprocess.run`` 函数。"""
    return subprocess.CompletedProcess(args[0], 0, "", "")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """taskkill 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """taskkill 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("taskkill",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_taskkill_single_command(self) -> None:
        """taskkill 是单命令工具。"""
        assert fx.list_subcommands("taskkill") == []


# ============================================================================ #
# taskkill 测试
# ============================================================================ #
class TestTaskkill:
    """taskkill 工具测试。"""

    def test_kill_process_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """kill_process 返回 0 表示终止信号已发送。"""
        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", _subprocess_run_success)
        assert kill_process("chrome.exe") == 0

    def test_kill_process_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """kill_process 返回 1 表示未找到匹配进程。"""

        def run_not_found(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args[0], 1, "", "")

        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", run_not_found)
        assert kill_process("nonexistent") == 1

    def test_kill_process_windows_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 下 kill_process 用 taskkill。"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", _recording_subprocess_run(captured))
        kill_process("chrome.exe")
        assert captured[0][0] == "taskkill"
        assert "/f" in captured[0]
        assert "/im" in captured[0]
        assert "chrome.exe*" in captured[0]

    def test_kill_process_linux_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 下 kill_process 用 pkill。"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.taskkill.subprocess.run", _recording_subprocess_run(captured))
        kill_process("python")
        assert captured[0][0] == "pkill"
        assert "-f" in captured[0]
        assert "python*" in captured[0]

    def test_taskkill_run_multiple(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """taskkill_run 批量终止进程。"""
        monkeypatch.setattr("fcmd.cli.taskkill.kill_process", lambda *_: 0)
        taskkill_run(["chrome.exe", "python"])
        out = capsys.readouterr().out
        assert "chrome.exe" in out
        assert "python" in out
        assert "已发送终止信号" in out

    def test_taskkill_run_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """taskkill_run 未找到进程时打印提示。"""
        monkeypatch.setattr("fcmd.cli.taskkill.kill_process", lambda *_: 1)
        taskkill_run(["nonexistent"])
        out = capsys.readouterr().out
        assert "未找到匹配进程" in out

    def test_taskkill_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd taskkill <names> 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.taskkill.kill_process", lambda *_: 0)
        code = run_tool("taskkill", ["chrome.exe"])
        assert code == 0
        out = capsys.readouterr().out
        assert "chrome.exe" in out
