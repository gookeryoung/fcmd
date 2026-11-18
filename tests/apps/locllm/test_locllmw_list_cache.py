"""locllmw 模型浏览列表缓存：键规范化、TTL、磁盘往返与淘汰。"""

from __future__ import annotations

import json
from pathlib import Path

from fcmd.apps.locllm._locllmw_list_cache import ModelListCache, cache_key
from fcmd.apps.locllm.llmdown import ModelSearch, ModelSummary


def _search(page: int = 1) -> ModelSearch:
    return ModelSearch(
        (
            ModelSummary(
                repo="Qwen/Qwen3-Coder-30B",
                name="Qwen3-Coder-30B",
                downloads=12,
                stars=3,
                tasks=("text-generation",),
                tags=("code",),
                storage_size=1024,
            ),
        ),
        total=42,
        page=page,
        page_size=20,
    )


def test_cache_key_normalizes_keyword_and_sort() -> None:
    assert cache_key("  Qwen ", 2, 30, "Default") == cache_key("qwen", 2, 30, "default")
    assert cache_key("", 0, 20, "") == cache_key("", 1, 20, "default")


def test_memory_hit_marks_stale_after_ttl(tmp_path: Path) -> None:
    now = [1000.0]
    cache = ModelListCache(tmp_path / "list.json", ttl=60, clock=lambda: now[0])
    key = cache_key("qwen", 1, 20, "default")
    assert cache.get(key) is None
    cache.put(key, _search())
    hit = cache.get(key)
    assert hit is not None and hit.fresh
    now[0] += 61
    stale = cache.get(key)
    assert stale is not None
    assert not stale.fresh
    assert stale.search == _search()


def test_disk_roundtrip_restores_summary_fields(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "list.json"
    key = cache_key("qwen", 2, 20, "default")
    ModelListCache(path).put(key, _search(page=2))
    restored = ModelListCache(path).get(key)
    assert restored is not None
    assert restored.search == _search(page=2)
    assert restored.search.items[0].tasks == ("text-generation",)
    assert restored.search.items[0].tags == ("code",)


def test_corrupt_cache_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("{not json", encoding="utf-8")
    cache = ModelListCache(path)
    assert cache.get(cache_key("", 1, 20, "default")) is None
    cache.put(cache_key("", 1, 20, "default"), _search())
    assert cache.get(cache_key("", 1, 20, "default")) is not None


def test_invalid_entry_fields_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "entries": {
                    "bad": {"at": "now", "items": []},
                    "mixed": {
                        "at": 1,
                        "total": 2,
                        "page": 1,
                        "page_size": 20,
                        "items": [{"repo": "ok/model"}, {"name": "missing-repo"}],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    cache = ModelListCache(path)
    assert cache.get("bad") is None
    hit = cache.get("mixed")
    assert hit is not None
    assert [item.repo for item in hit.search.items] == ["ok/model"]


def test_clear_removes_memory_and_disk(tmp_path: Path) -> None:
    cache = ModelListCache(tmp_path / "cache.json")
    search = ModelSearch(items=(ModelSummary(repo="Qwen/demo"),), total=1, page=1, page_size=30)
    cache.put("k", search)
    assert (tmp_path / "cache.json").exists()
    cache.clear()
    assert cache.get("k") is None
    assert not (tmp_path / "cache.json").exists()


def test_oldest_entries_are_trimmed(tmp_path: Path) -> None:
    now = [100.0]
    cache = ModelListCache(tmp_path / "list.json", max_entries=2, clock=lambda: now[0])
    for index in range(3):
        now[0] += 1
        cache.put(f"page-{index}", _search(page=index + 1))
    assert cache.get("page-0") is None
    assert cache.get("page-1") is not None
    assert cache.get("page-2") is not None
