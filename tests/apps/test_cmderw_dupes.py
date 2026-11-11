"""重复文件查找纯逻辑测试：大小预筛、哈希确认、删除/移动计划。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.cmderw._cmderw_dupes import (
    DuplicateGroup,
    DuplicateReport,
    duplicate_paths,
    find_duplicates,
    move_duplicates,
)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class TestFindDuplicates:
    def test_groups_same_content(self, tmp_path: Path) -> None:
        write(tmp_path / "a.txt", b"same")
        write(tmp_path / "sub" / "b.txt", b"same")
        write(tmp_path / "c.txt", b"different")
        report = find_duplicates([tmp_path])
        assert len(report.groups) == 1
        group = report.groups[0]
        assert group.size == 4
        assert group.paths == (tmp_path / "a.txt", tmp_path / "sub" / "b.txt")
        assert group.wasted == 4
        assert report.wasted == 4
        assert report.scanned == 3

    def test_same_size_different_content_not_grouped(self, tmp_path: Path) -> None:
        write(tmp_path / "a.bin", b"abcd")
        write(tmp_path / "b.bin", b"abce")
        assert find_duplicates([tmp_path]).groups == ()

    def test_empty_files_skipped_by_default(self, tmp_path: Path) -> None:
        write(tmp_path / "a.txt", b"")
        write(tmp_path / "b.txt", b"")
        assert find_duplicates([tmp_path]).groups == ()
        included = find_duplicates([tmp_path], min_size=0)
        assert len(included.groups) == 1

    def test_unique_sizes_are_not_hashed(self, tmp_path: Path) -> None:
        write(tmp_path / "a.bin", b"aa")
        write(tmp_path / "b.bin", b"bbb")
        write(tmp_path / "c.bin", b"cc")
        events: list[str] = []
        find_duplicates([tmp_path], progress=lambda done, total, name: events.append(name))
        assert sorted(events) == ["a.bin", "c.bin"]

    def test_overlapping_roots_deduplicated(self, tmp_path: Path) -> None:
        write(tmp_path / "a.txt", b"same")
        write(tmp_path / "sub" / "b.txt", b"same")
        report = find_duplicates([tmp_path, tmp_path / "sub", tmp_path / "a.txt"])
        assert report.scanned == 2
        assert len(report.groups) == 1

    def test_bytes_skipped_by_traversal(self, tmp_path: Path) -> None:
        write(tmp_path / "a.pyc", b"cache")
        write(tmp_path / "b.pyc", b"cache")
        assert find_duplicates([tmp_path]).scanned == 0

    def test_missing_root(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="路径不存在"):
            find_duplicates([tmp_path / "missing"])

    def test_invalid_arguments(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="不支持的哈希算法"):
            find_duplicates([tmp_path], algorithm="nope")
        with pytest.raises(ValueError, match="最小大小"):
            find_duplicates([tmp_path], min_size=-1)

    def test_groups_sorted_by_wasted_bytes(self, tmp_path: Path) -> None:
        write(tmp_path / "big1.bin", b"x" * 100)
        write(tmp_path / "big2.bin", b"x" * 100)
        write(tmp_path / "small1.bin", b"y")
        write(tmp_path / "small2.bin", b"y")
        report = find_duplicates([tmp_path])
        assert [group.size for group in report.groups] == [100, 1]


class TestDuplicatePaths:
    def test_keeps_first_and_returns_extras(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        group = DuplicateGroup(4, "digest", (first, second))
        report = DuplicateReport((group,), 2, 4)
        assert duplicate_paths(report) == [second]

    def test_empty_report(self) -> None:
        assert duplicate_paths(DuplicateReport((), 0, 0)) == []


class TestMoveDuplicates:
    def test_moves_with_unique_names(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        target_dir = tmp_path / "dst"
        target_dir.mkdir()
        first = write(source_dir / "a.txt", b"1")
        second = write(source_dir / "sub" / "a.txt", b"2")
        (target_dir / "a.txt").write_bytes(b"occupied")
        events: list[tuple[int, int, str]] = []
        report = move_duplicates(
            [first, second],
            target_dir,
            progress=lambda done, total, name: events.append((done, total, name)),
        )
        assert report.completed == (first, second)
        assert report.errors == ()
        assert (target_dir / "a (2).txt").read_bytes() == b"1"
        assert (target_dir / "a (3).txt").read_bytes() == b"2"
        assert events[-1][0] == 2

    def test_errors_are_collected(self, tmp_path: Path) -> None:
        source_dir = tmp_path / "src"
        missing = source_dir / "gone.txt"
        target_dir = tmp_path / "dst"
        target_dir.mkdir()
        report = move_duplicates([missing], target_dir)
        assert report.completed == ()
        assert report.errors and report.errors[0][0] == missing

    def test_destination_must_be_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_bytes(b"x")
        with pytest.raises(NotADirectoryError, match="目标不是目录"):
            move_duplicates([], target)
