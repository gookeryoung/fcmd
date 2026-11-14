"""GGUF 精度缓存：命中、失效与损坏降级。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fcmd.apps.locllm import _llmgguf_cache
from fcmd.apps.locllm._llmgguf import GgufPrecision
from fcmd.apps.locllm._llmgguf_cache import (
    GGUF_CACHE_SCHEMA_VERSION,
    PrecisionCache,
    default_cache_path,
)


def _precision(label: str = "Q4_K_M 量化") -> GgufPrecision:
    return GgufPrecision(label=label, quantized=True, source="metadata", expected_bytes=123)


def test_round_trip_through_disk(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 16)
    path = tmp_path / "cache" / "gguf.json"
    cache = PrecisionCache(path)
    assert cache.get(model) is None
    cache.remember(model, _precision())
    cache.save()

    reloaded = PrecisionCache(path)
    assert reloaded.get(model) == _precision()


def test_invalidated_when_size_changes(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 16)
    cache = PrecisionCache(tmp_path / "cache.json")
    cache.remember(model, _precision())
    model.write_bytes(b"x" * 32)
    assert cache.get(model) is None


def test_invalidated_when_mtime_changes(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 16)
    cache = PrecisionCache(tmp_path / "cache.json")
    cache.remember(model, _precision())
    stat = model.stat()
    os.utime(model, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert cache.get(model) is None


def test_unknown_source_is_not_cached(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 16)
    cache = PrecisionCache(tmp_path / "cache.json")
    cache.remember(model, GgufPrecision(label="无法识别", quantized=None, source="unknown"))
    assert cache.get(model) is None


def test_version_mismatch_discards_entries(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x" * 16)
    path = tmp_path / "cache.json"
    cache = PrecisionCache(path)
    cache.remember(model, _precision())
    cache.save()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = GGUF_CACHE_SCHEMA_VERSION + 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert PrecisionCache(path).get(model) is None


def test_corrupt_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("not json", encoding="utf-8")
    assert PrecisionCache(path).get(tmp_path / "model.gguf") is None


def test_default_cache_path_honours_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert default_cache_path() == tmp_path / "cache" / "gguf-precision.json"


def _write_entries(path: Path, entries: object) -> None:
    path.write_text(
        json.dumps({"version": GGUF_CACHE_SCHEMA_VERSION, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_non_object_entry_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    _write_entries(path, {"a": ["not", "an", "object"]})
    assert PrecisionCache(path)._entries == {}


def test_unknown_source_entry_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    _write_entries(path, {"a": {"source": "bogus", "mtime_ns": 0, "size": 0, "label": "x", "quantized": True}})
    assert PrecisionCache(path)._entries == {}


def test_entry_missing_fields_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    _write_entries(path, {"a": {"source": "metadata"}})
    assert PrecisionCache(path)._entries == {}


def test_entries_not_dict_discards_all(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    _write_entries(path, ["a", "b"])
    assert PrecisionCache(path)._entries == {}


def test_remember_missing_model_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    cache = PrecisionCache(path)
    cache.remember(tmp_path / "missing.gguf", _precision())
    cache.save()
    assert cache.get(tmp_path / "missing.gguf") is None
    assert not path.exists()


def test_save_trims_oldest_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_llmgguf_cache, "_MAX_ENTRIES", 1)
    first = tmp_path / "a.gguf"
    second = tmp_path / "b.gguf"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    path = tmp_path / "cache.json"
    cache = PrecisionCache(path)
    cache.remember(first, _precision("F16 全量，未量化"))
    cache.remember(second, _precision())
    cache.save()

    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert len(entries) == 1
    assert next(iter(entries.values()))["label"] == "Q4_K_M 量化"


def test_save_write_failure_is_silent(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"x")
    cache = PrecisionCache(blocker / "cache.json")
    cache.remember(model, _precision())
    cache.save()
    assert blocker.is_file()
