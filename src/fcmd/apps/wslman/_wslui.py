"""WSLUI / WSL Dashboard 镜像源：拉取、缓存、匹配与测速择优。

数据来自 WSL Dashboard（owu/wsl-dashboard）使用的公开接口：

- ``https://api1.wslui.com/desktop/v1/helper/install`` 给出镜像清单地址；
- ``https://api2.wslui.com/co-creation/api/online-distros`` 返回每个发行版的多个
  下载源（url / mirror / format），WSL Dashboard 就是按这些源做并发测速后选最快。

这里只做纯数据与网络探测，不依赖 Qt；网络失败时回退缓存 / 空列表，不影响官方镜像
清单的展示。下载仍走 :mod:`fcmd.apps.wslman._source` 的通用下载器。
"""

from __future__ import annotations

import contextlib
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.wslman._mirrors import Candidate
from fcmd.infras.jsonio import read_json_file, write_json_atomic
from fcmd.infras.system import fcmd_home

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fcmd.apps.wslman._images import ImageEntry

__all__ = [
    "CACHE_TTL_SECONDS",
    "WsluiCatalog",
    "WsluiDistro",
    "WsluiSource",
    "cache_path",
    "download_filename",
    "load_catalog",
    "match_sources",
    "parse_catalog",
    "probe_tcp_latency",
    "probe_throughput",
    "rank_items",
    "rank_sources",
    "wslui_candidates",
]

INSTALL_INFO_URL = "https://api1.wslui.com/desktop/v1/helper/install"
MIRROR_FALLBACK_URL = "https://api2.wslui.com/co-creation/api/online-distros"
CACHE_TTL_SECONDS = 6 * 60 * 60
"""镜像源清单有效期：6 小时；失败时优先复用过期缓存并标注。"""

_CACHE_SCHEMA = "wslman/wslui-mirrors/1"
_PROBE_BYTES = 3 * 1024 * 1024
_PROBE_TIMEOUT_S = 6.0
_USER_AGENT = "fcmd-wslman/0.1"

_HOST_LABELS: dict[str, str] = {
    "mirrors.tuna.tsinghua.edu.cn": "清华 TUNA",
    "mirrors.ustc.edu.cn": "中科大 USTC",
    "mirrors.aliyun.com": "阿里云",
    "mirrors.cloud.tencent.com": "腾讯云",
    "mirrors.huaweicloud.com": "华为云",
    "mirrors.volces.com": "火山引擎",
    "mirrors.sohu.com": "搜狐",
    "mirrors.nju.edu.cn": "南京大学",
    "mirrors.bfsu.edu.cn": "北外",
    "mirrors.sjtug.sjtu.edu.cn": "上海交大",
    "mirrors.163.com": "网易",
    "mirrors.zju.edu.cn": "浙江大学",
    "mirrors.sustech.edu.cn": "南方科技大学",
}


@dataclass(frozen=True, slots=True)
class WsluiSource:
    """一个可下载源；``mirror`` 是 WSLUI 的内部标识，``format`` 形如 ``tar.xz``。"""

    url: str
    mirror: str
    format: str
    last_modified: str = ""

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    @property
    def label(self) -> str:
        return _HOST_LABELS.get(self.host, self.mirror or self.host or "镜像")


@dataclass(frozen=True, slots=True)
class WsluiDistro:
    """WSLUI 清单里的一个发行版条目。"""

    name: str
    version: str
    sources: tuple[WsluiSource, ...]

    @property
    def display_name(self) -> str:
        return f"{self.name} {self.version}".strip()


@dataclass(frozen=True, slots=True)
class WsluiCatalog:
    """一次镜像源清单结果；失败时 ``distros`` 为空并给出 warning。"""

    distros: tuple[WsluiDistro, ...]
    fetched_at: float = 0.0
    from_cache: bool = False
    stale: bool = False
    warnings: tuple[str, ...] = ()


def cache_path() -> Path:
    """``${FCMD_HOME:-~/.fcmd}/cache/wslman-wslui-mirrors.json``。"""
    return fcmd_home() / "cache" / "wslman-wslui-mirrors.json"


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").casefold())


def _parse_sources(raw: Any) -> tuple[WsluiSource, ...]:
    if not isinstance(raw, list):
        return ()
    sources: list[WsluiSource] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            continue
        key = url.strip()
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            WsluiSource(
                url=key,
                mirror=str(item.get("mirror") or ""),
                format=str(item.get("format") or ""),
                last_modified=str(item.get("last_modified") or ""),
            )
        )
    return tuple(sources)


def parse_catalog(raw: Any) -> tuple[WsluiDistro, ...]:
    """解析 API / 缓存 JSON；结构不对时返回空列表，由调用方决定是否降级。"""
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        return ()
    items = data.get("distros")
    if not isinstance(items, list):
        items = data.get("mirrors") if isinstance(data.get("mirrors"), list) else []
    distros: list[WsluiDistro] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        sources = _parse_sources(item.get("sources"))
        if not sources:
            continue
        distros.append(WsluiDistro(name.strip(), str(item.get("version") or "").strip(), sources))
    return tuple(distros)


def _http_json(url: str, *, timeout: float = 12.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def _mirror_list_url() -> str:
    try:
        payload = _http_json(INSTALL_INFO_URL, timeout=6.0)
        data = payload.get("data") if isinstance(payload, dict) else None
        online = data.get("online_distros") if isinstance(data, dict) else None
        url = online.get("url") if isinstance(online, dict) else None
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return url
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return MIRROR_FALLBACK_URL


def _read_cache() -> tuple[tuple[WsluiDistro, ...], float]:
    try:
        raw = read_json_file(cache_path())
    except (OSError, ValueError):
        return (), 0.0
    if not isinstance(raw, dict) or raw.get("schema") != _CACHE_SCHEMA:
        return (), 0.0
    distros = tuple(
        WsluiDistro(
            name=str(item.get("name") or ""),
            version=str(item.get("version") or ""),
            sources=tuple(
                WsluiSource(
                    url=str(src.get("url") or ""),
                    mirror=str(src.get("mirror") or ""),
                    format=str(src.get("format") or ""),
                    last_modified=str(src.get("last_modified") or ""),
                )
                for src in item.get("sources", [])
                if isinstance(src, dict) and str(src.get("url") or "").startswith(("https://", "http://"))
            ),
        )
        for item in raw.get("distros", [])
        if isinstance(item, dict)
    )
    fetched_at = raw.get("fetched_at")
    return distros, float(fetched_at) if isinstance(fetched_at, (int, float)) else 0.0


def _save_cache(distros: tuple[WsluiDistro, ...], fetched_at: float) -> None:
    payload = {
        "schema": _CACHE_SCHEMA,
        "fetched_at": fetched_at,
        "distros": [
            {
                "name": item.name,
                "version": item.version,
                "sources": [
                    {
                        "url": source.url,
                        "mirror": source.mirror,
                        "format": source.format,
                        "last_modified": source.last_modified,
                    }
                    for source in item.sources
                ],
            }
            for item in distros
        ],
    }
    with contextlib.suppress(OSError):
        write_json_atomic(cache_path(), payload)


def _fetch_catalog() -> tuple[WsluiDistro, ...]:
    """拉镜像源清单：先直取 WSL Dashboard 用的 ``online-distros``（一次往返），
    拿不到再走 ``install helper`` 指出的地址（多一次往返，仅当第一条路径失败）。

    两条路径返回的是同一份数据（WSL Dashboard 自身就从 ``online-distros`` 拿）；
    把常用的那条放前面，刷新清单就从 2 个串行请求降到 1 个。
    """
    try:
        distros = parse_catalog(_http_json(MIRROR_FALLBACK_URL, timeout=8.0))
        if distros:
            return distros
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return parse_catalog(_http_json(_mirror_list_url(), timeout=15.0))


def load_catalog(*, force: bool = False) -> WsluiCatalog:
    """读取镜像源清单：新鲜缓存直接返回，否则网络刷新；失败时复用过期缓存。"""
    cached, fetched_at = _read_cache()
    fresh = cached and (time.time() - fetched_at) <= CACHE_TTL_SECONDS
    if cached and fresh and not force:
        return WsluiCatalog(cached, fetched_at, from_cache=True)
    warnings: list[str] = []
    try:
        distros = _fetch_catalog()
        if not distros:
            raise ValueError("镜像源清单为空")
        now = time.time()
        _save_cache(distros, now)
        return WsluiCatalog(distros, now)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        warnings.append(f"WSLUI 镜像源清单获取失败：{exc}")
    if cached:
        return WsluiCatalog(cached, fetched_at, from_cache=True, stale=True, warnings=tuple(warnings))
    return WsluiCatalog((), warnings=tuple(warnings))


def match_sources(catalog: WsluiCatalog | None, entry: ImageEntry) -> tuple[WsluiSource, ...]:
    """给官方镜像条目匹配 WSLUI 源：优先同名 + 版本，其次下载文件名一致。"""
    if catalog is None or not catalog.distros:
        return ()
    official_names = {Path(urllib.parse.urlsplit(download.url).path).name.casefold() for download in entry.downloads}
    entry_keys = {_norm(entry.name), _norm(entry.friendly_name)} - {""}
    best: tuple[int, tuple[WsluiSource, ...]] = (0, ())
    for distro in catalog.distros:
        for source in distro.sources:
            if Path(urllib.parse.urlsplit(source.url).path).name.casefold() in official_names:
                return distro.sources
        dkey = _norm(distro.display_name)
        name_key = _norm(distro.name)
        version_key = _norm(distro.version)
        score = 0
        if dkey and dkey in entry_keys:
            score = 3
        elif name_key and version_key and any(key.startswith(name_key) and version_key in key for key in entry_keys):
            score = 2
        elif name_key and version_key and any(key == name_key for key in entry_keys):
            # 条目本身不带版本（如 ``Debian`` / ``archlinux``）时按发行版名匹配；
            # 带版本号的条目（如 ``AlmaLinux-8``）不会走到这里，避免拿错版本。
            score = 2
        elif (
            name_key
            and any(key.startswith(name_key) for key in entry_keys)
            and not any(any(ch.isdigit() for ch in key) for key in entry_keys)
        ):
            # 名字前缀也认（``kali-linux`` 对 ``Kali`` / ``Ubuntu`` 对 ``Ubuntu``），
            # 但官方条目带数字版本时不降级匹配，避免把 ``AlmaLinux-8`` 指向别的版本。
            score = 2
        elif dkey and any(key.startswith(dkey) for key in entry_keys):
            score = 1
        if score > best[0]:
            best = (score, distro.sources)
    return best[1]


def wslui_candidates(entry: ImageEntry, catalog: WsluiCatalog | None, *, arch: str = "x86_64") -> tuple[Candidate, ...]:
    """把匹配到的 WSLUI 源转成下载候选：每个源带自己的落盘名，且不能拿官方 sha256 校验。

    WSLUI 常提供与官方不同格式的制品（``lxc`` 的 ``tar.xz``），所以 ``verify=False``；
    下载器只在该候选上跳过官方摘要，不会把未校验的字节标成已验证。
    """
    sources = match_sources(catalog, entry)
    return tuple(
        Candidate(
            source.mirror or source.host or "wslui",
            source.label,
            source.url,
            False,
            filename=download_filename(entry, source, arch),
            verify=False,
        )
        for source in sources
    )


def download_filename(entry: ImageEntry, source: WsluiSource, arch: str = "x86_64") -> str:
    """下载落盘名：用实例名 + 架构 + 源格式，便于 ``mark_downloaded`` 重新识别。"""
    fmt = source.format.strip().lstrip(".").casefold()
    if not fmt:
        suffix = Path(urllib.parse.urlsplit(source.url).path).suffix or ".tar"
    elif fmt in {"wsl", "tar", "tar.gz", "tar.xz", "tar.bz2", "tgz", "txz"}:
        suffix = f".{fmt}"
    else:
        suffix = Path(urllib.parse.urlsplit(source.url).path).suffix or ".tar"
    return f"{entry.name}-{arch}{suffix}"


def _tcp_latency(url: str, *, timeout: float = 3.0) -> float:
    host = urllib.parse.urlsplit(url).hostname or ""
    if not host:
        return float("inf")
    start = time.monotonic()
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            return (time.monotonic() - start) * 1000.0
    except OSError:
        return float("inf")


def probe_tcp_latency(url: str, *, timeout: float = 3.0) -> float:
    """TCP 连接延迟（毫秒）；不可达返回 ``inf``（测试可注入替身）。"""
    return _tcp_latency(url, timeout=timeout)


def _probe_throughput(url: str, *, timeout: float = _PROBE_TIMEOUT_S) -> float:
    request = urllib.request.Request(
        url,
        headers={"Range": f"bytes=0-{_PROBE_BYTES - 1}", "User-Agent": _USER_AGENT},
    )
    start = time.monotonic()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            read = getattr(response, "read1", response.read)
            while total < _PROBE_BYTES:
                data = read(65536)
                if not data:
                    break
                total += len(data)
    except (OSError, urllib.error.URLError):
        return 0.0
    elapsed = time.monotonic() - start
    return total / elapsed if total and elapsed > 0 else 0.0


def probe_throughput(url: str, *, timeout: float = _PROBE_TIMEOUT_S) -> float:
    """Range 拉前 3 MiB 测吞吐（字节/秒）；失败返回 0（测试可注入替身）。"""
    return _probe_throughput(url, timeout=timeout)


def rank_items(
    items: Sequence[Any],
    *,
    probe: Callable[[str], float] | None = None,
    latency: Callable[[str], float] | None = None,
) -> tuple[Any, ...]:
    """并发测速后按最快到最慢排序；全部失败时回退 TCP 延迟。

    ``items`` 只需带 ``url`` 属性，因此 WSLUI 源与国内镜像候选可以共用同一套
    探测逻辑（:func:`fcmd.apps.wslman._mirrors.rank_candidates`）。
    """
    if len(items) <= 1:
        return tuple(items)
    probe = probe or probe_throughput
    latency = latency or probe_tcp_latency
    with ThreadPoolExecutor(max_workers=min(6, len(items)), thread_name_prefix="wslui-probe") as pool:
        measured = list(pool.map(lambda item: (item, probe(item.url)), items))
    if any(speed > 0 for _item, speed in measured):
        measured.sort(key=lambda pair: pair[1], reverse=True)
        return tuple(item for item, _speed in measured)
    with ThreadPoolExecutor(max_workers=min(6, len(items)), thread_name_prefix="wslui-latency") as pool:
        timed = list(pool.map(lambda item: (item, latency(item.url)), items))
    timed.sort(key=lambda pair: pair[1])
    return tuple(item for item, _latency in timed)


def rank_sources(
    sources: Sequence[WsluiSource],
    *,
    probe: Callable[[str], float] | None = None,
    latency: Callable[[str], float] | None = None,
) -> tuple[WsluiSource, ...]:
    """并发测速后按最快到最慢排序；全部失败时回退 TCP 延迟。"""
    return rank_items(sources, probe=probe, latency=latency)  # type: ignore[return-value]
