"""sysinfo 工具测试（DSL 内建动作声明 commands/sysinfo.toml）。

验证 ``fcmd sysinfo`` 的 DSL action 迁移语义：
- 工具注册（单命令 DSL 工具，内置声明，无参数）
- 声明契约：``__dsl_action__`` 标记、无 cmd（fn 任务形态）
- 执行语义：基于 psutil 收集并打印系统信息（内存/交换区/磁盘/CPU/开机时间）
- 字节格式化辅助 _format_bytes
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.core.toolkit.args import ToolSpec
from fcmd.dsl.actions.system import _format_bytes

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 sysinfo）

_GB = 1024**3


def _patch_psutil(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> None:
    """用固定数值替换 psutil 的系统信息接口，保证输出可断言。"""

    def _mem(kind: str = "") -> Any:
        return {
            "virtual": SimpleNamespace(total=16 * _GB, used=8 * _GB, available=6 * _GB, percent=50.0),
            "swap": SimpleNamespace(total=4 * _GB, used=_GB),
        }[kind]

    def _disk_usage(path: str) -> Any:
        assert path
        return SimpleNamespace(total=512 * _GB, used=128 * _GB, free=384 * _GB)

    defaults: dict[str, Any] = {
        "virtual_memory": lambda: _mem("virtual"),
        "swap_memory": lambda: _mem("swap"),
        "disk_usage": _disk_usage,
        "cpu_count": lambda: 8,
        "boot_time": lambda: 1_700_000_000.0,
    }
    defaults.update(overrides)
    for name, fake in defaults.items():
        monkeypatch.setattr(psutil, name, fake)


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
    """``fcmd sysinfo`` 执行语义（psutil 数据源）。"""

    def test_prints_sysinfo(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """打印系统信息表头与关键条目。"""
        _patch_psutil(monkeypatch)
        assert run_tool("sysinfo", []) == 0
        out = capsys.readouterr().out
        assert "系统信息" in out
        assert "Python 版本" in out
        assert "CPU 核心数" in out
        assert "工作目录" in out

    def test_memory_and_swap_from_psutil(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """内存/交换区数值来自 psutil，含百分比。"""
        _patch_psutil(monkeypatch)
        assert run_tool("sysinfo", []) == 0
        out = capsys.readouterr().out
        assert "内存总量" in out and "16.0 GB" in out
        assert "内存已用" in out and "(50.0%)" in out
        assert "内存可用" in out and "6.0 GB" in out
        assert "交换区总量" in out and "4.0 GB" in out
        assert "交换区已用" in out and "1.0 GB" in out

    def test_disk_info_present(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """磁盘信息（当前目录所在分区）正常收集。"""
        _patch_psutil(monkeypatch)
        assert run_tool("sysinfo", []) == 0
        out = capsys.readouterr().out
        assert "磁盘总量" in out and "512.0 GB" in out
        assert "磁盘已用" in out and "128.0 GB" in out
        assert "磁盘可用" in out and "384.0 GB" in out

    def test_disk_usage_error_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``disk_usage`` 抛 OSError（不支持的分区）时跳过磁盘条目，不影响其余输出。"""

        def _boom(_path: str) -> Any:
            raise OSError("unsupported")

        _patch_psutil(monkeypatch, disk_usage=_boom)
        assert run_tool("sysinfo", []) == 0
        out = capsys.readouterr().out
        assert "磁盘总量" not in out
        assert "内存总量" in out

    def test_boot_time_rendered(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """开机时间按本地时区格式化输出。"""
        _patch_psutil(monkeypatch)
        assert run_tool("sysinfo", []) == 0
        expected = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1_700_000_000.0))
        out = capsys.readouterr().out
        assert "开机时间" in out
        assert expected in out

    def test_boot_time_error_is_tolerated(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``boot_time`` 抛 OSError 时跳过该条目。"""

        def _boom() -> float:
            raise OSError("unsupported")

        _patch_psutil(monkeypatch, boot_time=_boom)
        assert run_tool("sysinfo", []) == 0
        assert "开机时间" not in capsys.readouterr().out

    def test_cpu_count_unknown(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """``cpu_count`` 返回 None 时显示未知。"""
        _patch_psutil(monkeypatch, cpu_count=lambda: None)
        assert run_tool("sysinfo", []) == 0
        assert "CPU 核心数" in capsys.readouterr().out


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
