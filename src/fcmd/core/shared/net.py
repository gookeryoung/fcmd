"""网络探测与镜像测速的共享实现。

被 DSL 网络动作（``urlcheck``）与 Python 工具（``envdev`` / ``llmdown``）
共用，故下沉到中立共享层，避免 tools → dsl 的反向依赖。

``urllib.request`` 在函数内惰性导入（连带 ssl 链约 37ms），避免工具发现期
为每个 CLI 命令付出启动成本。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.error import HTTPError, URLError

__all__ = [
    "UrlProbe",
    "check_url",
    "check_urls",
    "probe_mirrors",
    "probe_score",
    "unreachable_latency",
]

# ============================================================================
# urlcheck：可达性 + 延迟
# ============================================================================

_DEFAULT_CHECK_TIMEOUT = 5.0
_DEFAULT_WORKERS = 8
_PROBE_UA: str = "Mozilla/5.0 (compatible; fcmd-urlcheck)"


def unreachable_latency() -> float:
    """返回不可达时使用的延迟占位值（正无穷）。"""
    return float("inf")


def _probe_once(url: str, method: str, timeout: float) -> tuple[bool, float] | None:
    """执行单次请求并测量延迟。"""
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链

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
# 镜像测速：可达性 + 实测下载吞吐
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
    from urllib.request import Request, urlopen  # 惰性导入 ssl 链

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
