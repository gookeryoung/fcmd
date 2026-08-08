"""portcheck 工具测试。

验证 ``fcmd.cli.portcheck`` 模块：
- 工具注册
- is_port_in_use 端口占用检查
- check_port 单端口检查
- scan_ports 端口扫描
- get_port_occupant 占用进程详情查询（跨平台）
"""

from __future__ import annotations

import socket
import subprocess
import sys
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.portcheck
from fcmd.apis.toolkit import run_tool
from fcmd.cli.portcheck import (
    check_port,
    get_port_occupant,
    is_port_in_use,
    scan_ports,
)


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


# ---------------------------------------------------------------------- #
# 占用进程详情查询
# ---------------------------------------------------------------------- #
class TestGetPortOccupant:
    """``get_port_occupant`` 跨平台占用进程查询测试。"""

    def test_linux_parses_lsof_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Linux 下解析 lsof 输出为进程详情字典。"""
        monkeypatch.setattr(sys, "platform", "linux")
        lsof_out = (
            "COMMAND   PID  USER   FD   TYPE   DEVICE SIZE/OFF NODE NAME\n"
            "python   12345 zhou   3u  IPv4  123456      0t0  TCP 127.0.0.1:5173 (LISTEN)\n"
        )

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert cmd[0] == "lsof"
            assert ":5173" in cmd
            return subprocess.CompletedProcess(cmd, 0, lsof_out, "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        occupants = get_port_occupant(5173)
        assert len(occupants) == 1
        occ = occupants[0]
        assert occ["pid"] == "12345"
        assert occ["name"] == "python"
        assert occ["user"] == "zhou"
        assert occ["state"] == "LISTEN"

    def test_linux_lsof_no_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lsof 无匹配时返回空列表。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        assert get_port_occupant(59999) == []

    def test_linux_lsof_command_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lsof 未安装（FileNotFoundError）时静默返回空列表。"""
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "lsof not found")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        # 静默吞掉，避免污染主流程
        assert get_port_occupant(59999) == []

    def test_linux_lsof_header_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lsof 仅返回表头（无数据行）时返回空列表。"""
        monkeypatch.setattr(sys, "platform", "linux")
        lsof_out = "COMMAND   PID  USER   FD   TYPE   DEVICE SIZE/OFF NODE NAME\n"

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, lsof_out, "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        assert get_port_occupant(5173) == []

    def test_linux_lsof_malformed_line(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """lsof 数据行列数不足时跳过该行。"""
        monkeypatch.setattr(sys, "platform", "linux")
        lsof_out = (
            "COMMAND   PID  USER   FD   TYPE   DEVICE SIZE/OFF NODE NAME\n"
            "short\n"  # 列数不足，应跳过
            "python   12345 zhou   3u  IPv4  123456      0t0  TCP 127.0.0.1:5173 (LISTEN)\n"
        )

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, lsof_out, "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        occupants = get_port_occupant(5173)
        assert len(occupants) == 1
        assert occupants[0]["pid"] == "12345"

    def test_windows_parses_netstat_tasklist(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windows 下解析 netstat + tasklist 输出。"""
        monkeypatch.setattr(sys, "platform", "win32")
        netstat_out = (
            "活动连接\n\n"
            "  协议  本地地址          外部地址        状态           PID\n"
            "  TCP    127.0.0.1:5173          0.0.0.0:0              LISTENING       12345\n"
        )
        # tasklist 返回 CSV：进程名 + PID + ...
        tasklist_out = '"python.exe","12345","Console","1","12,345 K"\n'

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "netstat":
                return subprocess.CompletedProcess(cmd, 0, netstat_out, "")
            if cmd[0] == "tasklist":
                assert "/FI" in cmd
                assert "PID eq 12345" in cmd
                return subprocess.CompletedProcess(cmd, 0, tasklist_out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        occupants = get_port_occupant(5173)
        assert len(occupants) == 1
        occ = occupants[0]
        assert occ["pid"] == "12345"
        assert occ["name"] == "python.exe"
        assert occ["state"] == "LISTENING"
        assert occ["addr"] == "127.0.0.1:5173"
        # Windows 不含 user 字段
        assert "user" not in occ

    def test_windows_no_match(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windows netstat 无匹配端口时返回空列表。"""
        monkeypatch.setattr(sys, "platform", "win32")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, "无活动连接\n", "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        assert get_port_occupant(59999) == []

    def test_windows_dedup_pids(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同一 PID 多条 netstat 记录只查询一次 tasklist。"""
        monkeypatch.setattr(sys, "platform", "win32")
        # 同一 PID 出现两次（IPv4 + IPv6），并混入一条不匹配端口的行
        netstat_out = (
            "  TCP    127.0.0.1:5173          0.0.0.0:0              LISTENING       12345\n"
            "  TCP    127.0.0.1:8080          0.0.0.0:0              LISTENING       99999\n"
            "  TCP    [::1]:5173              [::]:0                 LISTENING       12345\n"
        )
        tasklist_out = '"node.exe","12345","Console","1","8,234 K"\n'
        call_count = {"tasklist": 0}

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "netstat":
                return subprocess.CompletedProcess(cmd, 0, netstat_out, "")
            if cmd[0] == "tasklist":
                call_count["tasklist"] += 1
                return subprocess.CompletedProcess(cmd, 0, tasklist_out, "")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        occupants = get_port_occupant(5173)
        # 去重后只有一个 PID（8080 那行被端口过滤掉）
        assert len(occupants) == 1
        assert call_count["tasklist"] == 1

    def test_windows_netstat_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windows netstat 未安装时返回空列表。"""
        monkeypatch.setattr(sys, "platform", "win32")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "netstat not found")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        assert get_port_occupant(5173) == []

    def test_windows_netstat_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Windows netstat 返回非零退出码时返回空列表。"""
        monkeypatch.setattr(sys, "platform", "win32")

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "error")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        assert get_port_occupant(5173) == []

    def test_windows_tasklist_not_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """tasklist 未安装时进程名降级为 <unknown>。"""
        monkeypatch.setattr(sys, "platform", "win32")
        netstat_out = "  TCP    127.0.0.1:5173          0.0.0.0:0              LISTENING       12345\n"

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "netstat":
                return subprocess.CompletedProcess(cmd, 0, netstat_out, "")
            if cmd[0] == "tasklist":
                raise FileNotFoundError(2, "tasklist not found")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        occupants = get_port_occupant(5173)
        assert len(occupants) == 1
        assert occupants[0]["name"] == "<unknown>"

    def test_windows_tasklist_empty_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """tasklist 输出为空时进程名降级为 <unknown>。"""
        monkeypatch.setattr(sys, "platform", "win32")
        netstat_out = "  TCP    127.0.0.1:5173          0.0.0.0:0              LISTENING       12345\n"

        def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "netstat":
                return subprocess.CompletedProcess(cmd, 0, netstat_out, "")
            if cmd[0] == "tasklist":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 1, "", "")

        monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
        occupants = get_port_occupant(5173)
        assert len(occupants) == 1
        assert occupants[0]["name"] == "<unknown>"


# ---------------------------------------------------------------------- #
# check_port 占用详情打印
# ---------------------------------------------------------------------- #
class TestCheckPortWithDetail:
    """``check_port`` 在端口占用时打印进程详情。"""

    def test_check_port_prints_detail_when_occupied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """端口被占用且查询到进程时，打印 PID/名称/状态。"""
        # 占用一个端口
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]

            monkeypatch.setattr(sys, "platform", "linux")
            lsof_out = (
                "COMMAND   PID  USER   FD   TYPE   DEVICE SIZE/OFF NODE NAME\n"
                f"python   99999 zhou   3u  IPv4  123456      0t0  TCP 127.0.0.1:{port} (LISTEN)\n"
            )

            def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(cmd, 0, lsof_out, "")

            monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
            check_port(port)
            out = capsys.readouterr().out
            assert "占用" in out
            assert "PID=99999" in out
            assert "python" in out

    def test_check_port_silent_when_query_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """端口占用但 lsof 失败时，仅打印"占用"，不抛错。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]

            monkeypatch.setattr(sys, "platform", "linux")

            def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(cmd, 1, "", "")

            monkeypatch.setattr("fcmd.cli.portcheck.subprocess.run", fake_run)
            check_port(port)
            out = capsys.readouterr().out
            assert "占用" in out
            # 不应打印详情行
            assert "PID=" not in out

    def test_check_port_free_no_detail(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """端口空闲时不查询进程详情。"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        check_port(free_port)
        out = capsys.readouterr().out
        assert "空闲" in out
        assert "PID=" not in out
