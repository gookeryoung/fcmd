"""which 工具测试（DSL 内建动作声明 commands/which.toml）。

验证 ``fcmd which`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明，别名 wch）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：逐条查找回显（shutil.which）、未找到提示、批量多条目
"""

from __future__ import annotations

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import _TOOL_ALIASES, ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 which）

_NOT_EXIST = "this_command_does_not_exist_xyz123"


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestWhichRegistration:
    """which 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """which 注册为内置 DSL 单命令工具。"""
        assert "which" in _TOOL_REGISTRY
        assert set(_TOOL_REGISTRY["which"]) == {None}

    def test_alias_wch(self) -> None:
        """别名 wch 指向 which（pyproject 入口名，避开系统 which 命令）。"""
        assert _TOOL_ALIASES.get("wch") == "which"

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        spec: ToolSpec = _TOOL_REGISTRY["which"][None]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == "which"
        assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """CLI 参数 schema 拷贝自动作实现签名：commands 为多值位置参数。"""
        spec = _TOOL_REGISTRY["which"][None]
        params = list(spec.func.__signature__.parameters)  # type: ignore[attr-defined]
        assert params == ["commands"]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestWhichRun:
    """``fcmd which`` 执行语义。"""

    def test_found_prints_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """存在的命令打印 ``<cmd> -> <path>``（python 在测试环境必定存在）。"""
        assert run_tool("which", ["python"]) == 0
        out = capsys.readouterr().out
        assert "python" in out
        assert "->" in out
        assert "未找到" not in out

    def test_not_found_prints_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """不存在的命令打印未找到，退出码 0。"""
        assert run_tool("which", [_NOT_EXIST]) == 0
        assert "未找到" in capsys.readouterr().out

    def test_multiple_commands(self, capsys: pytest.CaptureFixture[str]) -> None:
        """批量查找：逐条回显，每个命令一行。"""
        assert run_tool("which", ["python", _NOT_EXIST]) == 0
        result_lines = [ln for ln in capsys.readouterr().out.splitlines() if "->" in ln]
        assert len(result_lines) == 2

    def test_alias_entry_resolves(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """别名 wch 经 resolve_tool 解析后可正常执行。"""
        from fcmd.cli._discovery import resolve_tool

        monkeypatch.setattr("sys.argv", ["wch", "python"])
        assert run_tool(resolve_tool("wch"), ["python"]) == 0  # type: ignore[arg-type]
        assert "python" in capsys.readouterr().out
