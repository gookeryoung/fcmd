"""镜像源公用件：URL 改写、候选生成、测速排序与镜像配置模板。

只放跨应用通用的机制；具体镜像清单 / 服务探测地址 / apply 映射留在各应用
（``apps/wslman`` / ``apps/envdev``），避免 infra 认识业务数据。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Candidate",
    "Mirror",
    "MirrorSpec",
    "Proxy",
    "apply_mirror_config",
    "build_candidates",
    "rank_candidates",
    "service_user_agent",
]

MIRROR_USER_AGENTS: dict[str, str] = {
    "pip": "pip/24.0",
    "conda": "conda/24.9.2",
    "rustup": "rustup/1.27.1",
    "cargo": "cargo/1.83.0",
    "go": "go/1.22.0",
    "maven": "Maven/3.9.6",
    "npm": "npm/10.8.2 node/v20.17.0",
}
MIRROR_DEFAULT_UA = "fcmd-mirror-probe/0.3.10"


def service_user_agent(service: str) -> str:
    """按服务返回测速 UA：部分镜像站对非包管理器 UA 返回重定向循环。"""
    return MIRROR_USER_AGENTS.get(service, MIRROR_DEFAULT_UA)


@dataclass(frozen=True, slots=True)
class Mirror:
    """一个镜像站：``rules`` 是（官方 URL 前缀, 站内相对前缀）改写规则。"""

    ident: str
    label: str
    base: str
    rules: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Proxy:
    """第三方加速前缀：把官方 URL 原样拼到前缀后（GitHub Releases 用）。"""

    ident: str
    label: str
    prefix: str
    hosts: frozenset[str]


@dataclass(frozen=True, slots=True)
class Candidate:
    """一个可尝试的下载候选。"""

    ident: str
    label: str
    url: str
    trusted: bool
    filename: str = ""
    sha256: str | None = None
    verify: bool = True

    def to_data(self) -> dict[str, object]:
        return {"id": self.ident, "label": self.label, "url": self.url, "trusted": self.trusted}


@dataclass(slots=True)
class MirrorSpec:
    """镜像源配置模板：环境变量 + 配置文件 + 需要创建的目录。"""

    env_vars: dict[str, str] = field(default_factory=dict)
    config_path: Path | None = None
    config_content: str | None = None
    ensure_dirs: list[Path] = field(default_factory=list)


def apply_mirror_config(
    spec: MirrorSpec,
    *,
    persist_fn: Callable[[str, str], None] | None = None,
    label: str = "",
) -> None:
    """执行镜像源配置：写环境变量、建目录、写配置文件。"""
    if persist_fn is None:
        from fcmd.infras.system import persist_env as persist_fn  # type: ignore[no-redef]
    for name, value in spec.env_vars.items():
        persist_fn(name, value)
    for directory in spec.ensure_dirs:
        directory.mkdir(parents=True, exist_ok=True)
    if spec.config_path is None or spec.config_content is None:
        return
    spec.config_path.parent.mkdir(parents=True, exist_ok=True)
    spec.config_path.write_text(spec.config_content, encoding="utf-8")
    if label:
        print(f"{label} 镜像源已配置 -> {spec.config_path}")


def _rewrite(mirror: Mirror, url: str) -> str | None:
    for prefix, relative in mirror.rules:
        if url.startswith(prefix):
            return f"{mirror.base.rstrip('/')}/{relative}{url[len(prefix) :]}"
    return None


def _proxy_rewrite(proxy: Proxy, url: str) -> str | None:
    from urllib.parse import urlsplit

    if urlsplit(url).hostname in proxy.hosts:
        return f"{proxy.prefix}{url}"
    return None


def build_candidates(  # noqa: PLR0913 - 候选策略参数成组传入
    url: str,
    *,
    mirrors: Sequence[Mirror] = (),
    proxies: Sequence[Proxy] = (),
    official_label: str = "官方源",
    policy: str = "auto",
    verifiable: bool = True,
) -> tuple[Candidate, ...]:
    """按策略生成候选：镜像 / 加速前缀在前，官方源最后兜底。"""
    official = Candidate("official", official_label, url, True)
    if not verifiable or policy == "official":
        return (official,)
    selected: tuple[Mirror | Proxy, ...] = (
        (*mirrors, *proxies) if policy == "auto" else tuple(item for item in mirrors if item.ident == policy)
    )
    ordered: list[Candidate] = []
    for item in selected:
        rewritten = _rewrite(item, url) if isinstance(item, Mirror) else _proxy_rewrite(item, url)
        if rewritten is not None:
            ordered.append(Candidate(item.ident, item.label, rewritten, True))
    return (*ordered, official)


def rank_candidates(
    candidates: Sequence[Any],
    *,
    probe: Callable[[str], float],
    latency: Callable[[str], float],
    workers: int = 12,
) -> tuple[Any, ...]:
    """并发测速排序：有吞吐时按吞吐，全部失败时回退 TCP 延迟。"""
    if len(candidates) <= 1:
        return tuple(candidates)
    with ThreadPoolExecutor(max_workers=min(workers, len(candidates)), thread_name_prefix="mirror-probe") as pool:
        measured = list(pool.map(lambda item: (item, probe(item.url)), candidates))
    if any(speed > 0 for _item, speed in measured):
        measured.sort(key=lambda pair: pair[1], reverse=True)
        return tuple(item for item, _speed in measured)
    with ThreadPoolExecutor(max_workers=min(workers, len(candidates)), thread_name_prefix="mirror-latency") as pool:
        timed = list(pool.map(lambda item: (item, latency(item.url)), candidates))
    timed.sort(key=lambda pair: pair[1])
    return tuple(item for item, _latency in timed)
