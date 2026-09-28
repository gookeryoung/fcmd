"""envdev_core - envdev 子模块共享的公共辅助（私有，不参与工具发现）。

提供镜像源配置的统一模板：批量持久化环境变量 + 写入配置文件。
通过 ``is_dry_run`` 控制 dry-run 模式——dry-run 时不真的写文件或持久化，
只打印将要执行的操作。

本模块以下划线命名模式匹配 ``ensure_tools_discovered`` 的跳过规则，
不会被注册为 fcmd 工具。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "MirrorSpec",
    "apply_mirror_config",
    "is_dry_run",
    "set_dry_run",
]

# --------------------------------------------------------------------------- #
# dry-run 控制
# --------------------------------------------------------------------------- #

_dry_run_state: list[bool] = [False]


def set_dry_run(enabled: bool = True) -> None:
    """设置全局 dry-run 标志。

    Parameters
    ----------
    enabled:
        ``True`` 开启 dry-run，``False`` 关闭。
    """
    _dry_run_state[0] = enabled


def is_dry_run() -> bool:
    """返回当前是否处于 dry-run 模式。"""
    return _dry_run_state[0]


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

    dry-run 模式下仅打印将要执行的操作，不真正写文件或持久化。

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

    dry = is_dry_run()
    prefix = "[dry-run] " if dry else ""

    # 1) 环境变量
    for name, value in spec.env_vars.items():
        if dry:
            print(f"{prefix}持久化环境变量: {name}={value}")
            os.environ[name] = value  # dry-run 下也更新当前进程，方便后续 verify
        else:
            persist_fn(name, value)

    # 2) 创建目录
    for d in spec.ensure_dirs:
        if dry:
            print(f"{prefix}创建目录: {d}")
        else:
            d.mkdir(parents=True, exist_ok=True)

    # 3) 写入配置文件
    if spec.config_path is None or spec.config_content is None:
        return

    content = spec.config_content

    if dry:
        print(f"{prefix}写入配置文件: {spec.config_path}")
    else:
        spec.config_path.parent.mkdir(parents=True, exist_ok=True)
        spec.config_path.write_text(content, encoding="utf-8")

    if label and not dry:
        print(f"{label} 镜像源已配置 -> {spec.config_path}")
    elif label and dry:
        print(f"{prefix}{label} 镜像源将配置 -> {spec.config_path}")


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
