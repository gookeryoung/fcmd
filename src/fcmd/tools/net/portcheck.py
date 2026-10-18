"""portcheck - 端口检查工具。

检查端口是否被占用，支持单个端口检查与端口范围扫描。纯 Python socket 实现，跨平台。
端口被占用时，用 ``psutil.net_connections`` 查询占用进程详情（PID、名称、用户等）。

示例
----
    fcmd portcheck c 8080                 # 检查 8080 端口
    fcmd portcheck c 8080 --host 0.0.0.0  # 检查指定主机
    fcmd portcheck s 8000 8100            # 扫描 8000-8100 范围
"""

from __future__ import annotations

import socket
from typing import Any

import fcmd

__all__ = [
    "check_port",
    "get_port_occupant",
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


def _occupant_row(conn: Any) -> dict[str, str]:
    """把 psutil 的连接对象转成占用进程字典。

    名称/用户查询失败（进程已退出 / 权限不足）时降级为 ``<unknown>``
    与 ``<需要更高权限>``，不影响已拿到的 PID 与地址信息。
    """
    import psutil

    pid = conn.pid
    laddr = conn.laddr
    row = {
        "pid": str(pid),
        "name": "<unknown>",
        "user": "",
        "proto": "TCP",
        "addr": f"{laddr.ip}:{laddr.port}" if laddr is not None else "-",
        "state": conn.status or "-",
    }
    try:
        proc = psutil.Process(pid)
        row["name"] = proc.name()
        row["user"] = proc.username()
    except psutil.NoSuchProcess:
        pass
    except psutil.AccessDenied:
        row["user"] = "<需要更高权限>"
    return row


def get_port_occupant(port: int) -> list[dict[str, str]]:
    """查询占用指定端口的进程详情（跨平台，基于 psutil）。

    Parameters
    ----------
    port:
        目标端口号

    Returns
    -------
    list[dict[str, str]]
        每个占用进程一个字典，包含 ``pid``/``name``/``user``/``proto``/
        ``addr``/``state`` 字段；同一 PID 只出现一次。
        无匹配、psutil 未安装或权限不足时返回空列表。
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil 是核心依赖
        return []

    try:
        conns: Any = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, OSError):
        return []

    occupants: list[dict[str, str]] = []
    seen: set[int] = set()
    for conn in conns:
        if conn.laddr is None or conn.laddr.port != port:
            continue
        if conn.pid is None or conn.pid in seen:
            continue
        seen.add(conn.pid)
        occupants.append(_occupant_row(conn))
    return occupants


def check_port(port: int, host: str = "127.0.0.1") -> None:
    """检查并打印单个端口占用状态。

    端口被占用时，进一步查询并打印占用进程详情（PID、名称等）。

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
    if is_port_in_use(port, host):
        print(f"端口 {host}:{port} -> 占用")
        # 查询占用详情；查询失败不抛错，仅静默跳过
        for occ in get_port_occupant(port):
            pid = occ.get("pid", "?")
            name = occ.get("name", "?")
            state = occ.get("state", "?")
            user = occ.get("user")
            detail = f"  PID={pid}  名称={name}  状态={state}"
            if user:
                detail += f"  用户={user}"
            print(detail)
    else:
        print(f"端口 {host}:{port} -> 空闲")


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


@fcmd.main("portcheck")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
