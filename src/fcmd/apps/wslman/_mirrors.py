"""国内镜像源与 GitHub 加速：只收录实测与官方字节一致（或能用官方 sha256 校验）的源。

通用镜像类型 / URL 改写 / 候选排序在 :mod:`fcmd.infras.net.mirrors`；本模块只保留
WSL 发行版下载的镜像清单与 GitHub 加速前缀数据。

盘点结论（2026-11 实测，HEAD 比对 ``Content-Length`` / ``Last-Modified``）：

- ``releases.ubuntu.com`` 上的 WSL 镜像在中科大 / 阿里云 / 华为云 / 南京大学都有同路径副本，
  大小与官方完全一致（379,031,026 字节等）；
- ``download.fedoraproject.org/pub/fedora`` 在中科大镜像为 ``/fedora``，大小一致；
- ``fastly.mirror.pkgbuild.com/wsl`` （Arch Linux）在中科大 / 南京大学镜像为 ``/archlinux/wsl``，
  ``Content-Length`` / ``Last-Modified`` 全部一致（118,398,752 字节），且南京大学实测
  431 KiB/s，比官方 Fastly 的 170 KiB/s 快；
- GitHub Releases 上的发行版镜像（openSUSE / SUSE / AlmaLinux / RLC+）直连在本机基本不可用
  （拉 256 KiB 直接超时），但 ``ghfast.top`` / ``gh-proxy.com`` / ``ghproxy.net`` 能取到同一份
  字节（实测 57 / 24～46 / 8～18 KiB/s）。这类源统一用官方 sha256 校验，校验不过就当失败；
- ``gitlab.com``（eLxr）、``blob.core.windows.net``（Oracle Linux）、
  ``osrelease.download.prss.microsoft.com``（Azure Linux）、``salsa.debian.org``（Debian）、
  ``kali.download`` 未找到可验证的国内镜像，只走官方源；
- 清华 TUNA 对本机返回 403（按来源 IP 限制），无法验证，故不收录。

镜像只做「官方 URL 的等价改写 / 加加速前缀」，是否可信由调用方在下载完成后用官方 sha256
判定：没有 sha256 的条目一律只走官方源，绝不把镜像标成已验证。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from fcmd.infras.net.mirrors import Candidate, Mirror, Proxy, build_candidates
from fcmd.infras.net.mirrors import rank_candidates as _rank_candidates

__all__ = [
    "MIRRORS",
    "PROXIES",
    "Candidate",
    "Mirror",
    "Proxy",
    "candidates",
    "identifiers",
    "label",
    "rank_candidates",
    "trusted_ids",
]


MIRRORS: tuple[Mirror, ...] = (
    Mirror(
        "ustc",
        "中科大 USTC",
        "https://mirrors.ustc.edu.cn",
        (
            ("https://releases.ubuntu.com/", "ubuntu-releases/"),
            ("https://download.fedoraproject.org/pub/fedora/", "fedora/"),
            ("https://fastly.mirror.pkgbuild.com/", "archlinux/"),
        ),
    ),
    Mirror(
        "aliyun",
        "阿里云",
        "https://mirrors.aliyun.com",
        (("https://releases.ubuntu.com/", "ubuntu-releases/"),),
    ),
    Mirror(
        "huawei",
        "华为云",
        "https://mirrors.huaweicloud.com",
        (("https://releases.ubuntu.com/", "ubuntu-releases/"),),
    ),
    Mirror(
        "nju",
        "南京大学",
        "https://mirrors.nju.edu.cn",
        (
            ("https://releases.ubuntu.com/", "ubuntu-releases/"),
            ("https://fastly.mirror.pkgbuild.com/", "archlinux/"),
        ),
    ),
)
"""按优先级排列：同一条目命中多个镜像时，越靠前越先尝试。"""


GITHUB_HOSTS = frozenset({"github.com", "objects.githubusercontent.com"})

PROXIES: tuple[Proxy, ...] = (
    Proxy("ghfast", "ghfast.top 加速", "https://ghfast.top/", GITHUB_HOSTS),
    Proxy("gh-proxy", "gh-proxy 加速", "https://gh-proxy.com/", GITHUB_HOSTS),
    Proxy("ghproxy-net", "ghproxy.net 加速", "https://ghproxy.net/", GITHUB_HOSTS),
)
"""按实测速度从快到慢：GitHub Releases 直连在部分网络不可用，逐个尝试即可。"""

AUTO = "auto"
OFFICIAL = "official"


def identifiers() -> tuple[str, ...]:
    """可选策略：``auto`` / ``official`` / 各镜像 id。"""
    return (AUTO, OFFICIAL, *(mirror.ident for mirror in MIRRORS))


def label(ident: str) -> str:
    """把来源 id 转成展示文案；未知 id 原样返回。"""
    if ident == OFFICIAL:
        return "官方源"
    if ident == AUTO:
        return "自动（镜像源测速择优）"
    for mirror in MIRRORS:
        if mirror.ident == ident:
            return mirror.label
    for proxy in PROXIES:
        if proxy.ident == ident:
            return proxy.label
    return ident


def trusted_ids() -> tuple[str, ...]:
    return (OFFICIAL, *(mirror.ident for mirror in MIRRORS))


def rank_candidates(
    candidates: Sequence[Candidate],
    *,
    probe: Callable[[str], float] | None = None,
    latency: Callable[[str], float] | None = None,
) -> tuple[Candidate, ...]:
    """并发测速后按从快到慢排序；探测实现与 WSLUI 源共用一套。"""
    if len(candidates) <= 1:
        return tuple(candidates)
    from fcmd.apps.wslman._wslui import probe_tcp_latency, probe_throughput

    return _rank_candidates(
        candidates,
        probe=probe or probe_throughput,
        latency=latency or probe_tcp_latency,
    )  # type: ignore[return-value]


def candidates(url: str, *, policy: str = AUTO, verifiable: bool = True) -> tuple[Candidate, ...]:
    """按策略给出下载候选：镜像 / 加速前缀在前，官方源永远最后兜底。"""
    return build_candidates(
        url,
        mirrors=MIRRORS,
        proxies=PROXIES,
        official_label="官方源",
        policy=policy,
        verifiable=verifiable,
    )
