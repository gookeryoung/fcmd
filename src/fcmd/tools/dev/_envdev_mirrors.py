"""envdev 的镜像站点探测（MirrorZ 动态列表 / 分服务测速与自动应用）。

从 :mod:`fcmd.tools.dev.envdev` 拆出；``envdev`` 仍 re-export 全部函数与常量，
既有导入路径与 CLI 注册不变。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

import fcmd
from fcmd.core.shared.net import check_urls, probe_mirrors
from fcmd.tools.dev._envdev_langs import (
    _CARGO_PROBE_URLS,
    _CONDA_PROBE_URLS,
    _PIP_PROBE_URLS,
    _RUSTUP_PROBE_URLS,
    _setup_cargo_mirror,
    _setup_rustup_mirror,
    setup_conda_mirror,
    setup_python_mirror,
)
from fcmd.tools.dev.envdev_core import _MIRROR_DEFAULT_UA, _MIRROR_USER_AGENTS
from fcmd.tools.dev.envdev_go import _GO_PROXY_PROBE_URLS, _setup_go_mirror
from fcmd.tools.dev.envdev_java import _MAVEN_PROBE_URLS, _setup_maven_mirror
from fcmd.tools.dev.envdev_node import _NPM_PROBE_URLS, _setup_npm_mirror

logger = logging.getLogger(__name__)


_CERNET_MIRROR_SITES: dict[str, str] = {
    "tsinghua": "https://mirrors.tuna.tsinghua.edu.cn",
    "ustc": "https://mirrors.ustc.edu.cn",
    "zju": "https://mirrors.zju.edu.cn",
    "bfsu": "https://mirrors.bfsu.edu.cn",
    "sjtu": "https://mirror.sjtu.edu.cn",
    "nju": "https://mirror.nju.edu.cn",
    "pku": "https://mirrors.pku.edu.cn",
    "hit": "https://mirrors.hit.edu.cn",
    "cqu": "https://mirrors.cqu.edu.cn",
    "lzu": "https://mirror.lzu.edu.cn",
}
# ``mirror`` 子命令按服务测速的候选表（服务名 -> {镜像名: 该服务的测速地址}）。
_MIRROR_SERVICE_CANDIDATES: dict[str, dict[str, str]] = {
    "pip": _PIP_PROBE_URLS,
    "conda": _CONDA_PROBE_URLS,
    "rustup": _RUSTUP_PROBE_URLS,
    "cargo": _CARGO_PROBE_URLS,
    "go": _GO_PROXY_PROBE_URLS,
    "maven": _MAVEN_PROBE_URLS,
    "npm": _NPM_PROBE_URLS,
}
# MirrorZ CERNET 门户入口与 JS 包内嵌站点元数据的提取模式。
# 门户前端将参与镜像站的元数据以 ``JSON.parse('{"url":...,"abbr":...}')``
# 形式内嵌于 JS 包，从中可动态提取完整站点列表。
_MIRRORZ_HOME: str = "https://mirrors.cernet.edu.cn/"
_MIRRORZ_JS_PATH_RE: str = r'src="?([^"\s>]+\.js)'
_MIRRORZ_SITE_BLOB_RE: str = r"JSON\.parse\('(\{\"url\":.*?\})'\)"
# 聚合门户自身也会作为站点条目出现，需剔除
_MIRRORZ_PORTAL_URL: str = "https://mirrors.cernet.edu.cn"


def fetch_mirrorz_sites(timeout: float = 5.0) -> dict[str, str] | None:
    """动态拉取 MirrorZ（CERNET 教育网联合镜像站门户）收录的镜像站点列表。

    流程：抓取门户入口页 -> 定位 JS 包 -> 提取内嵌站点元数据
    （``JSON.parse('{"url":...}')`` 片段，含站名缩写与首页地址）->
    按首页地址去重（同址多站如 TUNA.NANO/NEO 仅保留首个）。

    Parameters
    ----------
    timeout:
        页面与 JS 包的抓取超时秒数（默认 ``5``）

    Returns
    -------
    dict[str, str] | None
        ``{站名缩写(小写): 首页地址}``；任一环节失败（网络错误、页面或
        包结构变化、解析结果为空）返回 ``None``，调用方应回退内置列表。
    """
    # 惰性导入 ssl 链（urllib.request → http.client → ssl）：本模块被
    # 工具发现导入，顶层导入会让每个 CLI 命令启动都付 ~37ms
    from urllib.error import URLError
    from urllib.parse import urljoin
    from urllib.request import Request, urlopen

    try:
        req_headers = {"User-Agent": "Mozilla/5.0 (compatible; fcmd-envdev)"}
        html_resp = urlopen(Request(_MIRRORZ_HOME, headers=req_headers), timeout=timeout)
        with html_resp:
            html = html_resp.read().decode("utf-8")
        js_match = re.search(_MIRRORZ_JS_PATH_RE, html)
        if js_match is None:
            logger.warning("MirrorZ 门户页面未找到 JS 包引用")
            return None
        js_resp = urlopen(Request(urljoin(_MIRRORZ_HOME, js_match.group(1)), headers=req_headers), timeout=timeout)
        with js_resp:
            js = js_resp.read().decode("utf-8", "ignore")
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning("拉取 MirrorZ 站点列表失败: %s", exc)
        return None

    sites: dict[str, str] = {}
    seen_urls: set[str] = set()
    for blob in re.findall(_MIRRORZ_SITE_BLOB_RE, js):
        # JS 单引号字符串中的 \' 在 JSON 中非法，先还原为 '
        raw = blob.replace("\\'", "'")
        try:
            data = json.loads(raw)
            url, abbr = str(data["url"]), str(data["abbr"])
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.debug("跳过无法解析的 MirrorZ 站点片段: %s", exc)
            continue
        if url.rstrip("/") == _MIRRORZ_PORTAL_URL:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        sites[abbr.lower()] = url

    if not sites:
        logger.warning("MirrorZ JS 包中未解析出任何站点")
        return None
    return sites


_MIRROR_SERVICE_APPLY = {
    "pip": setup_python_mirror,
    "conda": setup_conda_mirror,
    "rustup": _setup_rustup_mirror,
    "cargo": _setup_cargo_mirror,
    "go": _setup_go_mirror,
    "maven": _setup_maven_mirror,
    "npm": _setup_npm_mirror,
}


def _probe_service_mirrors(service: str, timeout: float, apply: bool) -> int:
    """探测单个服务的全部候选镜像，按速度推荐最快项（可选应用配置）。

    Parameters
    ----------
    service:
        服务名（``pip`` / ``conda`` / ``rustup`` / ``cargo`` / ``go`` / ``maven`` / ``npm``）
    timeout:
        单个候选镜像的探测超时秒数
    apply:
        是否把推荐镜像立即写入对应服务配置

    Returns
    -------
    int
        存在可达镜像返回 ``0``，全部不可达返回 ``1``。
    """
    candidates = _MIRROR_SERVICE_CANDIDATES[service]
    name_by_url = {url: name for name, url in candidates.items()}
    results = probe_mirrors(
        list(candidates.values()),
        timeout=timeout,
        user_agent=_MIRROR_USER_AGENTS.get(service, _MIRROR_DEFAULT_UA),
        allow_html=service == "pip",
    )

    print(f"[{service} 镜像测速]（共 {len(results)} 个候选，按实测下载吞吐与延迟排序）")
    reachable = 0
    selected: str | None = None
    for index, probe in enumerate(results, start=1):
        name = name_by_url[probe.url]
        if probe.ok:
            reachable += 1
            print(
                f"  {index}. {name:<12} {probe.url}  可访问  "
                f"延迟 {probe.latency_ms:.0f} ms  吞吐 {probe.throughput_kbps:.0f} KB/s"
            )
            if selected is None:
                selected = name
        else:
            print(f"  {index}. {name:<12} {probe.url}  不可访问（{probe.reason}）")

    if selected is None:
        print(f"\n{service} 镜像全部不可达，请检查网络")
        return 1

    print(f"\n推荐 {service} 镜像: {selected}")
    if apply:
        _MIRROR_SERVICE_APPLY[service](selected)
        print(f"已应用 {service} 镜像: {selected}")
    return 0


@fcmd.tool("envdev", subcommand="mirror", help="镜像站点探测 / 指定服务镜像测速与自动选取")
def check_cernet_mirrors(
    timeout: float = 3.0,
    source: Literal["auto", "builtin"] = "auto",
    service: Literal["", "pip", "conda", "rustup", "cargo", "go", "maven", "npm"] = "",
    apply: bool = False,
) -> int:
    """并发探测教育网镜像站点，或对指定服务做镜像测速与自动选优。

    默认行为（``service`` 为空）探测教育网镜像站点列表（只读，不做任何
    配置变更）。站点列表默认（``auto``）从 MirrorZ（``mirrors.cernet.edu.cn``，
    CERNET 教育网联合镜像站门户）动态拉取；拉取失败或显式指定 ``builtin``
    时使用内置列表（取自 ``help.mirrors.cernet.edu.cn`` 收录站点）。输出按
    访问速度排序的探测结果，供选择镜像源参考。

    指定 ``service`` 时改为探测该服务自身的候选镜像地址（如 pip 的 pypi
    索引、conda 的 anaconda 频道、npm 的 ``/-/ping``），按速度输出排名并
    推荐最快可达镜像；``apply=True`` 时立即把推荐镜像写入该服务配置。

    Parameters
    ----------
    timeout:
        单个站点/镜像的探测超时秒数（默认 ``3``）
    source:
        站点列表来源：``auto`` 动态拉取（失败回退内置） / ``builtin`` 内置列表
    service:
        指定服务名做镜像测速：``pip`` / ``conda`` / ``rustup`` / ``cargo`` /
        ``go`` / ``maven`` / ``npm``；空串表示探测教育网镜像站点
    apply:
        是否把测速推荐的最快镜像立即应用到对应服务（默认 ``False`` 只读）

    Returns
    -------
    int
        存在可达站点/镜像返回 ``0``，全部不可达返回 ``1``。
    """
    if service:
        return _probe_service_mirrors(service, timeout, apply)

    dynamic = False
    if source == "auto":
        fetched = fetch_mirrorz_sites(timeout)
        if fetched is not None:
            dynamic = True
            sites = fetched
            print(f"[站点列表] 动态拉取 MirrorZ：{len(sites)} 个站点")
        else:
            print("[站点列表] 动态拉取失败，回退内置列表")
            sites = dict(_CERNET_MIRROR_SITES)
    else:
        sites = dict(_CERNET_MIRROR_SITES)

    name_by_url = {url: name for name, url in sites.items()}
    results = check_urls(list(sites.values()), timeout=timeout)

    origin = "mirrors.cernet.edu.cn 动态列表" if dynamic else "help.mirrors.cernet.edu.cn 内置列表"
    print(f"[教育网镜像站点探测]（来源: {origin}，按访问速度排序）")
    reachable = 0
    for index, (url, ok, latency) in enumerate(results, start=1):
        if ok:
            reachable += 1
        speed = f"{latency:.0f} ms" if ok else "-"
        print(f"  {index}. {name_by_url[url]:<10} {url}  {'可访问' if ok else '不可访问'}  {speed}")

    if reachable == 0:
        print("\n全部镜像站点不可达，请检查网络")
        return 1
    print(f"\n可达 {reachable}/{len(results)} 个站点")
    return 0
