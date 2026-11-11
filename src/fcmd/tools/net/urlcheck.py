"""urlcheck - URL 可访问性与延迟检测。

示例
----
    fcmd urlcheck u https://example.com
    fcmd urlcheck r https://example.com https://www.python.org
"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras import net as netinfra

__all__ = ["urlcheck_r", "urlcheck_u"]


@fx.tool(
    "urlcheck",
    subcommand="u",
    help="检测单个 URL 可访问性与延迟",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 5）",
    },
)
def urlcheck_u(url: str, timeout: float = 5.0) -> None:
    """检测单个 URL 的可访问性并打印延迟。"""
    ok, latency = netinfra.urlcheck_single(url, timeout)
    if ok:
        print(f"{url} -> 可访问 (延迟 {latency:.0f} ms)")
    else:
        print(f"{url} -> 不可访问")


@fx.tool(
    "urlcheck",
    subcommand="r",
    help="批量探测 URL 并按访问速度排序",
    param_help={
        "urls": "目标 URL 列表",
        "timeout": "单个 URL 的超时秒数（默认 5）",
    },
)
def urlcheck_r(urls: list[str], timeout: float = 5.0) -> None:
    """并发探测多个 URL，按访问速度排序打印。"""
    if not urls:
        print("请提供至少一个 URL")
        return
    results = netinfra.urlcheck_many(urls, timeout)
    print(f"URL 探测结果（共 {len(results)} 个，按访问速度排序）:")
    for index, (url, ok, latency) in enumerate(results, start=1):
        speed = f"{latency:.0f} ms" if ok else "-"
        status = "可访问" if ok else "不可访问"
        print(f"  {index}. {url}  {status}  {speed}")


@fx.main("urlcheck")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
