"""nettool - HTTP 客户端工具。

使用标准库 ``urllib`` 执行 HTTP 请求：GET / POST / HEAD。

示例
----
    fcmd nettool get https://httpbin.org/get           # GET 请求
    fcmd nettool get https://httpbin.org/get --timeout 10
    fcmd nettool post https://httpbin.org/post --data '{"k":"v"}'
    fcmd nettool head https://httpbin.org/get            # 查看响应头
"""

from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fcmd

__all__ = [
    "http_get",
    "http_head",
    "http_post",
]

# 默认请求超时（秒）
_DEFAULT_TIMEOUT = 30


# ============================================================================
# 公共函数
# ============================================================================


def http_get(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """发送 HTTP GET 请求并返回响应体。

    Parameters
    ----------
    url:
        目标 URL
    timeout:
        超时秒数（默认 ``30``）

    Returns
    -------
    str
        响应体文本（UTF-8 解码，遇非法字节替换）

    Raises
    ------
    HTTPError
        HTTP 4xx/5xx 错误时
    URLError
        网络连接错误（DNS 解析失败、拒绝连接等）时
    """
    req = Request(url)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_post(url: str, data: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
    """发送 HTTP POST 请求并返回响应体。

    Parameters
    ----------
    url:
        目标 URL
    data:
        请求体（默认空串）
    timeout:
        超时秒数（默认 ``30``）

    Returns
    -------
    str
        响应体文本（UTF-8 解码，遇非法字节替换）

    Raises
    ------
    HTTPError
        HTTP 4xx/5xx 错误时
    URLError
        网络连接错误时
    """
    req = Request(url, data=data.encode("utf-8"), method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_head(url: str, timeout: int = _DEFAULT_TIMEOUT) -> dict[str, str]:
    """发送 HTTP HEAD 请求并返回响应头。

    Parameters
    ----------
    url:
        目标 URL
    timeout:
        超时秒数（默认 ``30``）

    Returns
    -------
    dict[str, str]
        响应头字典

    Raises
    ------
    HTTPError
        HTTP 4xx/5xx 错误时
    URLError
        网络连接错误时
    """
    req = Request(url, method="HEAD")
    with urlopen(req, timeout=timeout) as resp:
        return dict(resp.headers.items())


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("nettool", subcommand="get", help="HTTP GET 请求")
def get_cmd(url: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
    """发送 HTTP GET 请求并打印响应体。

    Parameters
    ----------
    url:
        目标 URL
    timeout:
        超时秒数（默认 ``30``）
    """
    try:
        print(http_get(url, timeout))
    except (HTTPError, URLError) as exc:
        print(f"请求失败: {exc}")


@fcmd.tool("nettool", subcommand="post", help="HTTP POST 请求")
def post_cmd(url: str, data: str = "", timeout: int = _DEFAULT_TIMEOUT) -> None:
    """发送 HTTP POST 请求并打印响应体。

    Parameters
    ----------
    url:
        目标 URL
    data:
        请求体（默认空串）
    timeout:
        超时秒数（默认 ``30``）
    """
    try:
        print(http_post(url, data, timeout))
    except (HTTPError, URLError) as exc:
        print(f"请求失败: {exc}")


@fcmd.tool("nettool", subcommand="head", help="HTTP HEAD 请求")
def head_cmd(url: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
    """发送 HTTP HEAD 请求并打印响应头。

    Parameters
    ----------
    url:
        目标 URL
    timeout:
        超时秒数（默认 ``30``）
    """
    try:
        headers = http_head(url, timeout)
        for key, value in headers.items():
            print(f"{key}: {value}")
    except (HTTPError, URLError) as exc:
        print(f"请求失败: {exc}")


def main() -> None:
    """``nettool`` 入口：等价于 ``fcmd nettool <args>``。"""
    from fcmd.cli._common import run_tool_main

    run_tool_main("nettool")


if __name__ == "__main__":
    main()
