"""``fcmd.infras.net.webpage``（由 ``infras/net.py`` 拆分）。"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

from . import http as _http

__all__ = [
    "WebsiteSaveResult",
    "asset_to_file_path",
    "collect_asset_urls",
    "download_asset",
    "download_assets_recursive",
    "extract_links",
    "is_ad_url",
    "is_relevant_url",
    "is_same_origin",
    "relative_url",
    "rewrite_css",
    "rewrite_html",
    "save_page",
    "save_website",
    "url_to_file_path",
]


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


_STATIC_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".css", ".js", ".ico", ".svg", ".pdf"})


_ASSET_ATTR_RE = re.compile(r'<(img|link|script)\b[^>]*?\b(?:src|href)=["\']([^"\']*)["\']', re.IGNORECASE)


_LINK_ATTR_RE = re.compile(r'<(a|img|link|script)\b[^>]*?\b(?:href|src)=["\']([^"\']*)["\']', re.IGNORECASE)


_BG_URL_RE = re.compile(r'url\(["\']?([^"\')]+)["\']?\)', re.IGNORECASE)


def _sanitize_query(query: str) -> str:
    """将 URL 查询串转换为安全的文件名片段。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", query)


def is_same_origin(base_url: str, target_url: str) -> bool:
    """判断两个 URL 是否同源（协议与主机一致）。"""
    base = urlparse(base_url)
    target = urlparse(target_url)
    return base.scheme == target.scheme and base.netloc == target.netloc


def is_ad_url(url: str) -> bool:
    """判断 URL 是否命中广告特征。"""
    parsed = urlparse(url)
    return any(re.search(pattern, parsed.netloc + parsed.path, re.IGNORECASE) for pattern in _AD_PATTERNS)


def is_relevant_url(base_url: str, url: str) -> bool:
    """判断 URL 是否值得爬取（同源、非广告、非静态资源）。"""
    if not is_same_origin(base_url, url):
        return False
    if is_ad_url(url):
        return False
    return Path(urlparse(url).path).suffix.lower() not in _STATIC_EXTENSIONS


def extract_links(html: str, base_url: str) -> list[str]:
    """从 HTML 中提取普通链接并转换为绝对 URL。"""
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
    """将页面 URL 映射为本地保存路径。"""
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
    """将资源 URL 映射为本地保存路径。"""
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
    """计算 ``to_file`` 相对 ``from_file`` 所在目录的 POSIX 相对路径。"""
    return os.path.relpath(to_file, from_file.parent).replace(os.sep, "/")


def download_asset(url: str, timeout: int = 30) -> bytes:
    """下载二进制资源（图片/CSS/JS）。"""
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链，见 http_get

    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; fcmd-websave)"})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def collect_asset_urls(html: str, base_url: str) -> list[str]:
    """收集页面中需下载的资源 URL。"""
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


def rewrite_html(  # noqa: PLR0913, PLR0917
    html: str,
    base_url: str,
    page_file: Path,
    local_assets: dict[str, Path],
    output_path: Path,
    rewrite_links: bool = False,
) -> str:
    """改写页面 HTML，将资源与页面链接指向本地文件。"""

    def replace_attr(match: re.Match[str]) -> str:
        tag = match.group(1).lower()
        raw = match.group(2).strip()
        if not raw or raw.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return match.group(0)
        full = urljoin(base_url, raw)
        parsed = urlparse(full)
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
    """改写 CSS 内容，将 ``url(...)`` 引用指向本地资源。"""

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
    """下载资源列表，CSS 文件内 url() 引用的资源一并下载并改写。"""
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
            download_assets_recursive(refs, output_path, asset_cache, timeout)
            data = rewrite_css(text, url, local_path, asset_cache).encode("utf-8")
        local_path.write_bytes(data)
        asset_cache[url] = local_path


def save_page(url: str, content: str, output_dir: Path) -> None:
    """将页面内容保存为本地 HTML 文件。"""
    file_path = url_to_file_path(url, output_dir)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


@dataclass(frozen=True)
class WebsiteSaveResult:
    """网页保存结果：输出目录与抓取失败的页面列表。"""

    output_path: Path
    warnings: list[tuple[str, str]]


def save_website(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> WebsiteSaveResult:
    """保存主页及其同源相关子页面，并下载图片/CSS/JS 供离线浏览。

    Returns
    -------
    WebsiteSaveResult
        ``output_path`` 为输出目录，``warnings`` 为 ``(url, 错误信息)`` 列表。
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    visited: set[str] = set()
    asset_cache: dict[str, Path | None] = {}
    warnings: list[tuple[str, str]] = []

    def crawl(current_url: str, current_depth: int) -> None:
        if current_depth > depth or current_url in visited:
            return
        visited.add(current_url)
        try:
            html = _http.http_get(current_url, timeout=timeout)
        except (HTTPError, URLError) as exc:
            warnings.append((current_url, str(exc)))
            return
        local_assets: dict[str, Path] = {}
        asset_urls = collect_asset_urls(html, current_url)
        download_assets_recursive(asset_urls, output_path, asset_cache, timeout)
        for asset_url in asset_urls:
            local_path = asset_cache.get(asset_url)
            if local_path is not None:
                local_assets[asset_url] = local_path
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
    return WebsiteSaveResult(output_path=output_path, warnings=warnings)
