"""nettool - HTTP 请求工具（GET / POST / HEAD）。

示例
----
    fcmd nettool get https://example.com
    fcmd nettool post https://httpbin.org/post --data '{"a":1}'
    fcmd nettool head https://example.com --timeout 10
"""

from __future__ import annotations

from urllib.error import HTTPError, URLError

import fcmd as fx
from fcmd.infras import net as netinfra

__all__ = ["nettool_get", "nettool_head", "nettool_post"]


@fx.tool(
    "nettool",
    subcommand="get",
    help="HTTP GET 请求",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 30）",
    },
)
def nettool_get(url: str, timeout: int = 30) -> None:
    """发送 HTTP GET 请求并打印响应体。"""
    try:
        print(netinfra.http_get(url, timeout))
    except (HTTPError, URLError) as exc:
        print(f"错误: 请求失败: {exc}")


@fx.tool(
    "nettool",
    subcommand="post",
    help="HTTP POST 请求",
    param_help={
        "url": "目标 URL",
        "data": "请求体（默认空串）",
        "timeout": "超时秒数（默认 30）",
    },
)
def nettool_post(url: str, data: str = "", timeout: int = 30) -> None:
    """发送 HTTP POST 请求并打印响应体。"""
    try:
        print(netinfra.http_post(url, data, timeout))
    except (HTTPError, URLError) as exc:
        print(f"错误: 请求失败: {exc}")


@fx.tool(
    "nettool",
    subcommand="head",
    help="HTTP HEAD 请求",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 30）",
    },
)
def nettool_head(url: str, timeout: int = 30) -> None:
    """发送 HTTP HEAD 请求并打印响应头。"""
    try:
        headers = netinfra.http_head(url, timeout)
    except (HTTPError, URLError) as exc:
        print(f"错误: 请求失败: {exc}")
        return
    for key, value in headers.items():
        print(f"{key}: {value}")


@fx.main("nettool")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
