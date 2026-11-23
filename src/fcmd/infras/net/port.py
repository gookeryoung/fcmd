"""``fcmd.infras.net.port``（由 ``infras/net.py`` 拆分）。"""

from __future__ import annotations

import os
import socket

__all__ = ["is_port_in_use"]


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
        # Windows 的 SO_REUSEADDR 允许「抢绑」已被 SO_REUSEADDR 监听的服务端口：
        # bind 会成功、检查恒为 False，于是 llm / 画图服务能重复启动并各自加载模型
        # （显存叠加）。只在 POSIX 上设它——POSIX 的 reuse 不会绕过 LISTEN 状态，
        # 仅用于避开 TIME_WAIT；Windows 下保持默认的独占语义才能探测到占用。
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return True
        return False
