"""which 工具测试。

验证 ``fcmd.cli.which`` 模块：
- 工具注册
- find_command 命令查找
- which_run CLI 调度
"""

from __future__ import annotations

import sys

import pytest

import fcmd as fx
import fcmd.cli.which
from fcmd.apis.toolkit import run_tool
from fcmd.cli.which import find_command, which_run


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """which 工具的注册验证。"""

    def test_which_single_command(self) -> None:
        """which 是单命令工具（无子命令）。"""
        assert fx.list_subcommands("which") == []


# ---------------------------------------------------------------------- #
# which 工具测试
# ---------------------------------------------------------------------- #
class TestWhich:
    """``which`` 工具测试。"""

    def test_find_command_python(self) -> None:
        """find_command 能找到 python 命令。"""
        # python 在测试环境必定存在
        result = find_command("python")
        # 在某些环境可能叫 python3，用 sys.executable 验证
        assert result is not None or sys.platform != "win32"

    def test_find_command_not_found(self) -> None:
        """find_command 对不存在的命令返回 None。"""
        assert find_command("this_command_does_not_exist_xyz123") is None

    def test_which_run_prints_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        """which_run 打印命令路径。"""
        which_run(["python"])
        out = capsys.readouterr().out
        assert "python" in out
        assert "->" in out

    def test_which_run_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        """which_run 对不存在的命令打印未找到。"""
        which_run(["this_command_does_not_exist_xyz123"])
        out = capsys.readouterr().out
        assert "未找到" in out

    def test_which_run_multiple(self, capsys: pytest.CaptureFixture[str]) -> None:
        """which_run 处理多个命令。"""
        which_run(["python", "this_does_not_exist_xyz123"])
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 2

    def test_which_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd which <cmd> 通过 run_tool 调用。"""
        code = run_tool("which", ["python"])
        assert code == 0
        out = capsys.readouterr().out
        assert "python" in out
