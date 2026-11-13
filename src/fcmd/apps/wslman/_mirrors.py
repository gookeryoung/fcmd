"""国内镜像源：只收录实测与官方字节一致的镜像，且只在能校验 sha256 时参与排序。

盘点结论（2026-11 实测，HEAD 比对 ``Content-Length``）：

- ``releases.ubuntu.com`` 上的 WSL 镜像在中科大 / 阿里云 / 华为云都有同路径副本，
  大小与官方完全一致（379,031,026 字节等）；
- ``download.fedoraproject.org/pub/fedora`` 在中科大镜像为 ``/fedora``，大小一致；
- 其余发行版的下载地址在 github.com / gitlab.com / salsa.debian.org / Microsoft CDN /
  kali.download / fastly.mirror.pkgbuild.com 上，未找到可验证的国内镜像；
- 清华 TUNA 对本机返回 403（疑似按来源 IP 限制），无法验证，故不收录。

镜像只做「官方 URL 的等价改写」，是否可信由调用方在下载完成后用官方 sha256 判定：
没有 sha256 的条目一律只走官方源，绝不把镜像标成已验证。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MIRRORS", "Candidate", "candidates", "identifiers", "label", "trusted_ids"]


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
)
"""按优先级排列：同一条目命中多个镜像时，越靠前越先尝试。"""

AUTO = "auto"
OFFICIAL = "official"


@dataclass(frozen=True, slots=True)
class Candidate:
    ident: str
    label: str
    url: str
    trusted: bool
    """是否可标为可信来源；镜像只有在有官方 sha256 可校验时才为 ``True``。"""

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
        return "自动（优先国内镜像）"
    for mirror in MIRRORS:
        if mirror.ident == ident:
            return mirror.label
    return ident


def trusted_ids() -> tuple[str, ...]:
    return (OFFICIAL, *(mirror.ident for mirror in MIRRORS))


def _rewrite(mirror: Mirror, url: str) -> str | None:
    for prefix, relative in mirror.rules:
        if url.startswith(prefix):
            return f"{mirror.base.rstrip('/')}/{relative}{url[len(prefix) :]}"
    return None


def candidates(url: str, *, policy: str = AUTO, verifiable: bool = True) -> tuple[Candidate, ...]:
    """按策略给出下载候选，官方源永远排在最后兜底。

    ``verifiable`` 表示调用方拿得到官方 sha256；为 ``False`` 时不使用任何镜像，
    避免把无法校验的第三方字节当成可信来源。
    """
    official = Candidate(OFFICIAL, label(OFFICIAL), url, True)
    if not verifiable or policy == OFFICIAL:
        return (official,)
    selected = MIRRORS if policy == AUTO else tuple(mirror for mirror in MIRRORS if mirror.ident == policy)
    ordered: list[Candidate] = []
    for mirror in selected:
        rewritten = _rewrite(mirror, url)
        if rewritten is not None:
            ordered.append(Candidate(mirror.ident, mirror.label, rewritten, True))
    return (*ordered, official)
