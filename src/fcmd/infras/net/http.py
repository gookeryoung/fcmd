"""``fcmd.infras.net.http``（由 ``infras/net.py`` 拆分）。"""

from __future__ import annotations

__all__ = ["http_get", "http_head", "http_post"]


_DEFAULT_TIMEOUT = 30


def http_get(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """发送 HTTP GET 请求并返回响应体。"""
    # 惰性导入 ssl 链（urllib.request → http.client → ssl），避免工具发现期加载
    from urllib.request import Request, urlopen

    req = Request(url)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_post(url: str, data: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
    """发送 HTTP POST 请求并返回响应体。"""
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链，见 http_get

    req = Request(url, data=data.encode("utf-8"), method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def http_head(url: str, timeout: int = _DEFAULT_TIMEOUT) -> dict[str, str]:
    """发送 HTTP HEAD 请求并返回响应头。"""
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链，见 http_get

    req = Request(url, method="HEAD")
    with urlopen(req, timeout=timeout) as resp:
        return dict(resp.headers.items())
