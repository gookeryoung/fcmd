"""envdev_core - envdev 子模块共享的公共辅助（私有，不参与工具发现）。

提供镜像源配置的统一模板：批量持久化环境变量 + 写入配置文件。

本模块以下划线命名模式匹配 ``ensure_tools_discovered`` 的跳过规则，
不会被注册为 fcmd 工具。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path

from fcmd.infras.net.mirrors import (
    MIRROR_DEFAULT_UA,
    MIRROR_USER_AGENTS,
    MirrorSpec,
    apply_mirror_config,
)

_MIRROR_USER_AGENTS = MIRROR_USER_AGENTS
_MIRROR_DEFAULT_UA = MIRROR_DEFAULT_UA  # 兼容旧导入名

__all__ = [
    "MirrorSpec",
    "apply_mirror_config",
    "auto_select_mirror",
    "resolve_mirror",
]


def auto_select_mirror(service: str, candidates: dict[str, str], timeout: float = 5.0) -> str | None:
    """并发探测单个服务支持的全部候选镜像，按实测下载速度自动选优。

    两阶段测速：先并发筛选可达性（区分限速/封禁），再对候选顺序实测
    下载吞吐，按吞吐与延迟综合排序。纯延迟排序无法识别「响应快但下载
    被限速」的镜像，故以真实吞吐为主指标。

    打印探测排名，返回评分最高的可达镜像名；全部不可达时返回 ``None``。

    Parameters
    ----------
    service:
        服务名（如 ``pip`` / ``conda`` / ``rustup`` / ``cargo``），用于打印标签
        并选择对应的探测 User-Agent。
    candidates:
        ``{镜像名: 实际服务 URL}`` 候选表。注意不同服务（如 pypi 与 conda
        频道、rustup 与 crates.io 索引）的服务地址互不相同，须传入各自
        服务的真实地址探测。
    timeout:
        单个镜像的单次探测超时秒数（默认 ``5``，失败项会自动重试一次）。
        超时过短会把「响应稍慢但可用」的高校镜像误判为不可达。

    Returns
    -------
    str | None
        综合评分最高的可达镜像名；全部不可达时返回 ``None``。
    """
    # 延迟导入避免加重导入链
    from fcmd.infras.net import probe_mirrors

    probes = probe_mirrors(
        list(candidates.values()),
        timeout=timeout,
        user_agent=MIRROR_USER_AGENTS.get(service, MIRROR_DEFAULT_UA),
        allow_html=service == "pip",
    )
    name_by_url = {url: name for name, url in candidates.items()}

    print(f"[{service} 镜像自动选优]（按实测下载吞吐与延迟排序）")
    selected: str | None = None
    for index, probe in enumerate(probes, start=1):
        name = name_by_url[probe.url]
        if probe.ok:
            print(
                f"  {index}. {name:<12} {probe.url}  可访问  "
                f"延迟 {probe.latency_ms:.0f} ms  吞吐 {probe.throughput_kbps:.0f} KB/s"
            )
            if selected is None:
                selected = name
        else:
            print(f"  {index}. {name:<12} {probe.url}  不可访问（{probe.reason}）")
    return selected


def resolve_mirror(service: str, mirror: str, candidates: dict[str, str], default: str) -> str:
    """解析镜像参数：显式指定原样返回；``auto`` 探测选优，全部不可达回退默认。

    Parameters
    ----------
    service:
        服务名（如 ``pip`` / ``conda`` / ``rustup`` / ``cargo``）。
    mirror:
        用户指定的镜像名；``auto`` 表示自动选优。
    candidates:
        ``{镜像名: 实际服务 URL}`` 候选表（须为该服务自身的地址）。
    default:
        全部候选不可达时回退的默认镜像名。

    Returns
    -------
    str
        最终选用的镜像名。
    """
    if mirror != "auto":
        return mirror
    selected = auto_select_mirror(service, candidates) or default
    print(f"已选用 {service} 镜像: {selected}")
    return selected


# --------------------------------------------------------------------------- #
# 跨平台通用辅助
# --------------------------------------------------------------------------- #


def pip_config_path() -> Path:
    """返回当前平台的 pip 配置文件路径。

    Linux/macOS: ``~/.pip/pip.conf``
    Windows: ``~/pip/pip.ini``
    """
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        return Path.home() / ".pip" / "pip.conf"
    return Path.home() / "pip" / "pip.ini"


def mirror_supported(mirror: str, supported: Iterable[str]) -> bool:
    """检查镜像源名称是否在支持列表中（大小写不敏感）。"""
    return mirror.lower() in {s.lower() for s in supported}
