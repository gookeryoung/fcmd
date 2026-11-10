"""``_cmderw_ops`` 纯逻辑测试：通配选择、快速过滤、目录统计与回收站。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from fcmd.tools.system import _cmderw_ops as ops


# ============================================================================ #
# 掩码 / 过滤 / 反选
# ============================================================================ #
class TestMasks:
    def test_split_masks(self) -> None:
        """分号分隔、去空白、丢弃空段。"""
        assert ops.split_masks(" *.py ; ;*.txt ") == ("*.py", "*.txt")
        assert ops.split_masks("") == ()

    @pytest.mark.parametrize(
        ("name", "mask", "expected"),
        [
            ("a.py", "*.py", True),
            ("A.PY", "*.py", True),
            ("a.txt", "*.py", False),
            ("a.py", "*.py;*.txt", True),
            ("a.txt", "*.py;*.txt", True),
            ("a.md", "*.py;*.txt", False),
            ("ab", "a?", True),
            ("abc", "a?", False),
            ("any", "*", True),
        ],
    )
    def test_matches_mask(self, name: str, mask: str, expected: bool) -> None:
        assert ops.matches_mask(name, mask) is expected

    def test_matches_mask_empty(self) -> None:
        """空掩码不命中任何名称。"""
        assert ops.matches_mask("a.py", " ; ") is False

    def test_match_names_preserves_order(self) -> None:
        names = ["b.txt", "a.py", "c.txt"]
        assert ops.match_names(names, "*.txt") == ["b.txt", "c.txt"]

    def test_filter_names(self) -> None:
        """快速过滤是忽略大小写的子串匹配，空关键词返回全部。"""
        names = ["Readme.md", "report.txt", "data.bin"]
        assert ops.filter_names(names, "RE") == ["Readme.md", "report.txt"]
        assert ops.filter_names(names, "zzz") == []
        assert ops.filter_names(names, "  ") == names

    def test_invert_names(self) -> None:
        names = ["a", "b", "c"]
        assert ops.invert_names(names, ["b"]) == ["a", "c"]
        assert ops.invert_names(names, names) == []
        assert ops.invert_names(names, []) == names


# ============================================================================ #
# 目录统计
# ============================================================================ #
class TestDirectorySize:
    def test_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_bytes(b"12345")
        assert ops.directory_size(target) == (5, 1)

    def test_tree(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"123")
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "b.txt").write_bytes(b"12345")
        assert ops.directory_size(tmp_path) == (8, 2)

    def test_skips_symlinks(self, tmp_path: Path) -> None:
        """符号链接文件与目录都不计入，避免跨卷环。"""
        target = tmp_path / "real.txt"
        target.write_bytes(b"1234")
        (tmp_path / "link.txt").symlink_to(target)
        directory = tmp_path / "tree"
        directory.mkdir()
        (directory / "inner.txt").write_bytes(b"12")
        (tmp_path / "link_dir").symlink_to(directory, target_is_directory=True)
        assert ops.directory_size(tmp_path) == (6, 2)

    def test_missing_path(self, tmp_path: Path) -> None:
        assert ops.directory_size(tmp_path / "missing") == (0, 0)


# ============================================================================ #
# 回收站
# ============================================================================ #
class TestTrash:
    def test_trash_root_uses_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        assert ops.trash_root() == tmp_path / "xdg" / "Trash"

    def test_trash_root_defaults_to_local_share(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert ops.trash_root() == tmp_path / ".local" / "share" / "Trash"

    def test_linux_file_writes_trashinfo(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        source = tmp_path / "a b.txt"
        source.write_text("x", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert not source.exists()
        assert target == tmp_path / "xdg" / "Trash" / "files" / "a b.txt"
        assert target.read_text(encoding="utf-8") == "x"
        info = (tmp_path / "xdg" / "Trash" / "info" / "a b.txt.trashinfo").read_text(encoding="utf-8")
        assert info.startswith("[Trash Info]\n")
        assert f"Path={quote(str(source.resolve()), safe='/')}" in info
        assert "DeletionDate=" in info

    def test_linux_directory_and_name_conflict(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        files_dir = tmp_path / "xdg" / "Trash" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "dir").mkdir()
        (files_dir / "dir" / "old.txt").write_text("old", encoding="utf-8")
        source = tmp_path / "dir"
        source.mkdir()
        (source / "new.txt").write_text("new", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert target == files_dir / "dir (2)"
        assert (target / "new.txt").read_text(encoding="utf-8") == "new"
        assert (files_dir / "dir" / "old.txt").read_text(encoding="utf-8") == "old"
        assert (tmp_path / "xdg" / "Trash" / "info" / "dir (2).trashinfo").is_file()

    def test_linux_trashinfo_conflict_forces_unique_name(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """files 下已无同名，但 info 记录占用时仍要换名，避免覆盖历史记录。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        info_dir = tmp_path / "xdg" / "Trash" / "info"
        info_dir.mkdir(parents=True)
        (info_dir / "a.txt.trashinfo").write_text("[Trash Info]\n", encoding="utf-8")
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert target.name == "a (2).txt"
        assert (info_dir / "a.txt.trashinfo").read_text(encoding="utf-8") == "[Trash Info]\n"

    def test_missing_source(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ops.send_to_trash(tmp_path / "missing")

    def test_rejects_root(self) -> None:
        with pytest.raises(ValueError):
            ops.send_to_trash(Path(Path("/").anchor))

    def test_windows_dispatch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """win32 分支交给 SHFileOperationW 封装，并返回原路径。"""
        monkeypatch.setattr(sys, "platform", "win32")
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        calls: list[Path] = []
        monkeypatch.setattr(ops, "_trash_windows", calls.append)

        assert ops.send_to_trash(source) == source
        assert calls == [source]

    def test_macos_moves_to_home_trash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("HOME", str(tmp_path))
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert target == tmp_path / ".Trash" / "a.txt"
        assert target.read_text(encoding="utf-8") == "x"

    def test_trash_paths_reports_progress_and_errors(self, tmp_path: Path) -> None:
        good = tmp_path / "good.txt"
        bad = tmp_path / "bad.txt"
        good.write_text("x", encoding="utf-8")
        bad.write_text("y", encoding="utf-8")
        events: list[tuple[int, int, str]] = []

        def mover(path: Path) -> Path:
            if path.name == "bad.txt":
                raise OSError("拒绝访问")
            return path

        def record(done: int, total: int, name: str) -> None:
            events.append((done, total, name))

        report = ops.trash_paths([good, bad], progress=record, mover=mover)

        assert report.completed == [good]
        assert report.errors == [(bad, "拒绝访问")]
        assert events == [(1, 2, "good.txt"), (2, 2, "bad.txt")]


# ============================================================================ #
# 批量重命名
# ============================================================================ #
class TestBuildNewName:
    def test_find_replace_is_literal(self) -> None:
        source = ops.RenameSource("IMG_001.png")
        assert ops.build_new_name(source, 1, ops.BulkRenameOptions(find="IMG", replace="photo")) == "photo_001.png"

    def test_find_treats_regex_chars_literally(self) -> None:
        source = ops.RenameSource("a.b.txt")
        options = ops.BulkRenameOptions(find=".", replace="_")
        assert ops.build_new_name(source, 1, options) == "a_b_txt"

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            ("keep", "My File.TXT"),
            ("lower", "my file.TXT"),
            ("upper", "MY FILE.TXT"),
            ("title", "My File.TXT"),
        ],
    )
    def test_case_applies_to_stem_only(self, case: str, expected: str) -> None:
        assert ops.build_new_name(ops.RenameSource("My File.TXT"), 1, ops.BulkRenameOptions(case=case)) == expected

    def test_unknown_case_mode(self) -> None:
        with pytest.raises(ValueError, match="大小写"):
            ops.build_new_name(ops.RenameSource("a.txt"), 1, ops.BulkRenameOptions(case="weird"))

    @pytest.mark.parametrize(
        ("extension", "expected"),
        [
            ("", "a.txt"),
            ("-", "a"),
            ("md", "a.md"),
            (".tar.gz", "a.tar.gz"),
        ],
    )
    def test_extension(self, extension: str, expected: str) -> None:
        assert ops.build_new_name(ops.RenameSource("a.txt"), 1, ops.BulkRenameOptions(extension=extension)) == expected

    def test_template_with_number(self) -> None:
        options = ops.BulkRenameOptions(template="{name}_{n}", digits=3)
        assert ops.build_new_name(ops.RenameSource("report.txt"), 7, options) == "report_007"

    def test_template_with_extension(self) -> None:
        options = ops.BulkRenameOptions(template="{n}-{name}.{ext}", digits=2, case="lower")
        assert ops.build_new_name(ops.RenameSource("Report.TXT"), 3, options) == "03-report.TXT"

    def test_template_without_extension(self) -> None:
        options = ops.BulkRenameOptions(template="{name}.bak")
        assert ops.build_new_name(ops.RenameSource("README"), 1, options) == "README.bak"

    def test_invalid_template(self) -> None:
        with pytest.raises(ValueError, match="模板无效"):
            ops.build_new_name(ops.RenameSource("a.txt"), 1, ops.BulkRenameOptions(template="{unknown}"))

    def test_directory_does_not_split_extension(self) -> None:
        options = ops.BulkRenameOptions(find=".", replace="_")
        source = ops.RenameSource("v1.2", is_dir=True)
        assert ops.build_new_name(source, 1, options) == "v1_2"

    def test_number_start_step_digits(self) -> None:
        options = ops.BulkRenameOptions(template="{name}-{n}", start=10, step=5, digits=2)
        assert ops.build_new_name(ops.RenameSource("a.txt"), 20, options) == "a-20"


class TestRenamePlan:
    def test_unchanged_entries(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(tmp_path, [ops.RenameSource("a.txt")], ops.BulkRenameOptions(find="zzz"))
        assert plan.items[0].status == "unchanged"
        assert plan.changes == ()
        assert plan.conflicts == ()

    def test_duplicate_targets_are_conflicts(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt"), ops.RenameSource("b.txt")],
            ops.BulkRenameOptions(template="same"),
        )
        assert [item.status for item in plan.items] == ["ok", "conflict"]
        assert "同名" in plan.items[1].note

    def test_existing_target_is_conflict(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(template="b.txt"),
        )
        assert plan.items[0].status == "conflict"
        assert plan.items[0].note == "目标已存在"

    def test_invalid_name_is_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(template="bad:name"),
        )
        assert plan.items[0].status == "conflict"
        assert "不能包含" in plan.items[0].note

    def test_case_only_rename_is_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(case="upper"),
        )
        assert plan.items[0].status == "ok"

    def test_apply_only_executes_ok_items(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt"), ops.RenameSource("b.txt")],
            ops.BulkRenameOptions(find=".txt", replace=".md"),
        )
        assert not plan.conflicts

        report = ops.apply_rename_plan(plan)

        assert report.errors == []
        assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"
        assert (tmp_path / "b.md").read_text(encoding="utf-8") == "b"
        assert not (tmp_path / "a.txt").exists()

    def test_undo_round_trip(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text(name, encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt"), ops.RenameSource("b.txt")],
            ops.BulkRenameOptions(template="{name}.bak"),
        )
        report = ops.apply_rename_plan(plan)
        assert len(report.completed) == 2

        undone = ops.undo_rename(report)

        assert undone.errors == []
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a.txt"
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b.txt"
        assert not (tmp_path / "a.txt.bak").exists()

    def test_undo_reports_occupied_source(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(find=".txt", replace=".md"),
        )
        report = ops.apply_rename_plan(plan)
        (tmp_path / "a.txt").write_text("new", encoding="utf-8")

        undone = ops.undo_rename(report)

        assert undone.completed == []
        assert "已被占用" in undone.errors[0][1]
        assert (tmp_path / "a.md").exists()


# ============================================================================ #
# 传输队列
# ============================================================================ #
class TestTransferQueue:
    def test_add_assigns_incrementing_ids(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        first, conflicts = queue.add(tmp_path, [(tmp_path / "a.txt", tmp_path / "out" / "a.txt", False)], move=False)
        second, conflicts2 = queue.add(tmp_path, [(tmp_path / "b.txt", tmp_path / "out" / "b.txt", False)], move=True)

        assert conflicts == [] and conflicts2 == []
        assert first is not None and second is not None
        assert (first.task_id, second.task_id) == (1, 2)
        assert [task.task_id for task in queue.tasks] == [1, 2]
        assert first.action == "复制"
        assert second.action == "移动"

    def test_add_rejects_same_target_and_names_both_tasks(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        target = tmp_path / "out" / "a.txt"
        first, _ = queue.add(tmp_path, [(tmp_path / "a.txt", target, False)], move=False)
        assert first is not None

        rejected, conflicts = queue.add(tmp_path, [(tmp_path / "b.txt", target, False)], move=True)

        assert rejected is None
        assert len(conflicts) == 1
        message = conflicts[0]
        assert str(target) in message
        assert "#1" in message
        assert "新任务" in message and "移动" in message
        assert str(tmp_path) in message
        assert len(queue.tasks) == 1

    def test_conflict_check_ignores_finished_tasks(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        target = tmp_path / "out" / "a.txt"
        first, _ = queue.add(tmp_path, [(tmp_path / "a.txt", target, False)], move=False)
        assert first is not None
        queue.cancel(first.task_id)

        accepted, conflicts = queue.add(tmp_path, [(tmp_path / "b.txt", target, False)], move=False)

        assert conflicts == []
        assert accepted is not None

    def test_cancel_pending_marks_all_items_cancelled(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(
            tmp_path,
            [(tmp_path / "a.txt", tmp_path / "a.out", False), (tmp_path / "b.txt", tmp_path / "b.out", False)],
            move=False,
        )
        assert task is not None

        message = queue.cancel(task.task_id)

        assert "已取消（尚未开始）" in message
        assert task.status == ops.QUEUE_CANCELLED
        assert [item.status for item in task.items] == [ops.ITEM_CANCELLED, ops.ITEM_CANCELLED]

    def test_cancel_running_stops_after_current_item(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(
            tmp_path,
            [
                (tmp_path / "a.txt", tmp_path / "a.out", False),
                (tmp_path / "b.txt", tmp_path / "b.out", False),
            ],
            move=False,
        )
        assert task is not None
        queue.begin(task)
        queue.begin_item(task.items[0])
        queue.complete_item(task.items[0])

        message = queue.cancel(task.task_id)
        assert "当前文件完成后停止" in message
        assert task.cancel_requested is True
        assert task.items[0].status == ops.ITEM_DONE

        queue.cancel_remaining(task)
        status = queue.finish(task)

        assert status == ops.QUEUE_CANCELLED
        assert task.cancel_requested is False
        assert task.items[0].status == ops.ITEM_DONE
        assert task.items[1].status == ops.ITEM_CANCELLED

    def test_cancel_finished_task_is_noop(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(tmp_path, [(tmp_path / "a.txt", tmp_path / "a.out", False)], move=False)
        assert task is not None
        queue.begin(task)
        queue.complete_item(task.items[0])
        queue.finish(task)

        assert "无需取消" in queue.cancel(task.task_id)
        assert queue.cancel(999) == "任务不存在"

    def test_finish_marks_failed_when_any_item_failed(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(
            tmp_path,
            [(tmp_path / "a.txt", tmp_path / "a.out", False), (tmp_path / "b.txt", tmp_path / "b.out", False)],
            move=False,
        )
        assert task is not None
        queue.begin(task)
        queue.complete_item(task.items[0])
        queue.begin_item(task.items[1])
        queue.fail_item(task.items[1], "磁盘已满")

        assert queue.finish(task) == ops.QUEUE_FAILED
        assert task.status_text == "有失败"
        assert task.failed_count == 1
        assert task.done_count == 1
        assert task.items[1].error == "磁盘已满"

    def test_finish_marks_done(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(tmp_path, [(tmp_path / "a.txt", tmp_path / "a.out", False)], move=False)
        assert task is not None
        queue.complete_item(task.items[0])

        assert queue.finish(task) == ops.QUEUE_DONE
        assert task.status_text == "已完成"

    def test_retry_only_selected_failed_items(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(
            tmp_path,
            [(tmp_path / "a.txt", tmp_path / "a.out", False), (tmp_path / "b.txt", tmp_path / "b.out", False)],
            move=False,
        )
        assert task is not None
        queue.begin(task)
        queue.fail_item(task.items[0], "失败 1")
        queue.fail_item(task.items[1], "失败 2")

        retry, conflicts = queue.retry_items(task.task_id, [task.items[1]])

        assert conflicts == []
        assert retry is not None
        assert retry.task_id == 2
        assert [item.source.name for item in retry.items] == ["b.txt"]
        assert retry.items[0].status == ops.ITEM_PENDING

    def test_retry_all_failed_items(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(
            tmp_path,
            [(tmp_path / "a.txt", tmp_path / "a.out", False), (tmp_path / "b.txt", tmp_path / "b.out", False)],
            move=False,
        )
        assert task is not None
        queue.fail_item(task.items[0], "失败")
        queue.complete_item(task.items[1])

        retry, _ = queue.retry_items(task.task_id)

        assert retry is not None
        assert [item.source.name for item in retry.items] == ["a.txt"]

    def test_retry_without_failed_items_returns_message(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(tmp_path, [(tmp_path / "a.txt", tmp_path / "a.out", False)], move=False)
        assert task is not None
        queue.complete_item(task.items[0])

        retry, conflicts = queue.retry_items(task.task_id)

        assert retry is None
        assert conflicts and "没有失败项" in conflicts[0]
        assert queue.retry_items(999) == (None, ["任务不存在"])

    def test_retry_can_force_overwrite(self, tmp_path: Path) -> None:
        queue = ops.TransferQueue()
        task, _ = queue.add(tmp_path, [(tmp_path / "a.txt", tmp_path / "a.out", False)], move=False)
        assert task is not None
        queue.fail_item(task.items[0], "半成品")

        retry, _ = queue.retry_items(task.task_id, overwrite_existing=True)

        assert retry is not None
        assert retry.items[0].overwrite is True


class TestQueueCleanup:
    def test_cleanup_partial_target_removes_file_and_dir(self, tmp_path: Path) -> None:
        file_target = tmp_path / "a.txt.part"
        file_target.write_text("partial", encoding="utf-8")
        dir_target = tmp_path / "dir.part"
        dir_target.mkdir()
        (dir_target / "child").write_text("x", encoding="utf-8")

        assert ops.cleanup_partial_target(file_target) is None
        assert ops.cleanup_partial_target(dir_target) is None

        assert not file_target.exists()
        assert not dir_target.exists()

    def test_cleanup_partial_target_missing_is_none(self, tmp_path: Path) -> None:
        assert ops.cleanup_partial_target(tmp_path / "missing") is None

    def test_cleanup_partial_target_refuses_root(self) -> None:
        message = ops.cleanup_partial_target(Path("/"))
        assert message is not None and "拒绝删除根目录" in message

    def test_cleanup_incomplete_items_only_touches_started_incomplete(self, tmp_path: Path) -> None:
        done_target = tmp_path / "done.out"
        pending_target = tmp_path / "pending.out"
        failed_target = tmp_path / "failed.out"
        user_target = tmp_path / "user.out"
        for path in (done_target, pending_target, failed_target, user_target):
            path.write_text("x", encoding="utf-8")

        done_item = ops.QueueItem(tmp_path / "done", done_target, status=ops.ITEM_DONE, started=True)
        pending_item = ops.QueueItem(tmp_path / "pending", pending_target, status=ops.ITEM_CANCELLED)
        failed_item = ops.QueueItem(tmp_path / "failed", failed_target, status=ops.ITEM_FAILED, started=True)
        user_item = ops.QueueItem(
            tmp_path / "user",
            user_target,
            status=ops.ITEM_FAILED,
            started=True,
            existed_before=True,
        )
        task = ops.QueueTask(
            task_id=1,
            destination=tmp_path,
            move=False,
            items=[done_item, pending_item, failed_item, user_item],
        )

        failures = ops.cleanup_incomplete_items(task)

        assert failures == []
        assert done_target.exists()
        assert pending_target.exists()
        assert not failed_target.exists()
        assert user_target.exists()
        assert task.cleanup_errors == []

    def test_cleanup_incomplete_items_reports_failures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "partial.out"
        target.write_text("x", encoding="utf-8")
        item = ops.QueueItem(tmp_path / "src", target, status=ops.ITEM_CANCELLED, started=True)
        task = ops.QueueTask(task_id=1, destination=tmp_path, move=False, items=[item])
        monkeypatch.setattr(ops, "cleanup_partial_target", lambda _target: "权限不足")

        failures = ops.cleanup_incomplete_items(task)

        assert failures == [(target, "权限不足")]
        assert task.cleanup_errors == [(target, "权限不足")]


# ============================================================================ #
# 目录比较（只读）
# ============================================================================ #
class TestCompareTrees:
    def _write(self, path: Path, content: str, mtime: float = 1_700_000_000.0) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def _roots(self, tmp_path: Path) -> tuple[Path, Path]:
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        return left, right

    def test_classifies_same_different_and_single_side(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "same.txt", "abc")
        self._write(right / "same.txt", "abc")
        self._write(left / "size.txt", "abc")
        self._write(right / "size.txt", "abcdef")
        self._write(left / "only-left.txt", "x")
        self._write(right / "only-right.txt", "y")

        report = ops.compare_trees(left, right)
        statuses = {entry.relative: entry.status for entry in report.entries}

        assert statuses["same.txt"] == ops.COMPARE_SAME
        assert statuses["size.txt"] == ops.COMPARE_DIFFERENT
        assert statuses["only-left.txt"] == ops.COMPARE_LEFT_ONLY
        assert statuses["only-right.txt"] == ops.COMPARE_RIGHT_ONLY
        assert report.counts()["different"] == 1
        assert report.counts()["total"] == 4
        diff = next(entry for entry in report.entries if entry.relative == "size.txt")
        assert diff.reason == "大小不同"
        assert (diff.left_size, diff.right_size) == (3, 6)

    def test_mtime_within_tolerance_is_same(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "abc", mtime=1_700_000_000)
        self._write(right / "a.txt", "abc", mtime=1_700_000_001.5)

        assert ops.compare_trees(left, right).entries[0].status == ops.COMPARE_SAME

    def test_mtime_beyond_tolerance_is_different(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "abc", mtime=1_700_000_000)
        self._write(right / "a.txt", "abc", mtime=1_700_000_010)

        entry = ops.compare_trees(left, right).entries[0]

        assert entry.status == ops.COMPARE_DIFFERENT
        assert entry.reason == "修改时间不同"

    def test_recursive_differences_and_directory_entry(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "sub" / "inner.txt", "one")
        self._write(right / "sub" / "inner.txt", "three")

        report = ops.compare_trees(left, right)
        statuses = {entry.relative: entry.status for entry in report.entries}

        assert statuses["sub"] == ops.COMPARE_SAME
        assert statuses["sub/inner.txt"] == ops.COMPARE_DIFFERENT

    def test_non_recursive_skips_children(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "sub" / "inner.txt", "one")
        self._write(right / "sub" / "inner.txt", "one")

        report = ops.compare_trees(left, right, ops.CompareOptions(recursive=False))

        assert [entry.relative for entry in report.entries] == ["sub"]
        assert report.entries[0].status == ops.COMPARE_SAME

    def test_hidden_entries_are_skipped_by_default(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / ".hidden", "x")
        self._write(right / ".hidden", "yy")
        self._write(left / "visible.txt", "x")
        self._write(right / "visible.txt", "x")

        default_entries = ops.compare_trees(left, right).entries
        assert [entry.relative for entry in default_entries] == ["visible.txt"]

        with_hidden = ops.compare_trees(left, right, ops.CompareOptions(include_hidden=True))
        statuses = {entry.relative: entry.status for entry in with_hidden.entries}
        assert statuses[".hidden"] == ops.COMPARE_DIFFERENT

    def test_deep_compare_detects_same_size_different_content(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "abc")
        self._write(right / "a.txt", "abd")

        shallow = ops.compare_trees(left, right).entries[0]
        assert shallow.status == ops.COMPARE_SAME

        deep = ops.compare_trees(left, right, ops.CompareOptions(deep=True)).entries[0]
        assert deep.status == ops.COMPARE_DIFFERENT
        assert deep.reason == "内容不同"

    def test_type_mismatch_between_file_and_directory(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "item", "file")
        (right / "item").mkdir()

        entry = ops.compare_trees(left, right).entries[0]

        assert entry.status == ops.COMPARE_DIFFERENT
        assert entry.reason == "类型不同"

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        with pytest.raises(ValueError, match="右侧目录不存在"):
            ops.compare_trees(left, right / "missing")

    def test_filtered_and_progress(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "x")
        self._write(right / "b.txt", "y")
        seen: list[str] = []

        def progress(done: int, total: int, name: str) -> None:
            assert 0 < done <= total
            seen.append(name)

        report = ops.compare_trees(left, right, progress=progress)

        assert seen == ["a.txt", "b.txt"]
        assert [entry.relative for entry in report.filtered(ops.COMPARE_LEFT_ONLY)] == ["a.txt"]
        assert [entry.relative for entry in report.filtered("all")] == ["a.txt", "b.txt"]
        assert report.filtered(None) == report.entries

    def test_symlinks_are_not_followed(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        try:
            (left / "linkdir").symlink_to(outside)
        except (OSError, NotImplementedError):
            return
        (right / "linkdir").mkdir()

        report = ops.compare_trees(left, right)
        statuses = {entry.relative: entry.status for entry in report.entries}

        assert statuses["linkdir"] == ops.COMPARE_DIFFERENT
        assert "linkdir/secret.txt" not in statuses
        assert all("outside" not in str(entry.left or "") for entry in report.entries)

    def test_symlink_targets_compared_without_following(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        try:
            (left / "link").symlink_to("target.txt")
            (right / "link").symlink_to("target.txt")
            (left / "link2").symlink_to("one.txt")
            (right / "link2").symlink_to("two.txt")
        except (OSError, NotImplementedError):
            return

        report = ops.compare_trees(left, right)
        statuses = {entry.relative: entry.status for entry in report.entries}

        assert statuses["link"] == ops.COMPARE_SAME
        assert statuses["link2"] == ops.COMPARE_DIFFERENT
        link2 = next(entry for entry in report.entries if entry.relative == "link2")
        assert link2.reason == "链接目标不同"
