"""which 工具测试。

验证 ``fcmd which``：
- 工具注册（单命令，别名 wch）
- 逐条查找回显（shutil.which）、未找到提示、批量多条目
"""

from __future__ import annotations

import pytest

from fcmd.cli._discovery import resolve_tool
from fcmd.core.toolkit import list_subcommands, list_tools, run_tool

_NOT_EXIST = "this_command_does_not_exist_xyz123"


class TestRegistration:
    """which 注册为单命令工具。"""

    def test_registered(self) -> None:
        """which 已注册且无子命令。"""
        assert "which" in list_tools()
        assert list_subcommands("which") == []

    def test_alias_wch(self) -> None:
        """别名 wch 指向 which。"""
        assert resolve_tool("wch") == "which"


class TestWhichRun:
    """``fcmd which`` 执行语义。"""

    def test_found_prints_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """存在的命令打印 ``<cmd> -> <path>``。"""
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
        """别名 wch 解析后可正常执行。"""
        monkeypatch.setattr("sys.argv", ["wch", "python"])
        assert run_tool(resolve_tool("wch"), ["python"]) == 0  # type: ignore[arg-type]
        assert "python" in capsys.readouterr().out
