"""L2 准 E2E：CLI 入口级冒烟测试。

所有用例走 ``python -m fcmd`` 真实子进程，验证 console_script 入口
在干净进程中可正确解析参数、路由工具、处理错误输入。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.conftest import CmdResult


@pytest.mark.e2e_smoke
class TestCliEntry:
    """CLI 主入口行为。"""

    def test_version(self, fcmd: Callable[..., CmdResult]) -> None:
        """--version 退出码 0，输出含版本号。"""
        r = fcmd(None, "--version")
        assert r.returncode == 0
        assert "0.3.4" in r.stdout or "fcmd" in r.stdout

    def test_no_args_lists_tools(self, fcmd: Callable[..., CmdResult]) -> None:
        """无参数列出工具，退出码 0。"""
        r = fcmd(None)
        assert r.returncode == 0
        assert "可用工具" in r.stdout or "fcmd v" in r.stdout

    def test_help_flag(self, fcmd: Callable[..., CmdResult]) -> None:
        """--help 列出工具。"""
        r = fcmd(None, "--help")
        assert r.returncode == 0

    def test_unknown_tool_returns_1(self, fcmd: Callable[..., CmdResult]) -> None:
        """未知工具退出码 1。"""
        r = fcmd("nonexistent_tool_xyz", expect=1)
        assert r.returncode == 1


@pytest.mark.e2e_smoke
class TestToolHelp:
    """各工具 --help 入口冒烟。"""

    @pytest.mark.parametrize(
        "tool",
        [
            "writefile",
            "jsontool",
            "csvtool",
            "yamtool",
            "mathtool",
            "hashtool",
            "pathtool",
            "calcdate",
            "regextool",
        ],
    )
    def test_tool_help_returns_0(self, fcmd: Callable[..., CmdResult], tool: str) -> None:
        """高频工具 fcmd <tool> --help 退出码 0。"""
        r = fcmd(tool, "--help")
        assert r.returncode == 0, f"{tool} --help 退出码 {r.returncode}\n{r.stderr}"
        # help 输出应含 usage 或中文描述
        assert "usage" in r.stdout.lower() or "帮助" in r.stdout or r.stdout.strip(), f"{tool} --help 输出为空"
