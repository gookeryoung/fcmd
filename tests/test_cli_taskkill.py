"""taskkill 工具测试（DSL 内建动作声明 commands/taskkill.toml）。

验证 ``fcmd taskkill`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明，别名 taskk）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：Windows taskkill.exe 绝对路径 + /FI 过滤器 / Unix pkill、
  逐条目回显、未匹配提示后继续（退出码 0）
- 回归：Windows 分支禁止裸 taskkill 命令名（防 PATH 递归调用 fcmd 自身 entry）
- dry-run 不执行
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import _TOOL_ALIASES, ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 taskkill）

_FAKE_RUN: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def _recording_run(captured: list[list[str]]):
    """构造记录调用参数的 subprocess.run 替身。"""

    def _run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return _FAKE_RUN

    return _run


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestTaskkillRegistration:
    """taskkill 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """taskkill 注册为内置 DSL 单命令工具。"""
        assert "taskkill" in _TOOL_REGISTRY
        assert set(_TOOL_REGISTRY["taskkill"]) == {None}

    def test_alias_taskk(self) -> None:
        """别名 taskk 指向 taskkill（pyproject 入口名保持不变）。"""
        assert _TOOL_ALIASES.get("taskk") == "taskkill"

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        spec: ToolSpec = _TOOL_REGISTRY["taskkill"][None]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == "taskkill"
        assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：names 为多值位置参数。"""
        spec = _TOOL_REGISTRY["taskkill"][None]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["names"]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestTaskkillRun:
    """``fcmd taskkill`` 执行语义。"""

    def test_windows_uses_fi_filter(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """Windows 分支：系统 taskkill.exe 绝对路径 + /FI 过滤器通配符。"""
        captured: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SystemRoot", r"C:\Windows")
        monkeypatch.setattr("subprocess.run", _recording_run(captured))
        assert run_tool("taskkill", ["chrome.exe"]) == 0
        assert captured[0] == [r"C:\Windows\System32\taskkill.exe", "/f", "/fi", "imagename eq chrome.exe*"]
        assert "已发送终止信号: chrome.exe" in capsys.readouterr().out

    def test_windows_never_calls_bare_taskkill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回归：命令首元素必须是绝对路径，不能是裸 ``taskkill``。

        fcmd 自身注册的 ``taskkill`` entry 与系统 taskkill.exe 同名，裸命令名
        经 PATH 查找会递归调用 fcmd 自身，指数级进程爆炸直至资源耗尽。
        """
        captured: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr("subprocess.run", _recording_run(captured))
        run_tool("taskkill", ["node"])
        assert captured[0][0].endswith("taskkill.exe")
        assert captured[0][0] != "taskkill"

    def test_unix_uses_pkill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unix 分支：``pkill -f <name>*``。"""
        captured: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("subprocess.run", _recording_run(captured))
        assert run_tool("taskkill", ["python"]) == 0
        assert captured[0] == ["pkill", "-f", "python*"]

    def test_multiple_names(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """批量终止：逐条目执行并回显。"""
        captured: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("subprocess.run", _recording_run(captured))
        assert run_tool("taskkill", ["chrome.exe", "python"]) == 0
        assert len(captured) == 2
        out = capsys.readouterr().out
        assert "终止进程: chrome.exe" in out
        assert "终止进程: python" in out

    def test_not_found_continues(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """未匹配（返回码 1）打印提示后继续，退出码 0（与原版一致）。"""

        def _run_not_found(_cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=["pkill"], returncode=1, stdout="", stderr="")

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("subprocess.run", _run_not_found)
        assert run_tool("taskkill", ["nonexistent_proc"]) == 0
        assert "未找到匹配进程或终止失败" in capsys.readouterr().out

    def test_dry_run_skips_execution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--dry-run 打印计划不执行：不发起 subprocess 调用。"""
        captured: list[list[str]] = []
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr("subprocess.run", _recording_run(captured))
        assert run_tool("taskkill", ["chrome.exe", "--dry-run"]) == 0
        assert captured == []
