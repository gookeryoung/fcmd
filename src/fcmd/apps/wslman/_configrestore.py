"""wslmanw「迁移配置文件」的还原记录。

从 :mod:`fcmd.apps.wslman._configfiles` 拆出：只描述「上次迁移 / 初始化对目标
实例做了什么」以及扫描快照 / 还原记录如何随设置文件往返，不依赖 Qt 或扫描逻辑。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fcmd.apps.wslman._configfiles import MigrationSnapshot

__all__ = [
    "RestoreEntry",
    "RestoreRecord",
    "snapshot_from_mapping",
    "snapshot_to_mapping",
]


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


_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "label",
    "tone",
    "detail",
    "matched",
    "total",
    "source_label",
    "source_path",
    "source_count",
    "target_count",
    "target_summary",
    "scanned_at",
    "group_counts",
)


def _count(raw: object) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def snapshot_to_mapping(snapshot: MigrationSnapshot) -> dict[str, str]:
    """把扫描快照压成设置文件里的字符串键值（只留状态与计数，不留内容）。"""
    state = snapshot.state
    values = {
        "label": state.label,
        "tone": state.tone,
        "detail": state.detail,
        "matched": str(state.matched),
        "total": str(state.total),
        "source_label": snapshot.source_label,
        "source_path": snapshot.source_path,
        "source_count": str(snapshot.source_count),
        "target_count": str(snapshot.target_count),
        "target_summary": snapshot.target_summary,
        "scanned_at": snapshot.scanned_at,
        "group_counts": snapshot.group_counts,
    }
    return {name: values.get(name, "") for name in _SNAPSHOT_FIELDS}


def snapshot_from_mapping(raw: object) -> MigrationSnapshot | None:
    """从设置文件恢复上次扫描结果；没有标签（坏行 / 旧版本）时返回 ``None``。"""
    # 函数内导入避免与 ``_configfiles`` 在导入期成环（后者在模块级导入本模块）。
    from fcmd.apps.wslman._configfiles import MigrationSnapshot, MigrationState

    if not isinstance(raw, Mapping):
        return None
    label = str(raw.get("label") or "").strip()
    if not label:
        return None
    state = MigrationState(
        label,
        str(raw.get("tone") or "muted").strip() or "muted",
        str(raw.get("detail") or "").strip(),
        _count(raw.get("matched")),
        _count(raw.get("total")),
    )
    return MigrationSnapshot(
        state=state,
        source_label=str(raw.get("source_label") or "").strip(),
        source_path=str(raw.get("source_path") or "").strip(),
        source_count=_count(raw.get("source_count")),
        target_count=_count(raw.get("target_count")),
        target_summary=str(raw.get("target_summary") or "").strip(),
        scanned_at=str(raw.get("scanned_at") or "").strip(),
        group_counts=str(raw.get("group_counts") or "").strip(),
    )
