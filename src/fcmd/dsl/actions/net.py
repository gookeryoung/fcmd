"""网络工具 DSL 动作：nettool / iptool / websave / urlcheck。

原模块 ``fcmd.tools.net.*`` 下四个工具均为纯 Python 实现（urllib / ipaddress
/ concurrent.futures / pathlib），无子进程调用。迁移后由 ``@action`` 装饰器
注册，TOML 声明 ``action = "<名>"`` 直接引用。
"""

from __future__ import annotations

import ipaddress
import os
import posixpath
import re
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse

from fcmd.dsl.actions import action

__all__: list[str] = []


# ============================================================================
# nettool 公共逻辑
# ============================================================================

_DEFAULT_TIMEOUT = 30


def http_get(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """发送 HTTP GET 请求并返回响应体。"""
    # 惰性导入 ssl 链（urllib.request → http.client → ssl）：本模块被动作
    # 注册表在工具发现期导入，顶层导入会让每个 CLI 命令启动都付 ~37ms
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


# ============================================================================
# nettool DSL 动作
# ============================================================================


@action(
    "nettool_get",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
    error_prefix="请求失败: ",
)
def nettool_get(url: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
    """发送 HTTP GET 请求并打印响应体。"""
    print(http_get(url, timeout))


@action(
    "nettool_post",
    param_help={
        "url": "目标 URL",
        "data": "请求体（默认空串）",
        "timeout": "超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
    error_prefix="请求失败: ",
)
def nettool_post(url: str, data: str = "", timeout: int = _DEFAULT_TIMEOUT) -> None:
    """发送 HTTP POST 请求并打印响应体。"""
    print(http_post(url, data, timeout))


@action(
    "nettool_head",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
    error_prefix="请求失败: ",
)
def nettool_head(url: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
    """发送 HTTP HEAD 请求并打印响应头。"""
    for key, value in http_head(url, timeout).items():
        print(f"{key}: {value}")


# ============================================================================
# iptool 公共逻辑
# ============================================================================


def parse_ip(ip: str) -> dict[str, str]:
    """解析 IP 地址，返回版本/私有/回环/多播/链路本地/压缩/展开表示。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"无效的 IP 地址: {ip!r}（{exc}）") from exc
    return {
        "version": str(addr.version),
        "is_private": str(addr.is_private),
        "is_loopback": str(addr.is_loopback),
        "is_multicast": str(addr.is_multicast),
        "is_link_local": str(addr.is_link_local),
        "compressed": addr.compressed,
        "exploded": addr.exploded,
    }


def subnet_info(ip: str, prefix: int) -> dict[str, str]:
    """计算 IPv4/IPv6 子网信息。"""
    try:
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError as exc:
        raise ValueError(f"无效的子网: {ip}/{prefix}（{exc}）") from exc

    if net.num_addresses >= 3:
        first = net.network_address + 1
        last = net.broadcast_address - 1
        host_range = str(first) if first == last else f"{first} - {last}"
    elif net.num_addresses == 2:
        host_range = f"{net.network_address} - {net.broadcast_address}"
    else:
        host_range = str(net.network_address)

    return {
        "network": str(net.network_address),
        "netmask": str(net.netmask),
        "broadcast": str(net.broadcast_address) if net.broadcast_address else "None",
        "prefixlen": str(net.prefixlen),
        "num_addresses": str(net.num_addresses),
        "host_range": host_range,
    }


def validate_ip(ip: str) -> bool:
    """校验 IP 地址格式是否有效。"""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


# ============================================================================
# iptool DSL 动作
# ============================================================================


@action("iptool_parse", param_help={"ip": "IP 地址字符串（IPv4 或 IPv6）"}, errors=(ValueError,))
def iptool_parse(ip: str) -> None:
    """解析 IP 地址并逐行打印属性。"""
    for key, value in parse_ip(ip).items():
        print(f"{key}: {value}")


@action(
    "iptool_subnet",
    param_help={
        "ip": "网络地址字符串",
        "prefix": "前缀长度",
    },
    errors=(ValueError,),
)
def iptool_subnet(ip: str, prefix: int) -> None:
    """计算子网信息并逐行打印。"""
    for key, value in subnet_info(ip, prefix).items():
        print(f"{key}: {value}")


@action("iptool_validate", param_help={"ip": "待校验的 IP 地址字符串"})
def iptool_validate(ip: str) -> None:
    """校验 IP 地址格式是否有效。"""
    if validate_ip(ip):
        print(f"有效: {ip}")
    else:
        print(f"无效: {ip}")


# ============================================================================
# websave 公共逻辑
# ============================================================================

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


def save_website(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> None:
    """保存主页及其同源相关子页面，并下载图片/CSS/JS 供离线浏览。"""
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
            print(f"错误: [websave] 警告: 无法获取 {current_url}: {exc}")
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
    print(f"[websave] 成功: 网站内容已保存到 {output_path.resolve()}")


# ============================================================================
# websave DSL 动作
# ============================================================================


@action(
    "websave",
    param_help={
        "url": "主页 URL",
        "output_dir": "输出目录（默认 saved_website）",
        "depth": "爬取深度（默认 2）",
        "timeout": "请求超时秒数（默认 30）",
    },
    errors=(HTTPError, URLError),
)
def websave(
    url: str,
    output_dir: str = "saved_website",
    depth: int = 2,
    timeout: int = 30,
) -> None:
    """保存网页内容（主页及相关子页面，含图片/CSS/JS）。"""
    save_website(url, output_dir, depth, timeout)


# ============================================================================
# urlcheck 公共逻辑
# ============================================================================

_DEFAULT_CHECK_TIMEOUT = 5.0
_DEFAULT_WORKERS = 8
_PROBE_UA: str = "Mozilla/5.0 (compatible; fcmd-urlcheck)"


def unreachable_latency() -> float:
    """返回不可达时使用的延迟占位值（正无穷）。"""
    return float("inf")


def _probe_once(url: str, method: str, timeout: float) -> tuple[bool, float] | None:
    """执行单次请求并测量延迟。"""
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链，见 http_get

    start = time.perf_counter()
    try:
        req = Request(url, method=method, headers={"User-Agent": _PROBE_UA})
        with urlopen(req, timeout=timeout):
            pass
    except HTTPError:
        if method == "HEAD":
            return None
        return (False, unreachable_latency())
    except (URLError, TimeoutError, OSError):
        return (False, unreachable_latency())
    return (True, (time.perf_counter() - start) * 1000)


def check_url(url: str, timeout: float = _DEFAULT_CHECK_TIMEOUT) -> tuple[bool, float]:
    """检测单个 URL 的可访问性与响应延迟。"""
    head = _probe_once(url, "HEAD", timeout)
    if head is not None:
        return head
    get = _probe_once(url, "GET", timeout)
    assert get is not None
    return get


def check_urls(
    urls: Sequence[str],
    timeout: float = _DEFAULT_CHECK_TIMEOUT,
    workers: int = _DEFAULT_WORKERS,
) -> list[tuple[str, bool, float]]:
    """并发探测多个 URL 并按访问速度排序。"""
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        results = list(pool.map(lambda u: (u, *check_url(u, timeout)), urls))
    return sorted(results, key=lambda r: (not r[1], r[2]))


# ============================================================================
# 镜像测速（可达性 + 实测下载吞吐）
# ============================================================================

_PROBE_SAMPLE_BYTES = 131072
_PROBE_QUICK_BYTES = 2048
_PROBE_CHUNK_BYTES = 65536
_PROBE_BENCHMARK_LIMIT = 6
_PROBE_SCORE_MIN_BYTES = 4096
_PROBE_MIRROR_UA = "fcmd-mirror-probe/0.3.10"
# 镜像探测并发数低于 urlcheck：部分镜像站 WAF 会对同源突发请求返回 403。
_PROBE_WORKERS = 4


@dataclass(frozen=True, slots=True)
class UrlProbe:
    """单个镜像地址的测速结果。

    Attributes
    ----------
    url:
        探测地址。
    ok:
        是否可用（2xx 且未命中限速/封禁）。
    latency_ms:
        首字节延迟（毫秒）。
    throughput_kbps:
        实测下载吞吐（KB/s），按截断采样的字节数/耗时计算。
    bytes_read:
        采样读取的字节数。
    reason:
        不可用原因（如 ``限速(429)`` / ``受限(403)`` / ``超时``）。
    """

    url: str
    ok: bool
    latency_ms: float
    throughput_kbps: float
    bytes_read: int
    reason: str = ""


def _describe_http_error(exc: HTTPError) -> str:
    """将 HTTP 错误码转换为可读原因（区分限速与封禁）。"""
    code = exc.code
    if code == 429:
        return "限速(429)"
    if code in (401, 403):
        return f"受限({code})"
    return f"HTTP {code}"


def _probe_download(
    url: str,
    timeout: float,
    user_agent: str,
    sample_bytes: int,
    allow_html: bool = False,
) -> UrlProbe:
    """GET 请求并读取至多 ``sample_bytes`` 字节，测量延迟与吞吐。

    ``allow_html=False`` 时若响应为 ``text/html`` 则判为不可用：镜像站的
    未知路径常被前端路由兜底为门户 HTML 页并返回 200，若不识别会把
    已失效的镜像路径误判为可用（如华为云已下线的 crates.io 索引）。
    pip 的 ``simple`` 索引本身就是 HTML，调用方需显式 ``allow_html=True``。
    """
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链，见 http_get

    start = time.perf_counter()
    try:
        req = Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
        with urlopen(req, timeout=timeout) as resp:
            latency_ms = (time.perf_counter() - start) * 1000
            if not allow_html and resp.headers.get_content_type() == "text/html":
                return UrlProbe(url, False, latency_ms, 0.0, 0, "返回网页而非构件")
            read = 0
            while read < sample_bytes:
                chunk = resp.read(min(_PROBE_CHUNK_BYTES, sample_bytes - read))
                if not chunk:
                    break
                read += len(chunk)
            elapsed = time.perf_counter() - start
    except HTTPError as exc:
        return UrlProbe(url, False, 0.0, 0.0, 0, _describe_http_error(exc))
    except (URLError, TimeoutError, OSError):
        return UrlProbe(url, False, 0.0, 0.0, 0, "超时/网络错误")
    throughput = read / elapsed / 1024 if elapsed > 0 else 0.0
    return UrlProbe(url, True, latency_ms, throughput, read)


def probe_score(probe: UrlProbe) -> float:
    """计算镜像综合评分：优先实测吞吐，样本过小时退化为延迟评分。

    纯延迟排序会把「响应快但实际下载被限速」的镜像（如部分商业 CDN）
    误判为最优；改用真实下载吞吐可反映带宽与限速情况。
    """
    if not probe.ok:
        return -1.0
    if probe.bytes_read >= _PROBE_SCORE_MIN_BYTES and probe.throughput_kbps > 0:
        return probe.throughput_kbps
    return 1000.0 / max(probe.latency_ms, 1.0)


def probe_mirrors(  # noqa: PLR0913, PLR0917
    urls: Sequence[str],
    timeout: float = _DEFAULT_CHECK_TIMEOUT,
    workers: int = _PROBE_WORKERS,
    user_agent: str = _PROBE_MIRROR_UA,
    sample_bytes: int = _PROBE_SAMPLE_BYTES,
    allow_html: bool = False,
) -> list[UrlProbe]:
    """两阶段镜像测速：并发筛选可达性，再顺序实测下载吞吐。

    阶段一并发读取 ``_PROBE_QUICK_BYTES`` 字节，快速筛掉不可达 / 被限速的
    地址并获取首字节延迟；失败项会并发重试一次，以排除偶发超时与 WAF 对
    突发请求的误伤；阶段二对延迟最低的若干候选顺序读取更大样本，避免并发
    抢带宽导致吞吐失真，最终按 :func:`probe_score` 降序返回。
    """
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        quick = list(pool.map(lambda u: _probe_download(u, timeout, user_agent, _PROBE_QUICK_BYTES, allow_html), urls))

    failed_urls = [p.url for p in quick if not p.ok]
    if failed_urls:
        with ThreadPoolExecutor(max_workers=min(workers, len(failed_urls))) as pool:
            retried = list(
                pool.map(lambda u: _probe_download(u, timeout, user_agent, _PROBE_QUICK_BYTES, allow_html), failed_urls)
            )
        quick = [next((r for r in retried if r.url == p.url and r.ok), p) for p in quick]

    reachable = sorted((p for p in quick if p.ok), key=lambda p: p.latency_ms)
    benchmarked: dict[str, UrlProbe] = {}
    for probe in reachable[:_PROBE_BENCHMARK_LIMIT]:
        measured = _probe_download(probe.url, timeout, user_agent, sample_bytes, allow_html)
        benchmarked[probe.url] = measured if measured.ok else probe

    results = [benchmarked.get(p.url, p) for p in quick]
    return sorted(results, key=lambda p: (not p.ok, -probe_score(p), p.latency_ms))


# ============================================================================
# urlcheck DSL 动作
# ============================================================================


@action(
    "urlcheck_u",
    param_help={
        "url": "目标 URL",
        "timeout": "超时秒数（默认 5）",
    },
)
def urlcheck_u(url: str, timeout: float = _DEFAULT_CHECK_TIMEOUT) -> None:
    """检测单个 URL 的可访问性并打印延迟。"""
    ok, latency = check_url(url, timeout)
    if ok:
        print(f"{url} -> 可访问 (延迟 {latency:.0f} ms)")
    else:
        print(f"{url} -> 不可访问")


@action(
    "urlcheck_r",
    param_help={
        "urls": "目标 URL 列表",
        "timeout": "单个 URL 的超时秒数（默认 5）",
    },
)
def urlcheck_r(urls: list[str], timeout: float = _DEFAULT_CHECK_TIMEOUT) -> None:
    """并发探测多个 URL，按访问速度排序打印。"""
    if not urls:
        print("请提供至少一个 URL")
        return
    results = check_urls(urls, timeout)
    print(f"URL 探测结果（共 {len(results)} 个，按访问速度排序）:")
    for index, (url, ok, latency) in enumerate(results, start=1):
        speed = f"{latency:.0f} ms" if ok else "-"
        status = "可访问" if ok else "不可访问"
        print(f"  {index}. {url}  {status}  {speed}")
