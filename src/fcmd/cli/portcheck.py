"""portcheck - 端口检查工具。

检查端口是否被占用，支持单个端口检查与端口范围扫描。纯 Python socket 实现，跨平台。
端口被占用时，调用系统命令查询占用进程详情（PID、名称、用户等）。

示例
----
    fcmd portcheck c 8080                 # 检查 8080 端口
    fcmd portcheck c 8080 --host 0.0.0.0  # 检查指定主机
    fcmd portcheck s 8000 8100            # 扫描 8000-8100 范围
"""

from __future__ import annotations

import socket
import subprocess
import sys

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


def _query_occupant_linux(port: int) -> list[dict[str, str]]:
    """Linux/macOS 通过 ``lsof`` 查询占用端口的进程。

    Parameters
    ----------
    port:
        目标端口号

    Returns
    -------
    list[dict[str, str]]
        每个占用进程一个字典，包含 ``pid``/``name``/``user``/``proto``/``state`` 字段。
        失败或无匹配时返回空列表。
    """
    # -P -n 禁用端口/主机名反查，加速并避免 DNS 抖动；-sTCP:LISTEN 仅取监听态
    cmd = ["lsof", "-i", f":{port}", "-P", "-n", "-sTCP:LISTEN"]
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except FileNotFoundError:
        # lsof 未安装（常见于精简容器）时静默返回空列表，不阻断主流程
        return []
    if result.returncode != 0 or not result.stdout:
        return []
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return []
    # 表头: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
    occupants: list[dict[str, str]] = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        name, pid, user = parts[0], parts[1], parts[2]
        # NAME 形如 "127.0.0.1:5173 (LISTEN)" 或 "*:5173 (LISTEN)"
        name_field = parts[8]
        occupants.append(
            {
                "pid": pid,
                "name": name,
                "user": user,
                "proto": "TCP",
                "addr": name_field,
                "state": "LISTEN",
            }
        )
    return occupants


def _query_occupant_windows(port: int) -> list[dict[str, str]]:
    """Windows 通过 ``netstat`` + ``tasklist`` 查询占用端口的进程。

    Parameters
    ----------
    port:
        目标端口号

    Returns
    -------
    list[dict[str, str]]
        每个占用进程一个字典，包含 ``pid``/``name``/``proto``/``addr``/``state`` 字段。
        失败或无匹配时返回空列表。
    """
    # netstat -ano 输出形如：
    #   Proto Local Address Foreign Address State PID
    #   TCP   127.0.0.1:5173  0.0.0.0:0  LISTENING  12345
    try:
        netstat = subprocess.run(["netstat", "-ano"], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return []
    if netstat.returncode != 0:
        return []
    pids: list[tuple[str, str, str, str]] = []  # (pid, proto, local_addr, state)
    seen_pids: set[str] = set()
    for line in netstat.stdout.splitlines():
        parts = line.split()
        # 期望至少 5 段：Proto Local Foreign State PID
        if len(parts) < 5 or parts[0] != "TCP":
            continue
        local_addr = parts[1]
        # 形如 "127.0.0.1:5173" 或 "[::1]:5173"
        if not local_addr.endswith(f":{port}"):
            continue
        state = parts[3]
        pid = parts[4]
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        pids.append((pid, "TCP", local_addr, state))

    if not pids:
        return []

    # 用 tasklist 查询进程名：tasklist /FI "PID eq <pid>" /FO CSV /NH
    occupants: list[dict[str, str]] = []
    for pid, proto, local_addr, state in pids:
        proc_name = "<unknown>"
        try:
            tl = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            proc_name = "<unknown>"
        else:
            if tl.returncode == 0 and tl.stdout.strip():
                # CSV 形如: "python.exe","12345","Console","1","12,345 K"
                first_line = tl.stdout.splitlines()[0].strip()
                if first_line.startswith('"'):
                    proc_name = first_line.split('"')[1]
        occupants.append(
            {
                "pid": pid,
                "name": proc_name,
                "proto": proto,
                "addr": local_addr,
                "state": state,
            }
        )
    return occupants


def get_port_occupant(port: int) -> list[dict[str, str]]:
    """查询占用指定端口的进程详情（跨平台）。

    - Linux/macOS 调用 ``lsof -i :<port> -P -n -sTCP:LISTEN``
    - Windows 调用 ``netstat -ano`` + ``tasklist``

    Parameters
    ----------
    port:
        目标端口号

    Returns
    -------
    list[dict[str, str]]
        每个占用进程一个字典，至少包含 ``pid``/``name``/``state`` 字段；
        Linux/macOS 额外含 ``user``。无匹配或查询失败时返回空列表。
    """
    if sys.platform == "win32":
        return _query_occupant_windows(port)
    return _query_occupant_linux(port)


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
    pass


if __name__ == "__main__":  # pragma: no cover
    main()
