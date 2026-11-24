"""iptool - IP 地址解析与子网计算。

示例
----
    fcmd iptool parse 192.168.1.10
    fcmd iptool subnet 192.168.1.0 24
    fcmd iptool validate 2001:db8::1
"""

from __future__ import annotations

import fcmd as fx
from fcmd.core.result import emit_result, report_error
from fcmd.infras import net as netinfra

__all__ = ["iptool_parse", "iptool_subnet", "iptool_validate"]


@fx.tool(
    "iptool",
    subcommand="parse",
    help="解析 IP 地址",
    param_help={"ip": "IP 地址字符串（IPv4 或 IPv6）"},
)
def iptool_parse(ip: str, json_output: bool = False) -> None:
    """解析 IP 地址并逐行打印属性。"""
    try:
        info = netinfra.parse_ip(ip)
    except ValueError as exc:
        report_error(
            "iptool",
            "parse",
            code="INVALID_ARGUMENT",
            message=str(exc),
            field="ip",
            expected="合法的 IPv4 / IPv6 地址字符串",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("iptool", "parse", {"ip": ip, **info})
        return
    for key, value in info.items():
        print(f"{key}: {value}")


@fx.tool(
    "iptool",
    subcommand="subnet",
    help="计算子网信息",
    param_help={
        "ip": "网络地址字符串",
        "prefix": "前缀长度",
    },
)
def iptool_subnet(ip: str, prefix: int, json_output: bool = False) -> None:
    """计算子网信息并逐行打印。"""
    try:
        info = netinfra.subnet_info(ip, prefix)
    except ValueError as exc:
        report_error(
            "iptool",
            "subnet",
            code="INVALID_ARGUMENT",
            message=str(exc),
            field="ip",
            expected="合法的网络地址与前缀长度（如 192.168.1.0 / 24）",
            json_output=json_output,
        )
        return
    if json_output:
        emit_result("iptool", "subnet", {"ip": ip, "prefix": prefix, **info})
        return
    for key, value in info.items():
        print(f"{key}: {value}")


@fx.tool(
    "iptool",
    subcommand="validate",
    help="校验 IP 地址格式",
    param_help={"ip": "待校验的 IP 地址字符串"},
)
def iptool_validate(ip: str, json_output: bool = False) -> None:
    """校验 IP 地址格式是否有效。"""
    valid = netinfra.validate_ip(ip)
    if json_output:
        emit_result("iptool", "validate", {"ip": ip, "valid": valid})
        return
    if valid:
        print(f"有效: {ip}")
    else:
        print(f"无效: {ip}")


@fx.main("iptool")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
