"""portcheck 工具测试。

验证 ``fcmd.cli.portcheck`` 模块：
- 工具注册
- is_port_in_use 端口占用检查
- check_port 单端口检查
- scan_ports 端口扫描
"""

from __future__ import annotations

import socket

import pytest

import fcmd as fx
import fcmd.cli.portcheck
from fcmd.apis.toolkit import run_tool
from fcmd.cli.portcheck import check_port, is_port_in_use, scan_ports


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """portcheck 工具的注册验证。"""

    def test_portcheck_subcommands(self) -> None:
        """portcheck 应有 c / s 子命令。"""
        subs = fx.list_subcommands("portcheck")
        assert "c" in subs
        assert "s" in subs


# ---------------------------------------------------------------------- #
# portcheck 工具测试
# ---------------------------------------------------------------------- #
class TestPortcheck:
    """``portcheck`` 工具测试。"""

    def test_is_port_in_use_free(self) -> None:
        """空闲端口返回 False。"""
        # 找一个可绑定的端口：用临时 socket 占用后释放，再用新 socket 验证
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        # socket 关闭后端口应空闲（SO_REUSEADDR 已设）
        assert is_port_in_use(free_port) is False

    def test_is_port_in_use_occupied(self) -> None:
        """占用端口返回 True。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            assert is_port_in_use(port) is True

    def test_check_port_free(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 打印空闲状态。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        check_port(free_port)
        out = capsys.readouterr().out
        assert "空闲" in out

    def test_check_port_occupied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 打印占用状态。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            check_port(port)
            out = capsys.readouterr().out
            assert "占用" in out

    def test_check_port_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 无效端口号打印提示。"""
        check_port(0)
        out = capsys.readouterr().out
        assert "无效" in out
        check_port(70000)
        out = capsys.readouterr().out
        assert "无效" in out

    def test_scan_ports_no_occupied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 无占用端口时打印提示。"""
        # 用一个不太可能被占用的高端口范围
        scan_ports(59999, 60000)
        out = capsys.readouterr().out
        assert "无占用端口" in out

    def test_scan_ports_invalid_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 无效范围打印提示。"""
        scan_ports(100, 50)
        out = capsys.readouterr().out
        assert "无效" in out

    def test_scan_ports_out_of_range(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 超出 65535 打印提示。"""
        scan_ports(70000, 80000)
        out = capsys.readouterr().out
        assert "无效" in out

    def test_portcheck_c_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd portcheck c <port> 通过 run_tool 调用。"""
        code = run_tool("portcheck", ["c", "59999"])
        assert code == 0
        out = capsys.readouterr().out
        assert "59999" in out

    def test_portcheck_s_via_run_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd portcheck s <start> <end> 通过 run_tool 调用。"""
        code = run_tool("portcheck", ["s", "59999", "60000"])
        assert code == 0
        out = capsys.readouterr().out
        assert "59999" in out

    def test_scan_ports_with_occupied(self, capsys: pytest.CaptureFixture[str]) -> None:
        """scan_ports 扫描到占用端口时打印列表。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            scan_ports(port, port)
            out = capsys.readouterr().out
            assert "占用端口" in out
            assert str(port) in out
