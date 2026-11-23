"""``fcmd.infras.net.ip``（由 ``infras/net.py`` 拆分）。"""

from __future__ import annotations

import ipaddress

__all__ = ["parse_ip", "subnet_info", "validate_ip"]


def parse_ip(ip: str) -> dict[str, str]:
    """解析 IP 地址，返回版本/私有/回环/多播/链路本地/压缩/展开表示。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"无效的 IP 地址: {ip!r}（{exc}）") from exc
    return {
        "version": str(addr.version),
        "is_private": str(addr.is_private),
        "is_loopback": str(addr.is_loopback),
        "is_multicast": str(addr.is_multicast),
        "is_link_local": str(addr.is_link_local),
        "compressed": addr.compressed,
        "exploded": addr.exploded,
    }


def subnet_info(ip: str, prefix: int) -> dict[str, str]:
    """计算 IPv4/IPv6 子网信息。"""
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError as exc:
        raise ValueError(f"无效的子网: {ip}/{prefix}（{exc}）") from exc

    if net.num_addresses >= 3:
        first = net.network_address + 1
        last = net.broadcast_address - 1
        host_range = str(first) if first == last else f"{first} - {last}"
    elif net.num_addresses == 2:
        host_range = f"{net.network_address} - {net.broadcast_address}"
    else:
        host_range = str(net.network_address)

    return {
        "network": str(net.network_address),
        "netmask": str(net.netmask),
        "broadcast": str(net.broadcast_address) if net.broadcast_address else "None",
        "prefixlen": str(net.prefixlen),
        "num_addresses": str(net.num_addresses),
        "host_range": host_range,
    }


def validate_ip(ip: str) -> bool:
    """校验 IP 地址格式是否有效。"""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False
