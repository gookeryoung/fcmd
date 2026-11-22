"""分页导航纯函数：页面 key 解析与循环切换。

Qt 侧边栏（:class:`fcmd.gui.sidebar.SidebarNav`）复用这里的口径；
本模块不导入任何 GUI 库，CI 可直接单测。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["next_page", "resolve_page_key"]


def resolve_page_key(keys: Sequence[str], target: str | int) -> str:
    """把 key 或索引解析为页面 key；未知 key 或越界索引抛 ``KeyError``。"""
    if isinstance(target, bool) or not isinstance(target, (str, int)):
        raise KeyError(target)
    if isinstance(target, int):
        try:
            return keys[target]
        except IndexError:
            raise KeyError(target) from None
    if target in keys:
        return target
    raise KeyError(target)


def next_page(keys: Sequence[str], current: str | None, step: int = 1) -> str:
    """按顺序循环切换到 ``current`` 之后第 ``step`` 个页面；空序列返回空串。"""
    if not keys:
        return ""
    try:
        index = keys.index(current) if current is not None else -1
    except ValueError:
        index = -1
    return keys[(index + step) % len(keys)]
