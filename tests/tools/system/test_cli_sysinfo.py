"""sysinfo 工具测试。

验证 ``fcmd sysinfo``：
- 工具注册（单命令）
- 基于 psutil 收集并打印系统信息（内存/交换区/磁盘/CPU/开机时间）
- 字节格式化辅助
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool
from fcmd.infras.system import collect_system_info, format_bytes
from tests.helpers import run_and_capture as _run_and_capture

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


# ============================================================================ #
# 注册
# ============================================================================ #
class TestRegistration:
    """sysinfo 注册为单命令工具。"""

    def test_registered(self) -> None:
        """sysinfo 已注册且无子命令。"""
        assert "sysinfo" in list_tools()
        assert list_subcommands("sysinfo") == []


# ============================================================================ #
# 执行语义
# ============================================================================ #
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
        """``disk_usage`` 抛 OSError 时跳过磁盘条目，不影响其余输出。"""

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


# ============================================================================ #
# JSON 机读输出
# ============================================================================ #
class TestJSONOutput:
    """``--json`` 增量输出：与默认文本同源，默认路径保持不变。"""

    def test_json_fields(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 返回英文稳定字段，值沿用文本中的原始字符串。"""
        _patch_psutil(monkeypatch)
        code, out = _run_and_capture("sysinfo", ["--json"], capsys)
        assert code == 0
        payload = json.loads(out)
        assert payload["schema"] == "fcmd-result/1"
        assert payload["ok"] is True
        assert payload["tool"] == "sysinfo"
        assert payload["subcommand"] is None
        info = collect_system_info()
        data = payload["data"]
        assert data["os"] == info["操作系统"]
        assert data["arch"] == info["架构"]
        assert data["python"] == info["Python 版本"]
        assert data["cpu"] == info["处理器"]
        assert data["cpu_cores"] == "8"
        assert data["cwd"] == info["工作目录"]
        assert data["memory"] == {"total": "16.0 GB", "used": "8.0 GB (50.0%)", "available": "6.0 GB"}
        assert data["swap"] == {"total": "4.0 GB", "used": "1.0 GB"}
        assert data["disk"] == {"total": "512.0 GB", "used": "128.0 GB", "free": "384.0 GB"}
        expected_boot = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1_700_000_000.0))
        assert data["boot_time"] == expected_boot

    def test_json_omits_disk_on_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """磁盘信息收集失败时省略 disk 分组，不编造字段。"""

        def _boom(_path: str) -> Any:
            raise OSError("unsupported")

        _patch_psutil(monkeypatch, disk_usage=_boom)
        _, out = _run_and_capture("sysinfo", ["--json"], capsys)
        data = json.loads(out)["data"]
        assert "disk" not in data
        assert data["memory"]["total"] == "16.0 GB"

    def test_json_omits_boot_time_on_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """开机时间收集失败时省略 boot_time 字段。"""

        def _boom() -> float:
            raise OSError("unsupported")

        _patch_psutil(monkeypatch, boot_time=_boom)
        _, out = _run_and_capture("sysinfo", ["--json"], capsys)
        data = json.loads(out)["data"]
        assert "boot_time" not in data

    def test_default_output_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """不加 --json 时仍是既有文本表，不混入 JSON 信封。"""
        _patch_psutil(monkeypatch)
        code, out = _run_and_capture("sysinfo", [], capsys)
        assert code == 0
        assert "系统信息" in out
        assert "CPU 核心数" in out
        assert '"schema"' not in out


# ============================================================================ #
# format_bytes 辅助
# ============================================================================ #
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
        assert format_bytes(size) == expected
