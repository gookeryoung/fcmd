"""主题运行时状态：已应用主题 / 字体缓存 / 运行时高度（weakref，按 app 隔离）。"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

_APPLIED: weakref.WeakKeyDictionary[Any, Theme] = weakref.WeakKeyDictionary()


_FAMILIES: weakref.WeakKeyDictionary[Any, tuple[str, ...]] = weakref.WeakKeyDictionary()


_RUNTIME_HEIGHTS: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()


_ACTIVE_THEMES: weakref.WeakKeyDictionary[Any, Theme] = weakref.WeakKeyDictionary()
