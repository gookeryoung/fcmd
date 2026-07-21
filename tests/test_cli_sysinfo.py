"""sysinfo 工具测试。

验证 ``fcmd.cli.sysinfo`` 模块：
- 工具注册
- _format_bytes 字节格式化
- collect_sysinfo 系统信息收集
- print_sysinfo 信息打印
"""

from __future__ import annotations

import sys

import pytest

import fcmd as fx
import fcmd.cli.sysinfo
from fcmd.apis.toolkit import run_tool
from fcmd.cli.sysinfo import _format_bytes, collect_sysinfo, print_sysinfo


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """sysinfo 工具的注册验证。"""

    def test_sysinfo_single_command(self) -> None:
        """sysinfo 是单命令工具（无子命令）。"""
        assert fx.list_subcommands("sysinfo") == []


# ---------------------------------------------------------------------- #
# sysinfo 工具测试
# ---------------------------------------------------------------------- #
class TestSysinfo:
    """``sysinfo`` 工具测试。"""

    def test_format_bytes_units(self) -> None:
        """_format_bytes 正确格式化各量级。"""
        assert _format_bytes(0) == "0.0 B"
        assert _format_bytes(1023) == "1023.0 B"
        assert _format_bytes(1024) == "1.0 KB"
        assert _format_bytes(1024 * 1024) == "1.0 MB"
        assert _format_bytes(1024 * 1024 * 1024) == "1.0 GB"

    def test_format_bytes_pb(self) -> None:
        """_format_bytes 超过 TB 进位到 PB。"""
        # 1 PB = 1024^5 字节
        pb = 1024**5
        assert _format_bytes(pb) == "1.0 PB"

    def test_collect_sysinfo_has_keys(self) -> None:
        """collect_sysinfo 返回必要键。"""
        info = collect_sysinfo()
        assert "Python 版本" in info
        assert "Python 路径" in info
        assert "平台" in info
        assert "架构" in info
        assert "操作系统" in info
        assert "CPU 核心数" in info
        assert "工作目录" in info

    def test_collect_sysinfo_python_version(self) -> None:
        """collect_sysinfo 的 Python 版本与 sys.version 一致。"""
        info = collect_sysinfo()
        assert info["Python 版本"] == sys.version.split()[0]

    def test_collect_sysinfo_disk(self) -> None:
        """collect_sysinfo 包含磁盘信息。"""
        info = collect_sysinfo()
        # 磁盘信息通常可获取（极罕见平台除外）
        if "磁盘总量" in info:
            assert "磁盘已用" in info
            assert "磁盘可用" in info

    def test_print_sysinfo(self, capsys: pytest.CaptureFixture[str]) -> None:
        """print_sysinfo 打印分隔线与信息。"""
        print_sysinfo()
        out = capsys.readouterr().out
        assert "系统信息" in out
        assert "=" * 50 in out
        assert "Python 版本" in out

    def test_sysinfo_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd sysinfo 通过 run_tool 调用。"""
        code = run_tool("sysinfo", [])
        assert code == 0
        out = capsys.readouterr().out
        assert "系统信息" in out

    def test_collect_sysinfo_with_resource_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """collect_sysinfo 在有 resource 模块的环境下收集内存峰值（Linux 路径）。"""
        import types

        fake_resource = types.ModuleType("resource")
        fake_resource.RUSAGE_SELF = 0  # type: ignore[attr-defined]
        fake_resource.getrusage = lambda _flags: types.SimpleNamespace(ru_maxrss=10240)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        info = collect_sysinfo()
        assert "内存峰值" in info
        # Linux 上 ru_maxrss 单位为 KB，10240 KB = 10 MB
        assert "MB" in info["内存峰值"]

    def test_collect_sysinfo_with_resource_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """collect_sysinfo 在 macOS 下内存峰值单位为字节（Darwin 路径）。"""
        import types

        fake_resource = types.ModuleType("resource")
        fake_resource.RUSAGE_SELF = 0  # type: ignore[attr-defined]
        # macOS 上 ru_maxrss 单位为字节，1024 字节 = 1 KB
        fake_resource.getrusage = lambda _flags: types.SimpleNamespace(ru_maxrss=1024)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        info = collect_sysinfo()
        assert "内存峰值" in info
        assert "KB" in info["内存峰值"]
