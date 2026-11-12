"""filescan 缓存测试：复用与失效、指纹口径、损坏自愈与进程池写入。"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from fcmd.apps.filescan import _engine
from fcmd.apps.filescan._cache import (
    CACHE_SCHEMA_VERSION,
    ScanCache,
    cache_fingerprint,
    default_cache_path,
)
from fcmd.apps.filescan._engine import ScanOptions, ScanResult, scan_paths
from fcmd.apps.filescan._rules import MATCH_KEYWORD, Matcher, Rule, RuleSet


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _ruleset(expression: str = "") -> RuleSet:
    rules = (
        Rule(id="phone", name="手机号", matcher=Matcher(kind=MATCH_KEYWORD, text="13812345678")),
        Rule(id="name", name="姓名", matcher=Matcher(kind=MATCH_KEYWORD, text="张三")),
    )
    return RuleSet(name="测试规则", rules=rules, expression=expression)


@pytest.fixture
def cache(tmp_path: Path) -> ScanCache:
    store = ScanCache(tmp_path / "cache" / "filescan.db")
    assert store.enabled
    return store


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    _write(root, "customer.txt", "姓名 张三\n手机 13812345678\n")
    _write(root, "clean.txt", "没有任何敏感内容\n")
    _write(root, "sub/log.txt", "无关内容\n")
    return root


def _scan(
    root: Path,
    store: ScanCache,
    ruleset: RuleSet | None = None,
    options: ScanOptions | None = None,
) -> ScanResult:
    rules = ruleset or _ruleset()
    opts = options or ScanOptions(workers=1)
    return scan_paths([root], rules, opts, cache=store, cache_key=cache_fingerprint(rules, opts))


def test_second_scan_reuses_unchanged_files(cache: ScanCache, corpus: Path) -> None:
    """未变化的文件直接复用：实扫 0、字节 0，命中明细保持一致。"""
    first = _scan(corpus, cache)
    assert first.stats.files_scanned == 3
    assert first.stats.files_reused == 0
    assert first.stats.hit_files == 1

    second = _scan(corpus, cache)
    assert second.stats.files_scanned == 0
    assert second.stats.bytes_read == 0
    assert second.stats.files_reused == 3
    assert second.stats.files_seen == 3
    assert second.stats.hit_files == first.stats.hit_files
    assert second.stats.hit_count == first.stats.hit_count
    assert [(str(hit.path), [(m.rule_id, m.line_no, m.excerpt) for m in hit.matches]) for hit in second.hits] == [
        (str(hit.path), [(m.rule_id, m.line_no, m.excerpt) for m in hit.matches]) for hit in first.hits
    ]


def test_no_hit_file_is_cached_without_inflating_hits(cache: ScanCache, corpus: Path) -> None:
    """「实扫过但无命中」也缓存，且不会把空命中算进命中文件数。"""
    first = _scan(corpus, cache)
    second = _scan(corpus, cache)
    assert second.stats.files_reused == first.stats.files_seen
    assert second.stats.hit_files == 1
    assert all(hit.matches for hit in second.hits)


def test_size_change_invalidates_file(cache: ScanCache, corpus: Path) -> None:
    """文件大小变化必须重扫，其它文件继续复用。"""
    _scan(corpus, cache)
    target = corpus / "clean.txt"
    target.write_text("文件变长了，且包含 张三 与 13812345678\n", encoding="utf-8")

    result = _scan(corpus, cache)
    assert result.stats.files_reused == 2
    assert result.stats.files_scanned == 1
    assert result.stats.hit_files == 2
    assert any(hit.path == target for hit in result.hits)


def test_mtime_change_invalidates_file(cache: ScanCache, corpus: Path) -> None:
    """内容与大小都不变、只改修改时间，也必须重扫。"""
    _scan(corpus, cache)
    target = corpus / "clean.txt"
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    result = _scan(corpus, cache)
    assert result.stats.files_scanned == 1
    assert result.stats.files_reused == 2


def test_content_change_with_same_size_invalidates_via_mtime(cache: ScanCache, corpus: Path) -> None:
    """内容变化（大小相同）会更新 mtime，缓存据此失效，不返回陈旧结果。"""
    _scan(corpus, cache)
    target = corpus / "clean.txt"
    stat = target.stat()
    # 等长改写：字节数不变，只有内容与 mtime 变化，缓存必须据此失效。
    target.write_text("13812345678" + " " * 13 + "\n", encoding="utf-8")
    assert target.stat().st_size == stat.st_size
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))  # 保证纳秒级差异

    result = _scan(corpus, cache)
    assert result.stats.files_scanned == 1
    assert result.stats.hit_files == 2  # 不含陈旧结果，新命中也被算进来


def test_different_cache_key_misses(cache: ScanCache, corpus: Path) -> None:
    """指纹变化（规则或命中相关选项变化）后整批失效。"""
    _scan(corpus, cache)
    renamed = replace(_ruleset(), rules=tuple(replace(rule, name=f"{rule.name}（改名）") for rule in _ruleset().rules))
    options = ScanOptions(workers=1)
    result = scan_paths([corpus], renamed, options, cache=cache, cache_key=cache_fingerprint(renamed, options))
    assert result.stats.files_reused == 0
    assert result.stats.files_scanned == 3


def test_clear_removes_entries_and_forces_rescan(cache: ScanCache, corpus: Path) -> None:
    """清理后条目归零，下一次扫描全部实扫。"""
    _scan(corpus, cache)
    assert cache.clear() == 3
    assert cache.clear() == 0

    result = _scan(corpus, cache)
    assert result.stats.files_reused == 0
    assert result.stats.files_scanned == 3


def test_lookup_and_store_without_cache_key_are_noops(cache: ScanCache, corpus: Path) -> None:
    """空指纹表示不使用缓存：不查也不写。"""
    cache.store_many("", [(corpus / "clean.txt", 1, 1, None)])
    assert cache.lookup_many("", [(corpus / "clean.txt", 1, 1)]) == {}
    assert cache.clear() == 0


def test_process_pool_writes_cache_from_main_process(
    cache: ScanCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """进程池调度下 worker 不碰数据库，主进程负责读写，第二次全部复用。"""
    monkeypatch.setattr(_engine, "PROCESS_MIN_FILES", 1)
    monkeypatch.setattr(_engine, "PROCESS_MIN_BYTES", 1)
    root = tmp_path / "many"
    for index in range(6):
        _write(root, f"file{index}.txt", f"姓名 张三 13812345678 #{index}\n")
    options = ScanOptions(workers=2)

    first = _scan(root, cache, options=options)
    assert first.stats.files_scanned == 6
    assert first.stats.files_reused == 0

    second = _scan(root, cache, options=options)
    assert second.stats.files_scanned == 0
    assert second.stats.files_reused == 6
    assert second.stats.hit_files == 6


def test_corrupt_database_is_rebuilt(tmp_path: Path) -> None:
    """数据库损坏时删库重建，扫描可继续使用缓存。"""
    path = tmp_path / "cache" / "filescan.db"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a sqlite database" * 100)

    store = ScanCache(path)
    assert store.enabled
    assert store.error is not None
    assert "重建" in store.error
    store.store_many("k", [(tmp_path / "a.txt", 3, 4, None)])
    assert store.lookup_many("k", [(tmp_path / "a.txt", 3, 4)]) == {tmp_path / "a.txt": None}
    store.close()


def test_read_error_rebuilds_and_retries(tmp_path: Path) -> None:
    """读取中途发现损坏：删库重建后重试，缓存恢复可用。"""
    path = tmp_path / "cache" / "filescan.db"
    store = ScanCache(path)
    store.store_many("k", [(tmp_path / "a.txt", 1, 1, None)])
    store.close()

    path.write_bytes(b"garbage" * 500)
    store._conn = sqlite3.connect(str(path))  # 模拟已连接但文件被替换成垃圾
    assert store.lookup_many("k", [(tmp_path / "a.txt", 1, 1)]) == {}
    assert store.enabled
    assert store.error is not None
    assert "重建" in store.error

    store.store_many("k", [(tmp_path / "b.txt", 2, 2, None)])
    assert store.lookup_many("k", [(tmp_path / "b.txt", 2, 2)]) == {tmp_path / "b.txt": None}
    store.close()


def test_schema_version_mismatch_rebuilds_entries(cache: ScanCache, tmp_path: Path) -> None:
    """表结构版本不符时清空旧条目后重建。"""
    path = cache.path
    cache.store_many("k", [(tmp_path / "a.txt", 3, 4, None)])
    cache.close()
    with sqlite3.connect(str(path)) as conn:
        conn.execute("UPDATE meta SET value = ? WHERE key = 'schema'", (str(CACHE_SCHEMA_VERSION + 1),))
        conn.commit()

    reopened = ScanCache(path)
    assert reopened.enabled
    assert reopened.lookup_many("k", [(tmp_path / "a.txt", 3, 4)]) == {}
    assert reopened.clear() == 0
    reopened.close()


def test_unusable_path_disables_cache_without_raising(tmp_path: Path) -> None:
    """路径不可写时降级为禁用缓存，读写与清理都安全返回。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    store = ScanCache(blocker / "cache.db")
    assert store.enabled is False
    assert store.error is not None
    store.store_many("k", [(tmp_path / "a.txt", 1, 1, None)])
    assert store.lookup_many("k", [(tmp_path / "a.txt", 1, 1)]) == {}
    assert store.clear() == 0
    store.close()


def test_engine_tolerates_cache_errors(corpus: Path) -> None:
    """缓存抛异常时扫描照常完成（缓存不得中断扫描）。"""

    class Broken:
        def lookup_many(self, *_args: object) -> dict[Path, object]:
            raise sqlite3.DatabaseError("boom")

        def store_many(self, *_args: object) -> None:
            raise sqlite3.DatabaseError("boom")

    result = scan_paths([corpus], _ruleset(), ScanOptions(workers=1), cache=Broken(), cache_key="k")  # type: ignore[arg-type]
    assert result.stats.files_scanned == 3
    assert result.stats.hit_files == 1


@pytest.mark.parametrize(
    "options",
    [
        ScanOptions(workers=1, recursive=False),
        ScanOptions(workers=8, max_depth=2, follow_symlinks=True),
        ScanOptions(workers=1, ext_whitelist=(".txt",), include_exts=(".log",)),
        ScanOptions(workers=1, force_exts=(".dat",), exclude_exts=(".txt",)),
        ScanOptions(workers=1, min_size=10, max_size=100, modified_after=1.0, modified_before=2.0),
        ScanOptions(workers=1, use_processes=False),
    ],
)
def test_display_and_scope_options_keep_fingerprint(options: ScanOptions) -> None:
    """只影响「扫哪些文件」或调度方式的选项不改变指纹。"""
    base = ScanOptions(workers=1)
    assert cache_fingerprint(_ruleset(), options) == cache_fingerprint(_ruleset(), base)


@pytest.mark.parametrize(
    "options",
    [
        ScanOptions(workers=1, skip_binary=False),
        ScanOptions(workers=1, scan_documents=False),
        ScanOptions(workers=1, max_file_bytes=1024),
        ScanOptions(workers=1, encodings=("utf-8",)),
        ScanOptions(workers=1, context_chars=80),
        ScanOptions(workers=1, max_hits_per_file=1),
    ],
)
def test_hit_affecting_options_change_fingerprint(options: ScanOptions) -> None:
    """影响命中或命中明细的选项必须改变指纹。"""
    assert cache_fingerprint(_ruleset(), options) != cache_fingerprint(_ruleset(), ScanOptions(workers=1))


def test_rules_and_mask_change_fingerprint() -> None:
    """规则增删改、表达式变化与打码开关都改变指纹。"""
    base = cache_fingerprint(_ruleset(), ScanOptions())
    assert cache_fingerprint(_ruleset("phone"), ScanOptions()) != base
    assert cache_fingerprint(_ruleset(expression=""), ScanOptions(), mask=False) != base

    added = replace(_ruleset(), rules=(*_ruleset().rules, Rule("email", "邮箱", Matcher(MATCH_KEYWORD, text="邮箱"))))
    assert cache_fingerprint(added, ScanOptions()) != base

    removed = replace(_ruleset(), rules=_ruleset().rules[:1])
    assert cache_fingerprint(removed, ScanOptions()) != base

    changed = replace(_ruleset(), rules=(replace(_ruleset().rules[0], matcher=Matcher(MATCH_KEYWORD, text="139")),))
    assert cache_fingerprint(changed, ScanOptions()) != base

    disabled = replace(_ruleset(), rules=(replace(_ruleset().rules[0], enabled=False),))
    assert cache_fingerprint(disabled, ScanOptions()) != base


def test_default_cache_path_prefers_fcmd_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """默认路径：``${FCMD_HOME}/cache/filescan-cache.db``，回落到 ``~/.fcmd``。"""
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert default_cache_path() == tmp_path / "cache" / "filescan-cache.db"

    monkeypatch.delenv("FCMD_HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_cache_path() == Path.home() / ".fcmd" / "cache" / "filescan-cache.db"
