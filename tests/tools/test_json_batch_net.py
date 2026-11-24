"""`--json` 接入批次 3：urlcheck / portcheck / iptool。"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from typing import Any

import pytest

from fcmd.core.toolkit import run_tool


def _payload(raw: str) -> dict[str, Any]:
    return json.loads(raw[raw.index("{") :])


@pytest.fixture()
def occupied_port() -> Iterator[int]:
    """绑定一个本地监听端口，返回其端口号（用例结束时释放）。"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


def test_urlcheck_u_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """u --json 给出 url/ok/latency_ms。"""
    from fcmd.infras import net as netinfra

    monkeypatch.setattr(netinfra, "urlcheck_single", lambda url, timeout=5.0: (True, 12.345))
    assert run_tool("urlcheck", ["u", "https://example.com", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data == {"url": "https://example.com", "ok": True, "latency_ms": 12.3}


def test_urlcheck_r_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """r --json 给出 count/reachable/results。"""
    from fcmd.infras import net as netinfra

    monkeypatch.setattr(
        netinfra,
        "urlcheck_many",
        lambda urls, timeout=5.0: [("https://a", True, 1.0), ("https://b", False, 0.0)],
    )
    assert run_tool("urlcheck", ["r", "https://a", "https://b", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["count"] == 2
    assert data["reachable"] == 1
    assert data["results"][1] == {"url": "https://b", "ok": False, "latency_ms": None}


def test_urlcheck_r_empty(capsys: pytest.CaptureFixture[str]) -> None:
    """空 URL 列表按用法错误呈现（CLI 侧由 argparse 拦为 2，函数侧给 3）。"""
    from fcmd.tools.net.urlcheck import urlcheck_r

    urlcheck_r([], json_output=True)
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_portcheck_c_json(occupied_port: int, capsys: pytest.CaptureFixture[str]) -> None:
    """c --json 报告占用状态与占用进程列表。"""
    assert run_tool("portcheck", ["c", str(occupied_port), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["host"] == "127.0.0.1"
    assert data["port"] == occupied_port
    assert data["in_use"] is True
    assert isinstance(data["occupants"], list)


def test_portcheck_c_free_json(capsys: pytest.CaptureFixture[str]) -> None:
    """未占用端口 in_use=False 且 occupants 为空。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()
    assert run_tool("portcheck", ["c", str(free_port), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["in_use"] is False
    assert data["occupants"] == []


def test_portcheck_c_invalid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """端口越界 → INVALID_ARGUMENT。"""
    assert run_tool("portcheck", ["c", "0", "--json"]) == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_portcheck_s_json(occupied_port: int, capsys: pytest.CaptureFixture[str]) -> None:
    """s --json 返回范围内占用端口列表。"""
    assert run_tool("portcheck", ["s", str(occupied_port), str(occupied_port), "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["start"] == occupied_port
    assert data["end"] == occupied_port
    assert data["occupied"] == [occupied_port]
    assert data["count"] == 1


def test_portcheck_s_invalid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """范围非法 → INVALID_ARGUMENT。"""
    assert run_tool("portcheck", ["s", "20", "10", "--json"]) == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_iptool_parse_json(capsys: pytest.CaptureFixture[str]) -> None:
    """parse --json 输出地址属性。"""
    assert run_tool("iptool", ["parse", "192.168.1.10", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["ip"] == "192.168.1.10"
    assert data["version"] == "4"
    assert data["compressed"] == "192.168.1.10"


def test_iptool_parse_invalid_json(capsys: pytest.CaptureFixture[str]) -> None:
    """非法 IP → INVALID_ARGUMENT。"""
    assert run_tool("iptool", ["parse", "not-an-ip", "--json"]) == 3
    assert _payload(capsys.readouterr().err)["code"] == "INVALID_ARGUMENT"


def test_iptool_subnet_json(capsys: pytest.CaptureFixture[str]) -> None:
    """subnet --json 输出网络信息。"""
    assert run_tool("iptool", ["subnet", "192.168.1.0", "24", "--json"]) == 0
    data = _payload(capsys.readouterr().out)["data"]
    assert data["network"] == "192.168.1.0"
    assert data["prefixlen"] == "24"


def test_iptool_validate_json(capsys: pytest.CaptureFixture[str]) -> None:
    """validate --json 用 valid 布尔表达结论。"""
    assert run_tool("iptool", ["validate", "10.0.0.1", "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"] == {"ip": "10.0.0.1", "valid": True}

    assert run_tool("iptool", ["validate", "999.1.1.1", "--json"]) == 0
    assert _payload(capsys.readouterr().out)["data"]["valid"] is False
