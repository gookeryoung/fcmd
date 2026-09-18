"""websave - 网页内容保存工具。

保存主页及其同源相关子页面，自动过滤广告等低相关链接；
同时下载页面引用的图片/CSS/JS 并改写引用为本地相对路径，供离线浏览。

示例
----
    fcmd websave https://example.com                    # 保存主页及一层子页面
    fcmd websave https://example.com -o saved_pages     # 保存到指定目录
    fcmd websave https://example.com --depth 3          # 爬取三级页面
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

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

# 静态资源后缀（页面链接命中则视为资源，不当作页面爬取）
_STATIC_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".css", ".js", ".ico", ".svg", ".pdf"})

# 匹配 <img>/<link>/<script> 的 src/href 属性（资源收集用）
_ASSET_ATTR_RE = re.compile(r'<(img|link|script)\b[^>]*?\b(?:src|href)=["\']([^"\']*)["\']', re.IGNORECASE)

# 匹配 <a>/<img>/<link>/<script> 的 href/src 属性（HTML 改写用）
_LINK_ATTR_RE = re.compile(r'<(a|img|link|script)\b[^>]*?\b(?:href|src)=["\']([^"\']*)["\']', re.IGNORECASE)

# 匹配内联 style / <style> 块中的 url(...)
_BG_URL_RE = re.compile(r'url\(["\']?([^"\')]+)["\']?\)', re.IGNORECASE)


def _sanitize_query(query: str) -> str:
    """将 URL 查询串转换为安全的文件名片段（非字母数字字符替换为下划线）。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", query)


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


def is_ad_url(url: str) -> bool:
    """判断 URL 是否命中广告特征（域名或路径含广告关键词）。

    Parameters
    ----------
    url:
        待判断 URL

    Returns
    -------
    bool
        命中广告特征时返回 ``True``，否则 ``False``
    """
    parsed = urlparse(url)
    return any(re.search(pattern, parsed.netloc + parsed.path, re.IGNORECASE) for pattern in _AD_PATTERNS)


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
    if is_ad_url(url):
        return False
    return Path(urlparse(url).path).suffix.lower() not in _STATIC_EXTENSIONS


def extract_links(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取普通链接并转换为绝对 URL。

    锚点片段被去除，避免同一页面因不同锚点被重复抓取。

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
        absolute = urljoin(base_url, stripped)
        absolute_urls.append(absolute.split("#", 1)[0])
    return absolute_urls


def url_to_file_path(url: str, output_dir: Path) -> Path:
    """将页面 URL 映射为本地保存路径。

    查询参数编码进文件名，避免不同参数页面（如 ``details.html?product_id=1``
    与 ``?product_id=2``）互相覆盖。

    Parameters
    ----------
    url:
        页面 URL
    output_dir:
        输出目录

    Returns
    -------
    Path
        页面应保存到的文件路径
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        path = "index.html"
    elif not posixpath.splitext(path)[1]:
        path += ".html"
    if parsed.query:
        stem, ext = posixpath.splitext(path)
        path = f"{stem}_{_sanitize_query(parsed.query)}{ext}"
    return output_dir / path


def asset_to_file_path(url: str, output_dir: Path) -> Path:
    """将资源 URL 映射为本地保存路径（``assets/<主机>/<路径>``）。

    Parameters
    ----------
    url:
        资源 URL
    output_dir:
        输出目录

    Returns
    -------
    Path
        资源应保存到的文件路径
    """
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    path = posixpath.normpath(parsed.path).strip("/")
    if not path:
        path = "index"
    if parsed.query:
        stem, ext = posixpath.splitext(path)
        path = f"{stem}_{_sanitize_query(parsed.query)}{ext}"
    return output_dir / "assets" / host / path


def relative_url(from_file: Path, to_file: Path) -> str:
    """计算 ``to_file`` 相对 ``from_file`` 所在目录的 POSIX 相对路径。

    Parameters
    ----------
    from_file:
        引用方文件（页面文件）
    to_file:
        被引用文件（页面或资源）

    Returns
    -------
    str
        相对路径（使用 ``/`` 分隔）
    """
    return os.path.relpath(to_file, from_file.parent).replace(os.sep, "/")


def download_asset(url: str, timeout: int = 30) -> bytes:
    """下载二进制资源（图片/CSS/JS）。

    Parameters
    ----------
    url:
        资源 URL
    timeout:
        超时秒数（默认 ``30``）

    Returns
    -------
    bytes
        资源原始字节

    Raises
    ------
    HTTPError
        HTTP 4xx/5xx 错误时
    URLError
        网络连接错误时
    """
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; fcmd-websave)"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def collect_asset_urls(html: str, base_url: str) -> list[str]:
    """收集页面中需下载的资源 URL。

    - 图片（``img src``）不限来源（站点图片常在 CDN），广告域名除外
    - CSS/JS（``link``/``script``）仅收集同源，跳过跨域（多为统计/跟踪脚本）
    - 内联 ``url(...)`` 背景图同样收集
    - 重复资源只收集一次

    Parameters
    ----------
    html:
        页面 HTML 文本
    base_url:
        页面 URL（用于拼接相对链接）

    Returns
    -------
    list[str]
        需下载的资源绝对 URL 列表
    """
    assets: list[str] = []
    seen: set[str] = set()
    for match in _ASSET_ATTR_RE.finditer(html):
        tag = match.group(1).lower()
        raw = match.group(2).strip()
        if not raw or raw.startswith(("#", "javascript:", "data:")):
            continue
        full = urljoin(base_url, raw)
        if tag == "img":
            if is_ad_url(full):
                continue
        elif not is_same_origin(base_url, full) or is_ad_url(full):
            continue
        if full not in seen:
            seen.add(full)
            assets.append(full)
    for match in _BG_URL_RE.finditer(html):
        raw = match.group(1).strip()
        if not raw or raw.startswith(("data:", "javascript:")):
            continue
        full = urljoin(base_url, raw)
        if is_ad_url(full):
            continue
        if full not in seen:
            seen.add(full)
            assets.append(full)
    return assets


def rewrite_html(  # noqa: PLR0913 - 参数均为改写所需的独立配置项
    html: str,
    base_url: str,
    page_file: Path,
    local_assets: dict[str, Path],
    output_path: Path,
    rewrite_links: bool = False,
) -> str:
    """改写页面 HTML，将资源与页面链接指向本地文件。

    - ``img``/``link``/``script`` 引用改写为已下载资源的相对路径
    - 内联 ``background-image: url(...)`` 同样改写
    - ``a href`` 指向将被保存的同源页面时改写为本地相对路径（保留锚点）；
      未保存的链接（跨域/广告/超出深度）保留原值，保证在线仍可点击

    Parameters
    ----------
    html:
        原始页面 HTML
    base_url:
        页面 URL
    page_file:
        本页保存后的文件路径（用于计算相对路径）
    local_assets:
        已下载资源映射（URL -> 本地路径）
    output_path:
        输出目录（用于计算子页面文件路径）
    rewrite_links:
        是否改写页面链接（子页面将被保存时传 ``True``）

    Returns
    -------
    str
        改写后的 HTML
    """

    def replace_attr(match: re.Match[str]) -> str:
        tag = match.group(1).lower()
        raw = match.group(2).strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return match.group(0)
        full = urljoin(base_url, raw)
        parsed = urlparse(full)
        # 保留查询串：文件按 URL 落盘时查询已编码进文件名，但浏览器
        # 打开 file:// 链接会忽略查询串，故 ``style.css?time=`` 这类
        # JS 动态拼接的缓存规避链接离线仍可正确命中本地文件
        suffix = f"?{parsed.query}" if parsed.query else ""
        if tag == "a":
            if not rewrite_links or not is_relevant_url(base_url, full):
                return match.group(0)
            new_value = relative_url(page_file, url_to_file_path(full, output_path))
            if parsed.fragment:
                suffix += "#" + parsed.fragment
        else:
            if full not in local_assets:
                return match.group(0)
            new_value = relative_url(page_file, local_assets[full])
        new_value += suffix
        start = match.start(2) - match.start(0)
        end = match.end(2) - match.start(0)
        return match.group(0)[:start] + new_value + match.group(0)[end:]

    def replace_bg(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        if not raw or raw.startswith(("data:", "javascript:")):
            return match.group(0)
        full = urljoin(base_url, raw)
        if full not in local_assets:
            return match.group(0)
        new_value = relative_url(page_file, local_assets[full])
        parsed = urlparse(full)
        if parsed.query:
            new_value += "?" + parsed.query
        start = match.start(1) - match.start(0)
        end = match.end(1) - match.start(0)
        return match.group(0)[:start] + new_value + match.group(0)[end:]

    rewritten = _LINK_ATTR_RE.sub(replace_attr, html)
    return _BG_URL_RE.sub(replace_bg, rewritten)


def rewrite_css(
    css: str,
    css_url: str,
    css_file: Path,
    local_assets: dict[str, Path | None],
) -> str:
    """改写 CSS 内容，将 ``url(...)`` 引用指向本地资源。

    相对 ``url()`` 引用按 CSS 文件的 URL 拼接后与已下载资源匹配；
    查询串保留在改写后的引用中（``file://`` 下浏览器忽略查询串）。

    Parameters
    ----------
    css:
        CSS 文本
    css_url:
        CSS 文件的 URL（用于拼接相对引用）
    css_file:
        CSS 文件保存后的本地路径（用于计算相对路径）
    local_assets:
        已下载资源映射（URL -> 本地路径；``None`` 表示下载失败）

    Returns
    -------
    str
        改写后的 CSS 文本
    """

    def replace_url(match: re.Match[str]) -> str:
        raw = match.group(1).strip()
        if not raw or raw.startswith(("data:", "#", "javascript:")):
            return match.group(0)
        full = urljoin(css_url, raw)
        local_path = local_assets.get(full)
        if local_path is None:
            return match.group(0)
        new_value = relative_url(css_file, local_path)
        parsed = urlparse(full)
        if parsed.query:
            new_value += "?" + parsed.query
        start = match.start(1) - match.start(0)
        end = match.end(1) - match.start(0)
        return match.group(0)[:start] + new_value + match.group(0)[end:]

    return _BG_URL_RE.sub(replace_url, css)


def download_assets_recursive(
    asset_urls: list[str],
    output_path: Path,
    asset_cache: dict[str, Path | None],
    timeout: int = 30,
) -> None:
    """下载资源列表，CSS 文件内 ``url()`` 引用的资源一并下载并改写。

    - 通过 ``asset_cache`` 去重，同一资源只下载一次（失败以 ``None`` 标记，不重复尝试）
    - CSS 文件的 ``url()`` 引用递归处理（``@import`` 链），图片/字体等跨域来源同样下载
    - 下载失败的资源跳过，CSS 中相应引用保持原值

    Parameters
    ----------
    asset_urls:
        需下载的资源绝对 URL 列表
    output_path:
        输出目录
    asset_cache:
        资源缓存（URL -> 本地路径；``None`` 表示下载失败）
    timeout:
        请求超时秒数（默认 ``30``）
    """
    pending = [url for url in asset_urls if url not in asset_cache]
    while pending:
        url = pending.pop(0)
        if url in asset_cache:
            continue
        try:
            data = download_asset(url, timeout)
        except (HTTPError, URLError):
            asset_cache[url] = None
            continue
        local_path = asset_to_file_path(url, output_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.suffix.lower() == ".css":
            text = data.decode("utf-8", errors="replace")
            refs: list[str] = []
            for raw in _BG_URL_RE.findall(text):
                ref = raw.strip()
                if not ref or ref.startswith(("data:", "#", "javascript:")):
                    continue
                full = urljoin(url, ref)
                if is_ad_url(full):
                    continue
                refs.append(full)
            # 先下载子资源，改写时才能映射到本地路径
            download_assets_recursive(refs, output_path, asset_cache, timeout)
            data = rewrite_css(text, url, local_path, asset_cache).encode("utf-8")
        local_path.write_bytes(data)
        asset_cache[url] = local_path


def save_page(url: str, content: str, output_dir: Path) -> None:
    """将页面内容保存为本地 HTML 文件。

    Parameters
    ----------
    url:
        页面 URL
    content:
        页面 HTML 内容
    output_dir:
        输出目录
    """
    file_path = url_to_file_path(url, output_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def save_website(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> None:
    """保存主页及其同源相关子页面，并下载图片/CSS/JS 供离线浏览。

    从主页开始按深度优先爬取：仅跟随同源、非广告、非静态资源的链接；
    每页引用的图片（任意来源）、同源 CSS/JS 与内联背景图会下载到
    ``assets/`` 目录，CSS 文件内 ``url()`` 引用的图片/字体一并下载，
    HTML 与 CSS 中的引用统一改写为本地相对路径。

    Parameters
    ----------
    url:
        主页 URL
    output_dir:
        输出目录（默认 ``saved_website``）
    depth:
        爬取深度（默认 ``2``，主页及一层子页面）
    timeout:
        请求超时秒数（默认 ``30``）
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    visited: set[str] = set()
    asset_cache: dict[str, Path | None] = {}

    def crawl(current_url: str, current_depth: int) -> None:
        if current_depth > depth or current_url in visited:
            return
        visited.add(current_url)
        try:
            html = http_get(current_url, timeout=timeout)
        except (HTTPError, URLError) as exc:
            print(f"[websave] 警告: 无法获取 {current_url}: {exc}")
            return
        # 收集并下载本页资源（全站共享缓存，同一资源只下载一次；
        # CSS 内 url() 引用的图片随 CSS 递归下载）
        local_assets: dict[str, Path] = {}
        asset_urls = collect_asset_urls(html, current_url)
        download_assets_recursive(asset_urls, output_path, asset_cache, timeout)
        for asset_url in asset_urls:
            local_path = asset_cache.get(asset_url)
            if local_path is not None:
                local_assets[asset_url] = local_path
        # 改写 HTML（资源本地化、子页入口指向本地文件）后保存
        page_file = url_to_file_path(current_url, output_path)
        rewritten = rewrite_html(
            html,
            current_url,
            page_file,
            local_assets,
            output_path,
            rewrite_links=current_depth < depth,
        )
        save_page(current_url, rewritten, output_path)
        if current_depth < depth:
            for link in extract_links(html, current_url):
                if is_relevant_url(url, link):
                    crawl(link, current_depth + 1)

    crawl(url, 1)
    print(f"[websave] 成功: 网站内容已保存到 {output_path.resolve()}")


@fcmd.tool("websave", help="保存网页内容（主页及相关子页面，含图片/CSS/JS）")
def websave_cmd(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> None:
    """保存网页内容（主页及相关子页面，含图片/CSS/JS）。

    Parameters
    ----------
    url:
        主页 URL
    output_dir:
        输出目录（默认 ``saved_website``）
    depth:
        爬取深度（默认 ``2``，主页及一层子页面）
    timeout:
        请求超时秒数（默认 ``30``）
    """
    save_website(url, output_dir, depth, timeout)


@fcmd.main("websave")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
