"""``_cmderw_ops`` 纯逻辑测试：通配选择、快速过滤、目录统计与回收站。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from fcmd.tools.system import _cmderw_ops as ops
from fcmd.tools.system._cmderw_rename import (
    RenameInput,
    RenameOptions,
    build_rename_preview,
    preview_to_plan,
)


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
class TestRenameExecution:
    def test_apply_only_executes_ok_items(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        plan = ops.RenamePlan(
            (
                ops.RenameItem(first, "a.md", "ok"),
                ops.RenameItem(second, "bad:name", "conflict", "名称非法"),
            )
        )

        report = ops.apply_rename_plan(plan)

        assert report.errors == []
        assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"
        assert not (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.txt").exists()

    def test_undo_round_trip(self, tmp_path: Path) -> None:
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        plan = ops.RenamePlan(
            (
                ops.RenameItem(first, "a.bak", "ok"),
                ops.RenameItem(second, "b.bak", "ok"),
            )
        )
        report = ops.apply_rename_plan(plan)
        assert len(report.completed) == 2

        undone = ops.undo_rename(report)

        assert undone.errors == []
        assert first.read_text(encoding="utf-8") == "a"
        assert second.read_text(encoding="utf-8") == "b"
        assert not (tmp_path / "a.bak").exists()

    def test_undo_reports_occupied_source(self, tmp_path: Path) -> None:
        source = tmp_path / "a.txt"
        source.write_text("a", encoding="utf-8")
        report = ops.apply_rename_plan(ops.RenamePlan((ops.RenameItem(source, "a.md", "ok"),)))
        source.write_text("new", encoding="utf-8")

        undone = ops.undo_rename(report)

        assert undone.completed == []
        assert "已被占用" in undone.errors[0][1]
        assert (tmp_path / "a.md").exists()


class TestRenamePlan:
    def test_unchanged_entries(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        preview = build_rename_preview(RenameOptions(find="zzz"), [RenameInput(tmp_path / "a.txt")])

        assert preview.items[0].status == "unchanged"
        assert preview.ok == ()
        assert preview.conflicts == ()
        assert preview_to_plan(preview).changes == ()

    def test_duplicate_targets_are_conflicts(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(name_mask="same", ext_mask="-"),
            [RenameInput(tmp_path / "a.txt"), RenameInput(tmp_path / "b.txt")],
        )

        assert [item.status for item in preview.items] == ["ok", "conflict"]
        assert "同名" in preview.items[1].note

    def test_existing_target_is_conflict(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(name_mask="b", ext_mask=""),
            [RenameInput(tmp_path / "a.txt")],
        )

        assert preview.items[0].status == "conflict"
        assert preview.items[0].note == "目标已存在"

    def test_invalid_name_is_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(name_mask="bad:name", ext_mask="-"),
            [RenameInput(tmp_path / "a.txt")],
        )

        assert preview.items[0].status == "invalid"
        assert "不能包含" in preview.items[0].note
        assert preview_to_plan(preview).changes == ()

    def test_case_only_rename_is_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(case_mode="upper"),
            [RenameInput(tmp_path / "a.txt")],
        )

        assert preview.items[0].status == "ok"
        assert preview.items[0].new_name == "A.txt"

    def test_apply_only_executes_ok_items(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(find="txt", replace="md"),
            [RenameInput(tmp_path / "a.txt"), RenameInput(tmp_path / "b.txt")],
        )
        plan = preview_to_plan(preview)
        assert plan.conflicts == ()
        assert len(plan.changes) == 2

        report = ops.apply_rename_plan(plan)

        assert report.errors == []
        assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"
        assert (tmp_path / "b.md").read_text(encoding="utf-8") == "b"
        assert not (tmp_path / "a.txt").exists()

    def test_apply_honours_selected_paths(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(find="txt", replace="md"),
            [RenameInput(tmp_path / "a.txt"), RenameInput(tmp_path / "b.txt")],
        )

        plan = preview_to_plan(preview, [tmp_path / "b.txt"])
        ops.apply_rename_plan(plan)

        assert (tmp_path / "a.txt").exists()
        assert (tmp_path / "b.md").exists()

    def test_undo_round_trip(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text(name, encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(name_mask="[N].bak", ext_mask="-"),
            [RenameInput(tmp_path / "a.txt"), RenameInput(tmp_path / "b.txt")],
        )
        report = ops.apply_rename_plan(preview_to_plan(preview))
        assert len(report.completed) == 2

        undone = ops.undo_rename(report)

        assert undone.errors == []
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a.txt"
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b.txt"
        assert not (tmp_path / "a.bak").exists()

    def test_undo_reports_occupied_source(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(find="txt", replace="md"),
            [RenameInput(tmp_path / "a.txt")],
        )
        report = ops.apply_rename_plan(preview_to_plan(preview))
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

    def test_content_mode_detects_same_size_different_content(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "abc")
        self._write(right / "a.txt", "abd")

        shallow = ops.compare_trees(left, right).entries[0]
        assert shallow.status == ops.COMPARE_SAME

        deep = ops.compare_trees(left, right, ops.CompareOptions(mode=ops.COMPARE_MODE_CONTENT)).entries[0]
        assert deep.status == ops.COMPARE_DIFFERENT
        assert deep.reason == "内容不同"

    def test_name_mode_ignores_size_and_time(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "abc", mtime=1_700_000_000)
        self._write(right / "a.txt", "abcdef", mtime=1_700_000_900)

        entry = ops.compare_trees(left, right, ops.CompareOptions(mode=ops.COMPARE_MODE_NAME)).entries[0]

        assert entry.status == ops.COMPARE_SAME

    def test_size_mode_ignores_time(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "same.txt", "abc", mtime=1_700_000_000)
        self._write(right / "same.txt", "abd", mtime=1_700_000_900)
        self._write(left / "grow.txt", "abc")
        self._write(right / "grow.txt", "abcdef")

        report = ops.compare_trees(left, right, ops.CompareOptions(mode=ops.COMPARE_MODE_SIZE))
        statuses = {entry.relative: entry.status for entry in report.entries}

        assert statuses["same.txt"] == ops.COMPARE_SAME
        assert statuses["grow.txt"] == ops.COMPARE_DIFFERENT

    def test_unknown_mode_falls_back_to_time(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "abc", mtime=1_700_000_000)
        self._write(right / "a.txt", "abc", mtime=1_700_000_900)

        entry = ops.compare_trees(left, right, ops.CompareOptions(mode="bogus")).entries[0]

        assert entry.status == ops.COMPARE_DIFFERENT
        assert entry.reason == "修改时间不同"

    def test_include_and_exclude_masks(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "one")
        self._write(right / "a.txt", "two")
        self._write(left / "b.py", "one")
        self._write(right / "b.py", "two")

        included = ops.compare_trees(left, right, ops.CompareOptions(mask="*.txt")).entries
        assert [entry.relative for entry in included] == ["a.txt"]

        excluded = ops.compare_trees(left, right, ops.CompareOptions(exclude_mask="*.txt;*.md")).entries
        assert [entry.relative for entry in excluded] == ["b.py"]

    def test_files_only_skips_directory_entries(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "sub" / "inner.txt", "one")
        self._write(right / "sub" / "inner.txt", "three")

        entries = ops.compare_trees(left, right, ops.CompareOptions(files_only=True)).entries

        assert [entry.relative for entry in entries] == ["sub/inner.txt"]

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


# ============================================================================ #
# 目录同步（计划、执行前复核、删除）
# ============================================================================ #
class TestSyncPlan:
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

    def test_default_plan_copies_but_does_not_delete(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "a.txt", "a")
        self._write(right / "b.txt", "b")
        self._write(left / "c.txt", "old")
        self._write(right / "c.txt", "newer")

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report)

        assert [(item.relative, item.overwrite) for item in plan.copies] == [
            ("a.txt", False),
            ("c.txt", True),
        ]
        assert plan.deletes == []
        assert [item.relative for item in plan.skipped] == ["b.txt"]
        assert plan.skipped[0].skip_reason == "未勾选删除目标端多余文件"
        assert plan.counts() == {
            "copy": 2,
            "copy_new": 1,
            "copy_overwrite": 1,
            "delete": 0,
            "delete_dirs": 0,
            "skipped": 1,
            "same": 0,
        }

    def test_delete_files_requires_extra_checkbox(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(right / "extra.txt", "x")
        (right / "extra_dir").mkdir()
        self._write(right / "extra_dir" / "inner.txt", "y")

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report, ops.SyncOptions(delete_target_extra=True))

        assert [item.relative for item in plan.deletes] == ["extra.txt"]
        assert [item.relative for item in plan.skipped] == ["extra_dir"]
        assert plan.skipped[0].skip_reason == "未勾选删除多余目录"

    def test_delete_dirs_covers_children(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        (right / "extra_dir").mkdir()
        self._write(right / "extra_dir" / "inner.txt", "y")
        self._write(right / "extra.txt", "x")

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report, ops.SyncOptions(delete_target_extra=True, delete_dirs=True))

        assert [item.relative for item in plan.deletes] == ["extra.txt", "extra_dir"]
        assert all(not item.relative.startswith("extra_dir/") for item in plan.deletes)
        assert plan.counts()["delete_dirs"] == 1

    def test_directory_copy_covers_children(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "sub" / "inner.txt", "x")

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report)

        assert [item.relative for item in plan.copies] == ["sub"]
        item = plan.copies[0]
        assert item.is_dir
        assert item.source == left / "sub"
        assert item.target == right / "sub"

    def test_reverse_direction_swaps_source_and_delete_side(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "left-only.txt", "x")
        self._write(right / "right-only.txt", "y")

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(
            report,
            ops.SyncOptions(direction=ops.SYNC_RTL, delete_target_extra=True),
        )

        assert [(item.relative, item.source, item.target) for item in plan.copies] == [
            ("right-only.txt", right / "right-only.txt", left / "right-only.txt")
        ]
        assert [item.relative for item in plan.deletes] == ["left-only.txt"]
        assert plan.deletes[0].target == left / "left-only.txt"

    def test_bidirectional_copies_each_side_only(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "left-only.txt", "x")
        self._write(right / "right-only.txt", "y")

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report, ops.SyncOptions(direction=ops.SYNC_BOTH))

        assert [(item.relative, item.source, item.target) for item in plan.copies] == [
            ("left-only.txt", left / "left-only.txt", right / "left-only.txt"),
            ("right-only.txt", right / "right-only.txt", left / "right-only.txt"),
        ]
        assert plan.deletes == []
        assert plan.is_bidirectional

    def test_bidirectional_newer_side_wins_conflict(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "c.txt", "left", mtime=1_700_000_500)
        self._write(right / "c.txt", "right", mtime=1_700_000_000)

        report = ops.compare_trees(left, right)
        options = ops.SyncOptions(direction=ops.SYNC_BOTH, conflict=ops.SYNC_CONFLICT_NEWER)

        forward = ops.build_sync_plan(report, options).copies
        assert [(item.source, item.target) for item in forward] == [(left / "c.txt", right / "c.txt")]

        os.utime(right / "c.txt", (1_700_000_900, 1_700_000_900))
        reversed_plan = ops.build_sync_plan(ops.compare_trees(left, right), options).copies
        assert [(item.source, item.target) for item in reversed_plan] == [(right / "c.txt", left / "c.txt")]

    def test_bidirectional_conflict_policies(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "c.txt", "left", mtime=1_700_000_500)
        self._write(right / "c.txt", "right", mtime=1_700_000_000)
        report = ops.compare_trees(left, right)

        skip_plan = ops.build_sync_plan(
            report, ops.SyncOptions(direction=ops.SYNC_BOTH, conflict=ops.SYNC_CONFLICT_SKIP)
        )
        assert skip_plan.copies == []
        assert skip_plan.skipped[0].skip_reason == "两侧内容不同，按策略跳过"

        os.utime(right / "c.txt", (1_700_000_500, 1_700_000_500))
        tie_plan = ops.build_sync_plan(
            ops.compare_trees(left, right),
            ops.SyncOptions(direction=ops.SYNC_BOTH, conflict=ops.SYNC_CONFLICT_NEWER),
        )
        assert tie_plan.copies == []
        assert tie_plan.skipped[0].skip_reason == "两侧时间相同，无法判定较新一侧"

    def test_directed_newer_policy_skips_stale_source(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "c.txt", "left", mtime=1_700_000_000)
        self._write(right / "c.txt", "right", mtime=1_700_000_500)
        options = ops.SyncOptions(conflict=ops.SYNC_CONFLICT_NEWER)

        stale = ops.build_sync_plan(ops.compare_trees(left, right), options)
        assert stale.copies == []
        assert stale.skipped[0].skip_reason == "源端不较新，按策略跳过"

        os.utime(left / "c.txt", (1_700_000_900, 1_700_000_900))
        fresh = ops.build_sync_plan(ops.compare_trees(left, right), options)
        assert [(item.source, item.target) for item in fresh.copies] == [(left / "c.txt", right / "c.txt")]

    def test_unknown_conflict_falls_back_to_overwrite(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "c.txt", "left", mtime=1_700_000_500)
        self._write(right / "c.txt", "right", mtime=1_700_000_000)

        plan = ops.build_sync_plan(ops.compare_trees(left, right), ops.SyncOptions(conflict="bogus"))

        assert [item.relative for item in plan.copies] == ["c.txt"]

    def test_root_for_locates_owning_panel(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report)

        assert plan.root_for(right / "sub" / "a.txt") == right
        assert plan.root_for(left / "a.txt") == left

    def test_selection_split_toggle_and_invert(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "left-only.txt", "x")
        self._write(right / "right-only.txt", "y")
        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report, ops.SyncOptions(delete_target_extra=True))

        everything = ops.sync_select_all(plan)
        assert ops.split_sync_selection(plan, everything) == (plan.copies, plan.deletes, 0)

        first_key = ops.sync_item_key(plan.copies[0])
        toggled = ops.sync_toggle_selection(everything, first_key)
        copies, deletes, unselected = ops.split_sync_selection(plan, toggled)
        assert copies == []
        assert deletes == plan.deletes
        assert unselected == 1

        assert ops.sync_invert_selection(plan, everything) == frozenset()
        assert ops.sync_invert_selection(plan, frozenset()) == everything
        assert ops.sync_toggle_selection(toggled, first_key) == everything

    def test_format_sync_report_counts_and_truncation(self) -> None:
        report = ops.SyncRunReport(
            copied=3,
            copied_new=1,
            deleted=1,
            delete_not_executed=2,
            unselected=4,
            skipped=(("a", "大小已变化"), ("b", "源端缺失"), ("c", "符号链接")),
            failed=(("/tmp/x", "权限不足"),),
        )

        text = "\n".join(ops.format_sync_report(report, max_details=2))

        assert "复制已入队：3 项（新建 1，覆盖 2）" in text
        assert "删除完成：1 项" in text
        assert "删除未执行：2 项" in text
        assert "未勾选 4 项" in text
        assert "其余 1 项见预演清单" in text
        assert "删除失败 1 项" in text
        assert "传输队列" in text

    def test_format_sync_report_empty_and_enqueue_failure(self) -> None:
        assert ops.format_sync_report(ops.SyncRunReport()) == ["没有可执行的操作（全部被跳过或未勾选）。"]
        assert ops.format_sync_report(ops.SyncRunReport(enqueue_ok=False))[0].startswith("复制未入队")

    def test_symlink_entries_are_skipped(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        self._write(left / "target.txt", "x")
        try:
            (left / "link.txt").symlink_to("target.txt")
        except (OSError, NotImplementedError):
            return

        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report)

        assert [item.relative for item in plan.skipped] == ["link.txt"]
        assert plan.skipped[0].skip_reason == "符号链接不处理"

    def test_invalid_direction_raises(self, tmp_path: Path) -> None:
        left, right = self._roots(tmp_path)
        report = ops.compare_trees(left, right)

        with pytest.raises(ValueError, match="未知的同步方向"):
            ops.build_sync_plan(report, ops.SyncOptions(direction="sideways"))


class TestSyncRevalidate:
    def _write(self, path: Path, content: str, mtime: float = 1_700_000_000.0) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def _overwrite_item(self, tmp_path: Path) -> tuple[Path, Path, ops.SyncItem]:
        left = tmp_path / "left"
        right = tmp_path / "right"
        self._write(left / "c.txt", "old")
        self._write(right / "c.txt", "newer")
        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report)
        return left, right, plan.copies[0]

    def test_unchanged_target_passes(self, tmp_path: Path) -> None:
        _, _, item = self._overwrite_item(tmp_path)

        assert ops.revalidate_sync_item(item) is None

    def test_target_mtime_change_is_skipped(self, tmp_path: Path) -> None:
        _, right, item = self._overwrite_item(tmp_path)
        os.utime(right / "c.txt", (1_700_000_100, 1_700_000_100))

        assert ops.revalidate_sync_item(item) == "目标修改时间已变化"

    def test_target_size_change_is_skipped(self, tmp_path: Path) -> None:
        _, right, item = self._overwrite_item(tmp_path)
        self._write(right / "c.txt", "much longer content")

        reason = ops.revalidate_sync_item(item)

        assert reason == "目标大小已变化"

    def test_target_disappeared_is_skipped(self, tmp_path: Path) -> None:
        _, right, item = self._overwrite_item(tmp_path)
        (right / "c.txt").unlink()

        assert ops.revalidate_sync_item(item) == "目标已不存在（预演时存在）"

    def test_new_target_appearing_is_skipped(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        self._write(left / "a.txt", "a")
        right.mkdir()
        report = ops.compare_trees(left, right)
        item = ops.build_sync_plan(report).copies[0]
        self._write(right / "a.txt", "a")

        assert ops.revalidate_sync_item(item) == "目标已出现，请重新预演"

    def test_target_symlink_is_skipped(self, tmp_path: Path) -> None:
        _, right, item = self._overwrite_item(tmp_path)
        (right / "c.txt").unlink()
        try:
            (right / "c.txt").symlink_to("elsewhere")
        except (OSError, NotImplementedError):
            return

        assert ops.revalidate_sync_item(item) == "目标为符号链接，未处理"

    def test_delete_target_change_is_skipped(self, tmp_path: Path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        self._write(right / "extra.txt", "x")
        report = ops.compare_trees(left, right)
        plan = ops.build_sync_plan(report, ops.SyncOptions(delete_target_extra=True))
        os.utime(right / "extra.txt", (1_700_000_100, 1_700_000_100))

        assert ops.revalidate_sync_item(plan.deletes[0]) == "目标修改时间已变化"

    def test_batch_revalidate_splits_ready_and_skipped(self, tmp_path: Path) -> None:
        _, right, item = self._overwrite_item(tmp_path)
        gone = ops.SyncItem(
            ops.SYNC_COPY,
            "missing.txt",
            right / "missing.txt",
            source=tmp_path / "left" / "missing.txt",
            overwrite=True,
        )
        os.utime(right / "c.txt", (1_700_000_100, 1_700_000_100))

        ready, skipped = ops.revalidate_sync_items([item, gone])

        assert ready == []
        assert [entry.relative for entry, _reason in skipped] == ["c.txt", "missing.txt"]


class TestSyncDeleteExecution:
    def _items(self, count: int) -> list[ops.SyncItem]:
        return [ops.SyncItem(ops.SYNC_DELETE, f"f{index}.txt", Path(f"/tmp/f{index}.txt")) for index in range(count)]

    def test_deletes_stop_at_first_failure(self) -> None:
        items = self._items(3)
        calls: list[Path] = []

        def mover(path: Path) -> Path:
            calls.append(path)
            if path.name == "f1.txt":
                raise OSError("回收站不可用")
            return path

        report = ops.execute_sync_deletes(items, mover=mover)

        assert report.completed == [items[0]]
        assert report.failures == [(items[1], "回收站不可用")]
        assert report.not_executed == [items[2]]
        assert report.stopped
        assert calls == [item.target for item in items[:2]]

    def test_trash_unavailable_aborts_whole_batch(self) -> None:
        items = self._items(2)

        def mover(_path: Path) -> Path:
            raise OSError("平台不支持回收站")

        report = ops.execute_sync_deletes(items, mover=mover)

        assert report.completed == []
        assert report.failures[0][1] == "平台不支持回收站"
        assert report.not_executed == [items[1]]

    def test_progress_reports_completed_items(self) -> None:
        items = self._items(2)
        seen: list[str] = []

        def progress(index: int, total: int, name: str) -> None:
            assert total == 2
            assert 0 < index <= total
            seen.append(name)

        report = ops.execute_sync_deletes(items, mover=lambda path: path, progress=progress)

        assert len(report.completed) == 2
        assert seen == ["f0.txt", "f1.txt"]


class TestReadPreview:
    def test_text_preview_and_limit(self, tmp_path: Path) -> None:
        path = tmp_path / "note.txt"
        path.write_text("你好\nworld\n", encoding="utf-8")

        result = ops.read_preview(path)

        assert result.kind == ops.PREVIEW_TEXT
        assert result.text == "你好\nworld\n"
        assert result.truncated is False
        assert result.size == path.stat().st_size

    def test_text_truncation_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "big.txt"
        path.write_text("x" * 64, encoding="utf-8")

        result = ops.read_preview(path, text_limit=10)

        assert result.kind == ops.PREVIEW_TEXT
        assert result.truncated is True
        assert result.text == "x" * 10
        assert "已截断" in result.note

    def test_binary_gets_hex_dump(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        path.write_bytes(b"abc\x00\x01\x02def")

        result = ops.read_preview(path)

        assert result.kind == ops.PREVIEW_BINARY
        assert "00000000" in result.text
        assert "二进制文件" in result.note

    def test_pe_file_is_labeled(self, tmp_path: Path) -> None:
        path = tmp_path / "app.exe"
        path.write_bytes(b"MZ" + b"\x00" * 30)

        result = ops.read_preview(path)

        assert result.kind == ops.PREVIEW_BINARY
        assert "PE/可执行文件" in result.note

    def test_image_is_detected_by_magic(self, tmp_path: Path) -> None:
        path = tmp_path / "pixel.dat"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        result = ops.read_preview(path)

        assert result.kind == ops.PREVIEW_IMAGE
        assert "png" in result.note

    def test_directory_is_unsupported(self, tmp_path: Path) -> None:
        result = ops.read_preview(tmp_path)

        assert result.kind == ops.PREVIEW_UNSUPPORTED
        assert result.note == "目录不预览"

    def test_symlink_is_not_followed(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("secret", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            return

        result = ops.read_preview(link)

        assert result.kind == ops.PREVIEW_UNSUPPORTED
        assert "符号链接" in result.note

    def test_missing_file_is_unsupported(self, tmp_path: Path) -> None:
        result = ops.read_preview(tmp_path / "nope.txt")

        assert result.kind == ops.PREVIEW_UNSUPPORTED
        assert result.note == "文件已不存在"


class TestColorRules:
    NOW = 1_700_000_000.0

    def _subject(self, **overrides: object) -> ops.ColorSubject:
        data: dict[str, object] = {"name": "app.log"}
        data.update(overrides)
        return ops.ColorSubject(**data)  # type: ignore[arg-type]

    def test_pattern_size_and_kind(self) -> None:
        rule = ops.ColorRule(pattern="*.log", min_size=10, max_size=100, kind=ops.COLOR_KIND_FILE)

        assert rule.matches(self._subject(size=50), self.NOW)
        assert not rule.matches(self._subject(size=5), self.NOW)
        assert not rule.matches(self._subject(size=500), self.NOW)
        assert not rule.matches(self._subject(name="app.txt", size=50), self.NOW)
        assert not rule.matches(self._subject(name="logs", size=50, is_dir=True), self.NOW)

    def test_hidden_and_symlink_attributes(self) -> None:
        hidden_rule = ops.ColorRule(pattern="*", hidden=True)
        link_rule = ops.ColorRule(pattern="*", kind=ops.COLOR_KIND_LINK)

        assert hidden_rule.matches(self._subject(hidden=True), self.NOW)
        assert not hidden_rule.matches(self._subject(hidden=False), self.NOW)
        assert link_rule.matches(self._subject(is_symlink=True), self.NOW)
        assert not link_rule.matches(self._subject(is_symlink=False), self.NOW)

    def test_time_windows(self) -> None:
        recent = self._subject(mtime=self.NOW - 3 * 86400)
        old = self._subject(mtime=self.NOW - 30 * 86400)

        assert ops.ColorRule(newer_days=7).matches(recent, self.NOW)
        assert not ops.ColorRule(newer_days=7).matches(old, self.NOW)
        assert ops.ColorRule(older_days=20).matches(old, self.NOW)
        assert not ops.ColorRule(older_days=20).matches(recent, self.NOW)
        assert not ops.ColorRule(newer_days=7).matches(self._subject(mtime=0), self.NOW)

    def test_later_rules_override_earlier(self) -> None:
        rules = [
            ops.ColorRule(pattern="*.log", foreground="#ff0000"),
            ops.ColorRule(pattern="app.*", foreground="#00ff00", background="#000033"),
        ]

        style = ops.resolve_color_style(rules, self._subject(), self.NOW)

        assert style == ops.ColorStyle("#00ff00", "#000033")

    def test_foreground_and_background_merge_independently(self) -> None:
        rules = [
            ops.ColorRule(pattern="*.log", background="#000033"),
            ops.ColorRule(pattern="app.*", foreground="#00ff00"),
        ]

        style = ops.resolve_color_style(rules, self._subject(), self.NOW)

        assert style == ops.ColorStyle("#00ff00", "#000033")

    def test_no_match_returns_none(self) -> None:
        rules = [ops.ColorRule(pattern="*.tmp", foreground="#ffffff")]

        assert ops.resolve_color_style(rules, self._subject(), self.NOW) is None

    def test_parse_and_dump_roundtrip(self) -> None:
        rules = [
            ops.ColorRule(
                pattern="*.log",
                min_size=10,
                max_size=100,
                kind=ops.COLOR_KIND_FILE,
                hidden=False,
                newer_days=7.0,
                older_days=None,
                foreground="#ff0000",
                background="#000033",
            )
        ]

        raw = ops.dump_color_rules(rules)

        assert ops.parse_color_rules(raw) == rules

    def test_parse_ignores_invalid_payloads(self) -> None:
        raw = [
            None,
            {"pattern": "*.log"},
            {"pattern": "*.log", "foreground": "red"},
            {"pattern": "*.log", "foreground": "#FF0000", "background": "#000033"},
            "not-a-dict",
        ]

        rules = ops.parse_color_rules(raw)

        assert rules == [ops.ColorRule(pattern="*.log", foreground="#ff0000", background="#000033")]
        assert ops.parse_color_rules(None) == []

    def test_normalize_color(self) -> None:
        assert ops.normalize_color(" #AABBCC ") == "#aabbcc"
        assert ops.normalize_color("red") == ""
        assert ops.normalize_color(123) == ""


class TestDestructiveCommand:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("rm -rf build", "rm"),
            ("sudo rm note.txt", "rm"),
            ("echo done && del temp.txt", "del"),
            ("Remove-Item -Recurse old", "remove-item"),
            ("find . -name '*.tmp' -delete", "find -delete"),
            ("git status && ls", None),
            ("echo 'rm is dangerous'", None),
            ("", None),
        ],
    )
    def test_detects_delete_commands(self, command: str, expected: str | None) -> None:
        assert ops.destructive_command(command) == expected


class TestMenuState:
    """顶部菜单禁用状态与勾选变量映射。"""

    @staticmethod
    def _facts(**overrides: object) -> ops.MenuFacts:
        data: dict[str, object] = {"entry_count": 5, "selection_count": 1, "single_is_file": True}
        data.update(overrides)
        return ops.MenuFacts(**data)  # type: ignore[arg-type]

    def test_diff_text_requires_two_files(self) -> None:
        assert not ops.menu_state(self._facts(selection_count=2, two_files=True)).is_disabled("diff_text")
        assert ops.menu_state(self._facts(selection_count=2, two_files=False)).is_disabled("diff_text")
        assert ops.menu_state(self._facts(selection_count=1)).is_disabled("diff_text")

    def test_no_selection_disables_selection_actions(self) -> None:
        state = ops.menu_state(self._facts(selection_count=0, single_is_file=False))

        for action in ("open", "view", "edit", "open_with", "rename", "copy", "delete", "pack", "unpack"):
            assert state.is_disabled(action), action

    def test_multi_selection_keeps_batch_actions_enabled(self) -> None:
        state = ops.menu_state(self._facts(selection_count=3, single_is_file=False))

        for action in ("open", "view", "edit", "open_with", "properties", "rename", "unpack"):
            assert state.is_disabled(action), action
        for action in ("copy", "move", "delete", "permanent_delete", "pack", "bulk_rename"):
            assert not state.is_disabled(action), action

    def test_single_directory_disables_file_only_actions(self) -> None:
        state = ops.menu_state(self._facts(selection_count=1, single_is_file=False))

        for action in ("view", "edit", "open_with", "unpack"):
            assert state.is_disabled(action), action
        assert not state.is_disabled("open")

    def test_single_tab_disables_close_tab(self) -> None:
        assert ops.menu_state(self._facts(tab_count=1)).is_disabled("close_tab")
        assert not ops.menu_state(self._facts(tab_count=2)).is_disabled("close_tab")

    def test_history_bounds_disable_back_and_forward(self) -> None:
        start = ops.menu_state(self._facts(history_index=0, history_length=3))
        assert start.is_disabled("back")
        assert not start.is_disabled("forward")

        end = ops.menu_state(self._facts(history_index=2, history_length=3))
        assert not end.is_disabled("back")
        assert end.is_disabled("forward")

    def test_empty_panel_disables_selection_commands(self) -> None:
        state = ops.menu_state(self._facts(entry_count=0, selection_count=0, single_is_file=False))

        for action in ("select_all", "clear_selection", "invert_selection", "select_mask", "deselect_mask"):
            assert state.is_disabled(action), action

    def test_read_only_panel_disables_writes_but_not_view(self) -> None:
        state = ops.menu_state(self._facts(active_writable=False))

        for action in ("new_folder", "rename", "bulk_rename", "delete", "permanent_delete", "move", "pack"):
            assert state.is_disabled(action), action
        for action in ("view", "copy", "hash"):
            assert not state.is_disabled(action), action

    def test_busy_disables_long_running_actions(self) -> None:
        state = ops.menu_state(self._facts(busy=True))

        for action in ("refresh", "search", "compare", "sync", "copy", "hash"):
            assert state.is_disabled(action), action
        assert not state.is_disabled("switch_panel")

    def test_panel_availability(self) -> None:
        state = ops.menu_state(self._facts(panels_ready=False, active_exists=False))

        assert state.is_disabled("compare")
        assert state.is_disabled("sync")
        assert state.is_disabled("command_line")

    def test_check_states_mirror_actual_visibility(self) -> None:
        """菜单勾选变量必须与隐藏文件/目录树/快速查看的实际状态一致。"""
        facts = self._facts(
            show_hidden=True,
            tree_visible=False,
            quick_visible=True,
            sync_browse=True,
            branch_view=True,
            sort_key="size",
            view_mode="brief",
        )

        state = ops.menu_state(facts)

        assert state.show_hidden is True
        assert state.tree_visible is False
        assert state.quick_visible is True
        assert state.sync_browse is True
        assert state.branch_view is True
        assert state.sort_key == "size"
        assert state.view_mode == "brief"

    def test_invalid_view_mode_falls_back_to_detailed(self) -> None:
        assert ops.menu_state(self._facts(view_mode="grid")).view_mode == "detailed"

    def test_open_archive_requires_single_browsable_file(self) -> None:
        assert not ops.menu_state(self._facts(single_is_archive=True)).is_disabled("open_archive")
        assert ops.menu_state(self._facts(single_is_archive=False)).is_disabled("open_archive")
        assert ops.menu_state(self._facts(selection_count=2, single_is_archive=True)).is_disabled("open_archive")

    def test_archive_view_disables_write_and_real_path_actions(self) -> None:
        state = ops.menu_state(self._facts(archive_view=True))

        for action in (
            "open_archive",
            "new_folder",
            "rename",
            "bulk_rename",
            "delete",
            "permanent_delete",
            "move",
            "open_with",
            "properties",
            "hash",
            "pack",
            "unpack",
        ):
            assert state.is_disabled(action), action
        for action in ("open", "view", "copy", "refresh", "command_line"):
            assert not state.is_disabled(action), action
        assert state.archive_view is True


class TestSyncBrowse:
    """同步浏览：把一侧的目录变化折算成相对动作，另一侧按同样动作跟随。"""

    def test_enter_child_directory(self) -> None:
        assert ops.browse_step("/a", "/a/b") == "b"
        assert ops.browse_follow_path("/x", "b") == str(Path("/x/b"))

    def test_return_to_parent(self) -> None:
        assert ops.browse_step("/a/b", "/a") == ops.BROWSE_STEP_PARENT
        assert ops.browse_follow_path("/x/y", ops.BROWSE_STEP_PARENT) == str(Path("/x"))

    def test_unrelated_jump_is_not_followed(self) -> None:
        assert ops.browse_step("/a", "/c") is None
        assert ops.browse_step("/a", "/a/b/c") is None
        assert ops.browse_step("/a", "/a") is None
        assert ops.browse_step("", "/a") is None

    def test_follow_path_rejects_root_and_invalid_step(self) -> None:
        assert ops.browse_follow_path("/", ops.BROWSE_STEP_PARENT) is None
        assert ops.browse_follow_path("/x", None) is None
        assert ops.browse_follow_path("", "b") is None


class TestOpenWith:
    """打开方式：最近使用程序列表与启动命令。"""

    def test_normalize_filters_and_dedupes(self) -> None:
        assert ops.normalize_open_with_list([3, "", "  /bin/a  ", "/bin/a", "/bin/b"]) == ["/bin/a", "/bin/b"]

    def test_normalize_rejects_non_list_and_truncates(self) -> None:
        assert ops.normalize_open_with_list("bad") == []
        raw = [f"/bin/p{index}" for index in range(ops.OPEN_WITH_LIMIT + 4)]

        assert ops.normalize_open_with_list(raw) == raw[: ops.OPEN_WITH_LIMIT]

    def test_remember_moves_to_front_and_truncates(self) -> None:
        programs = ["/bin/a", "/bin/b"]

        assert ops.remember_open_with(programs, "/bin/b") == ["/bin/b", "/bin/a"]
        assert ops.remember_open_with(programs, "  ") == programs
        many = [f"/bin/p{index}" for index in range(ops.OPEN_WITH_LIMIT)]
        updated = ops.remember_open_with(many, "/bin/new")
        assert updated[0] == "/bin/new"
        assert len(updated) == ops.OPEN_WITH_LIMIT

    def test_open_with_command(self) -> None:
        assert ops.open_with_command(" /bin/editor ", "/tmp/a.txt") == ["/bin/editor", "/tmp/a.txt"]
        with pytest.raises(ValueError):
            ops.open_with_command("", "/tmp/a.txt")
        with pytest.raises(ValueError):
            ops.open_with_command("/bin/editor", " ")


class TestBranchView:
    """分支视图：相对路径显示名与复制时的相对子目录。"""

    def test_relative_path_inside_root(self) -> None:
        root = Path("/data")

        assert ops.branch_relative_path(root, root / "sub" / "a.txt") == str(Path("sub/a.txt"))
        assert ops.branch_relative_path(root, root / "a.txt") == "a.txt"

    def test_relative_path_outside_root_falls_back_to_name(self) -> None:
        assert ops.branch_relative_path(Path("/data"), Path("/other/a.txt")) == "a.txt"

    def test_relative_parent_dir(self) -> None:
        root = Path("/data")

        assert ops.relative_parent_dir(root, root / "a.txt") == ""
        assert ops.relative_parent_dir(root, root / "sub" / "deep" / "a.txt") == str(Path("sub/deep"))
        assert ops.relative_parent_dir(root, Path("/other/a.txt")) is None


class TestCommandLine:
    def test_normalize_history_filters_and_dedupes(self) -> None:
        raw = ["ls", 3, "  ", "git status", "ls", None, "pwd"]
        assert ops.normalize_command_history(raw) == ["ls", "git status", "pwd"]
        assert ops.normalize_command_history("ls") == []
        assert ops.normalize_command_history(None) == []

    def test_normalize_history_caps_length(self) -> None:
        raw = [f"cmd{i}" for i in range(ops.COMMAND_HISTORY_LIMIT + 10)]
        assert len(ops.normalize_command_history(raw)) == ops.COMMAND_HISTORY_LIMIT

    def test_push_moves_command_to_front(self) -> None:
        assert ops.command_history_push(["a", "b"], " c ") == ["c", "a", "b"]
        assert ops.command_history_push(["a", "b"], "b") == ["b", "a"]
        assert ops.command_history_push(["a"], "   ") == ["a"]

    def test_push_caps_length(self) -> None:
        history = [f"cmd{i}" for i in range(ops.COMMAND_HISTORY_LIMIT)]
        pushed = ops.command_history_push(history, "new")
        assert pushed[0] == "new"
        assert len(pushed) == ops.COMMAND_HISTORY_LIMIT
        assert pushed[-1] == history[-2]

    def test_step_without_history_returns_draft(self) -> None:
        assert ops.command_history_step([], -1, "draft", older=True) == ("draft", -1, "draft")

    def test_step_cycles_and_restores_draft(self) -> None:
        history = ["newest", "older", "oldest"]
        text, index, draft = ops.command_history_step(history, -1, "draft", older=True)
        assert (text, index, draft) == ("newest", 0, "draft")

        text, index, draft = ops.command_history_step(history, index, draft, older=True)
        assert (text, index) == ("older", 1)

        text, index, draft = ops.command_history_step(history, index, draft, older=True)
        assert (text, index) == ("oldest", 2)

        text, index, draft = ops.command_history_step(history, index, draft, older=True)
        assert (text, index) == ("oldest", 2)

        text, index, draft = ops.command_history_step(history, index, draft, older=False)
        assert (text, index) == ("older", 1)

        text, index, draft = ops.command_history_step(history, index, draft, older=False)
        assert (text, index) == ("newest", 0)

        text, index, draft = ops.command_history_step(history, index, draft, older=False)
        assert (text, index) == ("draft", -1)

    def test_step_newer_without_cycle_keeps_draft(self) -> None:
        assert ops.command_history_step(["a"], -1, "draft", older=False) == ("draft", -1, "draft")

    def test_split_command_token(self) -> None:
        assert ops.split_command_token("git status") == ("git", " status")
        assert ops.split_command_token("   ls -la") == ("ls", " -la")
        assert ops.split_command_token("") == ("", "")
        assert ops.split_command_token("   ") == ("", "   ")

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("git", "command"),
            ("./run", "path"),
            ("~/docs", "path"),
            ("sub/file", "path"),
            (".hidden", "path"),
            ("sub\\file", "path"),
        ],
    )
    def test_completion_mode(self, token: str, expected: str) -> None:
        assert ops.completion_mode(token) == expected

    def test_complete_token_unique(self) -> None:
        result = ops.complete_token("re", ["readme.md", "other.txt"])
        assert result.token == "readme.md"
        assert result.unique is True

    def test_complete_token_common_prefix(self) -> None:
        result = ops.complete_token("re", ["readme.md", "report.txt", "x"])
        assert result.token == "re"
        assert result.unique is False
        assert result.candidates == ("readme.md", "report.txt")

        result = ops.complete_token("rea", ["readme.md", "reap.txt"])
        assert result.token == "rea"
        result = ops.complete_token("re", ["readme.md", "reap.txt"])
        assert result.token == "rea"

    def test_complete_token_prefix_extends(self) -> None:
        result = ops.complete_token("g", ["git-status", "git-diff"])
        assert result.token == "git-"

    def test_complete_token_no_match(self) -> None:
        result = ops.complete_token("zz", ["a", "b"])
        assert result.token == "zz"
        assert result.candidates == ()
        assert result.unique is False


class TestViewModes:
    """视图模式与列宽的纯逻辑口径。"""

    def test_normalize_view_mode(self) -> None:
        for mode in ops.VIEW_MODES:
            assert ops.normalize_view_mode(mode) == mode
        for value in ("grid", "", None, 3, True):
            assert ops.normalize_view_mode(value) == ops.DEFAULT_VIEW_MODE

    def test_column_display_names(self) -> None:
        assert ops.column_display_names("brief") == ("name",)
        assert ops.column_display_names("detailed") == tuple(column for column, _label, _width in ops.PANEL_COLUMNS)
        assert ops.column_display_names("thumbnails") == ()
        assert ops.column_display_names("bad") == ops.column_display_names("detailed")

    def test_column_definitions_are_fixed_order(self) -> None:
        assert [column for column, _label, _width in ops.PANEL_COLUMNS] == [
            "name",
            "ext",
            "size",
            "mtime",
            "attr",
        ]
        assert set(ops.COLUMN_SORT) == {column for column, _label, _width in ops.PANEL_COLUMNS}
        assert ops.COLUMN_SORT["ext"] == "type"

    def test_normalize_column_widths_drops_unknown_and_clamps(self) -> None:
        widths = ops.normalize_column_widths(
            {
                "name": 500,
                "ext": 10,
                "size": 99999,
                "mtime": True,
                "attr": "90",
                "bogus": 120,
            }
        )

        assert widths == {
            "name": 500,
            "ext": ops.COLUMN_MIN_WIDTH,
            "size": ops.COLUMN_MAX_WIDTH,
        }

    def test_normalize_column_widths_non_dict(self) -> None:
        for value in (None, (), "100", 3):
            assert ops.normalize_column_widths(value) == {}


class TestThumbnails:
    """缩略图的格式判定、世代号、缓存与网格几何。"""

    @pytest.mark.parametrize(
        "name",
        ["a.png", "a.GIF", "a.jpg", "a.JPEG", "a.webp", "a.bmp", "a.tif", "a.tiff", "a.ico", "a.ppm"],
    )
    def test_thumbnail_plan_accepts_pillow_formats(self, name: str) -> None:
        assert ops.thumbnail_plan(name) == ops.THUMBNAIL_IMAGE

    @pytest.mark.parametrize("name", ["a.txt", "README", "a.png.txt", "a."])
    def test_thumbnail_plan_falls_back_to_badge(self, name: str) -> None:
        assert ops.thumbnail_plan(name) == ops.THUMBNAIL_BADGE

    def test_thumbnail_unsupported_hint(self) -> None:
        assert "TXT 不是支持的图片格式" in ops.thumbnail_unsupported_hint("a.txt")
        assert "README 不是支持的图片格式" in ops.thumbnail_unsupported_hint("README")

    @pytest.mark.parametrize("reason", ops.THUMBNAIL_INVALIDATION_REASONS)
    def test_tracker_invalidates_old_generation_for_each_reason(self, reason: str) -> None:
        tracker = ops.ThumbnailRequestTracker()
        generation = tracker.generation
        new_generation = tracker.invalidate(reason)
        assert new_generation == generation + 1
        assert tracker.reason == reason
        assert tracker.accept(generation) is False
        assert tracker.accept(new_generation) is True

    def test_tracker_accepts_current_generation_only(self) -> None:
        tracker = ops.ThumbnailRequestTracker(generation=3)
        assert tracker.accept(3) is True
        assert tracker.accept(2) is False
        assert tracker.accept(4) is False

    def test_cache_lru_by_item_count(self) -> None:
        cache = ops.ThumbnailCache(max_items=2, max_bytes=10_000)
        cache.put(("a", 0, 0, 0), object(), 1)
        cache.put(("b", 0, 0, 0), object(), 1)
        cache.get(("a", 0, 0, 0))
        cache.put(("c", 0, 0, 0), object(), 1)
        assert ("a", 0, 0, 0) in cache
        assert ("b", 0, 0, 0) not in cache
        assert ("c", 0, 0, 0) in cache
        assert len(cache) == 2

    def test_cache_evicts_by_byte_budget(self) -> None:
        cache = ops.ThumbnailCache(max_items=10, max_bytes=100)
        cache.put(("a", 0, 0, 0), object(), 60)
        cache.put(("b", 0, 0, 0), object(), 60)
        assert ("a", 0, 0, 0) not in cache
        assert ("b", 0, 0, 0) in cache
        assert cache.size_bytes == 60

    def test_cache_keeps_failed_entries_as_negative_results(self) -> None:
        cache = ops.ThumbnailCache()
        cache.put(("a", 0, 0, 0), None, 0)
        assert ("a", 0, 0, 0) in cache
        assert cache.get(("a", 0, 0, 0)) is None

    def test_cache_clear_releases_bytes(self) -> None:
        cache = ops.ThumbnailCache()
        cache.put(("a", 0, 0, 0), object(), 32)
        cache.clear()
        assert len(cache) == 0
        assert cache.size_bytes == 0

    def test_thumbnail_columns(self) -> None:
        assert ops.thumbnail_columns(0, 96, 8) == 1
        assert ops.thumbnail_columns(96, 96, 8) == 1
        assert ops.thumbnail_columns(200, 96, 8) == 2
        assert ops.thumbnail_columns(1000, 96, 8) == 9

    def test_thumbnail_visible_rows(self) -> None:
        assert ops.thumbnail_visible_rows(0, 0, 116, 0) == (0, -1)
        assert ops.thumbnail_visible_rows(0, 300, 116, 10) == (0, 3)
        assert ops.thumbnail_visible_rows(0, 300, 116, 2) == (0, 1)
        assert ops.thumbnail_visible_rows(116 * 5, 300, 116, 10) == (4, 8)
        assert ops.thumbnail_visible_rows(116 * 50, 300, 116, 10) == (0, -1)
        assert ops.thumbnail_visible_rows(0, 300, 116, 10, overscan=0) == (0, 2)

    def test_thumbnail_cache_key_changes_with_metadata(self) -> None:
        base = ops.thumbnail_cache_key("/a.png", 1, 2, 96)
        assert base == ops.thumbnail_cache_key("/a.png", 1, 2, 96)
        assert base != ops.thumbnail_cache_key("/a.png", 2, 2, 96)
        assert base != ops.thumbnail_cache_key("/a.png", 1, 3, 96)
        assert base != ops.thumbnail_cache_key("/a.png", 1, 2, 128)
