"""locllmw 模型浏览列表缓存：内存 LRU + 磁盘 JSON，带 TTL 与陈旧回显。

模型页每次进入 / 翻页都会请求 ModelScope 列表。网络请求是主要耗时，因此：

- 内存缓存按 ``(关键字, 页码, 每页条数, 排序)`` 命中，避免同一会话重复请求；
- 磁盘缓存写到 ``${FCMD_HOME:-~/.fcmd}/cache/locllmw-model-list.json``，
  重启后仍能秒开上次看过的页面；
- 超过 TTL 的条目仍先返回（陈旧回显），由调用方在后台刷新；
- 最多保留 ``MAX_ENTRIES`` 页，按写入时间淘汰；损坏 / 非法缓存静默忽略。

本模块只做数据存取与序列化，不导入 PySide6，也不执行网络请求。
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.locllm import _llmconfig
from fcmd.apps.locllm.llmdown import ModelSearch, ModelSummary

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "CachedSearch",
    "ModelListCache",
    "cache_key",
    "default_cache_path",
]

DEFAULT_TTL_SECONDS = 600.0
"""列表新鲜期：10 分钟内再次查看同一页不再请求网络。"""

DEFAULT_MAX_ENTRIES = 200
"""最多缓存的列表页数；一页约 20 条，200 页约 4000 条摘要。"""

_SCHEMA = 1


def default_cache_path() -> Path:
    """磁盘缓存路径：``${FCMD_HOME:-~/.fcmd}/cache/locllmw-model-list.json``。"""
    return _llmconfig.home_dir() / "cache" / "locllmw-model-list.json"


def cache_key(keyword: str, page: int, page_size: int, sort: str) -> str:
    """缓存键：关键字去空白 / 小写，分页与排序规范化。"""
    return f"{(keyword or '').strip().casefold()}|{max(int(page), 1)}|{int(page_size)}|{(sort or 'default').strip().casefold()}"


@dataclass(frozen=True, slots=True)
class CachedSearch:
    """缓存命中结果：``fresh=False`` 时应先展示再后台刷新。"""

    search: ModelSearch
    age: float
    fresh: bool


class ModelListCache:
    """带 TTL 的模型列表缓存（线程安全，可跨线程读写）。"""

    def __init__(
        self,
        path: Path | None = None,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path if path is not None else default_cache_path()
        self._ttl = max(float(ttl), 0.0)
        self._max_entries = max(int(max_entries), 1)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, ModelSearch]] = {}
        self._loaded = False

    def get(self, key: str) -> CachedSearch | None:
        """读取缓存；返回年龄与是否仍在新鲜期内。"""
        with self._lock:
            self._ensure_loaded()
            item = self._entries.get(key)
            if item is None:
                return None
            at, search = item
            age = max(0.0, self._clock() - at)
            return CachedSearch(search, age, age <= self._ttl)

    def put(self, key: str, search: ModelSearch) -> None:
        """写入一页结果并尝试落盘；磁盘失败不影响内存缓存。"""
        with self._lock:
            self._ensure_loaded()
            self._entries[key] = (self._clock(), search)
            self._trim_locked()
            self._save_locked()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return
        for key, value in entries.items():
            parsed = _entry_from_dict(value)
            if parsed is not None:
                self._entries[str(key)] = parsed
        self._trim_locked()

    def _trim_locked(self) -> None:
        if len(self._entries) <= self._max_entries:
            return
        ordered = sorted(self._entries.items(), key=lambda item: item[1][0], reverse=True)
        self._entries = dict(ordered[: self._max_entries])

    def _save_locked(self) -> None:
        payload = {
            "schema": _SCHEMA,
            "entries": {key: _entry_to_dict(at, search) for key, (at, search) in self._entries.items()},
        }
        with contextlib.suppress(OSError):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temp.replace(self.path)


def _entry_to_dict(at: float, search: ModelSearch) -> dict[str, Any]:
    return {
        "at": at,
        "total": search.total,
        "page": search.page,
        "page_size": search.page_size,
        "items": [_summary_to_dict(item) for item in search.items],
    }


def _entry_from_dict(raw: object) -> tuple[float, ModelSearch] | None:
    if not isinstance(raw, dict):
        return None
    at = raw.get("at")
    items = raw.get("items")
    if not isinstance(at, (int, float)) or not isinstance(items, list):
        return None
    total = raw.get("total")
    page = raw.get("page")
    page_size = raw.get("page_size")
    if not isinstance(total, int) or isinstance(total, bool):
        return None
    if not isinstance(page, int) or isinstance(page, bool):
        return None
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        return None
    summaries = tuple(summary for item in items if (summary := _summary_from_dict(item)) is not None)
    return float(at), ModelSearch(summaries, total, page, page_size)


def _summary_to_dict(item: ModelSummary) -> dict[str, Any]:
    return {
        "repo": item.repo,
        "name": item.name,
        "owner": item.owner,
        "chinese_name": item.chinese_name,
        "downloads": item.downloads,
        "stars": item.stars,
        "tasks": list(item.tasks),
        "tags": list(item.tags),
        "license": item.license,
        "updated": item.updated,
        "storage_size": item.storage_size,
    }


def _summary_from_dict(raw: object) -> ModelSummary | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("repo"), str):
        return None
    return ModelSummary(
        repo=raw["repo"],
        name=_text(raw.get("name")),
        owner=_text(raw.get("owner")),
        chinese_name=_text(raw.get("chinese_name")),
        downloads=_integer(raw.get("downloads")),
        stars=_integer(raw.get("stars")),
        tasks=_text_tuple(raw.get("tasks")),
        tags=_text_tuple(raw.get("tags")),
        license=_text(raw.get("license")),
        updated=_integer(raw.get("updated")),
        storage_size=_integer(raw.get("storage_size")),
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _text_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
