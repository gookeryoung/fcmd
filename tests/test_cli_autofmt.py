"""autofmt 工具测试。

autofmt 为纯 DSL 声明工具（``src/fcmd/commands/autofmt.toml``，原 Python
模块已删除）。验证：
- 工具注册与子命令可见性
- cmd 模板展开（fmt target / lint --fix on 固定 token）
- parser 接受 --fix 开关
"""

from __future__ import annotations

from typing import Any

import pytest

import fcmd as fx
from fcmd.apis._tool_args import _build_parser_for_tool
from fcmd.apis._tool_exec import _build_task_spec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _fake_subprocess_run(captured: list[Any]) -> Any:
    """创建记录调用的 fake ``subprocess.run``，返回成功结果。"""

    def run(cmd: Any, **kwargs: Any) -> Any:
        captured.append(cmd)
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    return run


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """autofmt 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """autofmt 应在 _TOOL_REGISTRY 中注册。"""
        assert "autofmt" in _TOOL_REGISTRY, "工具 'autofmt' 未注册"

    def test_autofmt_subcommands(self) -> None:
        """autofmt 应有 fmt/lint 子命令。"""
        subs = fx.list_subcommands("autofmt")
        assert "fmt" in subs
        assert "lint" in subs


# ============================================================================ #
# cmd 模板展开
# ============================================================================ #
class TestAutofmtCmdExpansion:
    """autofmt DSL 子命令的 cmd 模板展开。"""

    def test_fmt_with_target(self) -> None:
        """fmt 的 target 插值到 cmd。"""
        spec = _TOOL_REGISTRY["autofmt"]["fmt"]
        task = _build_task_spec(spec, {"target": "src"})
        assert task.cmd == ["ruff", "format", "src"]

    def test_fmt_default_target(self) -> None:
        """fmt 默认目标为当前目录（CLI 解析默认值后传入）。"""
        spec = _TOOL_REGISTRY["autofmt"]["fmt"]
        task = _build_task_spec(spec, {"target": "."})
        assert task.cmd == ["ruff", "format", "."]

    def test_lint_default_no_fix(self) -> None:
        """lint 默认不自动修复。"""
        spec = _TOOL_REGISTRY["autofmt"]["lint"]
        task = _build_task_spec(spec, {"target": ".", "fix": False})
        assert task.cmd == ["ruff", "check", "."]

    def test_lint_with_fix(self) -> None:
        """lint --fix 追加 --fix --unsafe-fixes 固定 token。"""
        spec = _TOOL_REGISTRY["autofmt"]["lint"]
        task = _build_task_spec(spec, {"target": "src", "fix": True})
        assert task.cmd == ["ruff", "check", "src", "--fix", "--unsafe-fixes"]

    def test_lint_parser_accepts_fix(self) -> None:
        """lint parser 接受 --fix 开关与 --target 选项。"""
        spec = _TOOL_REGISTRY["autofmt"]["lint"]
        parsed = _build_parser_for_tool(spec).parse_args(["--target", "src", "--fix"])
        assert parsed.target == "src"
        assert parsed.fix is True


# ============================================================================ #
# run_tool 集成
# ============================================================================ #
class TestAutofmtRunTool:
    """autofmt 通过 run_tool 集成测试。"""

    def test_fmt_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd autofmt fmt 通过 run_tool 调用。"""
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_subprocess_run(captured))
        assert run_tool("autofmt", ["fmt"]) == 0
        assert captured[0] == ["ruff", "format", "."]

    def test_lint_fix_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd autofmt lint --target src --fix 通过 run_tool 调用。"""
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_subprocess_run(captured))
        assert run_tool("autofmt", ["lint", "--target", "src", "--fix"]) == 0
        assert captured[0] == ["ruff", "check", "src", "--fix", "--unsafe-fixes"]
