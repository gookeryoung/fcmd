"""taskkill 工具测试。

验证 ``fcmd.cli.system.taskkill`` 模块：
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
import fcmd.cli.system.taskkill
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.system.taskkill import kill_process, taskkill_run


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
        monkeypatch.setattr("fcmd.cli.system.taskkill.subprocess.run", _subprocess_run_success)
        assert kill_process("chrome.exe") == 0

    def test_kill_process_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """kill_process 返回 1 表示未找到匹配进程。"""

        def run_not_found(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args[0], 1, "", "")

        monkeypatch.setattr("fcmd.cli.system.taskkill.subprocess.run", run_not_found)
        assert kill_process("nonexistent") == 1

    def test_kill_process_windows_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 下 kill_process 用系统 taskkill.exe 绝对路径 + /FI 过滤器。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.system.taskkill.subprocess.run", _recording_subprocess_run(captured))
        kill_process("chrome.exe")
        # 必须使用绝对路径，避免递归调用 fcmd 自身的 taskkill entry script
        assert captured[0][0] == r"C:\Windows\System32\taskkill.exe"
        assert "/f" in captured[0]
        # 用 /FI 过滤器替代 /IM 通配符（Win7 兼容）
        assert "/fi" in captured[0]
        assert "imagename eq chrome.exe*" in captured[0]

    def test_kill_process_windows_no_recursive_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 下 kill_process 不会调用 fcmd 自身的 taskkill entry script。

        回归测试：曾因 ``taskkill`` 与系统 taskkill.exe 同名，subprocess.run
        递归调用 fcmd entry 导致进程爆炸。修复后必须使用系统绝对路径。
        """
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.system.taskkill.subprocess.run", _recording_subprocess_run(captured))
        kill_process("explorer")
        # 命令首元素必须是绝对路径，不能是裸 "taskkill"（会触发 PATH 查找）
        assert captured[0][0].endswith("taskkill.exe")
        assert "\\" in captured[0][0]
        assert captured[0][0] != "taskkill"

    def test_kill_process_linux_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 下 kill_process 用 pkill。"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.system.taskkill.subprocess.run", _recording_subprocess_run(captured))
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
        monkeypatch.setattr("fcmd.cli.system.taskkill.kill_process", lambda *_: 0)
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
        monkeypatch.setattr("fcmd.cli.system.taskkill.kill_process", lambda *_: 1)
        taskkill_run(["nonexistent"])
        out = capsys.readouterr().out
        assert "未找到匹配进程" in out

    def test_taskkill_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd taskkill <names> 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.system.taskkill.kill_process", lambda *_: 0)
        code = run_tool("taskkill", ["chrome.exe"])
        assert code == 0
        out = capsys.readouterr().out
        assert "chrome.exe" in out
