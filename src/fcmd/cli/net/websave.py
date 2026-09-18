"""websave - 网页内容保存工具。

保存主页及其同源相关子页面，自动过滤广告、静态资源等低相关链接。

示例
----
    fcmd websave https://example.com                    # 仅保存主页
    fcmd websave https://example.com -o saved_pages     # 保存到指定目录
    fcmd websave https://example.com --depth 2          # 爬取两级页面
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

import fcmd
from fcmd.cli.net.nettool import http_get

__all__ = ["save_website"]

# 广告链接特征（匹配域名或路径）
_AD_PATTERNS: tuple[str, ...] = (
    r"ad\.",
    r"ads\.",
    r"banner",
    r"promotion",
    r"sponsor",
    r"doubleclick",
    r"googleads",
    r"adserver",
    r"advertising",
)

# 静态资源后缀（只保存页面，不下载资源）
_STATIC_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".css", ".js", ".ico", ".svg", ".pdf"})


def is_same_origin(base_url: str, target_url: str) -> bool:
    """判断两个 URL 是否同源（协议与主机一致）。

    Parameters
    ----------
    base_url:
        基准 URL
    target_url:
        待判断 URL

    Returns
    -------
    bool
        同源时返回 ``True``，否则 ``False``
    """
    base = urlparse(base_url)
    target = urlparse(target_url)
    return base.scheme == target.scheme and base.netloc == target.netloc


def is_relevant_url(base_url: str, url: str) -> bool:
    """判断 URL 是否值得爬取（同源、非广告、非静态资源）。

    Parameters
    ----------
    base_url:
        主页 URL
    url:
        待判断的链接

    Returns
    -------
    bool
        相关页面返回 ``True``；广告链接、静态资源、跨域链接返回 ``False``
    """
    if not is_same_origin(base_url, url):
        return False
    parsed = urlparse(url)
    # 域名或路径含广告特征则跳过
    for pattern in _AD_PATTERNS:
        if re.search(pattern, parsed.netloc + parsed.path, re.IGNORECASE):
            return False
    # 静态资源文件不保存
    return Path(parsed.path).suffix.lower() not in _STATIC_EXTENSIONS


def extract_links(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取普通链接并转换为绝对 URL。

    Parameters
    ----------
    html:
        页面 HTML 文本
    base_url:
        页面 URL（用于拼接相对链接）

    Returns
    -------
    list[str]
        绝对 URL 列表（排除锚点、mailto、tel 链接）
    """
    links = re.findall(r'<a[^>]+href=["\'](.*?)["\']', html, re.IGNORECASE)
    absolute_urls: list[str] = []
    for link in links:
        stripped = link.strip()
        if stripped.startswith(("#", "mailto:", "tel:")):
            continue
        absolute_urls.append(urljoin(base_url, stripped))
    return absolute_urls


def save_page(url: str, content: str, output_dir: Path) -> None:
    """将页面内容保存为本地 HTML 文件。

    按 URL 路径映射到 ``output_dir`` 下的相对路径，根路径保存为 ``index.html``。

    Parameters
    ----------
    url:
        页面 URL
    content:
        页面 HTML 内容
    output_dir:
        输出目录
    """
    path = urlparse(url).path.strip("/")
    if not path:
        path = "index.html"
    elif not path.endswith(".html"):
        path += ".html"
    file_path = output_dir / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def save_website(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 1,
    timeout: int = 30,
) -> None:
    """保存主页及其同源相关子页面。

    从主页开始按深度优先爬取：仅跟随同源、非广告、非静态资源的链接，
    每个页面保存为本地 HTML 文件。

    Parameters
    ----------
    url:
        主页 URL
    output_dir:
        输出目录（默认 ``saved_website``）
    depth:
        爬取深度（默认 ``1``，仅主页）
    timeout:
        请求超时秒数（默认 ``30``）
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    visited: set[str] = set()

    def crawl(current_url: str, current_depth: int) -> None:
        if current_depth > depth or current_url in visited:
            return
        visited.add(current_url)
        try:
            html = http_get(current_url, timeout=timeout)
        except (HTTPError, URLError) as exc:
            print(f"[websave] 警告: 无法获取 {current_url}: {exc}")
            return
        save_page(current_url, html, output_path)
        if current_depth < depth:
            for link in extract_links(html, current_url):
                if is_relevant_url(url, link):
                    crawl(link, current_depth + 1)

    crawl(url, 1)
    print(f"[websave] 成功: 网站内容已保存到 {output_path.resolve()}")


@fcmd.tool("websave", help="保存网页内容（主页及相关子页面）")
def websave_cmd(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 1,
    timeout: int = 30,
) -> None:
    """保存网页内容（主页及相关子页面）。

    Parameters
    ----------
    url:
        主页 URL
    output_dir:
        输出目录（默认 ``saved_website``）
    depth:
        爬取深度（默认 ``1``，仅主页）
    timeout:
        请求超时秒数（默认 ``30``）
    """
    save_website(url, output_dir, depth, timeout)


@fcmd.main("websave")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
