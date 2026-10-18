"""envdev_core - envdev 子模块共享的公共辅助（私有，不参与工具发现）。

提供镜像源配置的统一模板：批量持久化环境变量 + 写入配置文件。

本模块以下划线命名模式匹配 ``ensure_tools_discovered`` 的跳过规则，
不会被注册为 fcmd 工具。
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MirrorSpec",
    "apply_mirror_config",
    "auto_select_mirror",
    "resolve_mirror",
]


# --------------------------------------------------------------------------- #
# 镜像源配置模板
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class MirrorSpec:
    """单个镜像源的配置规格。

    将"镜像源名称"、"需要持久化的环境变量"、"要写入的配置文件路径和内容"
    捆绑在一起，供 :func:`apply_mirror_config` 统一执行。

    Parameters
    ----------
    env_vars:
        要持久化的 ``{名称: 值}`` 字典。
    config_path:
        要写入的配置文件路径（可选，为 ``None`` 时跳过写文件）。
    config_content:
        配置文件内容模板字符串。若为 ``None`` 则不写入；若为 ``callable``
        则在执行时调用以生成内容（支持依赖 mirror 名称动态生成）。
    ensure_dirs:
        执行前必须创建的目录列表（可选），如 sccache 缓存目录。
    """

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
    """执行镜像源配置：批量持久化环境变量 + 写入配置文件 + 创建目录。

    Parameters
    ----------
    spec:
        镜像源规格。
    persist_fn:
        环境变量持久化函数签名 ``(name, value) -> None``。
        默认为 ``fcmd.cli._env_persist.persist_env``，可注入假函数用于测试。
    label:
        打印日志时的标签（如 ``"Python"`` / ``"Rust"``）。
    """
    # 延迟导入避免反向依赖
    if persist_fn is None:
        from fcmd.cli._env_persist import persist_env as persist_fn  # type: ignore[no-redef]

    # 1) 环境变量
    for name, value in spec.env_vars.items():
        persist_fn(name, value)

    # 2) 创建目录
    for d in spec.ensure_dirs:
        d.mkdir(parents=True, exist_ok=True)

    # 3) 写入配置文件
    if spec.config_path is None or spec.config_content is None:
        return

    spec.config_path.parent.mkdir(parents=True, exist_ok=True)
    spec.config_path.write_text(spec.config_content, encoding="utf-8")

    if label:
        print(f"{label} 镜像源已配置 -> {spec.config_path}")


# --------------------------------------------------------------------------- #
# 镜像自动选优（按服务）
# --------------------------------------------------------------------------- #


def auto_select_mirror(service: str, candidates: dict[str, str], timeout: float = 2.0) -> str | None:
    """并发探测单个服务支持的全部候选镜像，按可访问性与速度自动选优。

    打印探测排名，返回最快可达的镜像名；全部不可达时返回 ``None``。

    Parameters
    ----------
    service:
        服务名（如 ``pip`` / ``conda`` / ``rustup`` / ``cargo``），仅用于打印标签。
    candidates:
        ``{镜像名: 实际服务 URL}`` 候选表。注意不同服务（如 pypi 与 conda
        频道、rustup 与 crates.io 索引）的服务地址互不相同，须传入各自
        服务的真实地址探测。
    timeout:
        单个镜像的探测超时秒数（默认 ``2``）

    Returns
    -------
    str | None
        最快可达的镜像名；全部不可达时返回 ``None``。
    """
    # 延迟导入避免加重导入链
    from fcmd.dsl.actions.net import check_urls

    name_by_url = {url: name for name, url in candidates.items()}
    results = check_urls(list(candidates.values()), timeout=timeout)

    print(f"[{service} 镜像自动选优]（按可访问性与访问速度排序）")
    selected: str | None = None
    for index, (url, ok, latency) in enumerate(results, start=1):
        name = name_by_url[url]
        speed = f"{latency:.0f} ms" if ok else "-"
        print(f"  {index}. {name:<12} {url}  {'可访问' if ok else '不可访问'}  {speed}")
        if ok and selected is None:
            selected = name
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
