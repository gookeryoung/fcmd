"""GGUF 精度缓存：命中、失效与损坏降级。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fcmd.apps.locllm._llmgguf import GgufPrecision
from fcmd.apps.locllm._llmgguf_cache import GGUF_CACHE_SCHEMA_VERSION, PrecisionCache


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
