"""filesync 核心逻辑测试。

覆盖扫描（过滤 / 隐藏 / 错误）、三种比较方式、同步计划（双向 / 镜像 /
更新 / 冲突）、执行（复制 / 删除 / 试运行 / 失败）与文本报告。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.filesync import _core
from fcmd.apps.filesync._core import (
    ACTION_COPY_LTR,
    ACTION_COPY_RTL,
    ACTION_DELETE,
    ACTION_NONE,
    CATEGORY_CONFLICT,
    CATEGORY_DIFFERENT,
    CATEGORY_EQUAL,
    CATEGORY_LEFT_ONLY,
    CATEGORY_RIGHT_ONLY,
    COMPARE_CONTENT,
    COMPARE_SIZE,
    COMPARE_TIME_SIZE,
    SYNC_MIRROR,
    SYNC_TWO_WAY,
    SYNC_UPDATE,
    CompareOptions,
    CompareResult,
    DiffItem,
    FileEntry,
    ScanError,
    SyncAction,
    SyncPlan,
    build_sync_plan,
    compare_trees,
    content_equal,
    execute_plan,
    format_compare_report,
    format_scan_errors,
    format_size,
    format_sync_report,
    normalize_compare_mode,
    normalize_sync_mode,
    parse_filter_text,
    scan_tree,
)


def _write(root: Path, relative: str, data: bytes = b"x", mtime: float | None = None) -> Path:
    """写入测试文件（自动建父目录，可选固定修改时间）。"""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    """创建左右空目录。"""
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    return left, right


def _entry(relative: str = "a.txt", size: int = 10, mtime: float = 100.0) -> FileEntry:
    return FileEntry(relative=relative, size=size, mtime=mtime)


def _result(
    items: list[DiffItem],
    mode: str = COMPARE_TIME_SIZE,
    tolerance: float = 2.0,
) -> CompareResult:
    return CompareResult(
        left=Path("/left"),
        right=Path("/right"),
        items=items,
        options=CompareOptions(mode=mode, time_tolerance=tolerance),
    )


# ============================================================================ #
# 扫描
# ============================================================================ #
class TestScan:
    """目录扫描与过滤。"""

    def test_collects_sorted_files(self, tmp_path: Path) -> None:
        _write(tmp_path, "b.txt", b"bb")
        _write(tmp_path, "a.txt", b"a")
        _write(tmp_path, "sub/c.bin", b"ccc")
        result = scan_tree(tmp_path)
        assert sorted(result.entries) == ["a.txt", "b.txt", "sub/c.bin"]
        assert result.entries["sub/c.bin"].size == 3
        assert result.entries["sub/c.bin"].relative == "sub/c.bin"
        assert result.entries["a.txt"].mtime > 0
        assert result.errors == []

    def test_missing_root_reports_error(self, tmp_path: Path) -> None:
        result = scan_tree(tmp_path / "missing")
        assert result.entries == {}
        assert len(result.errors) == 1
        assert "目录不存在" in result.errors[0].message

    def test_include_and_exclude(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py")
        _write(tmp_path, "b.txt")
        _write(tmp_path, "sub/c.py")
        options = CompareOptions(include=("*.py",), exclude=("sub/*",))
        result = scan_tree(tmp_path, options)
        assert sorted(result.entries) == ["a.py"]

    def test_skip_hidden_files_and_dirs(self, tmp_path: Path) -> None:
        _write(tmp_path, ".hidden")
        _write(tmp_path, ".git/config")
        _write(tmp_path, "visible.txt")
        result = scan_tree(tmp_path, CompareOptions(skip_hidden=True))
        assert sorted(result.entries) == ["visible.txt"]

    def test_walk_error_is_collected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``os.walk`` 的 onerror 回调写入错误而不中断。"""

        def fake_walk(root: Path, onerror: Any = None, **_kwargs: Any) -> Any:
            if onerror is not None:
                onerror(OSError(13, "permission denied", str(Path(root) / "locked")))
            return iter(())

        monkeypatch.setattr(_core.os, "walk", fake_walk)
        result = scan_tree(tmp_path)
        assert len(result.errors) == 1
        assert result.errors[0].relative == "locked"

    def test_walk_error_outside_root_uses_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """错误路径不在扫描根下时回落到文件名。"""

        def fake_walk(root: Path, onerror: Any = None, **_kwargs: Any) -> Any:
            if onerror is not None:
                onerror(OSError(13, "permission denied", "/elsewhere/locked"))
            return iter(())

        monkeypatch.setattr(_core.os, "walk", fake_walk)
        result = scan_tree(tmp_path)
        assert result.errors[0].relative == "locked"

    def test_stat_error_is_collected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """单文件 stat 失败时跳过该文件，其余文件照常收集。"""
        _write(tmp_path, "bad.txt")
        _write(tmp_path, "good.txt")
        original = Path.stat

        def fake_stat(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
            if self.name == "bad.txt":
                raise OSError("denied")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", fake_stat)
        result = scan_tree(tmp_path)
        assert sorted(result.entries) == ["good.txt"]
        assert [error.relative for error in result.errors] == ["bad.txt"]


class TestContentEqual:
    """内容比较。"""

    def test_equal_and_different(self, tmp_path: Path) -> None:
        left = _write(tmp_path, "left.bin", b"0123456789" * 10)
        right = _write(tmp_path, "right.bin", b"0123456789" * 10)
        assert content_equal(left, right, chunk_size=7)
        right.write_bytes(b"0123456789" * 9 + b"x")
        assert not content_equal(left, right, chunk_size=7)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        left = _write(tmp_path, "left.bin", b"x")
        with pytest.raises(OSError):
            content_equal(left, tmp_path / "missing.bin")


# ============================================================================ #
# 比较
# ============================================================================ #
class TestCompare:
    """三种比较方式与错误记录。"""

    def test_side_only_and_counts(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "only-left.txt", b"a")
        _write(right, "only-right.txt", b"b")
        _write(left, "common.txt", b"same")
        _write(right, "common.txt", b"same")
        result = compare_trees(left, right)
        categories = {item.relative: item.category for item in result.items}
        assert categories == {
            "common.txt": CATEGORY_EQUAL,
            "only-left.txt": CATEGORY_LEFT_ONLY,
            "only-right.txt": CATEGORY_RIGHT_ONLY,
        }
        assert result.counts() == {
            CATEGORY_LEFT_ONLY: 1,
            CATEGORY_RIGHT_ONLY: 1,
            CATEGORY_DIFFERENT: 0,
            CATEGORY_EQUAL: 1,
            CATEGORY_CONFLICT: 0,
        }
        assert result.count(CATEGORY_LEFT_ONLY) == 1
        assert [item.relative for item in result.category_items(CATEGORY_RIGHT_ONLY)] == ["only-right.txt"]

    def test_size_mode_ignores_time_and_content(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "a.bin", b"abc", mtime=1000)
        _write(right, "a.bin", b"xyz", mtime=2000)
        result = compare_trees(left, right, CompareOptions(mode=COMPARE_SIZE))
        assert result.items[0].category == CATEGORY_EQUAL

    def test_time_mode_tolerance_and_difference(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "a.bin", b"abc", mtime=1000)
        _write(right, "a.bin", b"abc", mtime=1001)
        assert compare_trees(left, right).items[0].category == CATEGORY_EQUAL
        os.utime(right / "a.bin", (1010, 1010))
        item = compare_trees(left, right).items[0]
        assert item.category == CATEGORY_DIFFERENT
        assert item.detail == "修改时间不同"

    def test_content_mode(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "a.bin", b"abc", mtime=1000)
        _write(right, "a.bin", b"abc", mtime=5000)
        options = CompareOptions(mode=COMPARE_CONTENT)
        assert compare_trees(left, right, options).items[0].category == CATEGORY_EQUAL
        _write(right, "a.bin", b"xyz", mtime=5000)
        item = compare_trees(left, right, options).items[0]
        assert item.category == CATEGORY_DIFFERENT
        assert item.detail == "内容不同"

    def test_content_read_error_is_recorded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        left, right = _pair(tmp_path)
        _write(left, "a.bin", b"abc")
        _write(right, "a.bin", b"abc")

        def boom(_left: Path, _right: Path, _chunk: int = 0) -> bool:
            raise OSError("read failed")

        monkeypatch.setattr(_core, "content_equal", boom)
        result = compare_trees(left, right, CompareOptions(mode=COMPARE_CONTENT))
        assert result.items[0].category == CATEGORY_DIFFERENT
        assert "内容比较失败" in result.errors[0].message

    def test_missing_side_errors_carry_side(self, tmp_path: Path) -> None:
        right = tmp_path / "right"
        _write(right, "a.txt", b"a")
        result = compare_trees(tmp_path / "missing", right)
        assert result.errors[0].side == "left"
        assert result.items[0].category == CATEGORY_RIGHT_ONLY

    def test_invalid_mode_raises(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        with pytest.raises(ValueError):
            compare_trees(left, right, CompareOptions(mode="fast"))


# ============================================================================ #
# 参数规范化
# ============================================================================ #
class TestNormalization:
    """模式与过滤文本规范化。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("TIME_SIZE", COMPARE_TIME_SIZE), (" content ", COMPARE_CONTENT), ("size", COMPARE_SIZE)],
    )
    def test_compare_mode_valid(self, value: str, expected: str) -> None:
        assert normalize_compare_mode(value) == expected

    def test_compare_mode_invalid(self) -> None:
        with pytest.raises(ValueError):
            normalize_compare_mode("fast")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("TWO_WAY", SYNC_TWO_WAY), (" mirror ", SYNC_MIRROR), ("update", SYNC_UPDATE)],
    )
    def test_sync_mode_valid(self, value: str, expected: str) -> None:
        assert normalize_sync_mode(value) == expected

    def test_sync_mode_invalid(self) -> None:
        with pytest.raises(ValueError):
            normalize_sync_mode("bidirectional")

    def test_parse_filter_text(self) -> None:
        assert parse_filter_text("*.py;\n *.md ;; ") == ("*.py", "*.md")
        assert parse_filter_text("") == ()

    @pytest.mark.parametrize(
        ("size", "expected"),
        [(0, "0 B"), (1023, "1023 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB"), (2**31, "2.0 GB")],
    )
    def test_format_size(self, size: int, expected: str) -> None:
        assert format_size(size) == expected


# ============================================================================ #
# 同步计划
# ============================================================================ #
class TestSyncPlan:
    """三种同步模式的计划生成。"""

    def test_mirror_plan(self) -> None:
        items = [
            DiffItem("left.txt", CATEGORY_LEFT_ONLY, _entry("left.txt", 7), None),
            DiffItem("right.txt", CATEGORY_RIGHT_ONLY, None, _entry("right.txt", 8)),
            DiffItem("diff.txt", CATEGORY_DIFFERENT, _entry("diff.txt", 9), _entry("diff.txt", 5)),
            DiffItem("same.txt", CATEGORY_EQUAL, _entry("same.txt"), _entry("same.txt")),
        ]
        plan = build_sync_plan(_result(items), SYNC_MIRROR)
        assert [action.kind for action in plan.actions] == [ACTION_COPY_LTR, ACTION_DELETE, ACTION_COPY_LTR]
        assert plan.copy_count == 2
        assert plan.delete_count == 1
        assert plan.bytes_to_copy == 7 + 9
        assert plan.conflicts == []
        copy_action = plan.actions[0]
        assert copy_action.source is not None and copy_action.source.name == "left.txt"
        assert copy_action.target is not None and copy_action.target.name == "left.txt"
        assert plan.actions[1].reason == "镜像模式删除右侧多余文件"
        assert plan.actions[1].action_text == "删除右侧"

    def test_update_plan_only_copies_newer_left(self) -> None:
        items = [
            DiffItem("right.txt", CATEGORY_RIGHT_ONLY, None, _entry("right.txt")),
            DiffItem("newer.txt", CATEGORY_DIFFERENT, _entry("newer.txt", mtime=200), _entry("newer.txt", mtime=100)),
            DiffItem("older.txt", CATEGORY_DIFFERENT, _entry("older.txt", mtime=100), _entry("older.txt", mtime=200)),
        ]
        plan = build_sync_plan(_result(items), SYNC_UPDATE)
        assert [action.relative for action in plan.actions] == ["newer.txt"]
        assert plan.actions[0].reason == "更新模式左侧较新"
        assert plan.conflicts == []

    def test_two_way_plan_and_conflict(self) -> None:
        items = [
            DiffItem("left.txt", CATEGORY_LEFT_ONLY, _entry("left.txt"), None),
            DiffItem("right.txt", CATEGORY_RIGHT_ONLY, None, _entry("right.txt")),
            DiffItem(
                "left-newer.txt",
                CATEGORY_DIFFERENT,
                _entry("left-newer.txt", mtime=300),
                _entry("left-newer.txt", mtime=100),
            ),
            DiffItem(
                "right-newer.txt",
                CATEGORY_DIFFERENT,
                _entry("right-newer.txt", mtime=100),
                _entry("right-newer.txt", mtime=300),
            ),
            DiffItem("conflict.txt", CATEGORY_DIFFERENT, _entry("conflict.txt", 5), _entry("conflict.txt", 9)),
        ]
        plan = build_sync_plan(_result(items), SYNC_TWO_WAY)
        assert [action.kind for action in plan.actions] == [
            ACTION_COPY_LTR,
            ACTION_COPY_RTL,
            ACTION_COPY_LTR,
            ACTION_COPY_RTL,
        ]
        assert [action.reason for action in plan.actions] == ["仅左侧存在", "仅右侧存在", "左侧较新", "右侧较新"]
        assert [item.relative for item in plan.conflicts] == ["conflict.txt"]

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            build_sync_plan(_result([]), "mirror-only")


# ============================================================================ #
# 执行
# ============================================================================ #
class TestExecute:
    """复制 / 删除 / 试运行 / 失败收集。"""

    def test_mirror_execution(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "copy.txt", b"payload")
        _write(right, "remove.txt", b"x")
        plan = build_sync_plan(compare_trees(left, right), SYNC_MIRROR)
        report = execute_plan(plan)
        assert (right / "copy.txt").read_bytes() == b"payload"
        assert not (right / "remove.txt").exists()
        assert report.copied == 1
        assert report.deleted == 1
        assert report.bytes_copied == 7
        assert report.errors == []
        assert report.success

    def test_dry_run_does_not_touch_filesystem(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "copy.txt", b"payload")
        _write(right, "remove.txt", b"x")
        plan = build_sync_plan(compare_trees(left, right), SYNC_MIRROR)
        report = execute_plan(plan, dry_run=True)
        assert not (right / "copy.txt").exists()
        assert (right / "remove.txt").exists()
        assert report.dry_run
        assert report.copied == 1
        assert report.deleted == 1
        assert report.bytes_copied == 7

    def test_progress_callback(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "a.txt", b"a")
        plan = build_sync_plan(compare_trees(left, right), SYNC_MIRROR)
        events: list[tuple[int, int, str]] = []
        execute_plan(plan, progress=lambda done, total, action: events.append((done, total, action.kind)))
        assert events == [(1, 1, ACTION_COPY_LTR)]

    def test_copy_failure_is_recorded(self, tmp_path: Path) -> None:
        action = SyncAction(
            kind=ACTION_COPY_LTR,
            relative="missing.txt",
            source=tmp_path / "missing.txt",
            target=tmp_path / "target" / "missing.txt",
            size=1,
        )
        report = execute_plan(SyncPlan(mode=SYNC_MIRROR, actions=[action]))
        assert report.copied == 0
        assert not report.success
        assert report.errors[0].relative == "missing.txt"

    def test_delete_failure_is_recorded(self, tmp_path: Path) -> None:
        action = SyncAction(
            kind=ACTION_DELETE,
            relative="gone.txt",
            source=None,
            target=tmp_path / "gone.txt",
        )
        report = execute_plan(SyncPlan(mode=SYNC_MIRROR, actions=[action]))
        assert report.deleted == 0
        assert not report.success
        assert report.errors[0].relative == "gone.txt"

    def test_action_without_paths_fails(self) -> None:
        action = SyncAction(kind=ACTION_COPY_LTR, relative="a.txt", source=None, target=None)
        report = execute_plan(SyncPlan(mode=SYNC_MIRROR, actions=[action]))
        assert not report.success
        assert "缺少源或目标路径" in report.errors[0].message
        assert _core.ACTION_TEXT[ACTION_NONE] == "-"

    def test_delete_action_without_target_fails(self) -> None:
        action = SyncAction(kind=ACTION_DELETE, relative="a.txt", source=None, target=None)
        report = execute_plan(SyncPlan(mode=SYNC_MIRROR, actions=[action]))
        assert not report.success
        assert "缺少目标路径" in report.errors[0].message

    def test_copy_creates_parent_directories(self, tmp_path: Path) -> None:
        source = _write(tmp_path, "src/a.txt", b"abc")
        action = SyncAction(
            kind=ACTION_COPY_LTR,
            relative="nested/a.txt",
            source=source,
            target=tmp_path / "out" / "nested" / "a.txt",
            size=3,
        )
        report = execute_plan(SyncPlan(mode=SYNC_MIRROR, actions=[action]))
        assert report.success
        assert (tmp_path / "out" / "nested" / "a.txt").read_bytes() == b"abc"


# ============================================================================ #
# 文本报告
# ============================================================================ #
class TestReports:
    """比较 / 同步报告文本。"""

    def _compare_result(self, tmp_path: Path) -> CompareResult:
        left, right = _pair(tmp_path)
        _write(left, "same.txt", b"a")
        _write(right, "same.txt", b"a")
        _write(left, "only-left.txt", b"a")
        return compare_trees(left, right)

    def test_compare_report_hides_equal_by_default(self, tmp_path: Path) -> None:
        text = format_compare_report(self._compare_result(tmp_path))
        assert "左侧:" in text
        assert "仅左侧" in text
        assert "same.txt" not in text
        assert "only-left.txt" in text

    def test_compare_report_shows_equal_on_request(self, tmp_path: Path) -> None:
        text = format_compare_report(self._compare_result(tmp_path), show_equal=True)
        assert "same.txt" in text
        assert "相同" in text

    def test_compare_report_no_differences(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        _write(left, "same.txt", b"a")
        _write(right, "same.txt", b"a")
        assert "(没有差异)" in format_compare_report(compare_trees(left, right))

    def test_compare_report_empty_trees(self, tmp_path: Path) -> None:
        left, right = _pair(tmp_path)
        assert "(两侧均为空)" in format_compare_report(compare_trees(left, right))

    def test_compare_report_errors(self, tmp_path: Path) -> None:
        result = compare_trees(tmp_path / "missing", tmp_path)
        text = format_compare_report(result)
        assert "扫描错误" in text
        assert "左侧" in text
        assert format_scan_errors(result.errors).startswith("  - 左侧")

    def test_sync_report(self) -> None:
        report = execute_plan(
            SyncPlan(
                mode=SYNC_MIRROR,
                actions=[
                    SyncAction(
                        kind=ACTION_COPY_LTR,
                        relative="a.txt",
                        source=None,
                        target=None,
                    )
                ],
            )
        )
        text = format_sync_report(report)
        assert "同步完成：复制 0 项" in text
        assert "失败 1 项" in text
        assert "失败明细" in text

    def test_sync_report_dry_run(self) -> None:
        report = execute_plan(SyncPlan(mode=SYNC_MIRROR), dry_run=True)
        assert format_sync_report(report).startswith("试运行：复制 0 项")


def test_scan_error_default_side() -> None:
    """未指定 side 的扫描错误按空串处理（展示层回落到「扫描」）。"""
    error = ScanError("a.txt", "denied")
    assert error.side == ""
    assert "扫描 a.txt" in format_scan_errors([error])
