"""iptool 工具测试。

验证 ``fcmd.cli.net.iptool`` 模块：
- 工具注册与三子命令结构（parse/subnet/validate）
- ``parse_ip``/``subnet_info``/``validate_ip``
- IPv4/IPv6 与错误分支
- CLI 子命令端到端
"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.net.iptool import (
    parse_ip,
    subnet_info,
    validate_ip,
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """iptool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "iptool" in list_tools()

    def test_subcommands(self) -> None:
        """iptool 有 parse/subnet/validate 三个子命令。"""
        subs = list_subcommands("iptool")
        assert set(subs) == {"parse", "subnet", "validate"}


# ============================================================================ #
# parse_ip
# ============================================================================ #
class TestParseIp:
    """parse_ip 解析测试。"""

    def test_ipv4_basic(self) -> None:
        """IPv4 基本解析。"""
        info = parse_ip("192.168.1.1")
        assert info["version"] == "4"
        assert info["is_private"] == "True"
        assert info["compressed"] == "192.168.1.1"

    def test_ipv4_loopback(self) -> None:
        """IPv4 回环地址。"""
        info = parse_ip("127.0.0.1")
        assert info["is_loopback"] == "True"
        assert info["version"] == "4"

    def test_ipv4_public(self) -> None:
        """IPv4 公共地址。"""
        info = parse_ip("8.8.8.8")
        assert info["is_private"] == "False"

    def test_ipv4_multicast(self) -> None:
        """IPv4 多播地址。"""
        info = parse_ip("224.0.0.1")
        assert info["is_multicast"] == "True"

    def test_ipv6_basic(self) -> None:
        """IPv6 基本解析。"""
        info = parse_ip("::1")
        assert info["version"] == "6"
        assert info["is_loopback"] == "True"
        assert info["compressed"] == "::1"
        assert info["exploded"] == "0000:0000:0000:0000:0000:0000:0000:0001"

    def test_ipv6_full(self) -> None:
        """IPv6 完整地址。"""
        info = parse_ip("2001:db8::1")
        assert info["version"] == "6"
        assert info["compressed"] == "2001:db8::1"

    def test_ipv6_link_local(self) -> None:
        """IPv6 链路本地地址。"""
        info = parse_ip("fe80::1")
        assert info["is_link_local"] == "True"

    def test_invalid_ip_raises(self) -> None:
        """无效 IP 抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的 IP 地址"):
            parse_ip("999.999.999.999")
        with pytest.raises(ValueError, match="无效的 IP 地址"):
            parse_ip("not.an.ip")
        with pytest.raises(ValueError, match="无效的 IP 地址"):
            parse_ip("::g")

    def test_ipv4_exploded_same_as_compressed(self) -> None:
        """IPv4 的 exploded 与 compressed 相同。"""
        info = parse_ip("192.168.1.1")
        assert info["exploded"] == info["compressed"]


# ============================================================================ #
# subnet_info
# ============================================================================ #
class TestSubnetInfo:
    """subnet_info 子网计算测试。"""

    def test_ipv4_24(self) -> None:
        """IPv4 /24 子网。"""
        info = subnet_info("192.168.1.0", 24)
        assert info["network"] == "192.168.1.0"
        assert info["netmask"] == "255.255.255.0"
        assert info["broadcast"] == "192.168.1.255"
        assert info["prefixlen"] == "24"
        assert info["num_addresses"] == "256"
        assert "192.168.1.1" in info["host_range"]
        assert "192.168.1.254" in info["host_range"]

    def test_ipv4_16(self) -> None:
        """IPv4 /16 子网。"""
        info = subnet_info("172.16.0.0", 16)
        assert info["netmask"] == "255.255.0.0"
        assert info["num_addresses"] == "65536"

    def test_ipv4_30(self) -> None:
        """IPv4 /30 子网（仅 2 个可用主机）。"""
        info = subnet_info("10.0.0.0", 30)
        assert info["num_addresses"] == "4"
        assert "10.0.0.1" in info["host_range"]
        assert "10.0.0.2" in info["host_range"]

    def test_ipv4_32(self) -> None:
        """IPv4 /32 单主机（网络地址本身作为唯一主机）。"""
        info = subnet_info("192.168.1.1", 32)
        assert info["num_addresses"] == "1"
        # /32 的 hosts() 返回单元素列表，范围为首尾相同
        assert "192.168.1.1" in info["host_range"]

    def test_ipv6_64(self) -> None:
        """IPv6 /64 子网。"""
        info = subnet_info("2001:db8::", 64)
        assert info["network"] == "2001:db8::"
        assert info["prefixlen"] == "64"

    def test_non_network_address_normalized(self) -> None:
        """非网络地址被规范化（strict=False）。"""
        info = subnet_info("192.168.1.100", 24)
        assert info["network"] == "192.168.1.0"

    def test_invalid_prefix_raises(self) -> None:
        """无效前缀长度抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的子网"):
            subnet_info("192.168.1.0", 33)

    def test_invalid_ip_raises(self) -> None:
        """无效 IP 抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的子网"):
            subnet_info("999.999.999.999", 24)


# ============================================================================ #
# validate_ip
# ============================================================================ #
class TestValidateIp:
    """validate_ip 校验测试。"""

    def test_valid_ipv4(self) -> None:
        """有效 IPv4。"""
        assert validate_ip("192.168.1.1") is True
        assert validate_ip("0.0.0.0") is True
        assert validate_ip("255.255.255.255") is True

    def test_valid_ipv6(self) -> None:
        """有效 IPv6。"""
        assert validate_ip("::1") is True
        assert validate_ip("2001:db8::1") is True
        assert validate_ip("::") is True

    def test_invalid_ipv4(self) -> None:
        """无效 IPv4。"""
        assert validate_ip("999.999.999.999") is False
        assert validate_ip("192.168.1") is False
        assert validate_ip("192.168.1.256") is False

    def test_invalid_ipv6(self) -> None:
        """无效 IPv6。"""
        assert validate_ip("::g") is False
        assert validate_ip("2001:db8::1::2") is False

    def test_non_ip_string(self) -> None:
        """非 IP 字符串。"""
        assert validate_ip("not an ip") is False
        assert validate_ip("") is False


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestIptoolCLI:
    """``iptool`` 通过 ``run_tool`` 调用测试。"""

    def test_parse_ipv4(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd iptool parse 192.168.1.1。"""
        code = run_tool("iptool", ["parse", "192.168.1.1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "version: 4" in out
        assert "is_private: True" in out

    def test_parse_ipv6(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd iptool parse ::1。"""
        code = run_tool("iptool", ["parse", "::1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "version: 6" in out
        assert "is_loopback: True" in out

    def test_parse_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """parse 无效 IP 提示。"""
        code = run_tool("iptool", ["parse", "999.999.999.999"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的 IP 地址" in out

    def test_subnet(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd iptool subnet 192.168.1.0 24。"""
        code = run_tool("iptool", ["subnet", "192.168.1.0", "24"])
        assert code == 0
        out = capsys.readouterr().out
        assert "network: 192.168.1.0" in out
        assert "netmask: 255.255.255.0" in out
        assert "broadcast: 192.168.1.255" in out
        assert "num_addresses: 256" in out

    def test_subnet_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """subnet 无效输入提示。"""
        code = run_tool("iptool", ["subnet", "192.168.1.0", "33"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效的子网" in out

    def test_validate_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd iptool validate 192.168.1.1。"""
        code = run_tool("iptool", ["validate", "192.168.1.1"])
        assert code == 0
        out = capsys.readouterr().out
        assert "有效" in out

    def test_validate_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd iptool validate 999.999.999.999。"""
        code = run_tool("iptool", ["validate", "999.999.999.999"])
        assert code == 0
        out = capsys.readouterr().out
        assert "无效" in out
