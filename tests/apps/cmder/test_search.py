"""搜索过滤与结果排序纯逻辑测试。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from fcmd.apps.cmder import _search as search


class TestParseSize:
    def test_plain_bytes(self) -> None:
        assert search.parse_size("2048") == 2048
        assert search.parse_size(" 1 ") == 1

    def test_units(self) -> None:
        assert search.parse_size("1k") == 1024
        assert search.parse_size("1.5M") == 1536 * 1024
        assert search.parse_size("2 GiB") == 2 * 1024**3

    def test_empty_is_none(self) -> None:
        assert search.parse_size("   ") is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="无法识别的大小"):
            search.parse_size("abc")
        with pytest.raises(ValueError, match="无法识别的大小"):
            search.parse_size("12x")


class TestParseDatetime:
    def test_start_of_day(self) -> None:
        stamp = search.parse_datetime("2026-01-02")
        assert stamp == datetime(2026, 1, 2).timestamp()

    def test_end_of_day(self) -> None:
        stamp = search.parse_datetime("2026-01-02", end_of_day=True)
        assert stamp == datetime(2026, 1, 2, 23, 59, 59).timestamp()

    def test_empty_is_none(self) -> None:
        assert search.parse_datetime("") is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            search.parse_datetime("2026/01/02")


class TestFilters:
    def test_inactive_filters_pass_everything(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        filters = search.SearchFilters()
        assert not filters.active
        assert search.filter_paths([target], filters) == [target]

    def test_size_bounds_are_inclusive(self, tmp_path: Path) -> None:
        tiny = tmp_path / "tiny.txt"
        tiny.write_bytes(b"1")
        small = tmp_path / "small.txt"
        small.write_bytes(b"123")
        big = tmp_path / "big.txt"
        big.write_bytes(b"123456")
        filters = search.SearchFilters(min_size=3, max_size=5)
        assert search.filter_paths([tiny, small, big], filters) == [small]

    def test_time_bounds(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        stamp = target.stat().st_mtime
        assert search.filter_paths([target], search.SearchFilters(modified_after=stamp - 10))
        assert not search.filter_paths([target], search.SearchFilters(modified_after=stamp + 10))
        assert not search.filter_paths([target], search.SearchFilters(modified_before=stamp - 10))

    def test_missing_path_is_dropped(self, tmp_path: Path) -> None:
        ghost = tmp_path / "ghost"
        assert not search.path_matches(ghost, search.SearchFilters(min_size=0))


class TestResults:
    def _results(self, tmp_path: Path) -> list[search.SearchResult]:
        first = tmp_path / "b.txt"
        first.write_bytes(b"12")
        second = tmp_path / "A.txt"
        second.write_bytes(b"1234")
        return search.collect_results([first, second, first])

    def test_collect_dedupes_and_fills_stat(self, tmp_path: Path) -> None:
        results = self._results(tmp_path)
        assert [result.path.name for result in results] == ["b.txt", "A.txt"]
        assert [result.size for result in results] == [2, 4]
        assert all(result.modified > 0 for result in results)

    def test_missing_path_uses_zero_stat(self, tmp_path: Path) -> None:
        result = search.collect_results([tmp_path / "ghost"])[0]
        assert (result.size, result.modified) == (0, 0.0)

    def test_sort_by_name_is_case_insensitive(self, tmp_path: Path) -> None:
        assert [item.path.name for item in search.sort_results(self._results(tmp_path))] == ["A.txt", "b.txt"]

    def test_sort_by_size_and_reverse(self, tmp_path: Path) -> None:
        assert [item.size for item in search.sort_results(self._results(tmp_path), "size")] == [2, 4]
        assert [item.size for item in search.sort_results(self._results(tmp_path), "size", reverse=True)] == [4, 2]

    def test_sort_unknown_column_falls_back_to_name(self, tmp_path: Path) -> None:
        assert [item.path.name for item in search.sort_results(self._results(tmp_path), "unknown")] == [
            "A.txt",
            "b.txt",
        ]

    def test_sort_by_path_and_modified(self, tmp_path: Path) -> None:
        results = self._results(tmp_path)
        assert [item.path.name for item in search.sort_results(results, "path")] == ["A.txt", "b.txt"]
        modified = sorted(results, key=lambda item: item.modified)
        assert [item.path.name for item in search.sort_results(results, "modified")] == [
            item.path.name for item in modified
        ]

    def test_sort_columns_are_labelled(self) -> None:
        assert set(search.SORT_COLUMNS) == {"name", "size", "modified", "path"}


def test_filter_rejects_unreadable_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    def broken(_path: Path) -> os.stat_result:
        raise OSError("denied")

    monkeypatch.setattr(Path, "stat", broken)
    assert not search.path_matches(target, search.SearchFilters(min_size=0))
