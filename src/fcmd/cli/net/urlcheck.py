"""urlcheck - URL 可访问性与访问速度检测工具。

并发探测 URL 列表：检测是否可访问并测量响应延迟（毫秒），
常用于镜像源选优（配合 ``fcmd envdev mirror`` / ``envdev lang <语言> --mirror auto``）。

示例
----
    fcmd urlcheck u https://mirrors.ustc.edu.cn            # 检测单个 URL
    fcmd urlcheck r https://a.com https://b.com            # 批量探测并按速度排序
    fcmd urlcheck r https://a.com https://b.com --timeout 3
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import fcmd

__all__ = [
    "check_url",
    "check_urls",
    "unreachable_latency",
]

# 默认探测超时（秒）
_DEFAULT_TIMEOUT = 5.0

# 并发探测线程数上限
_DEFAULT_WORKERS = 8


def unreachable_latency() -> float:
    """返回不可达时使用的延迟占位值（正无穷，排序时自然落到末尾）。"""
    return float("inf")


# 探测请求 UA（部分镜像站 WAF 拦截默认 Python-urllib UA）
_PROBE_UA: str = "Mozilla/5.0 (compatible; fcmd-urlcheck)"


def _probe_once(url: str, method: str, timeout: float) -> tuple[bool, float] | None:
    """执行单次请求并测量延迟。

    Returns
    -------
    tuple[bool, float] | None
        ``(可访问, 延迟毫秒)``。收到成功响应（2xx/3xx）判可访问；
        HEAD 收到 HTTP 错误响应（4xx/5xx）时返回 ``None`` 回退 GET 复测
        （部分站点仅禁用 HEAD）；GET 收到 HTTP 错误响应判不可访问。
        连接级错误（DNS 失败、拒绝连接、超时）判不可访问。
    """
    start = time.perf_counter()
    try:
        req = Request(url, method=method, headers={"User-Agent": _PROBE_UA})
        with urlopen(req, timeout=timeout):
            pass
    except HTTPError:
        # 成功响应之外的状态码：HEAD 可能仅被禁用（405 等），回退 GET 复测；
        # GET 仍报错则站点当前确实不可用（如 WAF 全站拦截、502/503）
        if method == "HEAD":
            return None
        return (False, unreachable_latency())
    except (URLError, TimeoutError, OSError):
        # DNS 解析失败、拒绝连接、超时等连接级错误——不可访问
        return (False, unreachable_latency())
    return (True, (time.perf_counter() - start) * 1000)


def check_url(url: str, timeout: float = _DEFAULT_TIMEOUT) -> tuple[bool, float]:
    """检测单个 URL 的可访问性与响应延迟。

    先发 HEAD 请求（开销最小）；HEAD 收到 HTTP 错误响应时回退 GET 验证
    （部分站点仅禁用 HEAD，回退可避免误判）。可访问以成功响应
    （2xx/3xx）为准；HTTP 错误响应（4xx/5xx，如 WAF 全站拦截、502/503）
    与连接级错误（DNS 失败、拒绝连接、超时）均判不可访问。
    延迟为请求发出到响应头返回的耗时（毫秒）。

    Parameters
    ----------
    url:
        目标 URL
    timeout:
        超时秒数（默认 ``5``）

    Returns
    -------
    tuple[bool, float]
        ``(是否可访问, 延迟毫秒)``；不可访问时延迟为正无穷
        （可用 :func:`unreachable_latency` 比较）。
    """
    head = _probe_once(url, "HEAD", timeout)
    if head is not None:
        return head
    get = _probe_once(url, "GET", timeout)
    assert get is not None  # GET 必有最终结果（None 仅限 HEAD 被拒回退）
    return get


def check_urls(
    urls: Sequence[str],
    timeout: float = _DEFAULT_TIMEOUT,
    workers: int = _DEFAULT_WORKERS,
) -> list[tuple[str, bool, float]]:
    """并发探测多个 URL 并按访问速度排序。

    可访问的 URL 按延迟升序排列在前，不可访问的排在末尾。

    Parameters
    ----------
    urls:
        目标 URL 列表
    timeout:
        单个 URL 的超时秒数（默认 ``5``）
    workers:
        并发线程数上限（默认 ``8``）

    Returns
    -------
    list[tuple[str, bool, float]]
        ``(url, 是否可访问, 延迟毫秒)`` 列表，按可达优先 + 延迟升序排序。
    """
    if not urls:
        return []

    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        results = list(pool.map(lambda u: (u, *check_url(u, timeout)), urls))

    return sorted(results, key=lambda r: (not r[1], r[2]))


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("urlcheck", subcommand="u", help="检测单个 URL 可访问性与延迟")
def check_url_cmd(url: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
    """检测单个 URL 的可访问性并打印延迟。

    Parameters
    ----------
    url:
        目标 URL
    timeout:
        超时秒数（默认 ``5``）
    """
    ok, latency = check_url(url, timeout)
    if ok:
        print(f"{url} -> 可访问 (延迟 {latency:.0f} ms)")
    else:
        print(f"{url} -> 不可访问")


@fcmd.tool("urlcheck", subcommand="r", help="批量探测 URL 并按访问速度排序")
def check_urls_cmd(urls: list[str], timeout: float = _DEFAULT_TIMEOUT) -> None:
    """并发探测多个 URL，按访问速度排序打印。

    Parameters
    ----------
    urls:
        目标 URL 列表（至少一个）
    timeout:
        单个 URL 的超时秒数（默认 ``5``）
    """
    if not urls:
        print("请提供至少一个 URL")
        return

    results = check_urls(urls, timeout)
    print(f"URL 探测结果（共 {len(results)} 个，按访问速度排序）:")
    for index, (url, ok, latency) in enumerate(results, start=1):
        speed = f"{latency:.0f} ms" if ok else "-"
        status = "可访问" if ok else "不可访问"
        print(f"  {index}. {url}  {status}  {speed}")


@fcmd.main("urlcheck")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
