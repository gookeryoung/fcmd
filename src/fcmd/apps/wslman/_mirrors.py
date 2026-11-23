"""国内镜像源与 GitHub 加速：只收录实测与官方字节一致（或能用官方 sha256 校验）的源。

盘点结论（2026-11 实测，HEAD 比对 ``Content-Length`` / ``Last-Modified``）：

- ``releases.ubuntu.com`` 上的 WSL 镜像在中科大 / 阿里云 / 华为云 / 南京大学都有同路径副本，
  大小与官方完全一致（379,031,026 字节等）；
- ``download.fedoraproject.org/pub/fedora`` 在中科大镜像为 ``/fedora``，大小一致；
- ``fastly.mirror.pkgbuild.com/wsl`` （Arch Linux）在中科大 / 南京大学镜像为 ``/archlinux/wsl``，
  ``Content-Length`` 与 ``Last-Modified`` 全部一致（118,398,752 字节），且南京大学实测
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

import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class Mirror:
    ident: str
    label: str
    base: str
    rules: tuple[tuple[str, str], ...]
    """``(官方 URL 前缀, 镜像站内相对前缀)``；命中即把官方路径原样拼到镜像站后面。"""


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


@dataclass(frozen=True, slots=True)
class Proxy:
    """加速前缀：把官方 URL 原样拼到第三方加速站后面（GitHub Releases 用）。

    这一类源不保证与官方同源，因此只在调用方拿得到官方 sha256（``verifiable``）时
    才参与候选；下载后的摘要校验就是它们唯一的信任依据。
    """

    ident: str
    label: str
    prefix: str
    hosts: frozenset[str]


GITHUB_HOSTS = frozenset({"github.com", "objects.githubusercontent.com"})

PROXIES: tuple[Proxy, ...] = (
    Proxy("ghfast", "ghfast.top 加速", "https://ghfast.top/", GITHUB_HOSTS),
    Proxy("gh-proxy", "gh-proxy 加速", "https://gh-proxy.com/", GITHUB_HOSTS),
    Proxy("ghproxy-net", "ghproxy.net 加速", "https://ghproxy.net/", GITHUB_HOSTS),
)
"""按实测速度从快到慢：GitHub Releases 直连在部分网络不可用，逐个尝试即可。"""

AUTO = "auto"
OFFICIAL = "official"


@dataclass(frozen=True, slots=True)
class Candidate:
    ident: str
    label: str
    url: str
    trusted: bool
    """是否可标为可信来源；镜像只有在有官方 sha256 可校验时才为 ``True``。"""

    filename: str = ""
    """该候选自己的落盘文件名；不同来源可能是不同制品（``.wsl`` / ``.tar.xz``），
    只有同字节的源才共用调用方给的目标文件名。"""

    sha256: str | None = None
    """该候选自己的摘要；为空时沿用调用方的 ``expected_sha256``。"""

    verify: bool = True
    """``False`` 表示候选是另一份制品（例如 WSLUI 的 lxc rootfs），不能拿官方
    sha256 校验，下载后按未校验处理。"""

    def to_data(self) -> dict[str, object]:
        return {"id": self.ident, "label": self.label, "url": self.url, "trusted": self.trusted}


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


def _rewrite(mirror: Mirror, url: str) -> str | None:
    for prefix, relative in mirror.rules:
        if url.startswith(prefix):
            return f"{mirror.base.rstrip('/')}/{relative}{url[len(prefix) :]}"
    return None


def _proxy_rewrite(proxy: Proxy, url: str) -> str | None:
    """命中加速站支持的主机时，把整条官方 URL 拼到加速前缀后。"""
    if urllib.parse.urlsplit(url).hostname in proxy.hosts:
        return f"{proxy.prefix}{url}"
    return None


def rank_candidates(
    candidates: Sequence[Candidate],
    *,
    probe: Callable[[str], float] | None = None,
    latency: Callable[[str], float] | None = None,
) -> tuple[Candidate, ...]:
    """并发测速后按从快到慢排序；探测实现与 WSLUI 源共用一套（见 ``_wslui``）。"""
    if len(candidates) <= 1:
        return tuple(candidates)
    from fcmd.apps.wslman._wslui import rank_items

    return rank_items(candidates, probe=probe, latency=latency)


def candidates(url: str, *, policy: str = AUTO, verifiable: bool = True) -> tuple[Candidate, ...]:
    """按策略给出下载候选，官方源永远排在最后兜底。

    ``verifiable`` 表示调用方拿得到官方 sha256；为 ``False`` 时不使用任何镜像，
    避免把无法校验的第三方字节当成可信来源。加速前缀只在 ``auto`` 策略下参与
    （它不是可选的「镜像站」，而是 GitHub 地址的取法之一），同样要求可校验。
    """
    official = Candidate(OFFICIAL, label(OFFICIAL), url, True)
    if not verifiable or policy == OFFICIAL:
        return (official,)
    if policy == AUTO:
        selected: tuple[Mirror | Proxy, ...] = (*MIRRORS, *PROXIES)
    else:
        selected = tuple(mirror for mirror in MIRRORS if mirror.ident == policy)
    ordered: list[Candidate] = []
    for item in selected:
        rewritten = _rewrite(item, url) if isinstance(item, Mirror) else _proxy_rewrite(item, url)
        if rewritten is not None:
            ordered.append(Candidate(item.ident, item.label, rewritten, True))
    return (*ordered, official)
