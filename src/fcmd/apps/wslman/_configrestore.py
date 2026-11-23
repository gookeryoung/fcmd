"""wslmanw「迁移配置文件」的还原记录。

从 :mod:`fcmd.apps.wslman._configfiles` 拆出：只描述「上次迁移 / 初始化对目标
实例做了什么」以及如何随设置文件往返，不依赖 Qt 或扫描逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["RestoreEntry", "RestoreRecord"]


@dataclass(frozen=True, slots=True)
class RestoreEntry:
    """一次迁移对单个目标文件做过的操作，供「还原上次迁移」使用。"""

    path: str
    backup: str = ""
    """覆盖前的备份路径（相对 ``$HOME``）；空串表示迁移前目标不存在。"""
    size: int = 0
    """迁移写入后的字节数，用于删除新增文件前确认它没被改过。"""

    @property
    def existed(self) -> bool:
        return bool(self.backup)


@dataclass(frozen=True, slots=True)
class RestoreRecord:
    """最近一次迁移的还原记录（按实例持久化）。"""

    stamp: str
    source: str = ""
    entries: tuple[RestoreEntry, ...] = ()

    @property
    def created(self) -> int:
        return sum(1 for item in self.entries if not item.existed)

    @property
    def overwritten(self) -> int:
        return sum(1 for item in self.entries if item.existed)

    @property
    def summary(self) -> str:
        action = self.source or "操作"
        return f"{self.stamp} {action}的 {len(self.entries)} 个文件（新增 {self.created} · 备份 {self.overwritten}）"

    def to_mapping(self) -> dict[str, str]:
        rows = [[item.path, item.backup, str(item.size)] for item in self.entries]
        return {
            "stamp": self.stamp,
            "source": self.source,
            "entries": json.dumps(rows, ensure_ascii=False),
        }

    @classmethod
    def from_mapping(cls, raw: object) -> RestoreRecord | None:
        if not isinstance(raw, Mapping):
            return None
        stamp = str(raw.get("stamp") or "").strip()
        if not stamp:
            return None
        try:
            rows = json.loads(str(raw.get("entries") or "[]"))
        except (TypeError, ValueError):
            return None
        if not isinstance(rows, list):
            return None
        entries: list[RestoreEntry] = []
        for row in rows:
            if not isinstance(row, list) or not row:
                continue
            path = str(row[0] or "").strip()
            backup = str(row[1] or "").strip() if len(row) > 1 else ""
            try:
                size = int(str(row[2])) if len(row) > 2 else 0
            except (TypeError, ValueError):
                size = 0
            if path:
                entries.append(RestoreEntry(path, backup, max(size, 0)))
        if not entries:
            return None
        return cls(stamp, str(raw.get("source") or "").strip(), tuple(entries))
