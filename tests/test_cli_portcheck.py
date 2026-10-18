"""portcheck 工具测试。

验证 ``fcmd.tools.net.portcheck`` 模块：
- 工具注册
- is_port_in_use 端口占用检查
- check_port 单端口检查
- scan_ports 端口扫描
- get_port_occupant 占用进程详情查询（psutil）
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

import fcmd as fx
import fcmd.tools.net.portcheck
from fcmd.core.toolkit import run_tool
from fcmd.tools.net.portcheck import (
    check_port,
    get_port_occupant,
    is_port_in_use,
    scan_ports,
)


# ---------------------------------------------------------------------- #
# psutil 测试替身
# ---------------------------------------------------------------------- #
def _conn(port: int | None, pid: int | None, *, ip: str = "127.0.0.1", status: str = "LISTEN") -> Any:
    """构造 psutil 连接对象替身（``laddr`` 为 None 时端口无意义）。"""
    laddr = SimpleNamespace(ip=ip, port=port) if port is not None else None
    return SimpleNamespace(laddr=laddr, pid=pid, status=status)


def _fake_proc(name: str = "python", user: str = "zhou") -> Any:
    """构造 psutil.Process 替身工厂。"""

    def factory(pid: int) -> Any:
        return SimpleNamespace(pid=pid, name=lambda: name, username=lambda: user)

    return factory


def _patch_psutil(
    monkeypatch: pytest.MonkeyPatch,
    conns: list[Any] | None = None,
    *,
    factory: Any = None,
    error: BaseException | None = None,
) -> None:
    """替换 ``psutil.net_connections`` / ``psutil.Process``。"""

    def fake_net_connections(kind: str = "inet") -> list[Any]:
        assert kind == "tcp"
        if error is not None:
            raise error
        return conns or []

    monkeypatch.setattr(psutil, "net_connections", fake_net_connections)
    if factory is not None:
        monkeypatch.setattr(psutil, "Process", factory)


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

    def test_check_port_occupied(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """check_port 打印占用状态。"""
        _patch_psutil(monkeypatch, [])
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
    """``get_port_occupant`` 基于 psutil 的占用进程查询测试。"""

    def test_returns_matching_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """命端口的连接被转换为进程详情字典。"""
        _patch_psutil(monkeypatch, [_conn(5173, 12345)], factory=_fake_proc())
        occupants = get_port_occupant(5173)
        assert len(occupants) == 1
        occ = occupants[0]
        assert occ["pid"] == "12345"
        assert occ["name"] == "python"
        assert occ["user"] == "zhou"
        assert occ["proto"] == "TCP"
        assert occ["addr"] == "127.0.0.1:5173"
        assert occ["state"] == "LISTEN"

    def test_filters_other_ports(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """其他端口的连接被过滤。"""
        conns = [_conn(8080, 1), _conn(5173, 2)]
        _patch_psutil(monkeypatch, conns, factory=_fake_proc())
        occupants = get_port_occupant(5173)
        assert [o["pid"] for o in occupants] == ["2"]

    def test_skips_connection_without_pid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无 PID 的连接（系统保留）被跳过。"""
        _patch_psutil(monkeypatch, [_conn(5173, None)], factory=_fake_proc())
        assert get_port_occupant(5173) == []

    def test_skips_connection_without_laddr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无本地地址的连接被跳过。"""
        _patch_psutil(monkeypatch, [_conn(None, 1)], factory=_fake_proc())
        assert get_port_occupant(5173) == []

    def test_dedups_same_pid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一 PID 的多条连接（IPv4 + IPv6）只输出一行。"""
        conns = [_conn(5173, 12345), _conn(5173, 12345, ip="::1")]
        _patch_psutil(monkeypatch, conns, factory=_fake_proc())
        assert len(get_port_occupant(5173)) == 1

    def test_no_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无匹配端口的连接时返回空列表。"""
        _patch_psutil(monkeypatch, [_conn(8080, 1)], factory=_fake_proc())
        assert get_port_occupant(59999) == []

    def test_net_connections_access_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``net_connections`` 权限不足时静默返回空列表。"""
        _patch_psutil(monkeypatch, error=psutil.AccessDenied(pid=0))
        assert get_port_occupant(5173) == []

    def test_net_connections_os_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``net_connections`` 抛 OSError（平台不支持）时静默返回空列表。"""
        _patch_psutil(monkeypatch, error=OSError("unsupported"))
        assert get_port_occupant(5173) == []

    def test_process_gone_keeps_unknown_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """进程已退出（NoSuchProcess）时名称降级为 <unknown>。"""

        def factory(pid: int) -> Any:
            raise psutil.NoSuchProcess(pid)

        _patch_psutil(monkeypatch, [_conn(5173, 12345)], factory=factory)
        occ = get_port_occupant(5173)[0]
        assert occ["pid"] == "12345"
        assert occ["name"] == "<unknown>"
        assert occ["user"] == ""

    def test_process_access_denied_marks_high_privilege(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """权限不足时用户字段提示需要更高权限。"""

        def factory(pid: int) -> Any:
            raise psutil.AccessDenied(pid)

        _patch_psutil(monkeypatch, [_conn(5173, 12345)], factory=factory)
        occ = get_port_occupant(5173)[0]
        assert occ["name"] == "<unknown>"
        assert occ["user"] == "<需要更高权限>"

    def test_missing_status_falls_back_to_dash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """连接无状态（UDP 风格）时状态显示 ``-``。"""
        _patch_psutil(monkeypatch, [_conn(5173, 1, status="")], factory=_fake_proc())
        assert get_port_occupant(5173)[0]["state"] == "-"


# ---------------------------------------------------------------------- #
# check_port 占用详情打印
# ---------------------------------------------------------------------- #
class TestCheckPortWithDetail:
    """``check_port`` 在端口占用时打印进程详情。"""

    def _occupied_port(self) -> Any:
        """上下文管理器：占用一个本机端口。"""
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def test_check_port_prints_detail_when_occupied(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """端口被占用且查询到进程时，打印 PID/名称/状态。"""
        with self._occupied_port() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]

            _patch_psutil(monkeypatch, [_conn(port, 99999)], factory=_fake_proc())
            check_port(port)
            out = capsys.readouterr().out
            assert "占用" in out
            assert "PID=99999" in out
            assert "python" in out
            assert "用户=zhou" in out

    def test_check_port_silent_when_query_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """端口占用但 psutil 查询失败时，仅打印"占用"，不抛错。"""
        with self._occupied_port() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]

            _patch_psutil(monkeypatch, error=psutil.AccessDenied(pid=0))
            check_port(port)
            out = capsys.readouterr().out
            assert "占用" in out
            # 不应打印详情行
            assert "PID=" not in out

    def test_check_port_free_no_detail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """端口空闲时不查询进程详情。"""
        _patch_psutil(monkeypatch, [])
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        check_port(free_port)
        out = capsys.readouterr().out
        assert "空闲" in out
        assert "PID=" not in out
