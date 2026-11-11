"""autofmt 工具测试（普通 Python 工具模块）。

验证：
- 工具注册与子命令可见性
- ``fmt`` 纯 exec 子命令（cmd 模板插值）
- ``lint`` 的 ``--fix`` 追加 ``--fix --unsafe-fixes``
- 失败返回非零退出码
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import fcmd as fx
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool

ensure_tools_discovered()


def _fake_run(captured: list[Any], returncode: int = 0) -> Any:
    def run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return run


class TestAutofmtRegistration:
    """autofmt 工具注册验证。"""

    def test_registered(self) -> None:
        assert "autofmt" in _TOOL_REGISTRY

    def test_subcommands(self) -> None:
        assert fx.list_subcommands("autofmt") == ["fmt", "lint"]


class TestAutofmtFmt:
    """``autofmt fmt`` 纯 exec 语义。"""

    def test_fmt_via_run_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run(captured))
        assert run_tool("autofmt", ["fmt"]) == 0
        assert captured[0] == ["ruff", "format", "."]

    def test_fmt_custom_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run(captured))
        assert run_tool("autofmt", ["fmt", "--target", "src"]) == 0
        assert captured[0] == ["ruff", "format", "src"]


class TestAutofmtLint:
    """``autofmt lint`` 的 fix 语义。"""

    def test_lint_default_no_fix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.tools.dev.autofmt.subprocess.run", _fake_run(captured))
        assert run_tool("autofmt", ["lint"]) == 0
        assert captured[0] == ["ruff", "check", "."]

    def test_lint_with_fix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.tools.dev.autofmt.subprocess.run", _fake_run(captured))
        assert run_tool("autofmt", ["lint", "--target", "src", "--fix"]) == 0
        assert captured[0] == ["ruff", "check", "src", "--fix", "--unsafe-fixes"]

    def test_lint_failure_returns_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.tools.dev.autofmt.subprocess.run", _fake_run(captured, returncode=1))
        assert run_tool("autofmt", ["lint"]) == 1

    def test_lint_dry_run_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.tools.dev.autofmt.subprocess.run", _fake_run(captured))
        assert run_tool("autofmt", ["lint", "--dry-run"]) == 0
        assert captured == []
