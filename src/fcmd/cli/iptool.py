"""iptool - IP 地址工具。

基于标准库 ``ipaddress`` 提供 IPv4/IPv6 地址解析、子网计算与格式校验。

示例
----
    fcmd iptool parse 192.168.1.1                  # 解析 IP
    fcmd iptool parse ::1                          # 解析 IPv6
    fcmd iptool subnet 192.168.1.0 24              # 计算子网
    fcmd iptool validate 192.168.1.1               # 校验 IP
    fcmd iptool validate 999.999.999.999          # 校验无效 IP
"""

from __future__ import annotations

import ipaddress

import fcmd

__all__ = [
    "parse_ip",
    "subnet_info",
    "validate_ip",
]


# ============================================================================
# 公共函数
# ============================================================================


def parse_ip(ip: str) -> dict[str, str]:
    """解析 IP 地址。

    Parameters
    ----------
    ip:
        IP 地址字符串（IPv4 或 IPv6）

    Returns
    -------
    dict[str, str]
        包含以下键的字典：
        - ``version``: IP 版本（``4`` 或 ``6``）
        - ``is_private``: 是否私有地址（``True``/``False``）
        - ``is_loopback``: 是否回环地址
        - ``is_multicast``: 是否多播地址
        - ``is_link_local``: 是否链路本地地址
        - ``compressed``: 压缩表示
        - ``exploded``: 展开表示（IPv6 有效；IPv4 与 compressed 相同）

    Raises
    ------
    ValueError
        IP 地址格式无效时
    """
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
    """计算 IPv4/IPv6 子网信息。

    Parameters
    ----------
    ip:
        网络地址字符串（如 ``192.168.1.0``）
    prefix:
        前缀长度（如 ``24``）

    Returns
    -------
    dict[str, str]
        包含以下键的字典：
        - ``network``: 网络地址
        - ``netmask``: 子网掩码
        - ``broadcast``: 广播地址（IPv4 总有；IPv6 无广播时为 ``None``）
        - ``prefixlen``: 前缀长度
        - ``num_addresses``: 地址总数
        - ``host_range``: 主机地址范围（``first - last``）

    Raises
    ------
    ValueError
        网络地址或前缀长度无效时
    """
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError as exc:
        raise ValueError(f"无效的子网: {ip}/{prefix}（{exc}）") from exc

    # 直接计算主机范围，避免 list(net.hosts()) 对大子网生成全量列表
    if net.num_addresses >= 3:
        # 普通子网：主机范围 = network+1 到 broadcast-1
        first = net.network_address + 1
        last = net.broadcast_address - 1
        host_range = str(first) if first == last else f"{first} - {last}"
    elif net.num_addresses == 2:
        # /31 或 /127（RFC 3021 点对点）：两个地址都是主机
        host_range = f"{net.network_address} - {net.broadcast_address}"
    else:
        # /32 或 /128：唯一地址即网络地址本身
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
    """校验 IP 地址格式是否有效。

    Parameters
    ----------
    ip:
        待校验的 IP 地址字符串

    Returns
    -------
    bool
        有效返回 ``True``，无效返回 ``False``
    """
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("iptool", subcommand="parse", help="解析 IP 地址")
def parse_cmd(ip: str) -> None:
    """解析 IP 地址并逐行打印属性。

    Parameters
    ----------
    ip:
        IP 地址字符串（IPv4 或 IPv6）
    """
    try:
        info = parse_ip(ip)
    except ValueError as exc:
        print(str(exc))
        return
    for key, value in info.items():
        print(f"{key}: {value}")


@fcmd.tool("iptool", subcommand="subnet", help="计算子网信息")
def subnet_cmd(ip: str, prefix: int) -> None:
    """计算子网信息并逐行打印。

    用法：``fcmd iptool subnet <ip> <prefix>``

    Parameters
    ----------
    ip:
        网络地址字符串
    prefix:
        前缀长度
    """
    try:
        info = subnet_info(ip, prefix)
    except ValueError as exc:
        print(str(exc))
        return
    for key, value in info.items():
        print(f"{key}: {value}")


@fcmd.tool("iptool", subcommand="validate", help="校验 IP 地址格式")
def validate_cmd(ip: str) -> None:
    """校验 IP 地址格式是否有效。

    Parameters
    ----------
    ip:
        待校验的 IP 地址字符串
    """
    if validate_ip(ip):
        print(f"有效: {ip}")
    else:
        print(f"无效: {ip}")


@fcmd.main("iptool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
