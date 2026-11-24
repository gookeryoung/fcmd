"""GUI 共用持久化辅助：路径历史、最近文件 / 目录与防抖写盘。

设计口径见 :doc:`docs/adr/0050-shared-ui-persistence`。本模块只处理「用户偏好 +
轻量界面状态」的小数据，不接触扫描结果 / 模型列表这类可重建内容；存储仍由
:class:`fcmd.gui.settings.SettingsStore` 负责原子写入与 ``.bak`` 回退。

- :class:`PathHistory`：按用途记录文件 / 目录对话框的上次位置；
- :class:`RecentFiles`：最近文件 / 目录的去重、最新置顶与上限裁剪；
- :class:`DebouncedSaver`：文本 / 筛选类改动防抖落盘，关闭窗口时强制 flush。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import MutableMapping, Sequence

    from PySide6.QtCore import QObject

__all__ = [
    "DebouncedSaver",
    "PathHistory",
    "RecentFiles",
    "WindowState",
    "dialog_remember",
    "dialog_start",
    "merge_recent",
    "normalize_recent",
]

RECENT_LIMIT = 10
"""最近文件 / 目录的默认上限。"""


def _display(value: str | Path) -> str:
    """最近列表的展示文本：保持用户 / 历史条目原有分隔符，不重写路径。"""
    return str(value).strip()


def _case_key(value: str) -> str:
    """去重键：Windows 路径大小写不敏感，其它平台保持原样。"""
    return value.casefold() if os.name == "nt" else value


def _existing_dir(value: object) -> str:
    """把已存值还原成可用目录：目录原样；文件取父目录；失效返回空串。"""
    if not isinstance(value, str) or not value.strip():
        return ""
    path = Path(value).expanduser()
    if path.is_dir():
        return str(path)
    if path.is_file():
        return str(path.parent)
    return ""


def _chosen_dir(chosen: str | Path) -> str:
    """把用户本次选择还原成目录：目录本身；文件 / 新文件名取父目录。"""
    path = Path(chosen).expanduser()
    if path.is_dir():
        return str(path)
    parent = path.parent
    return str(parent) if str(parent) not in ("", ".") else ""


def merge_recent(
    items: Sequence[object],
    path: str | Path,
    *,
    limit: int = RECENT_LIMIT,
) -> list[str]:
    """把 ``path`` 放到最近列表头部：去重、过滤非法项并截断到 ``limit``。

    与旧 ``add_recent_file`` 保持同一行为（不校验路径是否存在），存在性过滤留给
    展示层，避免设置文件在移动硬盘 / 网络盘离线时被清空。
    """
    entry = _display(path)
    if not entry or limit <= 0:
        return []
    result = [entry]
    seen = {_case_key(entry)}
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        text = _display(item)
        key = _case_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result[:limit]


def normalize_recent(raw: object, *, limit: int = RECENT_LIMIT) -> list[str]:
    """把设置文件里的最近列表规范化为去重后的字符串列表。"""
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        text = _display(item)
        key = _case_key(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def dialog_start(value: object) -> str:
    """单字段版对话框初始目录：已存目录仍存在才返回，否则回落空串。"""
    return _existing_dir(value)


def dialog_remember(chosen: str | Path) -> str:
    """单字段版「记住本次选择」：目录本身；文件 / 新文件名取父目录。"""
    return _chosen_dir(chosen)


class PathHistory:
    """文件 / 目录对话框的上次位置，读写都落在调用方的设置映射上。

    ``payload`` 是应用自己的设置字典（由 ``SettingsStore`` 读写）；本类只负责
    取值、校验与写回，不决定何时落盘。
    """

    def __init__(self, payload: MutableMapping[str, Any]) -> None:
        self._payload = payload

    def raw(self, key: str) -> str:
        """读取设置里的原始字符串（不做存在性校验）。"""
        value = self._payload.get(key)
        return value if isinstance(value, str) else ""

    def start(self, key: str) -> str:
        """对话框初始目录：上次目录仍存在时返回，否则回落空串（系统默认）。"""
        return dialog_start(self._payload.get(key))

    def remember(self, key: str, chosen: str | Path) -> str:
        """记录本次选择所在目录并返回；无法推导目录时保持原值。"""
        directory = dialog_remember(chosen)
        if directory:
            self._payload[key] = directory
        return directory or self.raw(key)

    def default_name(self, key: str, filename: str) -> str:
        """保存对话框的默认路径：上次目录 + 文件名；没有历史时只用文件名。"""
        start = self.start(key)
        return str(Path(start) / filename) if start else filename


class WindowState:
    """轻量界面状态：页面、开关、分栏 / 表格尺寸，读写调用方的设置映射。

    只收「小体积、可重建」的状态；展开项、滚动像素位置、选中行索引不在此列。
    字段缺失 / 类型不符时一律回落默认值，避免旧设置把窗口恢复到不可用布局。
    """

    def __init__(self, payload: MutableMapping[str, Any]) -> None:
        self._payload = payload

    def text(self, key: str, default: str = "") -> str:
        value = self._payload.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else default

    def flag(self, key: str, default: bool = False) -> bool:
        value = self._payload.get(key)
        return value if isinstance(value, bool) else default

    def choice(self, key: str, choices: Sequence[str], default: str) -> str:
        value = self.text(key)
        return value if value in choices else default

    def set(self, key: str, value: Any) -> None:
        self._payload[key] = value

    def sizes(self, key: str, *, count: int, default: Sequence[int]) -> list[int]:
        """读取固定数量的正整数尺寸；缺失 / 非法 / 数量不足时回落默认值。"""
        raw = self._payload.get(key)
        values = (
            [item for item in raw if isinstance(item, int) and not isinstance(item, bool) and item > 0]
            if isinstance(raw, (list, tuple))
            else []
        )
        fallback = [max(1, int(item)) for item in default[:count]]
        if len(values) < count:
            return fallback
        return values[:count]

    def remember_sizes(self, key: str, sizes: Sequence[int]) -> list[int]:
        values = [max(1, int(item)) for item in sizes if isinstance(item, int) and not isinstance(item, bool)]
        if values:
            self._payload[key] = values
        return values


class RecentFiles:
    """最近文件 / 目录列表，读写同一个设置映射。

    ``key`` 默认 ``recent_files``；需要独立列表（如最近目录）时换 key 即可。
    """

    def __init__(
        self,
        payload: MutableMapping[str, Any],
        key: str = "recent_files",
        *,
        limit: int = RECENT_LIMIT,
    ) -> None:
        self._payload = payload
        self._key = key
        self._limit = max(1, limit)

    def items(self) -> list[str]:
        """当前列表（已去重、截断；不校验路径是否仍存在）。"""
        return normalize_recent(self._payload.get(self._key), limit=self._limit)

    def add(self, path: str | Path) -> list[str]:
        """把路径置顶写入并返回新列表。"""
        items = merge_recent(self.items(), path, limit=self._limit)
        self._payload[self._key] = items
        return items


class DebouncedSaver:
    """防抖写盘：连续 ``touch`` 只在静默 ``delay_ms`` 后写一次，``flush`` 立即写。

    ``flush`` 由调用方提供（通常是 ``lambda: store.save(payload)``）；写入失败按
    既有 ``SettingsStore`` 口径静默处理，不在关闭路径抛异常。
    """

    def __init__(
        self,
        flush: Callable[[], None],
        *,
        delay_ms: int = 300,
        parent: QObject | None = None,
    ) -> None:
        from PySide6.QtCore import QTimer

        self._flush = flush
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.setInterval(max(0, delay_ms))
        self._timer.timeout.connect(self.flush)

    def touch(self) -> None:
        """标记有改动：重新开始计时。"""
        self._timer.start()

    def flush(self) -> None:
        """立即写盘并取消待触发的计时器。"""
        self._timer.stop()
        self._flush()

    def cancel(self) -> None:
        """丢弃待写改动（例如批量重建期间临时停写）。"""
        self._timer.stop()

    def pending(self) -> bool:
        """是否有尚未落盘的改动。"""
        return self._timer.isActive()
