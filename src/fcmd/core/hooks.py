"""fcmd.core.hooks —— 分层钩子，供 core 在运行期调用上层实现。

``core`` 位于依赖图底层，不应导入 ``fcmd.cli`` 等上层包。工具发现通过本
模块注册回调实现反转：组合根（``fcmd`` 包）导入时注册实现，core 只调用
已注册钩子。
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "ensure_discovered",
    "register_discovery_hook",
]

DiscoveryHook = Callable[[], None]

_discovery_hook: DiscoveryHook | None = None


def register_discovery_hook(hook: DiscoveryHook) -> None:
    """注册工具发现回调（由组合根 ``fcmd`` 包调用）。"""
    global _discovery_hook  # noqa: PLW0603 - 钩子为模块级单例
    _discovery_hook = hook


def ensure_discovered() -> None:
    """调用已注册的工具发现回调；未注册时为无操作。"""
    if _discovery_hook is not None:
        _discovery_hook()
