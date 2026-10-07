"""sysinfo 工具测试（DSL 内建动作声明 commands/sysinfo.toml）。

验证 ``fcmd sysinfo`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明，无参数）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：收集并打印系统信息（Python 版本/磁盘/CPU 等）
- 字节格式化辅助 _format_bytes
"""

from __future__ import annotations

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.dsl.actions import _format_bytes

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 sysinfo）


# ---------------------------------------------------------------------- #
# 注册与声明验证
# ---------------------------------------------------------------------- #
class TestSysinfoRegistration:
    """sysinfo 经内置 DSL（action 原语）注册。"""

    def test_registered_as_dsl_single_command(self) -> None:
        """sysinfo 注册为内置 DSL 单命令工具。"""
        assert "sysinfo" in _TOOL_REGISTRY
        assert set(_TOOL_REGISTRY["sysinfo"]) == {None}

    def test_action_contract(self) -> None:
        """合成函数携带 __dsl_action__ 标记，cmd 为 None（fn 任务形态）。"""
        spec: ToolSpec = _TOOL_REGISTRY["sysinfo"][None]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == "sysinfo"
        assert not getattr(spec.func, "__dsl_empty_body__", False)

    def test_signature_from_action_impl(self) -> None:
        """无参数动作：签名不含任何参数。"""
        spec = _TOOL_REGISTRY["sysinfo"][None]
        assert list(spec.func.__signature__.parameters) == []  # type: ignore[attr-defined]


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestSysinfoRun:
    """``fcmd sysinfo`` 执行语义。"""

    def test_prints_sysinfo(self, capsys: pytest.CaptureFixture[str]) -> None:
        """打印系统信息表头与关键条目。"""
        assert run_tool("sysinfo", []) == 0
        out = capsys.readouterr().out
        assert "系统信息" in out
        assert "Python 版本" in out
        assert "CPU 核心数" in out
        assert "工作目录" in out

    def test_disk_info_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        """磁盘信息（当前目录所在分区）正常收集。"""
        assert run_tool("sysinfo", []) == 0
        out = capsys.readouterr().out
        assert "磁盘总量" in out
        assert "磁盘可用" in out


# ---------------------------------------------------------------------- #
# _format_bytes 辅助
# ---------------------------------------------------------------------- #
class TestFormatBytes:
    """字节格式化辅助。"""

    @pytest.mark.parametrize(
        ("size", "expected"),
        [
            (0, "0.0 B"),
            (512, "512.0 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024**2, "1.0 MB"),
            (1024**3, "1.0 GB"),
        ],
    )
    def test_format(self, size: int, expected: str) -> None:
        """字节按 1024 进制格式化为人类可读字符串。"""
        assert _format_bytes(size) == expected
