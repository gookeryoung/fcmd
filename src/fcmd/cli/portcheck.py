"""portcheck - 端口检查工具。

检查端口是否被占用，支持单个端口检查与端口范围扫描。纯 Python socket 实现，跨平台。

示例
----
    fcmd portcheck c 8080                 # 检查 8080 端口
    fcmd portcheck c 8080 --host 0.0.0.0  # 检查指定主机
    fcmd portcheck s 8000 8100            # 扫描 8000-8100 范围
"""

from __future__ import annotations

import socket

import fcmd

__all__ = [
    "check_port",
    "is_port_in_use",
    "scan_ports",
]


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查指定端口是否被占用。

    通过尝试 ``bind`` 判断端口是否可绑定，仅检测 TCP。
    使用 ``SO_REUSEADDR`` 避免 TIME_WAIT 状态干扰，结果反映"能否绑定该端口"。

    Parameters
    ----------
    port:
        目标端口号（1-65535）
    host:
        目标主机（默认 ``127.0.0.1``）

    Returns
    -------
    bool
        端口被占用时返回 ``True``，否则 ``False``
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
        return False


def check_port(port: int, host: str = "127.0.0.1") -> None:
    """检查并打印单个端口占用状态。

    Parameters
    ----------
    port:
        目标端口号（1-65535）
    host:
        目标主机（默认 ``127.0.0.1``）
    """
    if not 1 <= port <= 65535:
        print(f"端口号无效: {port} (应在 1-65535 范围内)")
        return
    status = "占用" if is_port_in_use(port, host) else "空闲"
    print(f"端口 {host}:{port} -> {status}")


def scan_ports(start: int, end: int, host: str = "127.0.0.1") -> None:
    """扫描端口范围并打印被占用的端口。

    Parameters
    ----------
    start:
        起始端口号
    end:
        结束端口号（含）
    host:
        目标主机（默认 ``127.0.0.1``）
    """
    if start < 1 or end > 65535 or start > end:
        print(f"端口范围无效: {start}-{end} (应在 1-65535 范围内且 start <= end)")
        return
    occupied: list[int] = []
    for port in range(start, end + 1):
        if is_port_in_use(port, host):
            occupied.append(port)
    if not occupied:
        print(f"端口范围 {start}-{end} 内无占用端口")
        return
    print(f"端口范围 {start}-{end} 内占用端口 ({len(occupied)} 个):")
    for port in occupied:
        print(f"  {host}:{port}")


@fcmd.tool("portcheck", subcommand="c", help="检查端口占用")
def check_port_cmd(port: int, host: str = "127.0.0.1") -> None:
    """检查单个端口是否被占用。

    Parameters
    ----------
    port:
        目标端口号（1-65535）
    host:
        目标主机（默认 ``127.0.0.1``）
    """
    check_port(port, host)


@fcmd.tool("portcheck", subcommand="s", help="扫描端口范围")
def scan_ports_cmd(start: int, end: int, host: str = "127.0.0.1") -> None:
    """扫描端口范围，列出被占用的端口。

    Parameters
    ----------
    start:
        起始端口号
    end:
        结束端口号（含）
    host:
        目标主机（默认 ``127.0.0.1``）
    """
    scan_ports(start, end, host)
