"""autofmt 工具测试。

验证 ``fcmd.cli.autofmt`` 模块：
- 工具注册
- fmt 子命令（ruff format）
- lint 子命令（ruff check）
- CLI 调度
"""

from __future__ import annotations

from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.autofmt
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.autofmt import fmt, lint
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助：创建 fake run_command 函数（避免 lambda ARG005）
# ============================================================================ #
def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


def _success_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回成功结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """autofmt 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """autofmt 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("autofmt",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_autofmt_subcommands(self) -> None:
        """autofmt 应有 fmt/lint 子命令。"""
        subs = fx.list_subcommands("autofmt")
        assert "fmt" in subs
        assert "lint" in subs


# ============================================================================ #
# autofmt 测试
# ============================================================================ #
class TestAutofmt:
    """autofmt 工具测试。"""

    def test_fmt_default_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fmt 默认目标为当前目录。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _recording_run(calls))
        fmt()
        assert calls[0] == ["ruff", "format", "."]
        out = capsys.readouterr().out
        assert "ruff format 完成" in out

    def test_fmt_with_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fmt 指定目标路径。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _recording_run(calls))
        fmt("src")
        assert calls[0] == ["ruff", "format", "src"]

    def test_lint_default_no_fix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lint 默认不自动修复。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _recording_run(calls))
        lint()
        assert calls[0] == ["ruff", "check", "."]
        assert "--fix" not in calls[0]

    def test_lint_with_fix(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lint --fix 添加 --fix --unsafe-fixes。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _recording_run(calls))
        lint("src", fix=True)
        assert "ruff" in calls[0]
        assert "check" in calls[0]
        assert "src" in calls[0]
        assert "--fix" in calls[0]
        assert "--unsafe-fixes" in calls[0]

    def test_lint_with_target(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lint 指定目标路径。"""
        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _recording_run(calls))
        lint("tests")
        assert calls[0] == ["ruff", "check", "tests"]

    def test_fmt_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd autofmt fmt 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _success_run)
        code = run_tool("autofmt", ["fmt"])
        assert code == 0
        out = capsys.readouterr().out
        assert "ruff format 完成" in out

    def test_lint_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd autofmt lint --target src --fix 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.autofmt.run_command", _success_run)
        code = run_tool("autofmt", ["lint", "--target", "src", "--fix"])
        assert code == 0
        out = capsys.readouterr().out
        assert "ruff check 完成" in out
