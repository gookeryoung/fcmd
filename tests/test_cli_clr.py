"""clr 工具测试。

验证 ``fcmd.cli.clr`` 模块：
- 工具注册
- 跨平台清屏（Windows 用 cls，Linux 用 clear）
- 失败与未找到分支
- CLI 调度
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.clr
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.clr import clear_screen


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """clr 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """clr 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("clr",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_clr_single_command(self) -> None:
        """clr 是单命令工具。"""
        assert fx.list_subcommands("clr") == []


# ============================================================================ #
# clr 测试
# ============================================================================ #
class TestClr:
    """clr 工具测试。"""

    def test_clear_screen_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows 下 clear_screen 调用 cls（shell=True）。"""
        monkeypatch.setattr(sys, "platform", "win32")
        captured: list[tuple[Any, dict[str, Any]]] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        result = clear_screen()
        assert result == 0
        # Windows 传字符串 cmd="cls" + shell=True
        assert captured[0][0] == "cls"
        assert captured[0][1].get("shell") is True

    def test_clear_screen_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Linux 下 clear_screen 调用 clear（shell=False）。"""
        monkeypatch.setattr(sys, "platform", "linux")
        captured: list[tuple[Any, dict[str, Any]]] = []

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        result = clear_screen()
        assert result == 0
        # Linux 传字符串 cmd="clear" + shell=False
        assert captured[0][0] == "clear"
        assert captured[0][1].get("shell") is False

    def test_clear_screen_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """clear_screen 失败时返回非零退出码（不抛异常）。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        assert clear_screen() == 1

    def test_clear_screen_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """clear_screen 命令未找到时抛 RuntimeError（含命令名）。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "系统找不到指定的文件。")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="清屏命令未找到: clear"):
            clear_screen()

    def test_clr_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd clr 通过 run_tool 调用（fn 任务，mock subprocess.run）。"""

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("fcmd.cli.clr.subprocess.run", fake_run)
        code = run_tool("clr", [])
        assert code == 0
